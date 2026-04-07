"""Power Interpreter - API Key Authentication

Production-safe header-based authentication for inbound requests.

Behavior:
- If `settings.API_KEY` is configured, requests must include `X-API-Key`
  with the exact configured value.
- If `settings.API_KEY` is missing, authentication fails closed by default.
- Optional explicit dev bypass:
    `ALLOW_UNAUTHENTICATED_DEV=true`

Intended consumers:
- SimTheory MCP
- Timothy
"""

from fastapi import HTTPException, Security
from fastapi.security import APIKeyHeader
from starlette.status import HTTP_403_FORBIDDEN, HTTP_500_INTERNAL_SERVER_ERROR

from app.config import settings


API_KEY_HEADER_NAME = "X-API-Key"

api_key_header = APIKeyHeader(
    name=API_KEY_HEADER_NAME,
    auto_error=False,
)


def _allow_unauthenticated_dev() -> bool:
    """Explicit opt-in for local/dev no-auth mode.

    Defaults to False if the setting does not exist.
    Accepts: true / 1 / yes / on
    """
    raw = getattr(settings, "ALLOW_UNAUTHENTICATED_DEV", False)
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() in {"true", "1", "yes", "on"}


def _configured_api_key() -> str | None:
    """Return configured API key, normalized."""
    value = getattr(settings, "API_KEY", None)
    if value is None:
        return None

    value = str(value).strip()
    return value or None


def _missing_key_exception() -> HTTPException:
    return HTTPException(
        status_code=HTTP_403_FORBIDDEN,
        detail=f"Missing API key. Include {API_KEY_HEADER_NAME} header.",
    )


def _invalid_key_exception() -> HTTPException:
    return HTTPException(
        status_code=HTTP_403_FORBIDDEN,
        detail="Invalid API key.",
    )


def _server_auth_not_configured_exception() -> HTTPException:
    return HTTPException(
        status_code=HTTP_500_INTERNAL_SERVER_ERROR,
        detail="Server authentication is not configured.",
    )


async def verify_api_key(api_key: str | None = Security(api_key_header)) -> str:
    """Validate the inbound API key.

    Returns:
        The validated API key string, or "dev-mode" when explicit dev bypass is enabled.

    Raises:
        HTTPException:
            - 500 if server auth is not configured and dev bypass is not enabled
            - 403 if client key is missing
            - 403 if client key is invalid
    """
    configured_api_key = _configured_api_key()

    # Explicit local/dev bypass only when intentionally enabled
    if configured_api_key is None:
        if _allow_unauthenticated_dev():
            return "dev-mode"
        raise _server_auth_not_configured_exception()

    if not api_key:
        raise _missing_key_exception()

    if api_key != configured_api_key:
        raise _invalid_key_exception()

    return api_key
