"""Power Interpreter - Health Check Routes

Public endpoints (no auth required).
"""

from fastapi import APIRouter
from datetime import datetime
from app import __version__
from app.config import settings

router = APIRouter()


@router.get("/health")
async def health_check():
    """Health check endpoint for Railway"""
    db_status = "not_configured"
    
    if settings.DATABASE_URL:
        try:
            from app.database import check_database
            db_ok = await check_database()
            db_status = "connected" if db_ok else "disconnected"
        except Exception:
            db_status = "error"
    
    return {
        "status": "healthy",
        "version": __version__,
        "service": "power-interpreter",
        "database": db_status,
        "timestamp": datetime.utcnow().isoformat()
    }


@router.get("/")
async def root():
    """Root endpoint"""
    return {
        "service": "Power Interpreter MCP",
        "version": __version__,
        "description": "General-purpose sandboxed Python execution engine",
        "docs": "/docs",
        "health": "/health"
    }
