"""Power Interpreter - Session Management Routes

Sessions provide workspace isolation:
- Each session has its own sandbox directory
- Files and datasets can be scoped to sessions
- Jobs track which session they belong to
"""

import uuid
from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.config import settings
from app.database import get_session_factory
from app.models import Session


router = APIRouter()


def _safe_parse_session_uuid(session_id: str) -> uuid.UUID:
    """Parse a session UUID and return a 400 if invalid."""
    try:
        return uuid.UUID(session_id)
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid session_id format",
        )


class CreateSessionRequest(BaseModel):
    """Request to create a new session."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, description="Session name")
    description: Optional[str] = Field(default="", description="Session description")
    metadata: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Additional metadata",
    )


class CreateSessionResponse(BaseModel):
    """Response returned after session creation."""

    session_id: str
    name: str
    description: Optional[str]
    sandbox_dir: str
    created_at: str


class SessionSummary(BaseModel):
    """Lightweight session summary for list responses."""

    session_id: str
    name: str
    description: str
    created_at: str
    updated_at: str


class SessionListResponse(BaseModel):
    """Response returned when listing sessions."""

    sessions: list[SessionSummary]
    count: int


class SessionDetailResponse(BaseModel):
    """Detailed session response."""

    session_id: str
    name: str
    description: str
    is_active: bool
    created_at: str
    updated_at: str
    metadata: Dict[str, Any]


class DeleteSessionResponse(BaseModel):
    """Response returned after session deactivation."""

    session_id: str
    name: str
    deleted: bool
    message: str


@router.post("/sessions", response_model=CreateSessionResponse)
async def create_session(request: CreateSessionRequest) -> CreateSessionResponse:
    """Create a new workspace session.

    Sessions isolate files, datasets, and jobs.
    Use the session_id in other API calls to scope operations.
    """
    session_uuid = uuid.uuid4()
    session_id = str(session_uuid)

    factory = get_session_factory()
    async with factory() as db_session:
        session = Session(
            id=session_uuid,
            name=request.name.strip(),
            description=request.description or "",
            metadata_=request.metadata or {},
        )
        db_session.add(session)
        await db_session.commit()

    session_dir = settings.SANDBOX_DIR / session_id
    session_dir.mkdir(parents=True, exist_ok=True)

    return CreateSessionResponse(
        session_id=session_id,
        name=request.name.strip(),
        description=request.description,
        sandbox_dir=str(session_dir),
        created_at=datetime.utcnow().isoformat(),
    )


@router.get("/sessions", response_model=SessionListResponse)
async def list_sessions() -> SessionListResponse:
    """List all active sessions."""
    factory = get_session_factory()
    async with factory() as db_session:
        result = await db_session.execute(
            select(Session)
            .where(Session.is_active == True)
            .order_by(Session.created_at.desc())
        )
        sessions = result.scalars().all()

        return SessionListResponse(
            sessions=[
                SessionSummary(
                    session_id=str(s.id),
                    name=s.name,
                    description=s.description,
                    created_at=s.created_at.isoformat(),
                    updated_at=s.updated_at.isoformat(),
                )
                for s in sessions
            ],
            count=len(sessions),
        )


@router.get("/sessions/{session_id}", response_model=SessionDetailResponse)
async def get_session(session_id: str) -> SessionDetailResponse:
    """Get session details."""
    session_uuid = _safe_parse_session_uuid(session_id)

    factory = get_session_factory()
    async with factory() as db_session:
        result = await db_session.execute(
            select(Session).where(Session.id == session_uuid)
        )
        session = result.scalar_one_or_none()

        if not session:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Session not found",
            )

        return SessionDetailResponse(
            session_id=str(session.id),
            name=session.name,
            description=session.description,
            is_active=session.is_active,
            created_at=session.created_at.isoformat(),
            updated_at=session.updated_at.isoformat(),
            metadata=session.metadata_ or {},
        )


@router.delete("/sessions/{session_id}", response_model=DeleteSessionResponse)
async def delete_session(session_id: str) -> DeleteSessionResponse:
    """Soft-delete a session by setting is_active=False.

    Session sandbox files are preserved for recovery.
    The session will no longer appear in list_sessions.
    """
    session_uuid = _safe_parse_session_uuid(session_id)

    factory = get_session_factory()
    async with factory() as db_session:
        result = await db_session.execute(
            select(Session).where(Session.id == session_uuid)
        )
        session = result.scalar_one_or_none()

        if not session:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Session not found",
            )

        if not session.is_active:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Session already deactivated",
            )

        session.is_active = False
        await db_session.commit()

        return DeleteSessionResponse(
            session_id=str(session.id),
            name=session.name,
            deleted=True,
            message=f"Session '{session.name}' deactivated. Sandbox files preserved.",
        )
