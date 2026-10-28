"""Read table metadata from an allowed, temporary SQLAlchemy engine."""

import logging

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import inspect
from sqlalchemy.engine import Engine

from app.auth import get_current_user
from app.connections import _build_engine
from app.models import (
    ConnectionTestRequest,
    DatabaseSchemaResponse,
    SchemaColumn,
    SchemaForeignKey,
    SchemaPrimaryKey,
    SchemaRelationship,
    SchemaTable,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/connections", tags=["connections"])


def inspect_database_schema(connection_url: str) -> DatabaseSchemaResponse:
    """Inspect the default schema, applying the same URL policy as connection tests.

    The connection URL is used only to create this temporary engine and is never
    included in the returned schema or log messages.
    """
    engine: Engine | None = None
    try:
        # Importing the shared builder keeps URL restrictions, DNS/IP checks,
        # TLS requirements, and connection timeouts consistent across endpoints.
        engine = _build_engine(connection_url)
        with engine.connect() as connection:
            inspector = inspect(connection)
            default_schema = (
                inspector.default_schema_name
                if engine.dialect.name == "mysql"
                else None
            )
            table_schemas = [(default_schema, name) for name in inspector.get_table_names()]
            tables: list[SchemaTable] = []

            for schema_name, table_name in table_schemas:
                primary_key_data = inspector.get_pk_constraint(table_name, schema=schema_name)
                primary_key_columns = list(primary_key_data.get("constrained_columns") or [])
                primary_key_set = set(primary_key_columns)
                columns = [
                    SchemaColumn(
                        name=column["name"],
                        data_type=str(column["type"]),
                        nullable=bool(column.get("nullable", True)),
                        primary_key=column["name"] in primary_key_set,
                    )
                    for column in inspector.get_columns(table_name, schema=schema_name)
                ]

                foreign_keys: list[SchemaForeignKey] = []
                relationships: list[SchemaRelationship] = []
                for foreign_key in inspector.get_foreign_keys(table_name, schema=schema_name):
                    local_columns = list(foreign_key.get("constrained_columns") or [])
                    referred_columns = list(foreign_key.get("referred_columns") or [])
                    referred_table = foreign_key.get("referred_table")
                    # SQLAlchemy reports some dialect-specific reflection records
                    # without a target table; omit those incomplete relationships.
                    if not referred_table:
                        continue
                    referred_schema = foreign_key.get("referred_schema")
                    foreign_keys.append(
                        SchemaForeignKey(
                            name=foreign_key.get("name"),
                            columns=local_columns,
                            referred_schema=referred_schema,
                            referred_table=referred_table,
                            referred_columns=referred_columns,
                            on_delete=(foreign_key.get("options") or {}).get("ondelete"),
                        )
                    )
                    relationships.append(
                        SchemaRelationship(
                            from_columns=local_columns,
                            to_schema=referred_schema,
                            to_table=referred_table,
                            to_columns=referred_columns,
                        )
                    )

                tables.append(
                    SchemaTable(
                        name=table_name,
                        schema_name=schema_name,
                        columns=columns,
                        primary_key=SchemaPrimaryKey(
                            name=primary_key_data.get("name"),
                            columns=primary_key_columns,
                        ),
                        foreign_keys=foreign_keys,
                        relationships=relationships,
                    )
                )

        return DatabaseSchemaResponse(tables=tables)
    except HTTPException:
        raise
    except Exception as exc:
        # Driver exceptions can include the submitted URL and its password.
        logger.warning("Database schema inspection failed (%s).", type(exc).__name__)
        raise HTTPException(
            status_code=400,
            detail="Could not inspect the database schema. Check that the database is reachable and supports schema inspection.",
        ) from None
    finally:
        if engine is not None:
            try:
                engine.dispose()
            except Exception as exc:
                logger.warning("Temporary schema-inspection engine disposal failed (%s).", type(exc).__name__)


@router.post("/schema", response_model=DatabaseSchemaResponse)
def get_database_schema(
    payload: ConnectionTestRequest,
    _claims: dict[str, Any] = Depends(get_current_user),
) -> DatabaseSchemaResponse:
    """Return metadata for a protected database connection without persisting it."""
    return inspect_database_schema(payload.connection_url.get_secret_value())
