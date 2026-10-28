"""API request and response models."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


class HealthResponse(BaseModel):
    status: str
    service: str


class AuthUserResponse(BaseModel):
    id: str
    email: str | None = None
    role: str | None = None


class ConnectionTestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    connection_url: SecretStr

    @field_validator("connection_url")
    @classmethod
    def validate_connection_url_length(cls, value: SecretStr) -> SecretStr:
        raw_url = value.get_secret_value()
        if not raw_url.strip():
            raise ValueError("A connection URL is required.")
        if len(raw_url) > 2048:
            raise ValueError("The connection URL must be 2048 characters or fewer.")
        return value


class ConnectionTestResponse(BaseModel):
    success: bool
    message: str


class SchemaColumn(BaseModel):
    name: str
    data_type: str
    nullable: bool
    primary_key: bool


class SchemaPrimaryKey(BaseModel):
    name: str | None = None
    columns: list[str]


class SchemaForeignKey(BaseModel):
    name: str | None = None
    columns: list[str]
    referred_schema: str | None = None
    referred_table: str
    referred_columns: list[str]
    on_delete: str | None = None


class SchemaRelationship(BaseModel):
    from_columns: list[str]
    to_schema: str | None = None
    to_table: str
    to_columns: list[str]


class SchemaTable(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str
    schema_name: str | None = Field(default=None, alias="schema")
    columns: list[SchemaColumn]
    primary_key: SchemaPrimaryKey
    foreign_keys: list[SchemaForeignKey]
    relationships: list[SchemaRelationship]


class DatabaseSchemaResponse(BaseModel):
    tables: list[SchemaTable]


class SQLGenerationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str
    explanation: str


class SQLGenerationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    question: str = Field(min_length=1, max_length=4000)
    database_schema: DatabaseSchemaResponse = Field(alias="schema")
    dialect: str = Field(min_length=1, max_length=32)


class AgentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    connection_url: SecretStr
    question: str = Field(min_length=1, max_length=4000)
    dialect: str = Field(min_length=1, max_length=32)

    @field_validator("connection_url")
    @classmethod
    def validate_connection_url_length(cls, value: SecretStr) -> SecretStr:
        raw_url = value.get_secret_value()
        if not raw_url.strip():
            raise ValueError("A connection URL is required.")
        if len(raw_url) > 2048:
            raise ValueError("The connection URL must be 2048 characters or fewer.")
        return value


class AgentResponse(BaseModel):
    query: str
    explanation: str
    columns: list[str] = Field(default_factory=list)
    rows: list[list[Any]] = Field(default_factory=list)
    row_count: int = 0
    truncated: bool = False
    attempt_count: int = 0


class SQLValidationResult(BaseModel):
    valid: bool
    query: str | None = None
    error: str | None = None


class QueryExecutionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    connection_url: SecretStr
    query: str = Field(min_length=1, max_length=10000)
    database_schema: DatabaseSchemaResponse = Field(alias="schema")
    dialect: str = Field(min_length=1, max_length=32)
    question: str | None = Field(default=None, max_length=4000)

    @field_validator("connection_url")
    @classmethod
    def validate_connection_url_length(cls, value: SecretStr) -> SecretStr:
        raw_url = value.get_secret_value()
        if not raw_url.strip():
            raise ValueError("A connection URL is required.")
        if len(raw_url) > 2048:
            raise ValueError("The connection URL must be 2048 characters or fewer.")
        return value


class QueryExecutionResponse(BaseModel):
    success: bool
    columns: list[str] = Field(default_factory=list)
    rows: list[list[Any]] = Field(default_factory=list)
    row_count: int = 0
    truncated: bool = False
    error: str | None = None
    query: str | None = None
    explanation: str | None = None
    attempt_count: int = 0
    repaired: bool = False
    error_kind: str | None = Field(default=None, exclude=True)
