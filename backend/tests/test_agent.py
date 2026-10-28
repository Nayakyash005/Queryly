import json

from fastapi.testclient import TestClient

from app.auth import get_current_user
from app.main import app
from app.models import (
    AgentResponse,
    DatabaseSchemaResponse,
    QueryExecutionResponse,
    SQLGenerationResult,
    SQLValidationResult,
)
from app.sql_generator import SQLGenerationError


SCHEMA = DatabaseSchemaResponse.model_validate({"tables": []})


def _events(monkeypatch, *, generated=None, validation=None, executed=None, repair=None):
    from app import agent

    monkeypatch.setattr(agent, "inspect_database_schema", lambda _url: SCHEMA)
    monkeypatch.setattr(agent, "generate_sql", generated or (lambda *_: SQLGenerationResult(query="SELECT 1", explanation="Returns one.")))
    monkeypatch.setattr(agent, "validate_sql", validation or (lambda *_: SQLValidationResult(valid=True, query="SELECT 1")))
    monkeypatch.setattr(agent, "execute_query", executed or (lambda *_: QueryExecutionResponse(success=True, columns=["1"], rows=[[1]], row_count=1)))
    monkeypatch.setattr(agent, "repair_sql", repair or (lambda *_: SQLGenerationResult(query="SELECT 1", explanation="Returns one.")))
    return list(agent.agent_event_stream("sqlite+pysqlite:///:memory:", "Count", "sqlite"))


def _payload(events, event_name):
    block = next(item for item in events if item.startswith(f"event: {event_name}\n"))
    return json.loads(block.split("data: ", 1)[1])


def test_agent_success_emits_stages_and_final_result(monkeypatch):
    events = _events(monkeypatch)
    stages = [json.loads(event.split("data: ", 1)[1])["stage"] for event in events if event.startswith("event: stage")]
    assert stages == ["analyzing_schema", "drafting_sql", "validating_sql", "executing_query", "completed"]
    assert _payload(events, "result") == AgentResponse(
        query="SELECT 1", explanation="Returns one.", columns=["1"], rows=[[1]], row_count=1,
    ).model_dump(mode="json")


def test_agent_generation_failure_does_not_attempt_repair(monkeypatch):
    def fail(*_args):
        raise SQLGenerationError("SQL generation is not configured. Set GROQ_API_KEY and GROQ_MODEL.")

    events = _events(monkeypatch, generated=fail, repair=lambda *_: (_ for _ in ()).throw(AssertionError("must not repair")))
    failure = _payload(events, "failed")
    assert "not configured" in failure["error"]
    assert failure["attempt_count"] == 0
    assert any(event.startswith("event: stage\n") and json.loads(event.split("data: ", 1)[1])["stage"] == "failed" for event in events)
    assert not any(event.startswith("event: repairing_sql") for event in events)


def test_agent_rejects_unsafe_validation_failure_without_execution(monkeypatch):
    from app import agent

    executed = []
    monkeypatch.setattr(agent, "execute_query", lambda *_: executed.append(True))
    events = _events(
        monkeypatch,
        validation=lambda *_: SQLValidationResult(valid=False, error="Only read-only SELECT queries are allowed."),
        repair=lambda *_: (_ for _ in ()).throw(AssertionError("unsafe query must not be repaired")),
    )
    assert _payload(events, "failed")["error"] == "Only read-only SELECT queries are allowed."
    assert not executed


def test_agent_repairs_sql_execution_error_then_completes(monkeypatch):
    from app import agent

    attempts = iter([
        QueryExecutionResponse(success=False, error="The query could not be completed. Check the SQL and database connection.", error_kind="sql"),
        QueryExecutionResponse(success=True, columns=["total"], rows=[[4]], row_count=1),
    ])
    repaired = []
    events = _events(
        monkeypatch,
        executed=lambda *_: next(attempts),
        repair=lambda *_: repaired.append(True) or SQLGenerationResult(query="SELECT 4", explanation="Returns four."),
    )
    assert _payload(events, "result")["query"] == "SELECT 4"
    assert _payload(events, "result")["attempt_count"] == 1
    assert len(repaired) == 1
    assert any(event.startswith("event: stage\n") and json.loads(event.split("data: ", 1)[1])["stage"] == "repairing_sql" for event in events)


def test_agent_repairs_parser_error_and_revalidates_before_execute(monkeypatch):
    displayed_sql = (
        "SELECT c.id, c.name, COUNT(o.id) AS order_count FROM customers c "
        "JOIN orders o ON c.id = o.customer_id GROUP BY c.id, c.name "
        "ORDER BY order_count DESC LIMIT 1;"
    )
    validations = iter([
        SQLValidationResult(valid=False, error="The query could not be parsed as sqlite SQL."),
        SQLValidationResult(valid=True, query="SELECT 4"),
    ])
    validation_calls = []
    executed_queries = []
    repair_inputs = []

    def repair(question, schema, dialect, failed_sql, error):
        repair_inputs.append((question, schema, dialect, failed_sql, error))
        return SQLGenerationResult(query="SELECT 4", explanation="Returns four.")

    events = _events(
        monkeypatch,
        generated=lambda *_: SQLGenerationResult(query=displayed_sql, explanation="Customer order count."),
        validation=lambda *_: validation_calls.append(True) or next(validations),
        executed=lambda _url, query, *_: executed_queries.append(query) or QueryExecutionResponse(
            success=True, columns=["4"], rows=[[4]], row_count=1,
        ),
        repair=repair,
    )
    result = _payload(events, "result")
    assert result["query"] == "SELECT 4"
    assert result["attempt_count"] == 1
    assert len(validation_calls) == 2
    assert executed_queries == ["SELECT 4"]
    assert repair_inputs == [("Count", SCHEMA, "sqlite", displayed_sql, "The query could not be parsed as sqlite SQL.")]
    stages = [json.loads(event.split("data: ", 1)[1])["stage"] for event in events if event.startswith("event: stage")]
    assert stages == ["analyzing_schema", "drafting_sql", "validating_sql", "sql_error", "repairing_sql", "validating_sql", "executing_query", "completed"]


def test_agent_stops_after_three_repairs(monkeypatch):
    from app import agent

    repair_calls = []
    execution_calls = []
    events = _events(
        monkeypatch,
        validation=lambda *_: SQLValidationResult(valid=False, error="The query references a column not present in the inspected schema."),
        executed=lambda *_: execution_calls.append(True),
        repair=lambda *_: repair_calls.append(True) or SQLGenerationResult(query="SELECT missing", explanation="Try again."),
    )
    assert _payload(events, "failed")["attempt_count"] == 3
    assert len(repair_calls) == 3
    assert not execution_calls


def test_agent_endpoint_requires_authentication():
    response = TestClient(app).post(
        "/api/query/agent",
        json={"connection_url": "sqlite+pysqlite:///:memory:", "question": "Count", "dialect": "sqlite"},
    )
    assert response.status_code == 401


def test_agent_endpoint_streams_events_for_authenticated_user(monkeypatch):
    from app import agent

    app.dependency_overrides[get_current_user] = lambda: {"sub": "user-123"}
    monkeypatch.setattr(agent, "inspect_database_schema", lambda _url: SCHEMA)
    monkeypatch.setattr(agent, "generate_sql", lambda *_: SQLGenerationResult(query="SELECT 1", explanation="Returns one."))
    monkeypatch.setattr(agent, "validate_sql", lambda *_: SQLValidationResult(valid=True, query="SELECT 1"))
    monkeypatch.setattr(agent, "execute_query", lambda *_: QueryExecutionResponse(success=True, columns=["1"], rows=[[1]], row_count=1))
    try:
        response = TestClient(app).post(
            "/api/query/agent",
            headers={"Authorization": "Bearer test-token"},
            json={"connection_url": "sqlite+pysqlite:///:memory:", "question": "Count", "dialect": "sqlite"},
        )
    finally:
        app.dependency_overrides.pop(get_current_user, None)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache, no-transform"
    assert response.headers["x-accel-buffering"] == "no"
    assert response.text.count("event: stage\n") == 5
    assert all(f'"stage":"{stage}"' in response.text for stage in (
        "analyzing_schema", "drafting_sql", "validating_sql", "executing_query", "completed",
    ))
    assert "\n\n" in response.text and "event: result\n" in response.text
