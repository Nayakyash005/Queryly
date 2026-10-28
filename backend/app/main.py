"""FastAPI application entry point."""

from fastapi import Depends, FastAPI, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.auth import get_current_user
from app.models import AuthUserResponse, HealthResponse
from app.connections import router as connections_router
from app.schema_inspector import router as schema_router
from app.query_executor import router as query_router
from app.sql_generator import router as generation_router
from app.agent import router as agent_router

settings = get_settings()


def configure_cors(app: FastAPI, frontend_origin: str) -> None:
    """Configure exact frontend origins, normalizing an optional trailing slash."""
    configured_origin = frontend_origin.strip().rstrip("/")
    allowed_origins = list(
        dict.fromkeys(
            [configured_origin, "http://localhost:5173", "http://127.0.0.1:5173"]
        )
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
    )

app = FastAPI(title=settings.app_name)
app.include_router(connections_router)
app.include_router(schema_router)
app.include_router(query_router)
app.include_router(generation_router)
app.include_router(agent_router)
configure_cors(app, settings.frontend_origin)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    """Redact request input that could contain database credentials in 422s."""
    if request.url.path in {
        "/api/connections/test",
        "/api/connections/schema",
        "/api/query/execute",
        "/api/query/agent",
    }:
        safe_errors = [
            {"loc": error.get("loc", ()), "msg": error.get("msg", "Invalid request."), "type": error.get("type", "value_error")}
            for error in exc.errors()
        ]
        return JSONResponse(status_code=422, content={"detail": safe_errors})
    return await request_validation_exception_handler(request, exc)


@app.get("/health", response_model=HealthResponse, tags=["health"])
async def health_check() -> HealthResponse:
    """Report that the API process is running."""
    return HealthResponse(status="ok", service=settings.app_name)


@app.get("/api/auth/me", response_model=AuthUserResponse, tags=["auth"])
async def get_me(claims: dict = Depends(get_current_user)) -> AuthUserResponse:
    """Return basic fields from a verified Supabase access token."""
    return AuthUserResponse(
        id=claims["sub"],
        email=claims.get("email"),
        role=claims.get("role"),
    )
