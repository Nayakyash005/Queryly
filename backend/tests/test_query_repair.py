import json
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.auth import get_current_user
from app.main import app
from app.models import QueryExecutionResponse, SQLGenerationResult, SQLValidationResult
from app.query_repair import execute_query_with_repair
from app.schema_inspector import inspect_database_schema
from app.sql_generator import SQLGenerationError
from app.sql_validator import validate_sql as real_validate_sql

SAMPLE_DB = Path(__file__).resolve().parents[1] / "data" / "sample.db"
CONNECTION_URL = f"sqlite+pysqlite:///{SAMPLE_DB.as_posix()}"


@pytest.fixture(scope="module")
def inspected_schema():
    assert SAMPLE_DB.is_file()
    return inspect_database_schema(CONNECTION_URL)


def test_run_query_executes_exact_sql_without_repair(monkeypatch, inspected_schema):
    query = "SELECT name FROM customers ORDER BY id LIMIT 1"
    monkeypatch.setattr("app.query_repair.repair_sql", lambda *_: pytest.fail("valid SQL must not be repaired"))

    result = execute_query_with_repair(CONNECTION_URL, query, "List a customer", "sqlite")

    assert result.success is True
    assert result.query == query
    assert result.attempt_count == 0
    assert result.repaired is False
    assert result.rows == [["Maya Chen"]]


def test_run_query_repairs_invalid_column_and_revalidates_before_execute(monkeypatch, inspected_schema):
    original = "SELECT missing FROM customers"
    corrected = "SELECT name FROM customers ORDER BY id LIMIT 1"
    repair_inputs = []
    validations = []

    def validate(query, schema, dialect):
        validations.append(query)
        return real_validate_sql(query, schema, dialect)

    def repair(question, schema, dialect, failed_sql, safe_error):
        repair_inputs.append((question, dialect, failed_sql, safe_error))
        assert len(schema.tables) == 4
        return SQLGenerationResult(query=corrected, explanation="Returns one customer.")

    monkeypatch.setattr("app.query_repair.validate_sql", validate)
    monkeypatch.setattr("app.query_repair.repair_sql", repair)
    result = execute_query_with_repair(CONNECTION_URL, original, "List a customer", "sqlite")

    assert result.success is True
    assert result.query == corrected
    assert result.repaired is True
    assert result.attempt_count == 1
    assert validations == [original, corrected]
    assert repair_inputs[0][:3] == ("List a customer", "sqlite", original)
    assert "column not present" in repair_inputs[0][3]


def test_run_query_exhaustion_preserves_the_original_sql(monkeypatch, inspected_schema):
    original = "SELECT missing FROM customers"
    repair_calls = []

    def repair(*args):
        repair_calls.append(args)
        return SQLGenerationResult(query="SELECT still_missing FROM customers", explanation="Retry.")

    monkeypatch.setattr("app.query_repair.repair_sql", repair)
    result = execute_query_with_repair(CONNECTION_URL, original, "List customers", "sqlite")

    assert result.success is False
    assert result.query == original
    assert result.repaired is False
    assert result.attempt_count == 3
    assert len(repair_calls) == 3
    assert "column not present" in (result.error or "")


def test_run_query_does_not_repair_unsafe_sql(monkeypatch, inspected_schema):
    monkeypatch.setattr("app.query_repair.repair_sql", lambda *_: pytest.fail("unsafe SQL must not be repaired"))
    result = execute_query_with_repair(CONNECTION_URL, "DELETE FROM customers", "Delete customers", "sqlite")

    assert result.success is False
    assert result.query == "DELETE FROM customers"
    assert result.attempt_count == 0
    assert "Only read-only SELECT" in (result.error or "")


def test_run_query_does_not_repair_connection_security_failures(monkeypatch):
    monkeypatch.setattr(
        "app.query_repair.inspect_database_schema",
        lambda _url: (_ for _ in ()).throw(HTTPException(status_code=400, detail="Private network hosts are not allowed.")),
    )
    monkeypatch.setattr("app.query_repair.repair_sql", lambda *_: pytest.fail("connection policy failures must not be repaired"))

    with pytest.raises(HTTPException, match="Private network hosts"):
        execute_query_with_repair("postgresql://blocked", "SELECT 1", "Return one", "postgres")


def test_run_query_does_not_retry_missing_groq_configuration(monkeypatch, inspected_schema):
    calls = []

    def unavailable(*_args):
        calls.append(True)
        raise SQLGenerationError("SQL generation is not configured. Set GROQ_API_KEY and GROQ_MODEL.")

    monkeypatch.setattr("app.query_repair.repair_sql", unavailable)
    original = "SELECT missing FROM customers"
    result = execute_query_with_repair(CONNECTION_URL, original, "List customers", "sqlite")

    assert result.success is False
    assert result.query == original
    assert result.attempt_count == 1
    assert len(calls) == 1
    assert "not configured" in (result.error or "")


def test_run_query_endpoint_authenticates_before_repair(inspected_schema):
    with TestClient(app) as client:
        response = client.post(
            "/api/query/execute",
            json={
                "connection_url": CONNECTION_URL,
                "query": "SELECT missing FROM customers",
                "schema": inspected_schema.model_dump(mode="json", by_alias=True),
                "dialect": "sqlite",
                "question": "List customers",
            },
        )

    assert response.status_code == 401


def test_run_query_stream_emits_repair_stages_in_order(monkeypatch, inspected_schema):
    from app import query_repair

    original = "SELECT missing FROM customers"
    corrected = "SELECT name FROM customers ORDER BY id LIMIT 1"
    monkeypatch.setattr(query_repair, "inspect_database_schema", lambda _url: inspected_schema)
    monkeypatch.setattr(
        query_repair,
        "repair_sql",
        lambda *_: SQLGenerationResult(query=corrected, explanation="Returns one customer."),
    )
    monkeypatch.setattr(
        query_repair,
        "execute_query",
        lambda _url, query, *_: QueryExecutionResponse(
            success=True, columns=["name"], rows=[["Maya Chen"]], row_count=1,
        ) if query == corrected else pytest.fail("Invalid SQL reached execution"),
    )
    app.dependency_overrides[get_current_user] = lambda: {"sub": "test-user"}
    try:
        response = TestClient(app).post(
            "/api/query/execute/stream",
            headers={"Authorization": "Bearer test-token"},
            json={
                "connection_url": CONNECTION_URL,
                "query": original,
                "schema": inspected_schema.model_dump(mode="json", by_alias=True),
                "dialect": "sqlite",
                "question": "List a customer",
            },
        )
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    stages = [
        json.loads(block.split("data: ", 1)[1])["stage"]
        for block in response.text.split("\n\n")
        if block.startswith("event: stage\n")
    ]
    assert stages == ["analyzing_schema", "validating_sql", "sql_error", "repairing_sql", "validating_sql", "executing_query", "completed"]
    assert '"query":"' + corrected + '"' in response.text


def test_run_query_stream_requires_authentication(inspected_schema):
    response = TestClient(app).post(
        "/api/query/execute/stream",
        json={
            "connection_url": CONNECTION_URL,
            "query": "SELECT 1",
            "schema": inspected_schema.model_dump(mode="json", by_alias=True),
            "dialect": "sqlite",
            "question": "Return one",
        },
    )
    assert response.status_code == 401
