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
Version: 1.0.4
"""

import logging
import asyncio
import re
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

# =============================================================================
# SESSION CACHE for SSE proxy
# =============================================================================
# We cache the session_id so we don't have to open a new SSE connection
# for every single POST from SimTheory. Sessions are created on first
# request and reused until they expire or fail.
# =============================================================================
_cached_session_id = None
_session_lock = asyncio.Lock()


async def _get_or_create_session_id() -> str:
    """
    Get a cached session_id or create a new one by connecting to the
    SSE endpoint and reading the 'endpoint' event.
    
    The SSE endpoint sends:
        event: endpoint
        data: /messages/?session_id=<uuid>
    
    We parse the session_id from that data line.
    """
    global _cached_session_id
    
    if _cached_session_id:
        logger.info(f"SSE proxy: using cached session_id={_cached_session_id}")
        return _cached_session_id
    
    async with _session_lock:
        # Double-check after acquiring lock
        if _cached_session_id:
            return _cached_session_id
        
        port = 8080
        sse_url = f"http://127.0.0.1:{port}/mcp/sse"
        
        logger.info(f"SSE proxy: creating new session via GET {sse_url}")
        
        try:
            async with httpx.AsyncClient() as client:
                # Stream the SSE response - we just need the first few events
                async with client.stream("GET", sse_url, timeout=10.0) as response:
                    buffer = ""
                    async for chunk in response.aiter_text():
                        buffer += chunk
                        logger.info(f"SSE proxy: received SSE data chunk: {repr(chunk[:200])}")
                        
                        # Look for the endpoint event with session_id
                        # Format: event: endpoint\ndata: /messages/?session_id=<uuid>\n\n
                        match = re.search(r'session_id=([a-f0-9-]+)', buffer)
                        if match:
                            _cached_session_id = match.group(1)
                            logger.info(f"SSE proxy: extracted session_id={_cached_session_id}")
                            return _cached_session_id
                        
                        # Safety: don't read forever
                        if len(buffer) > 4096:
                            logger.error("SSE proxy: read 4KB without finding session_id")
                            break
                            
        except Exception as e:
            logger.error(f"SSE proxy: failed to create session: {e}")
        
        return None


async def _invalidate_session():
    """Clear the cached session so next request creates a new one."""
    global _cached_session_id
    _cached_session_id = None
    logger.info("SSE proxy: session cache invalidated")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifecycle management"""
    # --- STARTUP ---
    logger.info("="*60)
    logger.info("Power Interpreter MCP v1.0.4 starting...")
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
    logger.info(f"  SSE POST proxy: v2 with auto-session (POST /mcp/sse -> /mcp/messages/)")
    
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
    version="1.0.4",
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
# SSE POST PROXY v2 - Auto-session for SimTheory MCP client
# =============================================================================
# SimTheory's MCP client POSTs to /mcp/sse WITHOUT a session_id.
# The MCP SDK requires session_id on /mcp/messages/.
#
# This proxy:
# 1. Creates an SSE session by connecting to GET /mcp/sse internally
# 2. Extracts the session_id from the 'endpoint' event
# 3. Caches the session_id for reuse
# 4. Forwards the POST to /mcp/messages/?session_id=xxx
# 5. If the session expires (400/404), creates a new one and retries
#
# This MUST be defined before app.mount("/mcp", ...) so FastAPI matches
# this route before the mounted sub-application.
# =============================================================================

@app.post("/mcp/sse")
async def proxy_mcp_sse_post(request: Request):
    """
    Proxy POST /mcp/sse -> /mcp/messages/?session_id=xxx
    
    Automatically creates and caches SSE sessions for SimTheory compatibility.
    """
    body = await request.body()
    
    logger.info(f"SSE POST proxy v2: received POST /mcp/sse")
    logger.info(f"  Body length: {len(body)} bytes")
    logger.info(f"  Body preview: {body[:200]}")
    
    # Get or create a session
    session_id = await _get_or_create_session_id()
    
    if not session_id:
        logger.error("SSE POST proxy: failed to obtain session_id")
        return Response(
            content=b'{"jsonrpc":"2.0","error":{"code":-32603,"message":"Failed to create MCP session"},"id":null}',
            status_code=500,
            media_type="application/json",
        )
    
    # Forward to /mcp/messages/ with session_id
    port = 8080
    target_url = f"http://127.0.0.1:{port}/mcp/messages/?session_id={session_id}"
    
    logger.info(f"SSE POST proxy v2: forwarding to {target_url}")
    
    # Forward headers
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
                timeout=60.0,
            )
        
        logger.info(f"SSE POST proxy v2: response status={response.status_code}")
        logger.info(f"SSE POST proxy v2: response body={response.text[:500]}")
        
        # If session expired or invalid, invalidate cache and retry once
        if response.status_code in (400, 404):
            logger.warning("SSE POST proxy v2: session may be expired, retrying with new session")
            await _invalidate_session()
            
            # Get a fresh session
            new_session_id = await _get_or_create_session_id()
            if new_session_id:
                retry_url = f"http://127.0.0.1:{port}/mcp/messages/?session_id={new_session_id}"
                logger.info(f"SSE POST proxy v2: retrying with {retry_url}")
                
                async with httpx.AsyncClient() as client2:
                    response = await client2.post(
                        retry_url,
                        content=body,
                        headers=forward_headers,
                        timeout=60.0,
                    )
                
                logger.info(f"SSE POST proxy v2: retry response status={response.status_code}")
                logger.info(f"SSE POST proxy v2: retry response body={response.text[:500]}")
        
        return Response(
            content=response.content,
            status_code=response.status_code,
            headers=dict(response.headers),
            media_type=response.headers.get("content-type"),
        )
        
    except httpx.ConnectError as e:
        logger.error(f"SSE POST proxy v2: connection error: {e}")
        return Response(
            content=b'{"jsonrpc":"2.0","error":{"code":-32603,"message":"Internal proxy connection failed"},"id":null}',
            status_code=502,
            media_type="application/json",
        )
    except httpx.TimeoutException as e:
        logger.error(f"SSE POST proxy v2: timeout: {e}")
        return Response(
            content=b'{"jsonrpc":"2.0","error":{"code":-32603,"message":"Internal proxy timeout"},"id":null}',
            status_code=504,
            media_type="application/json",
        )
    except Exception as e:
        logger.error(f"SSE POST proxy v2: unexpected error: {e}")
        return Response(
            content=b'{"jsonrpc":"2.0","error":{"code":-32603,"message":"Internal proxy error"},"id":null}',
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
