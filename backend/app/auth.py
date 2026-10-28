"""Supabase access-token verification dependencies."""

from functools import lru_cache
import logging
from typing import Any

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from httpx import ConnectError, ConnectTimeout, HTTPError, TimeoutException
from supabase import Client, create_client
from supabase_auth.errors import AuthError

from app.config import get_settings

bearer_scheme = HTTPBearer(auto_error=False)
logger = logging.getLogger(__name__)


@lru_cache
def get_supabase_client() -> Client:
    """Build a server-side client with the public key for Auth verification."""
    settings = get_settings()
    if not settings.supabase_url or not settings.supabase_anon_key:
        raise RuntimeError("SUPABASE_URL and SUPABASE_ANON_KEY must be configured.")
    return create_client(settings.supabase_url, settings.supabase_anon_key)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> dict[str, Any]:
    """Verify the bearer token with Supabase and return trusted user claims."""
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="A valid access token is required.",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise unauthorized

    try:
        response = get_supabase_client().auth.get_claims(credentials.credentials)
        if isinstance(response, dict):
            claims = response.get("claims")
        else:
            claims = getattr(response, "claims", None)
        if not isinstance(claims, dict) or not claims.get("sub"):
            raise unauthorized
        return claims
    except HTTPException:
        raise
    except (AuthError, ValueError, RuntimeError) as exc:
        logger.info("Supabase rejected an access token (%s).", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Supabase rejected this access token. Sign in again to refresh your session.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    except (ConnectError, ConnectTimeout) as exc:
        logger.error("Could not connect to Supabase Auth while verifying a token (%s).", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "The API could not connect to the configured Supabase project. "
                "Confirm backend SUPABASE_URL matches the frontend project and check server network access."
            ),
        ) from exc
    except TimeoutException as exc:
        logger.error("Timed out contacting Supabase Auth while verifying a token (%s).", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Supabase Auth timed out while verifying this session. Please try again.",
        ) from exc
    except HTTPError as exc:
        logger.error("Supabase Auth request failed while verifying a token (%s).", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Supabase Auth could not verify this session. Check backend Supabase configuration and network access.",
        ) from exc
