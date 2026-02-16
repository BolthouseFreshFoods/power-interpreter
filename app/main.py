"""Power Interpreter MCP - Main Application

General-purpose sandboxed Python execution engine.
Designed for SimTheory.ai MCP integration.

Features:
- Execute Python code in sandboxed environment
- Async job queue for long-running operations (no timeouts)
- Large dataset support (1.5M+ rows via PostgreSQL)
- File upload/download management
- Pre-installed data science libraries

Author: Kaffer AI for Timothy Escamilla
Version: 1.0.0
"""

import logging
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.auth import verify_api_key
from app.routes import execute, jobs, files, data, sessions, health
from app.mcp_server import mcp

# Configure logging
logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL),
    format='%(asctime)s | %(name)s | %(levelname)s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)


def _get_mcp_app():
    """Get the MCP ASGI app, trying multiple methods for compatibility.
    
    Different versions of the mcp package expose different methods:
    - mcp >= 1.8: streamable_http_app()
    - mcp >= 1.0: sse_app() 
    - older: get_asgi_app()
    """
    # Try streamable_http_app first (mcp >= 1.8)
    if hasattr(mcp, 'streamable_http_app'):
        logger.info("MCP: using streamable_http_app()")
        return mcp.streamable_http_app()
    
    # Try sse_app (mcp >= 1.0)
    if hasattr(mcp, 'sse_app'):
        logger.info("MCP: using sse_app()")
        return mcp.sse_app()
    
    # Try get_asgi_app (older versions)
    if hasattr(mcp, 'get_asgi_app'):
        logger.info("MCP: using get_asgi_app()")
        return mcp.get_asgi_app()
    
    # Last resort - try as ASGI app directly
    logger.warning("MCP: no known app method found, attempting direct mount")
    return mcp


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifecycle management"""
    # --- STARTUP ---
    logger.info("="*60)
    logger.info("Power Interpreter MCP v1.0.0 starting...")
    logger.info("="*60)
    
    # Ensure directories exist
    settings.ensure_directories()
    
    # Initialize database (graceful - don't crash if DB not ready)
    db_ok = False
    if settings.DATABASE_URL:
        try:
            from app.database import init_database
            await init_database()
            db_ok = True
            logger.info("Database initialized successfully")
        except Exception as e:
            logger.warning(f"Database initialization failed: {e}")
            logger.warning("App will start without database. Some features disabled.")
    else:
        logger.warning("No DATABASE_URL configured. Running without database.")
        logger.warning("Set DATABASE_URL to enable: jobs, sessions, datasets, file tracking")
    
    # Start periodic job cleanup only if DB is available
    cleanup_task = None
    if db_ok:
        cleanup_task = asyncio.create_task(_periodic_cleanup())
    
    logger.info("Power Interpreter ready!")
    logger.info(f"  Database: {'connected' if db_ok else 'NOT CONNECTED'}")
    logger.info(f"  Sandbox dir: {settings.SANDBOX_DIR}")
    logger.info(f"  Max execution time: {settings.MAX_EXECUTION_TIME}s")
    logger.info(f"  Max memory: {settings.MAX_MEMORY_MB} MB")
    logger.info(f"  Max concurrent jobs: {settings.MAX_CONCURRENT_JOBS}")
    logger.info(f"  Job timeout: {settings.JOB_TIMEOUT}s")
    logger.info(f"  MCP server: mounted at /mcp")
    
    yield
    
    # --- SHUTDOWN ---
    logger.info("Power Interpreter shutting down...")
    if cleanup_task:
        cleanup_task.cancel()
    if db_ok:
        try:
            from app.database import shutdown_database
            await shutdown_database()
        except Exception:
            pass
    logger.info("Shutdown complete")


async def _periodic_cleanup():
    """Periodically clean up old jobs and temp files"""
    while True:
        try:
            await asyncio.sleep(3600)  # Every hour
            from app.engine.job_manager import job_manager
            count = await job_manager.cleanup_old_jobs()
            if count:
                logger.info(f"Periodic cleanup: removed {count} old jobs")
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Cleanup error: {e}")


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

# CORS (allow SimTheory.ai)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Railway handles security at network level
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- PUBLIC ROUTES (no auth) ---
app.include_router(health.router, tags=["Health"])

# --- PROTECTED ROUTES (API key required) ---
app.include_router(
    execute.router, 
    prefix="/api", 
    tags=["Execute"],
    dependencies=[Depends(verify_api_key)]
)
app.include_router(
    jobs.router, 
    prefix="/api", 
    tags=["Jobs"],
    dependencies=[Depends(verify_api_key)]
)
app.include_router(
    files.router, 
    prefix="/api", 
    tags=["Files"],
    dependencies=[Depends(verify_api_key)]
)
app.include_router(
    data.router, 
    prefix="/api", 
    tags=["Data"],
    dependencies=[Depends(verify_api_key)]
)
app.include_router(
    sessions.router, 
    prefix="/api", 
    tags=["Sessions"],
    dependencies=[Depends(verify_api_key)]
)

# --- MCP SERVER (SimTheory.ai tool discovery & execution) ---
# Mount the FastMCP server so SimTheory can discover and call all 10 tools
# Uses compatibility wrapper to support multiple mcp package versions
mcp_app = _get_mcp_app()
app.mount("/mcp", mcp_app)
