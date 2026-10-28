from pathlib import Path

import pytest

from app.schema_inspector import inspect_database_schema
from app.sql_validator import validate_sql


@pytest.fixture(scope="module")
def sample_schema():
    sample_db = Path(__file__).resolve().parents[1] / "data" / "sample.db"
    assert sample_db.is_file(), "Create backend/data/sample.db before running validator tests."
    return inspect_database_schema(f"sqlite+pysqlite:///{sample_db.as_posix()}")


@pytest.mark.parametrize(
    "query",
    [
        "SELECT id, name, city FROM customers",
        "SELECT c.name FROM customers AS c WHERE c.city = 'Austin'",
    ],
)
def test_accepts_schema_valid_selects(sample_schema, query: str) -> None:
    result = validate_sql(query, sample_schema, "sqlite")

    assert result.valid is True
    assert result.query == query
    assert result.error is None


def test_accepts_join_with_aliases_and_qualified_columns(sample_schema) -> None:
    result = validate_sql(
        "SELECT c.name, o.order_date FROM customers c "
        "JOIN orders o ON c.id = o.customer_id",
        sample_schema,
        "sqlite",
    )

    assert result.valid is True


def test_accepts_screenshot_query_with_actual_sample_schema(sample_schema) -> None:
    query = (
        "SELECT c.id, c.name, COUNT(o.id) AS order_count FROM customers c "
        "JOIN orders o ON c.id = o.customer_id GROUP BY c.id, c.name "
        "ORDER BY order_count DESC LIMIT 1;"
    )
    result = validate_sql(query, sample_schema, "sqlite")

    assert result.valid is True
    assert result.error is None


def test_accepts_aggregate_query(sample_schema) -> None:
    result = validate_sql(
        "SELECT category, COUNT(*) AS product_count, AVG(price) AS average_price "
        "FROM products GROUP BY category",
        sample_schema,
        "sqlite",
    )

    assert result.valid is True


def test_accepts_cte_and_validates_its_inner_and_outer_columns(sample_schema) -> None:
    result = validate_sql(
        "WITH recent_orders AS (SELECT id, customer_id FROM orders) "
        "SELECT r.customer_id FROM recent_orders r",
        sample_schema,
        "sqlite",
    )

    assert result.valid is True


@pytest.mark.parametrize(
    ("query", "expected_error"),
    [
        ("SELECT * FROM customer_records", "table or schema"),
        ("SELECT missing FROM customers", "column not present"),
        ("SELECT c.missing FROM customers c", "column not present"),
        ("SELECT c.name FROM customers c JOIN orders o ON c.id = o.missing", "column not present"),
    ],
)
def test_rejects_unknown_tables_and_columns(sample_schema, query: str, expected_error: str) -> None:
    result = validate_sql(query, sample_schema, "sqlite")

    assert result.valid is False
    assert result.query is None
    assert expected_error in (result.error or "")


@pytest.mark.parametrize(
    "query",
    [
        "INSERT INTO customers (name, email, city) VALUES ('x', 'x@example.com', 'x')",
        "UPDATE customers SET city = 'x'",
        "DELETE FROM customers",
        "DROP TABLE customers",
        "ALTER TABLE customers ADD COLUMN active INTEGER",
        "CREATE TABLE extra (id INTEGER)",
        "SELECT name INTO archive FROM customers",
        "SELECT name FROM customers FOR UPDATE",
        "WITH removed AS (DELETE FROM customers RETURNING id) SELECT id FROM removed",
        "SELECT writefile('/tmp/out', name) FROM customers",
        "SELECT custom_side_effect(name) FROM customers",
    ],
)
def test_rejects_write_or_state_changing_operations(sample_schema, query: str) -> None:
    result = validate_sql(query, sample_schema, "sqlite")

    assert result.valid is False
    assert result.error


def test_rejects_multiple_statements(sample_schema) -> None:
    result = validate_sql("SELECT id FROM customers; SELECT id FROM products", sample_schema, "sqlite")

    assert result.valid is False
    assert result.error == "Only one SQL statement is allowed."


def test_accepts_postgresql_and_mysql_dialects_and_rejects_unsupported_dialect(sample_schema) -> None:
    postgres_result = validate_sql(
        "SELECT name FROM customers WHERE name ILIKE 'm%'",
        sample_schema,
        "postgresql",
    )
    mysql_result = validate_sql(
        "SELECT c.name FROM customers AS c WHERE c.city = 'Austin'",
        sample_schema,
        "mysql",
    )
    unsupported_result = validate_sql("SELECT name FROM customers", sample_schema, "oracle")

    assert postgres_result.valid is True
    assert mysql_result.valid is True
    assert unsupported_result.valid is False
    assert "supports SQLite, PostgreSQL, and MySQL" in (unsupported_result.error or "")


def test_mysql_identifier_validation_and_system_schema_restriction() -> None:
    schema = {
        "tables": [
            {"name": "Customers", "schema": "sales", "columns": [{"name": "CustomerName"}]},
            {"name": "user", "schema": "mysql", "columns": [{"name": "User"}]},
        ]
    }
    valid = validate_sql("SELECT `CustomerName` FROM sales.`Customers`", schema, "mysql")
    system = validate_sql("SELECT User FROM mysql.user", schema, "mysql")

    assert valid.valid is True
    assert system.valid is False
    assert "not present in the inspected schema" in (system.error or "")


def test_postgresql_quoted_identifiers_preserve_case() -> None:
    mixed_case_schema = {
        "tables": [
            {
                "name": "MixedCase",
                "schema": None,
                "columns": [{"name": "CaseCol"}],
            }
        ]
    }

    quoted = validate_sql('SELECT "CaseCol" FROM "MixedCase"', mixed_case_schema, "postgres")
    incorrectly_unquoted = validate_sql("SELECT CaseCol FROM MixedCase", mixed_case_schema, "postgres")

    assert quoted.valid is True
    assert incorrectly_unquoted.valid is False


def test_rejects_unapproved_schema_and_system_tables(sample_schema) -> None:
    unapproved_schema = validate_sql("SELECT name FROM public.customers", sample_schema, "sqlite")
    system_table = validate_sql("SELECT name FROM sqlite_master", sample_schema, "sqlite")

    assert unapproved_schema.valid is False
    assert "not present in the inspected schema" in (unapproved_schema.error or "")
    assert system_table.valid is False
