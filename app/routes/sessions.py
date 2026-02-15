"""Power Interpreter - Session Management Routes

Sessions provide workspace isolation:
- Each session has its own sandbox directory
- Files and datasets can be scoped to sessions
- Jobs track which session they belong to
"""

import uuid
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Optional, Dict
from datetime import datetime
from sqlalchemy import select

from app.models import Session
from app.database import get_session_factory
from app.config import settings

router = APIRouter()


class CreateSessionRequest(BaseModel):
    """Request to create a new session"""
    name: str = Field(..., description="Session name")
    description: Optional[str] = Field(default="", description="Session description")
    metadata: Optional[Dict] = Field(default=None, description="Additional metadata")


@router.post("/sessions")
async def create_session(request: CreateSessionRequest):
    """Create a new workspace session
    
    Sessions isolate files, datasets, and jobs.
    Use the session_id in other API calls to scope operations.
    """
    session_id = str(uuid.uuid4())
    
    factory = get_session_factory()
    async with factory() as db_session:
        session = Session(
            id=uuid.UUID(session_id),
            name=request.name,
            description=request.description or "",
            metadata_=request.metadata or {}
        )
        db_session.add(session)
        await db_session.commit()
    
    # Create sandbox directory
    session_dir = settings.SANDBOX_DIR / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    
    return {
        "session_id": session_id,
        "name": request.name,
        "description": request.description,
        "sandbox_dir": str(session_dir),
        "created_at": datetime.utcnow().isoformat()
    }


@router.get("/sessions")
async def list_sessions():
    """List all sessions"""
    factory = get_session_factory()
    async with factory() as db_session:
        result = await db_session.execute(
            select(Session).where(Session.is_active == True).order_by(Session.created_at.desc())
        )
        sessions = result.scalars().all()
        
        return {
            "sessions": [{
                "session_id": str(s.id),
                "name": s.name,
                "description": s.description,
                "created_at": s.created_at.isoformat(),
                "updated_at": s.updated_at.isoformat(),
            } for s in sessions],
            "count": len(sessions)
        }


@router.get("/sessions/{session_id}")
async def get_session(session_id: str):
    """Get session details"""
    factory = get_session_factory()
    async with factory() as db_session:
        result = await db_session.execute(
            select(Session).where(Session.id == uuid.UUID(session_id))
        )
        session = result.scalar_one_or_none()
        
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        
        return {
            "session_id": str(session.id),
            "name": session.name,
            "description": session.description,
            "is_active": session.is_active,
            "created_at": session.created_at.isoformat(),
            "updated_at": session.updated_at.isoformat(),
            "metadata": session.metadata_
        }
