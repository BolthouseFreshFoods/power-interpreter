"""Power Interpreter - User Tracker

Per-session user identity and resource monitoring.
Tiered identity resolution: ms_auth email > user_hint > session_id.

v2.10.0
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, Optional

log = logging.getLogger("power-interpreter.user-tracker")


@dataclass
class SessionInfo:
    """Tracks identity and resource usage for a single session."""

    session_id: str
    identity: str
    identity_source: str = "session_id"  # "session_id" | "user_hint" | "ms_auth"
    first_seen: str = ""
    last_active: str = ""
    executions: int = 0
    total_exec_ms: float = 0.0
    memory_peak_mb: float = 0.0
    files_created: int = 0
    errors: int = 0

    def __post_init__(self):
        now = datetime.now(timezone.utc).isoformat()
        if not self.first_seen:
            self.first_seen = now
        if not self.last_active:
            self.last_active = now


class UserTracker:
    """Singleton registry mapping session_ids to user identity + metrics.

    Identity resolution (highest priority wins):
      1. ms_auth email  - set when Microsoft auth completes
      2. user_hint      - optional label passed at create_session
      3. session_id     - always available as fallback
    """

    _instance: Optional["UserTracker"] = None

    def __new__(cls) -> "UserTracker":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._sessions: Dict[str, SessionInfo] = {}
            log.info("UserTracker initialized")
        return cls._instance

    # -- Registration -------------------------------------------------

    def register_session(
        self, session_id: str, user_hint: Optional[str] = None
    ) -> SessionInfo:
        """Register a new session. Called from create_session tool.

        If the session already exists, updates last_active and optionally
        upgrades identity from session_id to user_hint (but never
        downgrades from ms_auth or user_hint).
        """
        if session_id in self._sessions:
            existing = self._sessions[session_id]
            existing.last_active = datetime.now(timezone.utc).isoformat()
            # Only upgrade from session_id -> user_hint, never downgrade
            if user_hint and existing.identity_source == "session_id":
                existing.identity = user_hint
                existing.identity_source = "user_hint"
                log.info(
                    "Session '%s' identity upgraded to hint: %s",
                    session_id,
                    user_hint,
                )
            return existing

        identity = user_hint or session_id
        source = "user_hint" if user_hint else "session_id"
        info = SessionInfo(
            session_id=session_id,
            identity=identity,
            identity_source=source,
        )
        self._sessions[session_id] = info
        log.info(
            "Session registered: %s (identity=%s, source=%s)",
            session_id,
            identity,
            source,
        )
        return info

    def enrich_from_auth(self, session_id: str, email: str) -> None:
        """Upgrade session identity when ms_auth succeeds.

        This is the highest-priority identity source. Always overwrites
        previous identity regardless of source.
        """
        if session_id not in self._sessions:
            self.register_session(session_id)

        info = self._sessions[session_id]
        old_identity = info.identity
        info.identity = email
        info.identity_source = "ms_auth"
        info.last_active = datetime.now(timezone.utc).isoformat()
        log.info(
            "Session '%s' identity enriched: %s -> %s (source=ms_auth)",
            session_id,
            old_identity,
            email,
        )

    # -- Lookup -------------------------------------------------------

    def get_identity(self, session_id: str) -> str:
        """Return best-known identity for a session."""
        info = self._sessions.get(session_id)
        return info.identity if info else session_id

    def get_log_prefix(self, session_id: str) -> str:
        """Formatted prefix for log lines.

        Returns:
            '[identity|session_id]' if identity differs from session_id
            '[session_id]' if identity is just the session_id
        """
        identity = self.get_identity(session_id)
        if identity == session_id:
            return f"[{session_id}]"
        return f"[{identity}|{session_id}]"

    def get_session_info(self, session_id: str) -> Optional[SessionInfo]:
        """Return full SessionInfo, or None if not tracked."""
        return self._sessions.get(session_id)

    def get_all_sessions(self) -> Dict[str, SessionInfo]:
        """Return copy of all tracked sessions."""
        return dict(self._sessions)

    # -- Resource Tracking --------------------------------------------

    def record_execution(
        self,
        session_id: str,
        exec_time_ms: float,
        memory_mb: float = 0.0,
        error: bool = False,
    ) -> None:
        """Record a completed execution against a session.

        Called from executor.py after each code execution completes.
        Automatically registers session if not already tracked.
        """
        if session_id not in self._sessions:
            self.register_session(session_id)

        info = self._sessions[session_id]
        info.executions += 1
        info.total_exec_ms += exec_time_ms
        info.last_active = datetime.now(timezone.utc).isoformat()

        if memory_mb > info.memory_peak_mb:
            info.memory_peak_mb = memory_mb

        if error:
            info.errors += 1

    def record_file_created(
        self, session_id: str, count: int = 1
    ) -> None:
        """Increment file-created counter for a session."""
        if session_id not in self._sessions:
            self.register_session(session_id)
        self._sessions[session_id].files_created += count

    # -- Session Lifecycle --------------------------------------------

    def remove_session(self, session_id: str) -> bool:
        """Remove a session from tracking. Called on delete_session."""
        if session_id in self._sessions:
            info = self._sessions.pop(session_id)
            log.info(
                "Session removed: %s (identity=%s, executions=%d)",
                session_id,
                info.identity,
                info.executions,
            )
            return True
        return False

    # -- Admin --------------------------------------------------------

    def summary(self) -> dict:
        """Generate admin summary of all tracked sessions.

        Returns a JSON-serializable dict suitable for the
        /admin/sessions endpoint.
        """
        sessions = []
        for info in self._sessions.values():
            sessions.append(
                {
                    "session_id": info.session_id,
                    "identity": info.identity,
                    "identity_source": info.identity_source,
                    "first_seen": info.first_seen,
                    "last_active": info.last_active,
                    "executions": info.executions,
                    "total_exec_ms": round(info.total_exec_ms, 1),
                    "memory_peak_mb": round(info.memory_peak_mb, 1),
                    "files_created": info.files_created,
                    "errors": info.errors,
                }
            )

        return {
            "active_sessions": len(sessions),
            "authenticated_users": len(
                set(
                    s["identity"]
                    for s in sessions
                    if s["identity_source"] == "ms_auth"
                )
            ),
            "anonymous_sessions": len(
                [
                    s
                    for s in sessions
                    if s["identity_source"] != "ms_auth"
                ]
            ),
            "sessions": sessions,
        }
