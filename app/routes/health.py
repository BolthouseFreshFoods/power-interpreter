"""Power Interpreter - Health Check Routes

Public endpoints (no auth required).
"""

from fastapi import APIRouter
from datetime import datetime
from app.database import check_database
from app.engine.job_manager import job_manager
from app import __version__

router = APIRouter()


@router.get("/health")
async def health_check():
    """Health check endpoint for Railway"""
    db_ok = await check_database()
    
    return {
        "status": "healthy" if db_ok else "degraded",
        "version": __version__,
        "service": "power-interpreter",
        "database": "connected" if db_ok else "disconnected",
        "active_jobs": job_manager.active_job_count,
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
