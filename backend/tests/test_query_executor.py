from collections.abc import Generator
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.auth import get_current_user
from app.main import app
from app.query_executor import _canonical_dialect, _json_safe, execute_query
from app.schema_inspector import inspect_database_schema


SAMPLE_DB = Path(__file__).resolve().parents[1] / "data" / "sample.db"


@pytest.fixture(scope="module")
def schema_payload() -> dict:
    assert SAMPLE_DB.is_file(), "Create backend/data/sample.db before running query-executor tests."
    schema = inspect_database_schema(f"sqlite+pysqlite:///{SAMPLE_DB.as_posix()}")
    return schema.model_dump(mode="json", by_alias=True)


@pytest.fixture
def authenticated_client() -> Generator[TestClient, None, None]:
    app.dependency_overrides[get_current_user] = lambda: {"sub": "test-user-id"}
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def post_query(client: TestClient, schema: dict, query: str, connection_url: str | None = None):
    return client.post(
        "/api/query/execute",
        json={
            "connection_url": connection_url or f"sqlite+pysqlite:///{SAMPLE_DB.as_posix()}",
            "query": query,
            "schema": schema,
            "dialect": "sqlite",
        },
    )


def test_json_safe_conversion_handles_dates_decimals_and_nulls() -> None:
    assert _json_safe(Decimal("12.3400")) == "12.3400"
    assert _json_safe(date(2026, 1, 2)) == "2026-01-02"
    assert _json_safe(datetime(2026, 1, 2, tzinfo=timezone.utc)) == "2026-01-02T00:00:00+00:00"
    assert _json_safe(None) is None


def test_mysql_dialect_is_canonicalized_and_executes_in_read_only_transaction(monkeypatch) -> None:
    from app.models import SQLValidationResult

    statements = []

    class Transaction:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    class Result:
        def fetchmany(self, _limit):
            return [(7,)]

        def keys(self):
            return ["value"]

    class Cursor:
        def execute(self, statement):
            statements.append(statement)

        def close(self):
            pass

    class DBAPIConnection:
        def cursor(self):
            return Cursor()

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        @property
        def connection(self):
            return SimpleNamespace(driver_connection=DBAPIConnection())

        def exec_driver_sql(self, statement):
            statements.append(statement)

        def commit(self):
            statements.append("COMMIT")

        def begin(self):
            return Transaction()

        def execute(self, statement):
            statements.append(str(statement))
            return Result()

    class Engine:
        dialect = SimpleNamespace(name="mysql")

        def connect(self):
            return Connection()

        def dispose(self):
            statements.append("DISPOSE")

    monkeypatch.setattr("app.query_executor._build_engine", lambda _url: Engine())
    monkeypatch.setattr("app.query_executor.get_settings", lambda: SimpleNamespace(query_timeout_seconds=5, max_query_rows=10))
    monkeypatch.setattr("app.query_executor.validate_sql", lambda query, *_args: SQLValidationResult(valid=True, query=query))

    result = execute_query("mysql+pymysql://example", "SELECT 7", {}, "mysql")

    assert _canonical_dialect("mysql+pymysql") == "mysql"
    assert result.success is True
    assert result.rows == [[7]]
    assert "SET SESSION TRANSACTION READ ONLY" in statements
    assert "SELECT 7" in statements
    assert statements[-1] == "DISPOSE"


def test_executes_select_against_sample_database_and_returns_json_safe_rows(
    authenticated_client: TestClient,
    schema_payload: dict,
) -> None:
    response = post_query(
        authenticated_client,
        schema_payload,
        "SELECT name, price FROM products ORDER BY id LIMIT 2",
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["success"] is True
    assert body["columns"] == ["name", "price"]
    assert body["rows"] == [["Trailhead Backpack", 89], ["Alpine Water Bottle", 24.5]]
    assert body["row_count"] == 2
    assert body["truncated"] is False
    assert body["error"] is None


def test_rejects_invalid_sql_before_execution(
    authenticated_client: TestClient,
    schema_payload: dict,
) -> None:
    response = post_query(authenticated_client, schema_payload, "DELETE FROM customers")

    assert response.status_code == 200
    assert response.json()["success"] is False
    assert "Only read-only SELECT" in response.json()["error"]
    assert response.json()["rows"] == []


def test_returns_empty_result_with_selected_columns(
    authenticated_client: TestClient,
    schema_payload: dict,
) -> None:
    response = post_query(
        authenticated_client,
        schema_payload,
        "SELECT name FROM customers WHERE id = -1",
    )

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert response.json()["columns"] == ["name"]
    assert response.json()["rows"] == []
    assert response.json()["row_count"] == 0
    assert response.json()["truncated"] is False


def test_enforces_configured_row_limit(
    authenticated_client: TestClient,
    schema_payload: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.query_executor.get_settings",
        lambda: SimpleNamespace(query_timeout_seconds=5, max_query_rows=3),
    )
    response = post_query(authenticated_client, schema_payload, "SELECT id FROM customers ORDER BY id")

    assert response.status_code == 200
    assert response.json()["row_count"] == 3
    assert len(response.json()["rows"]) == 3
    assert response.json()["truncated"] is True


def test_execution_endpoint_requires_authentication(schema_payload: dict) -> None:
    with TestClient(app) as client:
        response = post_query(client, schema_payload, "SELECT id FROM customers")

    assert response.status_code == 401


def test_disposes_engine_after_connection_failure(
    authenticated_client: TestClient,
    schema_payload: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    disposed = False

    class FailingEngine:
        dialect = SimpleNamespace(name="sqlite")

        def connect(self):
            raise RuntimeError("connection failed")

        def dispose(self) -> None:
            nonlocal disposed
            disposed = True

    monkeypatch.setattr("app.query_executor._build_engine", lambda _url: FailingEngine())
    response = post_query(authenticated_client, schema_payload, "SELECT id FROM customers")

    assert response.status_code == 200
    assert response.json()["success"] is False
    assert disposed is True


def test_user_id_in_body_is_not_accepted_as_authentication(
    authenticated_client: TestClient,
    schema_payload: dict,
) -> None:
    payload = {
        "connection_url": f"sqlite+pysqlite:///{SAMPLE_DB.as_posix()}",
        "query": "SELECT id FROM customers",
        "schema": schema_payload,
        "dialect": "sqlite",
        "user_id": "attacker-controlled-user-id",
    }
    response = authenticated_client.post("/api/query/execute", json=payload)

    assert response.status_code == 422
    assert "attacker-controlled-user-id" not in response.text
