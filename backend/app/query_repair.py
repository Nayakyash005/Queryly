"""Run an edited query with bounded, schema-grounded SQL repair."""

from collections.abc import Iterator

from app.models import QueryExecutionResponse
from app.query_executor import execute_query
from app.schema_inspector import inspect_database_schema
from app.sql_generator import SQLGenerationError, repair_sql
from app.sql_validator import validate_sql

MAX_REPAIR_ATTEMPTS = 3


def _repairable_validation_error(message: str) -> bool:
    lowered = message.casefold()
    # Do not repair schema/table denials: the validator uses that result for
    # unapproved and system schemas as well as ordinary missing tables.
    return any(fragment in lowered for fragment in (
        "could not be parsed",
        "references a column not present",
        "unknown table alias",
        "ambiguous table aliases",
        "ambiguous across",
    ))


def query_repair_events(
    connection_url: str,
    original_query: str,
    question: str,
    dialect: str,
) -> Iterator[tuple[str, QueryExecutionResponse | None]]:
    """Yield visible stages while executing the submitted SQL and any repairs.

    A fresh schema inspection ensures the repair prompt and validator use metadata
    read from the connected database, not caller-supplied metadata. Every candidate
    is validated here and again by ``execute_query`` immediately before execution.
    """
    yield "analyzing_schema", None
    schema = inspect_database_schema(connection_url)
    candidate = original_query
    explanation: str | None = None
    attempts = 0

    while True:
        yield "validating_sql", None
        validation = validate_sql(candidate, schema, dialect)
        if not validation.valid:
            error = validation.error or "The SQL query did not pass validation."
            yield "sql_error", None
            if not _repairable_validation_error(error):
                yield "failed", QueryExecutionResponse(success=False, error=error, query=original_query, attempt_count=attempts)
                return
        else:
            yield "executing_query", None
            result = execute_query(connection_url, candidate, schema, dialect)
            if result.success:
                completed = result.model_copy(update={
                    "query": candidate,
                    "explanation": explanation,
                    "attempt_count": attempts,
                    "repaired": candidate != original_query,
                })
                yield "completed", completed
                return
            error = result.error or "The query could not be completed."
            yield "sql_error", None
            if result.error_kind != "sql":
                yield "failed", QueryExecutionResponse(
                    success=False,
                    error=error,
                    query=original_query,
                    attempt_count=attempts,
                )
                return

        if attempts >= MAX_REPAIR_ATTEMPTS:
            yield "failed", QueryExecutionResponse(
                success=False,
                error=error,
                query=original_query,
                attempt_count=attempts,
            )
            return

        attempts += 1
        yield "repairing_sql", None
        try:
            corrected = repair_sql(question, schema, dialect, candidate, error)
        except SQLGenerationError as exc:
            # Configuration and Groq transport failures are final and never retried.
            if "malformed repair output" in str(exc).casefold() and attempts < MAX_REPAIR_ATTEMPTS:
                error = str(exc)
                continue
            yield "failed", QueryExecutionResponse(
                success=False,
                error=str(exc),
                query=original_query,
                attempt_count=attempts,
            )
            return
        candidate = corrected.query
        explanation = corrected.explanation


def execute_query_with_repair(
    connection_url: str,
    original_query: str,
    question: str,
    dialect: str,
) -> QueryExecutionResponse:
    """Compatibility wrapper for callers that need a regular JSON response."""
    response = None
    for _stage, payload in query_repair_events(connection_url, original_query, question, dialect):
        if payload is not None:
            response = payload
    return response or QueryExecutionResponse(
        success=False,
        error="The query could not be completed.",
        query=original_query,
    )
