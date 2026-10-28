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


def test_sqlite_in_memory_connection_succeeds(authenticated_client: TestClient) -> None:
    response = authenticated_client.post(
        "/api/connections/test",
        json={"connection_url": "sqlite+pysqlite:///:memory:"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "success": True,
        "message": "Database connection successful.",
    }


def test_malformed_connection_url_is_rejected_without_echoing_it(
    authenticated_client: TestClient,
) -> None:
    connection_url = "postgresql://user:do-not-leak@db.example.com:invalid/app"
    response = authenticated_client.post(
        "/api/connections/test",
        json={"connection_url": connection_url},
    )

    assert response.status_code == 422
    assert "valid SQLAlchemy connection URL" in response.text
    assert "do-not-leak" not in response.text
    assert connection_url not in response.text


def test_unsupported_dialect_is_rejected_without_echoing_credentials(
    authenticated_client: TestClient,
) -> None:
    connection_url = "oracle+oracledb://user:do-not-leak@db.example.com/app"
    response = authenticated_client.post(
        "/api/connections/test",
        json={"connection_url": connection_url},
    )

    assert response.status_code == 422
    assert "Unsupported database dialect" in response.text
    assert "do-not-leak" not in response.text
    assert connection_url not in response.text


def test_mysql_url_decodes_special_credentials_and_builds_pinned_tls_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    from app import connections

    captured = {}
    monkeypatch.setattr(connections, "_resolve_public_host", lambda host, port, kind: "203.0.113.8")
    monkeypatch.setattr(
        connections,
        "create_engine",
        lambda url, **kwargs: captured.update(url=url, **kwargs) or object(),
    )

    engine = connections._build_engine(
        "mysql+pymysql://app%40user:p%40ss%3A%2F%2F%3F%23%25@db.example.com:3307/sales%20data"
    )

    assert engine is not None
    assert captured["url"].drivername == "mysql+pymysql"
    assert captured["url"].username == "app@user"
    assert captured["url"].password == "p@ss://?#%"
    assert captured["url"].database == "sales data"
    assert captured["poolclass"].__name__ == "NullPool"
    assert callable(captured["creator"])


def test_postgres_engine_uses_supported_psycopg_and_libpq_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Check the driver's connect arguments without opening a network connection."""
    from psycopg.conninfo import make_conninfo
    from sqlalchemy import create_engine

    from app import connections

    captured = {}
    monkeypatch.setattr(connections, "_resolve_public_host", lambda *_args: "8.8.8.8")

    def capture_engine(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return create_engine(url, **kwargs)

    monkeypatch.setattr(connections, "create_engine", capture_engine)
    engine = connections._build_engine(
        "postgresql://app%40user:p%40ss%3A%2F%2F%3F%23%25@db.example.com:5433/sales%20data?sslmode=verify-full"
    )
    try:
        assert engine.dialect.name == "postgresql"
        assert engine.dialect.driver == "psycopg"
        assert captured["url"].username == "app@user"
        assert captured["url"].password == "p@ss://?#%"
        assert captured["url"].database == "sales data"
        assert captured["connect_args"] == {
            "connect_timeout": 5,
            "hostaddr": "8.8.8.8",
            "options": "-c statement_timeout=5000",
        }

        # SQLAlchemy produces ordinary psycopg/libpq keyword arguments, and
        # psycopg's own parser accepts the options used by the engine builder.
        _, dialect_args = engine.dialect.create_connect_args(engine.url)
        conninfo = make_conninfo(
            **{key: dialect_args[key] for key in ("host", "dbname", "port", "sslmode")},
            **captured["connect_args"],
        )
        assert "hostaddr=8.8.8.8" in conninfo
        assert "sslmode=verify-full" in conninfo
        assert "connect_timeout=5" in conninfo
        assert "statement_timeout=5000" in conninfo
        assert captured["poolclass"].__name__ == "NullPool"
    finally:
        engine.dispose()


def test_mysql_connection_creator_pins_public_address_and_requires_tls(monkeypatch: pytest.MonkeyPatch) -> None:
    import ssl
    import sys
    from types import SimpleNamespace

    from app import connections

    captured = {}
    pinned_socket = object()
    tls_context = ssl.create_default_context()

    class FakePyMySQLConnection:
        def connect(self, sock=None):
            captured["pinned_socket"] = sock

        def close(self):
            captured["closed"] = True

    def create_dbapi_connection(**kwargs):
        captured["connect"] = kwargs
        return FakePyMySQLConnection()

    monkeypatch.setattr(connections, "_resolve_public_host", lambda *_args: "198.51.100.12")
    monkeypatch.setattr(connections, "get_settings", lambda: SimpleNamespace(query_timeout_seconds=9))
    monkeypatch.setattr(connections.socket, "create_connection", lambda address, timeout: captured.update(address=address, timeout=timeout) or pinned_socket)
    monkeypatch.setattr(connections.ssl, "create_default_context", lambda: tls_context)
    monkeypatch.setitem(sys.modules, "pymysql", SimpleNamespace(connect=create_dbapi_connection))
    monkeypatch.setattr(connections, "create_engine", lambda _url, **kwargs: captured.update(engine=kwargs) or object())

    connections._build_engine("mysql+pymysql://user:password@db.example.com/sales")
    created_connection = captured["engine"]["creator"]()

    assert isinstance(created_connection, FakePyMySQLConnection)
    assert captured["address"] == ("198.51.100.12", 3306)
    assert captured["connect"]["host"] == "db.example.com"
    assert "sock" not in captured["connect"]
    assert captured["connect"]["defer_connect"] is True
    assert captured["pinned_socket"] is pinned_socket
    assert captured["connect"]["ssl"] is tls_context
    assert tls_context.check_hostname is True
    assert tls_context.verify_mode == ssl.CERT_REQUIRED
    assert captured["connect"]["read_timeout"] == 9


def test_mysql_loopback_host_is_blocked(authenticated_client: TestClient) -> None:
    response = authenticated_client.post(
        "/api/connections/test",
        json={"connection_url": "mysql+pymysql://user:password@127.0.0.1/sales"},
    )

    assert response.status_code == 422
    assert "private, local, or reserved" in response.json()["detail"]
    assert "password" not in response.text


def test_connection_failure_logs_driver_diagnostic_without_credentials(
    authenticated_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from app import connections

    connection_url = "mysql+pymysql://dbuser:very-secret-password@db.example.com/sales"
    monkeypatch.setattr(connections, "_resolve_public_host", lambda *_args: "203.0.113.8")

    def missing_driver(*_args, **_kwargs):
        raise ModuleNotFoundError(
            f"No module named 'pymysql'; failed URL={connection_url}; user=dbuser password=very-secret-password"
        )

    monkeypatch.setattr(connections, "create_engine", missing_driver)
    response = authenticated_client.post(
        "/api/connections/test",
        json={"connection_url": connection_url},
    )

    assert response.status_code == 200
    assert response.json()["success"] is False
    assert response.json()["message"] == "Could not connect to the database. Check that it is reachable and the credentials are correct."
    assert "ModuleNotFoundError" in caplog.text
    assert "No module named 'pymysql'" in caplog.text
    assert connection_url not in caplog.text
    assert "dbuser" not in caplog.text
    assert "very-secret-password" not in caplog.text
    assert "very-secret-password" not in response.text


def test_sqlite_file_outside_allowed_directory_is_rejected(
    authenticated_client: TestClient,
) -> None:
    existing_file_outside_allowed_directory = Path(__file__).resolve()
    connection_url = f"sqlite:///{existing_file_outside_allowed_directory.as_posix()}"
    response = authenticated_client.post(
        "/api/connections/test",
        json={"connection_url": connection_url},
    )

    assert response.status_code == 422
    assert "limited to existing files" in response.json()["detail"]


def test_connection_endpoint_requires_authentication() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/api/connections/test",
            json={"connection_url": "sqlite+pysqlite:///:memory:"},
        )

    assert response.status_code == 401


def test_engine_is_disposed_after_connection_failure(
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

    monkeypatch.setattr("app.connections.create_engine", lambda *args, **kwargs: FailingEngine())
    response = authenticated_client.post(
        "/api/connections/test",
        json={"connection_url": "sqlite+pysqlite:///:memory:"},
    )

    assert response.status_code == 200
    assert response.json()["success"] is False
    assert "do-not-leak" not in response.text
    assert "postgresql://" not in response.text
    assert disposed is True


def test_postgres_loopback_host_is_blocked(authenticated_client: TestClient) -> None:
    response = authenticated_client.post(
        "/api/connections/test",
        json={"connection_url": "postgresql://user:password@127.0.0.1/app"},
    )

    assert response.status_code == 422
    assert "private, local, or reserved" in response.json()["detail"]
    assert "password" not in response.text


def test_user_id_in_body_is_rejected_and_not_echoed(authenticated_client: TestClient) -> None:
    response = authenticated_client.post(
        "/api/connections/test",
        json={
            "connection_url": "sqlite+pysqlite:///:memory:",
            "user_id": "attacker-controlled-user-id",
        },
    )

    assert response.status_code == 422
    assert "attacker-controlled-user-id" not in response.text
