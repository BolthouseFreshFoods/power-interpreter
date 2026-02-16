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
Version: 1.0.2
"""

import logging
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, Request
from fastapi.responses import Response
from fastapi.middleware.cors import CORSMiddleware
import httpx

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


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifecycle management"""
    # --- STARTUP ---
    logger.info("="*60)
    logger.info("Power Interpreter MCP v1.0.2 starting...")
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
    logger.info(f"  MCP server: mounted at /mcp (SSE transport)")
    logger.info(f"  SSE POST proxy: enabled (POST /mcp/sse -> /mcp/messages/)")
    
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
    version="1.0.2",
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


# =============================================================================
# SSE POST PROXY - Fix for SimTheory MCP client compatibility
# =============================================================================
# SimTheory's MCP client POSTs tool call messages back to the same URL it
# connected to (POST /mcp/sse) instead of reading the 'endpoint' event from
# the SSE stream and POSTing to /mcp/messages/?session_id=xxx.
#
# This proxy intercepts POST /mcp/sse and forwards the request internally
# to /mcp/messages/ with the same query parameters and body.
#
# This MUST be defined before app.mount("/mcp", ...) so FastAPI matches
# this route before the mounted sub-application.
# =============================================================================

@app.post("/mcp/sse")
async def proxy_mcp_sse_post(request: Request):
    """
    Proxy POST /mcp/sse -> /mcp/messages/ for SimTheory compatibility.
    
    SimTheory sends MCP JSON-RPC messages (tool calls, initialize, etc.)
    to POST /mcp/sse instead of POST /mcp/messages/. This handler
    forwards those requests to the correct MCP message endpoint.
    """
    # Read the incoming request
    body = await request.body()
    query_string = str(request.url.query)
    
    # Build the target URL - forward to /mcp/messages/ with same query params
    target_path = f"/mcp/messages/"
    if query_string:
        target_path = f"{target_path}?{query_string}"
    
    # Get the port from the server
    port = settings.PORT if hasattr(settings, 'PORT') else 8080
    base_url = f"http://127.0.0.1:{port}"
    target_url = f"{base_url}{target_path}"
    
    logger.info(f"SSE POST proxy: forwarding POST /mcp/sse -> {target_path}")
    logger.info(f"  Query params: {query_string}")
    logger.info(f"  Body length: {len(body)} bytes")
    
    # Forward headers (pass through content-type and other relevant headers)
    forward_headers = {}
    if "content-type" in request.headers:
        forward_headers["content-type"] = request.headers["content-type"]
    if "accept" in request.headers:
        forward_headers["accept"] = request.headers["accept"]
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                target_url,
                content=body,
                headers=forward_headers,
                timeout=30.0,
            )
        
        logger.info(f"SSE POST proxy: response status={response.status_code}")
        
        # Return the response from the MCP message handler
        return Response(
            content=response.content,
            status_code=response.status_code,
            headers=dict(response.headers),
            media_type=response.headers.get("content-type"),
        )
    except httpx.ConnectError as e:
        logger.error(f"SSE POST proxy: connection error: {e}")
        return Response(
            content=b'{"error": "Internal proxy connection failed"}',
            status_code=502,
            media_type="application/json",
        )
    except httpx.TimeoutException as e:
        logger.error(f"SSE POST proxy: timeout: {e}")
        return Response(
            content=b'{"error": "Internal proxy timeout"}',
            status_code=504,
            media_type="application/json",
        )
    except Exception as e:
        logger.error(f"SSE POST proxy: unexpected error: {e}")
        return Response(
            content=b'{"error": "Internal proxy error"}',
            status_code=500,
            media_type="application/json",
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
# Mount the FastMCP SSE app at /mcp for MCP protocol support
# This provides: tool discovery, tool execution, SSE streaming
# Requires mcp>=1.6.0 for sse_app() support
#
# NOTE: The POST /mcp/sse proxy route above MUST be defined before this mount.
# FastAPI checks explicit routes before mounted sub-applications, so the proxy
# will intercept POST /mcp/sse while GET /mcp/sse still goes to the SSE app.
app.mount("/mcp", mcp.sse_app())
