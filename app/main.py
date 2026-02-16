"""Power Interpreter MCP - Main Application

General-purpose sandboxed Python execution engine.
Designed for SimTheory.ai MCP integration.

Version: 1.0.0
"""

import logging
import asyncio
import traceback
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.auth import verify_api_key

# Configure logging
logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL),
    format='%(asctime)s | %(name)s | %(levelname)s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)


# Track database state
_db_available = False


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifecycle management"""
    global _db_available
    
    # --- STARTUP ---
    logger.info("=" * 60)
    logger.info("Power Interpreter MCP v1.0.0 starting...")
    logger.info("=" * 60)
    
    # Ensure directories exist
    settings.ensure_directories()
    logger.info("Directories ready")
    
    # Try to initialize database (non-fatal if not available)
    try:
        if settings.DATABASE_URL:
            from app.database import init_database
            await init_database()
            _db_available = True
            logger.info("Database initialized successfully")
        else:
            logger.warning("DATABASE_URL not set - running without database")
    except Exception as e:
        logger.error(f"Database init failed (non-fatal): {e}")
        logger.error(traceback.format_exc())
        _db_available = False
    
    logger.info("Power Interpreter ready!")
    logger.info(f"  Sandbox dir: {settings.SANDBOX_DIR}")
    logger.info(f"  Max execution time: {settings.MAX_EXECUTION_TIME}s")
    logger.info(f"  Database: {'connected' if _db_available else 'NOT AVAILABLE'}")
    
    yield
    
    # --- SHUTDOWN ---
    logger.info("Power Interpreter shutting down...")
    if _db_available:
        try:
            from app.database import shutdown_database
            await shutdown_database()
        except Exception:
            pass
    logger.info("Shutdown complete")


# Create FastAPI app
app = FastAPI(
    title="Power Interpreter MCP",
    description=(
        "General-purpose sandboxed Python execution engine. "
        "Execute code, manage files, query large datasets, "
        "and run long-running analysis jobs without timeouts."
    ),
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- HEALTH CHECK (no auth, no imports that could fail) ---
@app.get("/health")
async def health_check():
    """Health check endpoint for Railway"""
    from app import __version__
    return {
        "status": "healthy",
        "version": __version__,
        "service": "power-interpreter",
        "database": "connected" if _db_available else "not_connected",
    }


@app.get("/")
async def root():
    """Root endpoint"""
    from app import __version__
    return {
        "service": "Power Interpreter MCP",
        "version": __version__,
        "description": "General-purpose sandboxed Python execution engine",
        "docs": "/docs",
        "health": "/health"
    }


# --- PROTECTED ROUTES (loaded after app starts) ---
try:
    from app.routes import execute, jobs, files, data, sessions
    
    app.include_router(
        execute.router, prefix="/api", tags=["Execute"],
        dependencies=[Depends(verify_api_key)]
    )
    app.include_router(
        jobs.router, prefix="/api", tags=["Jobs"],
        dependencies=[Depends(verify_api_key)]
    )
    app.include_router(
        files.router, prefix="/api", tags=["Files"],
        dependencies=[Depends(verify_api_key)]
    )
    app.include_router(
        data.router, prefix="/api", tags=["Data"],
        dependencies=[Depends(verify_api_key)]
    )
    app.include_router(
        sessions.router, prefix="/api", tags=["Sessions"],
        dependencies=[Depends(verify_api_key)]
    )
    logger.info("All API routes loaded")
except Exception as e:
    logger.error(f"Failed to load routes: {e}")
    logger.error(traceback.format_exc())
