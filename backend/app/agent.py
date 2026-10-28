"""Protected Text-to-SQL orchestration using existing inspection and execution services."""

import json
import logging
from collections.abc import Iterator
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from app.auth import get_current_user
from app.models import AgentRequest, AgentResponse
from app.query_executor import execute_query
from app.schema_inspector import inspect_database_schema
from app.sql_generator import SQLGenerationError, generate_sql, repair_sql
from app.sql_validator import validate_sql

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/query", tags=["query"])
MAX_REPAIR_ATTEMPTS = 3


def _event(name: str, data: dict[str, Any]) -> str:
    return f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False, separators=(',', ':'))}\n\n"


def _repairable_validation_error(message: str) -> bool:
    lowered = message.casefold()
    return any(fragment in lowered for fragment in (
        "could not be parsed", "references a column not present", "unknown table alias",
        "ambiguous table aliases", "ambiguous across",
    ))


def _repairable_generation_error(message: str) -> bool:
    lowered = message.casefold()
    return any(fragment in lowered for fragment in (
        "invalid response", "empty response", "malformed output", "malformed repair output", "incomplete output",
    ))


def agent_event_stream(
    connection_url: str,
    question: str,
    dialect: str,
) -> Iterator[str]:
    """Yield public progress events and a final result/failure as server-sent events."""
    repairs = 0
    query = ""
    explanation = ""

    def stage(name: str) -> str:
        return _event("stage", {"stage": name})

    def fail(message: str) -> Iterator[str]:
        yield _event("stage", {"stage": "failed"})
        yield _event("failed", {
            "query": query,
            "explanation": explanation,
            "error": message,
            "attempt_count": repairs,
        })

    def request_repair(failed_sql: str | None, error_message: str):
        nonlocal repairs
        last_error = error_message
        while repairs < MAX_REPAIR_ATTEMPTS:
            repairs += 1
            try:
                return repair_sql(question, schema, dialect, failed_sql or "", last_error)
            except SQLGenerationError as exc:
                last_error = str(exc)
                if not _repairable_generation_error(last_error):
                    raise
        raise SQLGenerationError(last_error)

    try:
        yield stage("analyzing_schema")
        schema = inspect_database_schema(connection_url)

        yield stage("drafting_sql")
        try:
            generated = generate_sql(question, schema, dialect)
        except SQLGenerationError as exc:
            if not _repairable_generation_error(str(exc)):
                raise
            yield stage("repairing_sql")
            generated = request_repair(None, str(exc))
        if not generated.query.strip():
            yield from fail(generated.explanation or "The request cannot be answered from the available schema.")
            return
        query, explanation = generated.query, generated.explanation

        while True:
            yield stage("validating_sql")
            validation = validate_sql(query, schema, dialect)
            if not validation.valid:
                message = validation.error or "The SQL query did not pass validation."
                yield stage("sql_error")
                if _repairable_validation_error(message) and repairs < MAX_REPAIR_ATTEMPTS:
                    yield stage("repairing_sql")
                    corrected = request_repair(query, message)
                    query, explanation = corrected.query, corrected.explanation
                    continue
                yield from fail(message)
                return

            # execute_query validates immediately before execution as a second
            # boundary. It also enforces connection restrictions and read-only mode.
            yield stage("executing_query")
            executed = execute_query(connection_url, query, schema, dialect)
            if executed.success:
                yield stage("completed")
                result = AgentResponse(
                    query=query,
                    explanation=explanation,
                    columns=executed.columns,
                    rows=executed.rows,
                    row_count=executed.row_count,
                    truncated=executed.truncated,
                    attempt_count=repairs,
                )
                yield _event("result", result.model_dump(mode="json"))
                return

            message = executed.error or "The query could not be completed."
            yield stage("sql_error")
            if executed.error_kind == "sql" and repairs < MAX_REPAIR_ATTEMPTS:
                yield stage("repairing_sql")
                corrected = request_repair(query, message)
                query, explanation = corrected.query, corrected.explanation
                continue
            yield from fail(message)
            return
    except HTTPException as exc:
        # Connection policy errors and upstream infrastructure failures are final.
        detail = exc.detail if isinstance(exc.detail, str) else "The database request could not be completed safely."
        yield from fail(detail)
    except SQLGenerationError as exc:
        # Configuration/model service failures are not SQL repair opportunities.
        yield from fail(str(exc))
    except Exception as exc:
        logger.warning("Text-to-SQL orchestration failed (%s).", type(exc).__name__)
        yield from fail("The query could not be completed. Review the database connection and try again.")


@router.post("/agent")
def run_agent_endpoint(
    payload: AgentRequest,
    _claims: dict[str, Any] = Depends(get_current_user),
) -> StreamingResponse:
    """Stream the authenticated agent's progress and final query result."""
    stream = agent_event_stream(
        payload.connection_url.get_secret_value(),
        payload.question,
        payload.dialect,
    )
    return StreamingResponse(
        stream,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )
