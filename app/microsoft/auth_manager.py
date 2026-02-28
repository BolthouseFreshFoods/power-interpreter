"""
Microsoft OAuth 2.0 Device Code Flow + Token Management

Uses the same Azure AD app registration as the Bolthouse Fresh Microsoft Connector.
Railway env vars: AZURE_TENANT_ID, AZURE_CLIENT_ID, AZURE_CLIENT_SECRET
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
    Tokens stored in-memory with optional Postgres persistence.
    """

    def __init__(self, db_pool=None):
        self.tenant_id = os.environ.get("AZURE_TENANT_ID", "")
        self.client_id = os.environ.get("AZURE_CLIENT_ID", "")
        self.client_secret = os.environ.get("AZURE_CLIENT_SECRET", "")
        self.authority = f"{AUTHORITY}/{self.tenant_id}" if self.tenant_id else ""
        self._tokens: Dict[str, Dict[str, Any]] = {}
        self._db_pool = db_pool
        self._http = httpx.AsyncClient(timeout=30)
        self._enabled = bool(self.tenant_id and self.client_id)

    @property
    def enabled(self) -> bool:
        return self._enabled

    # ── Device Code Flow ────────────────────────────────────────────

    async def start_device_login(self, user_id: str) -> Dict[str, Any]:
        """Initiate device code flow. Returns user_code + verification_uri."""
        if not self._enabled:
            return {"status": "error", "message": "Microsoft auth not configured. Set AZURE_TENANT_ID and AZURE_CLIENT_ID."}

        url = f"{self.authority}/oauth2/v2.0/devicecode"
        data = {
            "client_id": self.client_id,
            "scope": " ".join(GRAPH_SCOPES),
        }
        resp = await self._http.post(url, data=data)
        resp.raise_for_status()
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
            resp = await self._http.post(url, data=data)
            body = resp.json()

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
                return {"status": "error", "message": body.get("error_description", error)}

        return {"status": "error", "message": "Device login timed out."}

    # ── Token Management ────────────────────────────────────────────

    async def get_access_token(self, user_id: str) -> Optional[str]:
        """Get a valid access token, refreshing if needed."""
        token_data = self._tokens.get(user_id)

        if not token_data or token_data.get("status") != "authenticated":
            token_data = await self._load_token(user_id)
            if token_data:
                self._tokens[user_id] = token_data

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
                return True
        except Exception as e:
            logger.error(f"Token refresh failed for {user_id}: {e}")

        return False

    def is_authenticated(self, user_id: str) -> bool:
        token_data = self._tokens.get(user_id, {})
        return token_data.get("status") == "authenticated"

    # ── Postgres Persistence ────────────────────────────────────────

    async def ensure_db_table(self):
        """Create ms_tokens table if it doesn't exist."""
        if not self._db_pool:
            return
        try:
            async with self._db_pool.acquire() as conn:
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS ms_tokens (
                        user_id TEXT PRIMARY KEY,
                        token_data JSONB NOT NULL,
                        updated_at TIMESTAMPTZ DEFAULT NOW()
                    )
                """)
        except Exception as e:
            logger.warning(f"Failed to create ms_tokens table: {e}")

    async def _persist_token(self, user_id: str):
        if not self._db_pool:
            return
        token_data = self._tokens.get(user_id, {})
        try:
            async with self._db_pool.acquire() as conn:
                await conn.execute("""
                    INSERT INTO ms_tokens (user_id, token_data, updated_at)
                    VALUES ($1, $2, NOW())
                    ON CONFLICT (user_id)
                    DO UPDATE SET token_data = $2, updated_at = NOW()
                """, user_id, json.dumps(token_data))
        except Exception as e:
            logger.warning(f"Failed to persist token: {e}")

    async def _load_token(self, user_id: str) -> Optional[Dict]:
        if not self._db_pool:
            return None
        try:
            async with self._db_pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT token_data FROM ms_tokens WHERE user_id = $1", user_id
                )
                if row:
                    return json.loads(row["token_data"])
        except Exception:
            pass
        return None
