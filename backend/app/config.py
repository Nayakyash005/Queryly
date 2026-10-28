"""Environment-backed application settings."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Text-to-SQL Agent API"
    app_env: str = "development"
    api_v1_prefix: str = "/api/v1"
    frontend_origin: str = "http://localhost:5173"

    database_url: str = ""
    sqlite_allowed_directory: Path = Path(__file__).resolve().parents[1] / "data"
    supabase_url: str = ""
    supabase_anon_key: str = ""
    groq_api_key: str = ""
    groq_model: str = ""
    query_timeout_seconds: int = Field(default=5, ge=1, le=60)
    max_query_rows: int = Field(default=500, ge=1, le=10000)

    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[2] / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    """Return cached application settings."""
    return Settings()
