"""Parse and check generated read-only SQL against an inspected schema.

This module never opens a database connection or executes a statement.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError
from sqlglot.optimizer.scope import Scope, traverse_scope

from app.models import DatabaseSchemaResponse, SQLValidationResult

SUPPORTED_DIALECTS = {
    "sqlite": "sqlite",
    "postgres": "postgres",
    "postgresql": "postgres",
    "mysql": "mysql",
}

# Known side-effecting or filesystem/network-oriented functions that can appear
# inside syntactically valid SELECT statements. Unrecognized anonymous functions
# are rejected because their read-only behavior cannot be established here.
SIDE_EFFECT_FUNCTIONS = {
    "sqlite": {"load_extension", "writefile", "readfile", "changes", "last_insert_rowid"},
    "postgres": {
        "nextval", "setval", "pg_notify", "pg_advisory_lock", "pg_advisory_xact_lock",
        "pg_try_advisory_lock", "pg_try_advisory_xact_lock", "pg_advisory_unlock",
        "pg_advisory_unlock_all", "pg_cancel_backend", "pg_terminate_backend",
        "pg_reload_conf", "pg_rotate_logfile", "pg_read_file", "pg_read_binary_file",
        "pg_ls_dir", "pg_sleep", "lo_import", "lo_export", "dblink_exec", "set_config",
    },
    "mysql": {
        "benchmark", "get_lock", "load_file", "release_all_locks", "release_lock", "sleep",
    },
}
SAFE_ANONYMOUS_FUNCTIONS = {
    "sqlite": {
        "char", "concat", "concat_ws", "hex", "iif", "ifnull", "instr", "json",
        "json_array", "json_array_length", "json_extract", "json_insert", "json_object",
        "json_patch", "json_quote", "json_remove", "json_replace", "json_set", "json_type",
        "json_valid", "length", "ltrim", "printf", "quote", "replace", "round", "rtrim",
        "substr", "trim", "typeof", "unicode", "unhex",
    },
    "postgres": {
        "concat", "concat_ws", "decode", "encode", "ends_with", "greatest", "least",
        "md5", "regexp_match", "regexp_matches", "regexp_replace", "split_part", "starts_with",
        "to_char", "to_date", "to_number",
    },
    "mysql": {
        "date_format", "field", "find_in_set", "from_days", "if", "ifnull", "isnull",
        "json_contains", "json_extract", "json_length", "json_object", "json_unquote",
        "locate", "lpad", "ltrim", "make_set", "monthname", "nullif", "regexp_like",
        "regexp_replace", "rpad", "rtrim", "str_to_date", "substring_index", "sysdate",
        "to_days", "week", "yearweek",
    },
}


@dataclass(frozen=True)
class _TableInfo:
    name: str
    schema: str | None
    columns: tuple[str, ...]


@dataclass(frozen=True)
class _SourceInfo:
    columns: tuple[str, ...] | None
    schema: str | None = None


def _failure(message: str) -> SQLValidationResult:
    return SQLValidationResult(valid=False, error=message)


def _schema_tables(schema: DatabaseSchemaResponse | dict[str, Any]) -> list[_TableInfo]:
    if isinstance(schema, DatabaseSchemaResponse):
        raw_tables = [table.model_dump(mode="python", by_alias=True) for table in schema.tables]
    elif isinstance(schema, dict):
        raw_tables = schema.get("tables")
    else:
        raise ValueError
    if not isinstance(raw_tables, list):
        raise ValueError

    tables: list[_TableInfo] = []
    for table in raw_tables:
        if not isinstance(table, dict) or not isinstance(table.get("name"), str):
            raise ValueError
        raw_columns = table.get("columns")
        if not isinstance(raw_columns, list):
            raise ValueError
        columns = tuple(
            column["name"]
            for column in raw_columns
            if isinstance(column, dict) and isinstance(column.get("name"), str)
        )
        if len(columns) != len(raw_columns):
            raise ValueError
        schema_name = table.get("schema", table.get("schema_name"))
        if schema_name is not None and not isinstance(schema_name, str):
            raise ValueError
        tables.append(_TableInfo(name=table["name"], schema=schema_name, columns=columns))
    return tables


def _normalized_identifier(identifier: exp.Identifier | str, dialect: str) -> str:
    if isinstance(identifier, exp.Identifier):
        name = str(identifier.this)
        quoted = bool(identifier.args.get("quoted"))
    else:
        # Plain strings from reflected metadata and SQLGlot scope keys are
        # already-resolved names, so preserve PostgreSQL's physical spelling.
        name = identifier
        quoted = dialect == "postgres"
    if dialect == "sqlite":
        return name.casefold()
    if dialect == "mysql":
        # MySQL column names are case-insensitive. Table-name case sensitivity
        # varies by server configuration, so validation uses case-insensitive
        # matching and the database remains the final authority at execution.
        return name.casefold()
    # PostgreSQL folds unquoted identifiers to lowercase and preserves quoted names.
    return name if quoted else name.lower()


def _matches_identifier(identifier: exp.Identifier | str, actual_name: str, dialect: str) -> bool:
    return _normalized_identifier(identifier, dialect) == _normalized_identifier(actual_name, dialect)


def _source_alias(node: exp.Expression, alias: str, dialect: str) -> str:
    alias_identifier: exp.Identifier | None = None
    if isinstance(node, exp.Table):
        table_alias = node.args.get("alias")
        if table_alias is not None and isinstance(table_alias.this, exp.Identifier):
            alias_identifier = table_alias.this
        elif isinstance(node.this, exp.Identifier):
            alias_identifier = node.this
    elif isinstance(node.parent, exp.Subquery):
        table_alias = node.parent.args.get("alias")
        if table_alias is not None and isinstance(table_alias.this, exp.Identifier):
            alias_identifier = table_alias.this
    return _normalized_identifier(alias_identifier or alias, dialect)


def _resolve_table(table: exp.Table, tables: list[_TableInfo], dialect: str) -> _TableInfo | None:
    if not isinstance(table.this, exp.Identifier):
        return None
    if table.args.get("catalog"):
        return None
    name = table.this
    table_schema = table.args.get("db")
    matches = [candidate for candidate in tables if _matches_identifier(name, candidate.name, dialect)]
    if table_schema:
        if not isinstance(table_schema, exp.Identifier):
            return None
        matches = [
            candidate for candidate in matches
            if candidate.schema is not None and _matches_identifier(table_schema, candidate.schema, dialect)
        ]
    if len(matches) != 1:
        return None
    candidate = matches[0]
    normalized_name = candidate.name.casefold()
    normalized_schema = candidate.schema.casefold() if candidate.schema else ""
    if dialect == "sqlite" and normalized_name.startswith("sqlite_"):
        return None
    if dialect == "postgres" and (
        normalized_schema in {"pg_catalog", "information_schema"}
        or normalized_schema.startswith("pg_")
        or normalized_name.startswith("pg_")
    ):
        return None
    if dialect == "mysql" and normalized_schema in {
        "mysql", "information_schema", "performance_schema", "sys",
    }:
        return None
    return candidate


def _source_columns(source: TableInfoOrScope, tables: list[_TableInfo], dialect: str) -> _SourceInfo | None:
    if isinstance(source, exp.Table):
        info = _resolve_table(source, tables, dialect)
        if info is None:
            return None
        return _SourceInfo(columns=info.columns, schema=info.schema)
    if isinstance(source, Scope):
        selected_columns = list(source.outer_columns or source.expression.named_selects)
        if not selected_columns or "*" in selected_columns:
            return _SourceInfo(columns=None)
        return _SourceInfo(columns=tuple(selected_columns))
    return None


TableInfoOrScope = exp.Table | Scope


def _validate_query_scope(scope: Scope, tables: list[_TableInfo], dialect: str) -> str | None:
    # NATURAL/USING joins introduce implicit column references; reject those
    # forms until their inferred column behavior can be checked unambiguously.
    for join in scope.expression.args.get("joins") or []:
        if join.args.get("using") or join.args.get("method") == "NATURAL":
            return "NATURAL and USING joins are not supported by SQL validation."

    sources: dict[str, _SourceInfo] = {}
    for source_alias, (node, source) in scope.selected_sources.items():
        if not isinstance(source, (exp.Table, Scope)):
            return "Only tables, CTEs, and derived SELECTs can be used as query sources."
        if isinstance(source, exp.Table) and not isinstance(source.this, exp.Identifier):
            return "Table-valued functions are not allowed as query sources."
        source_info = _source_columns(source, tables, dialect)
        if source_info is None:
            return "The query references a table or schema not present in the inspected schema."
        alias_key = _source_alias(node, source_alias, dialect)
        if alias_key in sources:
            return "The query contains ambiguous table aliases."
        sources[alias_key] = source_info

    for column in scope.columns:
        catalog = column.args.get("catalog")
        schema_name = column.args.get("db")
        table_name = column.args.get("table")
        if catalog:
            return "Catalog-qualified columns are not allowed."

        source_info: _SourceInfo | None = None
        if isinstance(table_name, exp.Identifier):
            source_info = sources.get(_normalized_identifier(table_name, dialect))
            if source_info is None:
                return "The query references an unknown table alias."
            if schema_name:
                if not isinstance(schema_name, exp.Identifier) or source_info.schema is None:
                    return "The query references an unapproved schema."
                if not _matches_identifier(schema_name, source_info.schema, dialect):
                    return "The query references an unapproved schema."
        elif schema_name:
            return "Schema-qualified columns must also identify a table."

        column_identifier = column.args.get("this")
        if isinstance(column_identifier, exp.Star):
            continue
        if not isinstance(column_identifier, exp.Identifier):
            return "The query contains a column reference that cannot be validated."

        if source_info is not None:
            candidate_sources = [source_info]
        else:
            candidate_sources = list(sources.values())
        if not candidate_sources or any(source.columns is None for source in candidate_sources):
            return "A column reference through this derived source cannot be validated safely."

        matching_sources = [
            source for source in candidate_sources
            if any(_matches_identifier(column_identifier, actual, dialect) for actual in source.columns or ())
        ]
        if not matching_sources:
            return "The query references a column not present in the inspected schema."
        if source_info is None and len(matching_sources) > 1:
            return "An unqualified column reference is ambiguous across the query's tables."

    return None


def validate_sql(
    query: str,
    schema: DatabaseSchemaResponse | dict[str, Any],
    dialect: str,
) -> SQLValidationResult:
    """Validate one read-only SELECT query against inspected metadata, without execution."""
    if not isinstance(query, str) or not query.strip():
        return _failure("Enter a SQL query to validate.")
    normalized_dialect = dialect.strip().lower() if isinstance(dialect, str) else ""
    read_dialect = SUPPORTED_DIALECTS.get(normalized_dialect)
    if read_dialect is None:
        return _failure("SQL validation supports SQLite, PostgreSQL, and MySQL dialects.")
    try:
        tables = _schema_tables(schema)
    except (TypeError, ValueError, KeyError):
        return _failure("A valid inspected database schema is required.")

    try:
        statements = sqlglot.parse(query, read=read_dialect)
    except ParseError:
        return _failure(f"The query could not be parsed as {read_dialect} SQL.")
    executable = [statement for statement in statements if statement is not None]
    if len(executable) != 1:
        return _failure("Only one SQL statement is allowed.")
    expression = executable[0]
    if not isinstance(expression, (exp.Select, exp.SetOperation)):
        return _failure("Only read-only SELECT queries are allowed.")

    for node in expression.walk():
        if isinstance(node, (exp.DML, exp.DDL, exp.Command)):
            return _failure("Only read-only SELECT queries are allowed.")
        if isinstance(node, exp.Select) and (node.args.get("into") or node.args.get("locks")):
            return _failure("SELECT INTO and row-locking clauses are not allowed.")
        if isinstance(node, exp.Func):
            function_name = (node.name or node.sql_name()).casefold()
            if function_name in SIDE_EFFECT_FUNCTIONS[read_dialect]:
                return _failure("This query uses a function that is not allowed in read-only SQL.")
            if node.args.get("db") or node.args.get("catalog"):
                return _failure("Schema-qualified functions are not allowed.")
            if isinstance(node, exp.Anonymous) and function_name not in SAFE_ANONYMOUS_FUNCTIONS[read_dialect]:
                return _failure("The query uses a function that cannot be verified as read-only.")

    try:
        scopes = list(traverse_scope(expression))
    except Exception:
        return _failure("The query uses a SQL structure that cannot be validated safely.")
    for scope in scopes:
        message = _validate_query_scope(scope, tables, read_dialect)
        if message:
            return _failure(message)

    return SQLValidationResult(valid=True, query=query.strip())
