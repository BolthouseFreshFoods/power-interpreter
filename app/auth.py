"""Power Interpreter - API Key Authentication

Simple API key auth. The key is stored in Railway environment variables.
Only Kaffer (via SimTheory MCP) and Timothy have access.
"""

from fastapi import HTTPException, Security, Depends
from fastapi.security import APIKeyHeader
from starlette.status import HTTP_403_FORBIDDEN
from app.config import settings

# API key header
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def verify_api_key(api_key: str = Security(api_key_header)) -> str:
    """Verify the API key from request header"""
    if not settings.API_KEY:
        # If no API key configured, allow all (dev mode only)
        return "dev-mode"
    
    if not api_key:
        raise HTTPException(
            status_code=HTTP_403_FORBIDDEN,
            detail="Missing API key. Include X-API-Key header."
        )
    
    if api_key != settings.API_KEY:
        raise HTTPException(
            status_code=HTTP_403_FORBIDDEN,
            detail="Invalid API key."
        )
    
    return api_key
