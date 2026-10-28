"""Protected, non-persistent database connectivity checks."""

import ipaddress
import logging
import re
import ssl
import socket
import sqlite3
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, Engine, make_url
from sqlalchemy.exc import ArgumentError
from sqlalchemy.pool import NullPool

from app.auth import get_current_user
from app.config import get_settings
from app.models import ConnectionTestRequest, ConnectionTestResponse

router = APIRouter(prefix="/api/connections", tags=["connections"])
logger = logging.getLogger(__name__)

MAX_CONNECTION_URL_LENGTH = 2048
CONNECT_TIMEOUT_SECONDS = 5
QUERY_TIMEOUT_MS = 5000
ALLOWED_POSTGRES_SSL_MODES = {"require", "verify-ca", "verify-full"}
BLOCKED_HOST_SUFFIXES = (".localhost", ".local", ".internal", ".lan", ".home", ".test")


def _invalid_connection(message: str) -> HTTPException:
    return HTTPException(status_code=422, detail=message)


def _safe_connection_error_message(error: Exception, connection_url: str) -> str:
    """Return a bounded diagnostic with URL credentials and complete URLs removed."""
    message = str(error)
    try:
        parsed_url = make_url(connection_url.strip())
        for secret in (parsed_url.username, parsed_url.password):
            if secret:
                message = message.replace(secret, "[redacted]")
                message = message.replace(quote(secret, safe=""), "[redacted]")
        rendered_url = parsed_url.render_as_string(hide_password=True)
        message = message.replace(rendered_url, "[database URL redacted]")
    except (ArgumentError, ValueError):
        pass
    message = message.replace(connection_url.strip(), "[database URL redacted]")
    message = re.sub(
        r"(?i)\b(?:mysql(?:\+pymysql)?|postgres(?:ql)?(?:\+psycopg)?|sqlite(?:\+pysqlite)?)://[^\s\"')]+",
        "[database URL redacted]",
        message,
    )
    message = re.sub(
        r"(?i)\b(password|passwd|pwd)\s*[:=]\s*[^,\s)]+",
        r"\1=[redacted]",
        message,
    )
    return message[:500]


def _resolve_public_host(host: str, port: int, database_type: str = "PostgreSQL") -> str:
    normalized_host = host.rstrip(".").lower()
    if (
        not normalized_host
        or normalized_host == "localhost"
        or normalized_host.endswith(BLOCKED_HOST_SUFFIXES)
    ):
        raise _invalid_connection(f"{database_type} connections must use a public host.")

    try:
        addresses = [ipaddress.ip_address(normalized_host)]
    except ValueError:
        if "." not in normalized_host:
            raise _invalid_connection(f"{database_type} connections must use a public hostname.")
        try:
            resolved = socket.getaddrinfo(normalized_host, port, type=socket.SOCK_STREAM)
        except (socket.gaierror, OSError) as exc:
            raise _invalid_connection(f"The {database_type} hostname could not be resolved.") from exc
        addresses = []
        for result in resolved:
            try:
                address = ipaddress.ip_address(result[4][0].split("%", 1)[0])
            except ValueError:
                continue
            if address not in addresses:
                addresses.append(address)

    if not addresses or any(not address.is_global for address in addresses):
        raise _invalid_connection(
            f"{database_type} connections to private, local, or reserved network addresses are blocked."
        )

    # Pin the resolved address in connect_args to reduce DNS-rebinding risk while
    # preserving the hostname in the URL for TLS certificate verification.
    return str(addresses[0])


def _sqlite_engine(database: str | None) -> Engine:
    if database in (None, "", ":memory:"):
        return create_engine(
            "sqlite+pysqlite:///:memory:",
            connect_args={"timeout": CONNECT_TIMEOUT_SECONDS},
            poolclass=NullPool,
        )

    allowed_directory = get_settings().sqlite_allowed_directory.resolve()
    try:
        database_path = Path(database).expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise _invalid_connection(
            "SQLite files must already exist inside the configured SQLite data directory."
        ) from exc

    if not database_path.is_file() or not database_path.is_relative_to(allowed_directory):
        raise _invalid_connection(
            "SQLite file connections are limited to existing files inside the configured SQLite data directory."
        )

    # Open existing files in read-only mode so a check can never create or alter
    # a local database. Path.as_uri also escapes spaces and URI delimiters.
    read_only_uri = f"{database_path.as_uri()}?mode=ro"

    def connect_read_only() -> sqlite3.Connection:
        return sqlite3.connect(
            read_only_uri,
            uri=True,
            timeout=CONNECT_TIMEOUT_SECONDS,
        )

    return create_engine(
        "sqlite+pysqlite://",
        creator=connect_read_only,
        poolclass=NullPool,
    )


def _build_engine(connection_url: str) -> Engine:
    if len(connection_url) > MAX_CONNECTION_URL_LENGTH:
        raise _invalid_connection("The connection URL must be 2048 characters or fewer.")

    try:
        parsed_url = make_url(connection_url.strip())
    except (ArgumentError, ValueError) as exc:
        raise _invalid_connection("Enter a valid SQLAlchemy connection URL.") from exc

    if parsed_url.drivername in {"sqlite", "sqlite+pysqlite"}:
        if parsed_url.username or parsed_url.password or parsed_url.host or parsed_url.port:
            raise _invalid_connection("SQLite URLs must not include a host or credentials.")
        if parsed_url.query:
            raise _invalid_connection("SQLite URL query options are not supported.")
        if parsed_url.database and parsed_url.database.startswith("file:"):
            raise _invalid_connection("SQLite URI filenames are not accepted.")
        return _sqlite_engine(parsed_url.database)

    if parsed_url.drivername in {"mysql", "mysql+pymysql"}:
        return _build_mysql_engine(parsed_url)

    if parsed_url.drivername not in {"postgres", "postgresql", "postgresql+psycopg"}:
        raise _invalid_connection("Unsupported database dialect. Use PostgreSQL, MySQL, or SQLite.")

    try:
        port = parsed_url.port or 5432
    except ValueError as exc:
        raise _invalid_connection("The PostgreSQL port is invalid.") from exc
    if not 1 <= port <= 65535:
        raise _invalid_connection("The PostgreSQL port must be between 1 and 65535.")
    if not parsed_url.host or not parsed_url.database:
        raise _invalid_connection("The PostgreSQL URL must include a host and database name.")

    unsupported_options = set(parsed_url.query) - {"sslmode"}
    if unsupported_options:
        raise _invalid_connection("Only the PostgreSQL sslmode URL option is supported.")
    sslmode = parsed_url.query.get("sslmode", "require")
    if not isinstance(sslmode, str) or sslmode not in ALLOWED_POSTGRES_SSL_MODES:
        raise _invalid_connection("PostgreSQL connections must use sslmode=require, verify-ca, or verify-full.")

    host_address = _resolve_public_host(parsed_url.host, port)
    safe_url: URL = parsed_url.set(
        drivername="postgresql+psycopg",
        query={"sslmode": sslmode},
    )
    return create_engine(
        safe_url,
        connect_args={
            "connect_timeout": CONNECT_TIMEOUT_SECONDS,
            "hostaddr": host_address,
            "options": f"-c statement_timeout={QUERY_TIMEOUT_MS}",
        },
        poolclass=NullPool,
    )


def _build_mysql_engine(parsed_url: URL) -> Engine:
    try:
        port = parsed_url.port or 3306
    except ValueError as exc:
        raise _invalid_connection("The MySQL port is invalid.") from exc
    if not 1 <= port <= 65535:
        raise _invalid_connection("The MySQL port must be between 1 and 65535.")
    if not parsed_url.host or not parsed_url.database or not parsed_url.username:
        raise _invalid_connection("The MySQL URL must include a host, database name, and username.")
    if parsed_url.query:
        raise _invalid_connection("MySQL URL query options are not supported.")

    # Resolve and pin the TCP destination so DNS rebinding cannot redirect the
    # connection to local infrastructure. Keep the original hostname in the
    # DBAPI arguments for TLS certificate hostname verification.
    resolved_address = _resolve_public_host(parsed_url.host, port, "MySQL")
    connect_timeout = CONNECT_TIMEOUT_SECONDS
    read_timeout = max(1, int(get_settings().query_timeout_seconds))

    def connect_pinned():
        import pymysql

        pinned_socket = socket.create_connection(
            (resolved_address, port),
            timeout=connect_timeout,
        )
        dbapi_connection = None
        try:
            # PyMySQL accepts externally opened sockets on Connection.connect(),
            # not as a constructor/connect() keyword. defer_connect keeps the
            # hostname available for TLS verification while using the pinned IP.
            dbapi_connection = pymysql.connect(
                host=parsed_url.host,
                port=port,
                user=parsed_url.username,
                password=parsed_url.password,
                database=parsed_url.database,
                charset="utf8mb4",
                connect_timeout=connect_timeout,
                read_timeout=read_timeout,
                write_timeout=connect_timeout,
                ssl=ssl.create_default_context(),
                defer_connect=True,
            )
            dbapi_connection.connect(sock=pinned_socket)
            return dbapi_connection
        except Exception:
            if dbapi_connection is not None:
                dbapi_connection.close()
            else:
                pinned_socket.close()
            raise

    safe_url = parsed_url.set(drivername="mysql+pymysql")
    return create_engine(safe_url, creator=connect_pinned, poolclass=NullPool)


@router.post("/test", response_model=ConnectionTestResponse)
def test_database_connection(
    payload: ConnectionTestRequest,
    _claims: dict[str, Any] = Depends(get_current_user),
) -> ConnectionTestResponse:
    """Test one database URL without storing it or running caller-provided SQL."""
    engine: Engine | None = None
    failed = False
    try:
        engine = _build_engine(payload.connection_url.get_secret_value())
        with engine.connect() as connection:
            connection.execute(text("SELECT 1")).scalar_one()
    except HTTPException:
        raise
    except Exception as exc:
        # Driver exceptions may contain the submitted URL, username, or password.
        diagnostic = _safe_connection_error_message(exc, payload.connection_url.get_secret_value())
        logger.warning("Database connectivity check failed (%s): %s", type(exc).__name__, diagnostic)
        failed = True
    finally:
        if engine is not None:
            try:
                engine.dispose()
            except Exception as exc:
                diagnostic = _safe_connection_error_message(exc, payload.connection_url.get_secret_value())
                logger.warning("Temporary database engine disposal failed (%s): %s", type(exc).__name__, diagnostic)
                failed = True

    if failed:
        return ConnectionTestResponse(
            success=False,
            message="Could not connect to the database. Check that it is reachable and the credentials are correct.",
        )
    return ConnectionTestResponse(success=True, message="Database connection successful.")
