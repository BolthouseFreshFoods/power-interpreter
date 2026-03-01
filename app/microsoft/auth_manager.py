"""Microsoft OAuth 2.0 Device Code Flow + Token Management

Uses the same Azure AD app registration as the Bolthouse Fresh Microsoft Connector.
Railway env vars: AZURE_TENANT_ID, AZURE_CLIENT_ID, AZURE_CLIENT_SECRET

v1.9.2: Rewrote token persistence to use SQLAlchemy (same as rest of app).
         Removed asyncpg dependency. db_pool parameter no longer needed.
         Added is_authenticated_async() for full Postgres + memory check.
         Added try/except around all httpx calls to Microsoft.
"""

import os
import time
import json
import logging
import asyncio
from typing import Optional, Dict, Any

import httpx

logger = logging.getLogger(__name__)

# Azure AD endpoints
AUTHORITY = "https://login.microsoftonline.com"
GRAPH_SCOPES = [
    "Files.ReadWrite.All",       # OneDrive
    "Sites.ReadWrite.All",       # SharePoint
    "offline_access",            # Refresh tokens
]


class MSAuthManager:
    """
    Manages Microsoft OAuth tokens per user_id (email).
    Supports device-code flow for headless/Railway environments.
    Tokens cached in-memory with SQLAlchemy Postgres persistence.
    """

    def __init__(self):
        self.tenant_id = os.environ.get("AZURE_TENANT_ID", "")
        self.client_id = os.environ.get("AZURE_CLIENT_ID", "")
        self.client_secret = os.environ.get("AZURE_CLIENT_SECRET", "")
        self.authority = f"{AUTHORITY}/{self.tenant_id}" if self.tenant_id else ""
        self._tokens: Dict[str, Dict[str, Any]] = {}
        self._http = httpx.AsyncClient(timeout=30)
        self._enabled = bool(self.tenant_id and self.client_id)
        self._db_ready = False

    @property
    def enabled(self) -> bool:
        return self._enabled

    # ── Database Setup (SQLAlchemy) ─────────────────────────────────

    async def ensure_db_table(self):
        """Create ms_tokens table if it doesn't exist. Uses SQLAlchemy."""
        try:
            from app.database import get_session_factory
            from sqlalchemy import text

            factory = get_session_factory()
            if not factory:
                logger.warning("MS Auth: No database session factory available")
                return

            async with factory() as session:
                await session.execute(text("""
                    CREATE TABLE IF NOT EXISTS ms_tokens (
                        user_id TEXT PRIMARY KEY,
                        token_data JSONB NOT NULL,
                        updated_at TIMESTAMPTZ DEFAULT NOW()
                    )
                """))
                await session.commit()
            self._db_ready = True
            logger.info("MS Auth: ms_tokens table ready")
        except Exception as e:
            logger.warning(f"MS Auth: Failed to create ms_tokens table: {e}")
            logger.warning("MS Auth: Token persistence disabled (in-memory only)")

    # ── Device Code Flow ────────────────────────────────────────────

    async def start_device_login(self, user_id: str) -> Dict[str, Any]:
        """Initiate device code flow. Returns user_code + verification_uri."""
        if not self._enabled:
            return {
                "status": "error",
                "message": "Microsoft auth not configured. Set AZURE_TENANT_ID and AZURE_CLIENT_ID.",
            }

        url = f"{self.authority}/oauth2/v2.0/devicecode"
        data = {
            "client_id": self.client_id,
            "scope": " ".join(GRAPH_SCOPES),
        }

        try:
            resp = await self._http.post(url, data=data)
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            logger.error(f"MS Auth: Device code request failed: {e.response.status_code} {e.response.text}")
            return {
                "status": "error",
                "message": f"Microsoft rejected the auth request: {e.response.text[:200]}",
            }
        except Exception as e:
            logger.error(f"MS Auth: Device code request error: {e}")
            return {
                "status": "error",
                "message": f"Failed to contact Microsoft: {str(e)}",
            }

        result = resp.json()

        self._tokens[user_id] = {
            "status": "pending",
            "device_code": result["device_code"],
            "user_code": result["user_code"],
            "verification_uri": result["verification_uri"],
            "expires_at": time.time() + result["expires_in"],
            "interval": result.get("interval", 5),
        }

        return {
            "status": "pending",
            "user_code": result["user_code"],
            "verification_uri": result["verification_uri"],
            "message": (
                f"Visit **{result['verification_uri']}** "
                f"and enter code **{result['user_code']}**"
            ),
            "expires_in_seconds": result["expires_in"],
        }

    async def poll_device_login(self, user_id: str) -> Dict[str, Any]:
        """Poll for device code completion. Call after user enters code."""
        pending = self._tokens.get(user_id)
        if not pending or pending.get("status") != "pending":
            return {"status": "error", "message": "No pending login found."}

        url = f"{self.authority}/oauth2/v2.0/token"
        data = {
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "client_id": self.client_id,
            "device_code": pending["device_code"],
        }
        if self.client_secret:
            data["client_secret"] = self.client_secret

        max_attempts = 60
        interval = pending.get("interval", 5)

        for _ in range(max_attempts):
            try:
                resp = await self._http.post(url, data=data)
                body = resp.json()
            except Exception as e:
                logger.error(f"MS Auth: Poll request failed: {e}")
                await asyncio.sleep(interval)
                continue

            if resp.status_code == 200:
                self._tokens[user_id] = {
                    "status": "authenticated",
                    "access_token": body["access_token"],
                    "refresh_token": body.get("refresh_token"),
                    "expires_at": time.time() + body.get("expires_in", 3600),
                    "scope": body.get("scope", ""),
                }
                await self._persist_token(user_id)
                logger.info(f"MS Auth: {user_id} authenticated successfully")
                return {"status": "authenticated", "message": "Login successful."}

            error = body.get("error", "")
            if error == "authorization_pending":
                await asyncio.sleep(interval)
                continue
            elif error == "slow_down":
                interval += 5
                await asyncio.sleep(interval)
                continue
            else:
                self._tokens.pop(user_id, None)
                return {
                    "status": "error",
                    "message": body.get("error_description", error),
                }

        return {"status": "error", "message": "Device login timed out."}

    # ── Token Management ────────────────────────────────────────────

    async def get_access_token(self, user_id: str) -> Optional[str]:
        """Get a valid access token, refreshing if needed."""
        token_data = self._tokens.get(user_id)

        # Check in-memory first, then try Postgres
        if not token_data or token_data.get("status") != "authenticated":
            token_data = await self._load_token(user_id)
            if token_data:
                self._tokens[user_id] = token_data
                logger.info(f"MS Auth: Loaded persisted token for {user_id}")

        if not token_data or token_data.get("status") != "authenticated":
            return None

        # Refresh 5 min before expiry
        if time.time() > token_data.get("expires_at", 0) - 300:
            refreshed = await self._refresh_token(user_id)
            if not refreshed:
                return None

        return self._tokens[user_id].get("access_token")

    async def _refresh_token(self, user_id: str) -> bool:
        """Refresh an expired access token."""
        token_data = self._tokens.get(user_id, {})
        refresh_token = token_data.get("refresh_token")
        if not refresh_token:
            return False

        url = f"{self.authority}/oauth2/v2.0/token"
        data = {
            "grant_type": "refresh_token",
            "client_id": self.client_id,
            "refresh_token": refresh_token,
            "scope": " ".join(GRAPH_SCOPES),
        }
        if self.client_secret:
            data["client_secret"] = self.client_secret

        try:
            resp = await self._http.post(url, data=data)
            if resp.status_code == 200:
                body = resp.json()
                self._tokens[user_id] = {
                    "status": "authenticated",
                    "access_token": body["access_token"],
                    "refresh_token": body.get("refresh_token", refresh_token),
                    "expires_at": time.time() + body.get("expires_in", 3600),
                    "scope": body.get("scope", ""),
                }
                await self._persist_token(user_id)
                logger.info(f"MS Auth: Token refreshed for {user_id}")
                return True
            else:
                logger.warning(
                    f"MS Auth: Token refresh failed for {user_id}: "
                    f"{resp.status_code} {resp.text[:200]}"
                )
        except Exception as e:
            logger.error(f"MS Auth: Token refresh error for {user_id}: {e}")

        return False

    async def is_authenticated_async(self, user_id: str) -> bool:
        """Check auth status, including persisted tokens from Postgres."""
        token = await self.get_access_token(user_id)
        return token is not None

    def is_authenticated(self, user_id: str) -> bool:
        """Sync check — memory only. Use is_authenticated_async for full check."""
        token_data = self._tokens.get(user_id, {})
        return token_data.get("status") == "authenticated"

    # ── SQLAlchemy Persistence ──────────────────────────────────────

    async def _persist_token(self, user_id: str):
        """Save token to Postgres via SQLAlchemy."""
        if not self._db_ready:
            return

        token_data = self._tokens.get(user_id, {})
        # Don't persist pending or empty states
        if token_data.get("status") != "authenticated":
            return

        try:
            from app.database import get_session_factory
            from sqlalchemy import text

            factory = get_session_factory()
            async with factory() as session:
                await session.execute(
                    text("""
                        INSERT INTO ms_tokens (user_id, token_data, updated_at)
                        VALUES (:uid, :data, NOW())
                        ON CONFLICT (user_id)
                        DO UPDATE SET token_data = :data, updated_at = NOW()
                    """),
                    {
                        "uid": user_id,
                        "data": json.dumps(token_data),
                    },
                )
                await session.commit()
            logger.debug(f"MS Auth: Token persisted for {user_id}")
        except Exception as e:
            logger.warning(f"MS Auth: Failed to persist token for {user_id}: {e}")

    async def _load_token(self, user_id: str) -> Optional[Dict]:
        """Load token from Postgres via SQLAlchemy."""
        if not self._db_ready:
            return None

        try:
            from app.database import get_session_factory
            from sqlalchemy import text

            factory = get_session_factory()
            async with factory() as session:
                result = await session.execute(
                    text("SELECT token_data FROM ms_tokens WHERE user_id = :uid"),
                    {"uid": user_id},
                )
                row = result.fetchone()
                if row:
                    data = row[0]
                    if isinstance(data, str):
                        return json.loads(data)
                    return data  # Already a dict if JSONB
        except Exception as e:
            logger.warning(f"MS Auth: Failed to load token for {user_id}: {e}")

        return None
