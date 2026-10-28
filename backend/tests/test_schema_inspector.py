from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError

from app.auth import get_current_user
from app.main import app


@pytest.fixture
def authenticated_client() -> Generator[TestClient, None, None]:
    app.dependency_overrides[get_current_user] = lambda: {"sub": "test-user-id"}
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_schema_endpoint_describes_sample_tables_and_relationships(
    authenticated_client: TestClient,
) -> None:
    sample_db = Path(__file__).resolve().parents[1] / "data" / "sample.db"
    assert sample_db.is_file(), "Create the sample database with backend/scripts/create_sample_db.py first."
    connection_url = f"sqlite+pysqlite:///{sample_db.as_posix()}"

    response = authenticated_client.post(
        "/api/connections/schema",
        json={"connection_url": connection_url},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    tables = {table["name"]: table for table in body["tables"]}
    assert set(tables) == {"customers", "products", "orders", "order_items"}
    assert {column["name"] for column in tables["customers"]["columns"]} == {
        "id", "name", "email", "city"
    }
    assert tables["customers"]["primary_key"]["columns"] == ["id"]
    assert next(column for column in tables["customers"]["columns"] if column["name"] == "id")["primary_key"]

    orders_fks = tables["orders"]["foreign_keys"]
    assert any(
        foreign_key["columns"] == ["customer_id"]
        and foreign_key["referred_table"] == "customers"
        and foreign_key["referred_columns"] == ["id"]
        for foreign_key in orders_fks
    )
    order_item_targets = {
        foreign_key["referred_table"]: (foreign_key["columns"], foreign_key["referred_columns"])
        for foreign_key in tables["order_items"]["foreign_keys"]
    }
    assert order_item_targets == {"orders": (["order_id"], ["id"]), "products": (["product_id"], ["id"])}
    assert {relationship["to_table"] for relationship in tables["order_items"]["relationships"]} == {
        "orders", "products"
    }
    assert connection_url not in response.text


def test_schema_endpoint_requires_authentication() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/api/connections/schema",
            json={"connection_url": "sqlite+pysqlite:///:memory:"},
        )

    assert response.status_code == 401


def test_schema_endpoint_reuses_sqlite_path_restriction(
    authenticated_client: TestClient,
) -> None:
    outside_file = Path(__file__).resolve()
    response = authenticated_client.post(
        "/api/connections/schema",
        json={"connection_url": f"sqlite:///{outside_file.as_posix()}"},
    )

    assert response.status_code == 422
    assert "limited to existing files" in response.json()["detail"]


def test_schema_engine_is_disposed_when_inspection_fails(
    authenticated_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    disposed = False

    class FailingEngine:
        def connect(self) -> None:
            raise SQLAlchemyError("postgresql://user:do-not-leak@host/db")

        def dispose(self) -> None:
            nonlocal disposed
            disposed = True

    monkeypatch.setattr("app.schema_inspector._build_engine", lambda _url: FailingEngine())
    response = authenticated_client.post(
        "/api/connections/schema",
        json={"connection_url": "sqlite+pysqlite:///:memory:"},
    )

    assert response.status_code == 400
    assert "do-not-leak" not in response.text
    assert "postgresql://" not in response.text
    assert disposed is True


def test_mysql_schema_inspection_uses_selected_database_as_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    from app import schema_inspector

    calls = []

    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    class FakeEngine:
        dialect = SimpleNamespace(name="mysql")

        def connect(self):
            return FakeConnection()

        def dispose(self):
            calls.append(("dispose",))

    class FakeInspector:
        default_schema_name = "sales"

        def get_table_names(self):
            return ["Customers"]

        def get_pk_constraint(self, table, schema=None):
            calls.append(("pk", table, schema))
            return {"name": "PRIMARY", "constrained_columns": ["Id"]}

        def get_columns(self, table, schema=None):
            calls.append(("columns", table, schema))
            return [{"name": "Id", "type": "INTEGER", "nullable": False}]

        def get_foreign_keys(self, table, schema=None):
            calls.append(("fks", table, schema))
            return []

    monkeypatch.setattr(schema_inspector, "_build_engine", lambda _url: FakeEngine())
    monkeypatch.setattr(schema_inspector, "inspect", lambda _connection: FakeInspector())

    result = schema_inspector.inspect_database_schema("mysql+pymysql://user:pass@db.example.com/sales")

    assert result.tables[0].name == "Customers"
    assert result.tables[0].schema_name == "sales"
    assert ("columns", "Customers", "sales") in calls
    assert ("dispose",) in calls
