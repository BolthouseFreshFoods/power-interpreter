"""Power Interpreter - User Tracker

Per-session identity tracking for multi-user environments.

Resolves who is using each session via two identity tiers:
  Tier 1: ms_auth email (human identity) — set when user authenticates
  Tier 2: session_id (project/workload context) — set at session creation

Usage:
    tracker = UserTracker()
    tracker.register_session("analysis_q3")
    tracker.enrich_from_auth("analysis_q3", "jane@corp.com")
    identity = tracker.get_identity("analysis_q3")

Version: 2.10.0
"""

import logging
import threading
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class UserTracker:
    """Singleton tracker: maps session_id → user identity."""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._sessions = {}
                cls._instance._initialized = True
                logger.info("UserTracker initialized (v2.10.0)")
            return cls._instance

    def register_session(self, session_id: str):
        """Register a new session for tracking."""
        self._sessions[session_id] = {
            "session_id": session_id,
            "ms_auth_email": None,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "last_active": datetime.now(timezone.utc).isoformat(),
        }
        prefix = self._log_prefix(session_id)
        logger.info(f"{prefix} Session registered")

    def enrich_from_auth(self, session_id: str, email: str):
        """Enrich session with authenticated Microsoft email."""
        if session_id not in self._sessions:
            self._sessions[session_id] = {
                "session_id": session_id,
                "ms_auth_email": None,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "last_active": datetime.now(timezone.utc).isoformat(),
            }
        self._sessions[session_id]["ms_auth_email"] = email
        self._sessions[session_id]["last_active"] = datetime.now(timezone.utc).isoformat()
        prefix = self._log_prefix(session_id)
        logger.info(f"{prefix} Identity enriched from ms_auth")

    def remove_session(self, session_id: str):
        """Remove session tracking."""
        if session_id in self._sessions:
            del self._sessions[session_id]
            logger.info(f"[{session_id}] Session tracking removed")

    def get_identity(self, session_id: str) -> dict:
        """Get resolved identity for a session."""
        if session_id not in self._sessions:
            return {"session_id": session_id, "resolved": False}
        data = self._sessions[session_id]
        return {
            "session_id": session_id,
            "resolved": True,
            "email": data.get("ms_auth_email"),
            "display": data.get("ms_auth_email") or session_id,
        }

    def _log_prefix(self, session_id: str) -> str:
        """Generate log prefix: [identity|session_id]."""
        data = self._sessions.get(session_id, {})
        identity = data.get("ms_auth_email") or "unknown"
        return f"[{identity}|{session_id}]"

    def summary(self) -> dict:
        """Admin summary of all tracked sessions."""
        return {
            "total_sessions": len(self._sessions),
            "sessions": {
                sid: {
                    "display": self.get_identity(sid)["display"],
                    "email": d.get("ms_auth_email"),
                    "created": d.get("created_at"),
                    "active": d.get("last_active"),
                }
                for sid, d in self._sessions.items()
            }
        }
