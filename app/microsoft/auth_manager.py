"""Microsoft OAuth 2.0 Device Code Flow + Token Management
v1.9.4: Added get_default_user_id() so tools can auto-resolve user_id.
v2.9.3a: Added clear_user_token() and list_authenticated_users() for admin.
         No hardcoded emails -- default user resolved from most recent Postgres entry.
v3.0.4: Persistent auth hardening -- widened refresh safety margin to 600s (10 min), added refresh_count tracking, robust refresh_token fallback.
         Multi-user safety -- get_default_user_id() warns on cross-user risk.
         get_default_user_id_async() same multi-user detection.
"""
import os, time, json, logging, asyncio
from typing import Optional, Dict, Any, List
import httpx

logger = logging.getLogger(__name__)
AUTHORITY = "https://login.microsoftonline.com"
GRAPH_SCOPES = ["Files.ReadWrite.All", "Sites.ReadWrite.All", "offline_access"]

class MSAuthManager:
    def __init__(self):
        self.tenant_id = os.environ.get("AZURE_TENANT_ID", "")
        self.client_id = os.environ.get("AZURE_CLIENT_ID", "")
        self.client_secret = os.environ.get("AZURE_CLIENT_SECRET", "")
        self.authority = f"{AUTHORITY}/{self.tenant_id}" if self.tenant_id else ""
        self._tokens: Dict[str, Dict[str, Any]] = {}
        self._http = httpx.AsyncClient(timeout=30)
        self._enabled = bool(self.tenant_id and self.client_id)
        self._db_ready = False
        self._last_authenticated_user: Optional[str] = None

    @property
    def enabled(self): return self._enabled

    # -- FIX 1: Multi-user safe default resolution (v2.10.1) -------------
    def get_default_user_id(self) -> Optional[str]:
        """Resolve default user -- WARN if multiple users are active.

        v2.10.1: When multiple users are authenticated concurrently,
        _last_authenticated_user is a global singleton that could
        return the WRONG user's token. This now logs a warning when
        that risk exists so it's visible in Railway logs.

        Long-term fix: make user_email required on all Microsoft tools
        so this fallback is never needed.
        """
        authenticated = [
            uid for uid, data in self._tokens.items()
            if data.get("status") == "authenticated"
        ]
        if len(authenticated) == 0:
            return None
        if len(authenticated) == 1:
            self._last_authenticated_user = authenticated[0]
            return authenticated[0]
        # MULTIPLE users active -- log cross-user risk
        logger.warning(
            f"MS Auth: MULTI-USER WARNING -- {len(authenticated)} authenticated "
            f"users: {authenticated}. Returning last authenticated: "
            f"{self._last_authenticated_user}. "
            f"Pass user_email explicitly to avoid cross-user data access."
        )
        if self._last_authenticated_user in authenticated:
            return self._last_authenticated_user
        self._last_authenticated_user = authenticated[0]
        return authenticated[0]

    # -- FIX 2: Async multi-user safe default resolution (v2.10.1) -------
    async def get_default_user_id_async(self) -> Optional[str]:
        """Async default user resolve with DB fallback -- multi-user safe.

        v2.10.1: Same multi-user warning as sync version. Postgres
        fallback only fires when zero users are in memory -- safe
        because it loads exactly one user.
        """
        authenticated = [
            uid for uid, data in self._tokens.items()
            if data.get("status") == "authenticated"
        ]
        if len(authenticated) == 1:
            self._last_authenticated_user = authenticated[0]
            return authenticated[0]
        if len(authenticated) > 1:
            logger.warning(
                f"MS Auth: MULTI-USER WARNING (async) -- {len(authenticated)} "
                f"authenticated users: {authenticated}. Returning last: "
                f"{self._last_authenticated_user}. "
                f"Pass user_email explicitly to avoid cross-user data access."
            )
            if self._last_authenticated_user in authenticated:
                return self._last_authenticated_user
            self._last_authenticated_user = authenticated[0]
            return authenticated[0]
        # No in-memory users -- try Postgres (single-user safe)
        if self._db_ready:
            try:
                from app.database import get_session_factory
                from sqlalchemy import text
                factory = get_session_factory()
                async with factory() as session:
                    result = await session.execute(text("SELECT user_id FROM ms_tokens ORDER BY updated_at DESC LIMIT 1"))
                    row = result.fetchone()
                    if row:
                        uid = row[0]
                        td = await self._load_token(uid)
                        if td:
                            self._tokens[uid] = td
                            self._last_authenticated_user = uid
                            logger.info(f"MS Auth: Auto-resolved default user from DB: {uid}")
                            return uid
            except Exception as e:
                logger.warning(f"MS Auth: Failed to load default user: {e}")
        return None

    async def clear_user_token(self, user_id: str) -> Dict[str, Any]:
        """Remove a user's tokens from memory and Postgres.

        Use to purge stale entries (e.g., wrong email address).
        After clearing, the user must re-authenticate with ms_auth_start.
        """
        removed_memory = user_id in self._tokens
        self._tokens.pop(user_id, None)
        if self._last_authenticated_user == user_id:
            self._last_authenticated_user = None
        removed_db = False
        if self._db_ready:
            try:
                from app.database import get_session_factory
                from sqlalchemy import text
                async with get_session_factory()() as s:
                    result = await s.execute(text("DELETE FROM ms_tokens WHERE user_id = :uid"), {"uid": user_id})
                    await s.commit()
                    removed_db = result.rowcount > 0
            except Exception as e:
                logger.warning(f"MS Auth: Failed to clear token from DB: {e}")
        logger.info(f"MS Auth: clear_user_token({user_id}): memory={removed_memory}, db={removed_db}")
        return {"user_id": user_id, "removed_from_memory": removed_memory, "removed_from_database": removed_db}

    async def list_authenticated_users(self) -> List[Dict[str, Any]]:
        """List all users with tokens in memory and Postgres."""
        users = []
        memory_uids = set()
        for uid, data in self._tokens.items():
            if data.get("status") == "authenticated":
                users.append({"user_id": uid, "source": "memory", "is_default": uid == self._last_authenticated_user})
                memory_uids.add(uid)
        if self._db_ready:
            try:
                from app.database import get_session_factory
                from sqlalchemy import text
                async with get_session_factory()() as s:
                    result = await s.execute(text("SELECT user_id, updated_at FROM ms_tokens ORDER BY updated_at DESC"))
                    for row in result.fetchall():
                        uid = row[0]
                        if uid not in memory_uids:
                            users.append({"user_id": uid, "source": "database", "last_updated": str(row[1]), "is_default": False})
            except Exception as e:
                logger.warning(f"MS Auth: Failed to list DB users: {e}")
        return users

    async def ensure_db_table(self):
        try:
            from app.database import get_session_factory
            from sqlalchemy import text
            factory = get_session_factory()
            async with factory() as session:
                await session.execute(text("""
                    CREATE TABLE IF NOT EXISTS ms_tokens (
                        user_id TEXT PRIMARY KEY,
                        token_data JSONB NOT NULL,
                        updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                    )
                """))
                await session.commit()
            self._db_ready = True
            logger.info("MS Auth: Token table ready (Postgres)")
        except Exception as e:
            logger.warning(f"MS Auth: Could not create token table: {e}")
            self._db_ready = False

    async def start_device_login(self, user_id: str) -> Dict[str, Any]:
        """Start Device Code Flow for user authentication."""
        if not self._enabled:
            return {"status": "error", "message": "Microsoft integration not configured. Set AZURE_TENANT_ID and AZURE_CLIENT_ID."}
        data = {"client_id": self.client_id, "scope": " ".join(GRAPH_SCOPES)}
        try:
            resp = await self._http.post(f"{self.authority}/oauth2/v2.0/devicecode", data=data)
            if resp.status_code != 200:
                return {"status": "error", "message": f"Device code request failed: HTTP {resp.status_code}"}
            r = resp.json()
            self._tokens[user_id] = {"status": "pending", "device_code": r["device_code"], "interval": r.get("interval", 5), "expires_at": time.time() + r.get("expires_in", 900)}
            return {"status": "pending", "user_code": r["user_code"], "verification_uri": r["verification_uri"], "message": f"Visit **{r['verification_uri']}** and enter code **{r['user_code']}**", "expires_in_seconds": r["expires_in"]}
        except Exception as e:
            return {"status": "error", "message": f"Device code request error: {e}"}

    async def poll_device_login(self, user_id: str) -> Dict[str, Any]:
        pending = self._tokens.get(user_id)
        if not pending or pending.get("status") != "pending":
            return {"status": "error", "message": "No pending login found."}
        data = {"grant_type": "urn:ietf:params:oauth:grant-type:device_code", "client_id": self.client_id, "device_code": pending["device_code"]}
        if self.client_secret: data["client_secret"] = self.client_secret
        interval = pending.get("interval", 5)
        for attempt in range(60):
            try:
                resp = await self._http.post(f"{self.authority}/oauth2/v2.0/token", data=data)
                body = resp.json()
            except Exception as e:
                await asyncio.sleep(interval); continue
            if resp.status_code == 200:
                self._tokens[user_id] = {"status": "authenticated", "access_token": body["access_token"], "refresh_token": body.get("refresh_token"), "expires_at": time.time() + body.get("expires_in", 3600), "scope": body.get("scope", ""), "refresh_count": 0}
                self._last_authenticated_user = user_id
                await self._persist_token(user_id)
                logger.info(f"MS Auth: {user_id} authenticated (refresh={'YES' if 'refresh_token' in body else 'NO'})")
                return {"status": "authenticated", "message": "Login successful."}
            error = body.get("error", "")
            if error == "authorization_pending": await asyncio.sleep(interval); continue
            elif error == "slow_down": interval += 5; await asyncio.sleep(interval); continue
            else: self._tokens.pop(user_id, None); return {"status": "error", "message": body.get("error_description", error)}
        return {"status": "error", "message": "Timed out."}

    async def get_access_token(self, user_id: str) -> Optional[str]:
        td = self._tokens.get(user_id)
        if not td or td.get("status") != "authenticated":
            td = await self._load_token(user_id)
            if td: self._tokens[user_id] = td; self._last_authenticated_user = user_id
        if not td or td.get("status") != "authenticated": return None
        if td.get("expires_at", 0) - time.time() < 600:
            if not await self._refresh_token(user_id): return None
        return self._tokens[user_id].get("access_token")

    async def _refresh_token(self, user_id: str) -> bool:
        rt = self._tokens.get(user_id, {}).get("refresh_token")
        if not rt: return False
        data = {"grant_type": "refresh_token", "client_id": self.client_id, "refresh_token": rt, "scope": " ".join(GRAPH_SCOPES)}
        if self.client_secret: data["client_secret"] = self.client_secret
        try:
            resp = await self._http.post(f"{self.authority}/oauth2/v2.0/token", data=data)
            if resp.status_code == 200:
                body = resp.json()
                self._tokens[user_id] = {"status": "authenticated", "access_token": body["access_token"], "refresh_token": body.get("refresh_token", rt), "expires_at": time.time() + body.get("expires_in", 3600), "scope": body.get("scope", ""), "refresh_count": self._tokens.get(user_id, {}).get("refresh_count", 0) + 1}
                await self._persist_token(user_id)
                logger.info(f"MS Auth: Token refreshed for {user_id} (refresh_count={self._tokens.get(user_id, {}).get('refresh_count', 0)})")
                return True
            logger.warning(f"MS Auth: Refresh failed: HTTP {resp.status_code}")
        except Exception as e:
            logger.error(f"MS Auth: Refresh error: {e}")
        return False

    async def is_authenticated_async(self, user_id): return await self.get_access_token(user_id) is not None
    def is_authenticated(self, user_id): return self._tokens.get(user_id, {}).get("status") == "authenticated"

    async def _persist_token(self, user_id):
        if not self._db_ready: return
        td = self._tokens.get(user_id, {})
        if td.get("status") != "authenticated": return
        try:
            from app.database import get_session_factory
            from sqlalchemy import text
            async with get_session_factory()() as s:
                await s.execute(text("INSERT INTO ms_tokens (user_id, token_data, updated_at) VALUES (:uid, :data, NOW()) ON CONFLICT (user_id) DO UPDATE SET token_data = :data, updated_at = NOW()"), {"uid": user_id, "data": json.dumps(td)})
                await s.commit()
        except Exception as e:
            logger.warning(f"MS Auth: Persist failed: {e}")

    async def _load_token(self, user_id) -> Optional[Dict]:
        if not self._db_ready: return None
        try:
            from app.database import get_session_factory
            from sqlalchemy import text
            async with get_session_factory()() as s:
                result = await s.execute(text("SELECT token_data FROM ms_tokens WHERE user_id = :uid"), {"uid": user_id})
                row = result.fetchone()
                if row:
                    data = row[0] if not isinstance(row[0], str) else json.loads(row[0])
                    logger.info(f"MS Auth: Loaded from Postgres: {user_id} (refresh={'YES' if data.get('refresh_token') else 'NO'})")
                    return data
        except Exception as e:
            logger.warning(f"MS Auth: Load failed: {e}")
        return None
