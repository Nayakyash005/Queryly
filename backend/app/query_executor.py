"""Execute validated SELECT queries with read-only database safeguards."""

import base64
import json
import logging
import math
import sqlite3
import time
from datetime import date, datetime, time as time_type
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.auth import get_current_user
from app.config import get_settings
from app.connections import _build_engine
from app.models import (
    QueryExecutionRequest,
    QueryExecutionResponse,
    SQLValidationResult,
)
from app.sql_validator import validate_sql

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/query", tags=["query"])


def _canonical_dialect(dialect: str) -> str | None:
    normalized = dialect.strip().lower()
    if normalized in {"sqlite", "sqlite+pysqlite"}:
        return "sqlite"
    if normalized in {"postgres", "postgresql", "postgresql+psycopg"}:
        return "postgres"
    if normalized in {"mysql", "mysql+pymysql"}:
        return "mysql"
    return None


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date, time_type)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {"encoding": "base64", "data": base64.b64encode(bytes(value)).decode("ascii")}
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return str(value)


def _execution_error(message: str, error_kind: str | None = None) -> QueryExecutionResponse:
    return QueryExecutionResponse(success=False, error=message, error_kind=error_kind)


def _repairable_sql_exception(error: Exception) -> bool:
    """Classify only common SQL mistakes; connection and policy failures stop."""
    original = getattr(error, "orig", error)
    sqlstate = getattr(original, "sqlstate", None) or getattr(original, "pgcode", None)
    if sqlstate in {"42601", "42703", "42P01", "42804", "42883", "22012"}:
        return True
    mysql_code = original.args[0] if getattr(original, "args", None) and isinstance(original.args[0], int) else None
    if mysql_code in {1052, 1054, 1064, 1111, 1146, 1305, 1365, 1366}:
        return True
    if isinstance(original, sqlite3.OperationalError):
        message = str(original).casefold()
        return any(token in message for token in (
            "syntax error", "no such table", "no such column", "ambiguous column",
            "misuse of aggregate", "datatype mismatch", "no such function",
        ))
    return False


def _is_query_timeout(error: Exception, sqlite_timed_out: bool) -> bool:
    if sqlite_timed_out:
        return True
    original = getattr(error, "orig", None)
    return getattr(original, "sqlstate", None) == "57014" or getattr(original, "pgcode", None) == "57014"


def execute_query(
    connection_url: str,
    query: str,
    schema: Any,
    dialect: str,
) -> QueryExecutionResponse:
    """Validate then execute one query on a temporary, read-only connection."""
    settings = get_settings()
    timeout_seconds = settings.query_timeout_seconds
    max_rows = settings.max_query_rows
    engine: Engine | None = None
    response: QueryExecutionResponse | None = None
    sqlite_connection: sqlite3.Connection | None = None
    sqlite_timed_out = [False]
    deadline = [float("inf")]

    try:
        engine = _build_engine(connection_url)
        actual_dialect = engine.dialect.name
        requested_dialect = _canonical_dialect(dialect)
        connection_dialect = _canonical_dialect(actual_dialect)
        if requested_dialect is None or connection_dialect is None or requested_dialect != connection_dialect:
            response = _execution_error("The selected SQL dialect does not match the connected database.")
        else:
            with engine.connect() as connection:
                try:
                    if connection_dialect == "sqlite":
                        sqlite_connection = connection.connection.driver_connection

                        def interrupt_if_timed_out() -> int:
                            if time.monotonic() >= deadline[0]:
                                sqlite_timed_out[0] = True
                                return 1
                            return 0

                        sqlite_connection.set_progress_handler(interrupt_if_timed_out, 1000)
                        # SQLite's query_only flag applies to this DBAPI connection,
                        # including in-memory databases, in addition to file mode=ro.
                        connection.exec_driver_sql("PRAGMA query_only = ON")
                        connection.commit()
                    elif connection_dialect == "mysql":
                        # This temporary DBAPI connection is configured read-only
                        # before the query transaction begins. It is discarded
                        # with the temporary engine after this request.
                        raw_connection = connection.connection.driver_connection
                        cursor = raw_connection.cursor()
                        try:
                            cursor.execute("SET SESSION TRANSACTION READ ONLY")
                        finally:
                            cursor.close()

                    with connection.begin():
                        if connection_dialect == "postgres":
                            # Must be the first statement in this transaction.
                            connection.exec_driver_sql("SET TRANSACTION READ ONLY")
                            timeout_ms = timeout_seconds * 1000
                            connection.exec_driver_sql(f"SET LOCAL statement_timeout = '{timeout_ms}ms'")
                        else:
                            deadline[0] = time.monotonic() + timeout_seconds

                        # Deliberately validate immediately before executing the
                        # supplied statement; API callers cannot bypass validation.
                        validation: SQLValidationResult = validate_sql(query, schema, connection_dialect)
                        if not validation.valid:
                            response = _execution_error(
                                validation.error or "The SQL query did not pass validation."
                            )
                        else:
                            result = connection.execute(text(validation.query or query))
                            fetched = result.fetchmany(max_rows + 1)
                            truncated = len(fetched) > max_rows
                            rows = [
                                [_json_safe(value) for value in row]
                                for row in fetched[:max_rows]
                            ]
                            response = QueryExecutionResponse(
                                success=True,
                                columns=list(result.keys()),
                                rows=rows,
                                row_count=len(rows),
                                truncated=truncated,
                            )
                finally:
                    if sqlite_connection is not None:
                        try:
                            sqlite_connection.set_progress_handler(None, 0)
                        except Exception as exc:
                            logger.warning("SQLite query timeout handler cleanup failed (%s).", type(exc).__name__)
                        finally:
                            sqlite_connection = None
    except HTTPException:
        raise
    except Exception as exc:
        timed_out = _is_query_timeout(exc, sqlite_timed_out[0])
        logger.warning("Read-only query execution failed (%s).", type(exc).__name__)
        response = _execution_error(
            "The query exceeded the configured timeout."
            if timed_out
            else "The query could not be completed. Check the SQL and database connection.",
            "sql" if not timed_out and _repairable_sql_exception(exc) else "execution",
        )
    finally:
        if engine is not None:
            try:
                engine.dispose()
            except Exception as exc:
                logger.warning("Temporary query engine disposal failed (%s).", type(exc).__name__)
                response = _execution_error("The database query could not be finalized safely.")

    return response or _execution_error("The database query could not be completed.")


@router.post("/execute", response_model=QueryExecutionResponse)
def execute_query_endpoint(
    payload: QueryExecutionRequest,
    _claims: dict[str, Any] = Depends(get_current_user),
) -> QueryExecutionResponse:
    """Execute one schema-validated SELECT for an authenticated user."""
    if payload.question is not None:
        # The workspace supplies its current question to opt into SQL repair.
        # Requests from existing clients without a question retain direct execution.
        from app.query_repair import execute_query_with_repair

        return execute_query_with_repair(
            payload.connection_url.get_secret_value(),
            payload.query,
            payload.question,
            payload.dialect,
        )
    return execute_query(
        payload.connection_url.get_secret_value(),
        payload.query,
        payload.database_schema,
        payload.dialect,
    )


@router.post("/execute/stream")
def execute_query_stream_endpoint(
    payload: QueryExecutionRequest,
    _claims: dict[str, Any] = Depends(get_current_user),
) -> Any:
    """Stream validation, execution, and repair stages for Run Query."""
    from fastapi.responses import StreamingResponse
    from app.query_repair import query_repair_events

    def stream():
        try:
            for stage, result in query_repair_events(
                payload.connection_url.get_secret_value(),
                payload.query,
                payload.question or "",
                payload.dialect,
            ):
                yield f"event: stage\ndata: {json.dumps({'stage': stage}, separators=(',', ':'))}\n\n"
                if result is not None:
                    event_name = "result" if result.success else "failed"
                    yield f"event: {event_name}\ndata: {json.dumps(result.model_dump(mode='json'), ensure_ascii=False, separators=(',', ':'))}\n\n"
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, str) else "The database request could not be completed safely."
            yield f"event: stage\ndata: {json.dumps({'stage': 'failed'}, separators=(',', ':'))}\n\n"
            yield f"event: failed\ndata: {json.dumps({'success': False, 'error': detail, 'query': payload.query}, ensure_ascii=False, separators=(',', ':'))}\n\n"
        except Exception as exc:
            logger.warning("Streaming query execution failed (%s).", type(exc).__name__)
            yield f"event: stage\ndata: {json.dumps({'stage': 'failed'}, separators=(',', ':'))}\n\n"
            yield f"event: failed\ndata: {json.dumps({'success': False, 'error': 'The query could not be completed safely.', 'query': payload.query}, separators=(',', ':'))}\n\n"

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )
