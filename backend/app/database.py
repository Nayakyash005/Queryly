"""Database engine and session wiring."""

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    """Base class for future SQLAlchemy models."""


def create_database_engine() -> Engine | None:
    """Create the SQLAlchemy engine when a database URL is configured."""
    database_url = get_settings().database_url
    if not database_url:
        return None
    return create_engine(database_url, pool_pre_ping=True)


engine = create_database_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False) if engine else None
