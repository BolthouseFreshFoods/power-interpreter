"""Power Interpreter MCP - Main Application

General-purpose sandboxed Python execution engine.
Designed for SimTheory.ai MCP integration.

Features:
- Execute Python code in sandboxed environment
- Async job queue for long-running operations (no timeouts)
- Large dataset support (1.5M+ rows via PostgreSQL)
- File upload/download management
- Pre-installed data science libraries
- Persistent session state (kernel architecture)
- Auto file storage in Postgres with public download URLs
- Microsoft OneDrive + SharePoint integration (v1.9.0)

Version: 3.0.3

HISTORY:
  v1.7.2: fetch_from_url route fix, stable release
  v1.8.1: Chart serving route + inline base64 image blocks
  v1.9.0: Microsoft OneDrive + SharePoint integration (20 new MCP tools)
  v1.9.2: Token persistence rewrite (SQLAlchemy), ms_auth_poll tool
  v2.8.6: Version unification across all files
  v2.9.0: Trimmed all 34 tool descriptions for token optimization (~57% reduction)
  v2.9.1: Smart error handling for empty execute_code args (model-agnostic)
  v2.9.2: Response guardrails — truncate oversized MCP tool results
  v3.0.0: Context pressure guard — per-tool caps, pressure warnings, improved recovery
  v3.0.1: Pre-execution syntax guard — catch truncated code before sandbox
  v3.0.2: Route Python logs to stdout + response budget enforcement hook
  v3.0.3: Reduce MCP log noise + metadata-only execute_code request logging
"""

import asyncio
import inspect
import json
import logging
import sys
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response

from app.auth import verify_api_key
from app.config import settings
from app.context_guard import (
    get_effective_cap,
    get_empty_args_recovery_message,
    maybe_add_pressure_warning,
)
from app.mcp_server import mcp
from app.response_budget import enforce_response_budget
from app.routes import data, execute, files, health, jobs, sessions
from app.routes.files import public_router as download_router
from app.syntax_guard import check_syntax


logging.basicConfig(
    stream=sys.stdout,
    level=getattr(logging, settings.LOG_LEVEL),
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

MCP_RESPONSE_MAX_CHARS = 50_000
_skill_tools: dict[str, Any] = {}


def _jsonrpc_error(
    msg_id: Any,
    code: int,
    message: str,
    status_code: int = 200,
) -> JSONResponse:
    return JSONResponse(
        content={
            "jsonrpc": "2.0",
            "error": {"code": code, "message": message},
            "id": msg_id,
        },
        status_code=status_code,
    )


def _safe_json_loads(body_str: str) -> dict | list:
    return json.loads(body_str)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("=" * 60)
    logger.info("Power Interpreter MCP v3.0.3 starting...")
    logger.info("=" * 60)

    settings.ensure_directories()

    db_ok = False
    if settings.DATABASE_URL:
        try:
            from app.database import init_database

            await init_database()
            db_ok = True
            logger.info("Database initialized successfully")
        except Exception as e:
            logger.warning("Database initialization failed: %s", e)
            logger.warning("App will start without database. Some features disabled.")
    else:
        logger.warning("No DATABASE_URL configured. Running without database.")
        logger.warning("Set DATABASE_URL to enable: jobs, sessions, datasets, file tracking")

    if db_ok:
        try:
            from app.mcp_server import _ms_auth

            if _ms_auth:
                await _ms_auth.ensure_db_table()
                logger.info("Microsoft token persistence: ENABLED (Postgres)")
            else:
                logger.info("Microsoft token persistence: SKIPPED (no auth manager)")
        except Exception as e:
            logger.warning("Microsoft token table setup failed: %s", e)
            logger.warning("Microsoft auth will work but tokens won't persist across deploys")

        try:
            from app.skills_integration import initialize_skills

            _skills_result = await initialize_skills(mcp)
            if _skills_result:
                global _skill_tools
                _skill_tools = _skills_result
                logger.info("Skills layer: %s skill tools registered", len(_skill_tools))
            else:
                logger.info("Skills layer: no skills registered")
        except ImportError:
            logger.info("Skills layer: module not found, skipping")
        except Exception as e:
            logger.warning("Skills layer initialization failed: %s", e)

    cleanup_task = None
    if db_ok:
        cleanup_task = asyncio.create_task(_periodic_cleanup())

    public_url = settings.public_base_url

    logger.info("Power Interpreter ready!")
    logger.info("  Database: %s", "connected" if db_ok else "NOT CONNECTED")
    logger.info("  Sandbox dir: %s", settings.SANDBOX_DIR)
    logger.info(
        "  Public URL: %s",
        public_url or "(auto-detect from RAILWAY_PUBLIC_DOMAIN)",
    )
    logger.info("  Download endpoint: /dl/{file_id} (public, no auth)")
    logger.info("  Chart endpoint: /charts/{session_id}/{filename} (public, no auth)")
    logger.info("  Sandbox file max: %s MB", settings.SANDBOX_FILE_MAX_MB)
    logger.info("  Sandbox file TTL: %s hours", settings.SANDBOX_FILE_TTL_HOURS)
    logger.info("  Max execution time: %ss", settings.MAX_EXECUTION_TIME)
    logger.info("  Max memory: %s MB", settings.MAX_MEMORY_MB)
    logger.info("  Max concurrent jobs: %s", settings.MAX_CONCURRENT_JOBS)
    logger.info("  Job timeout: %ss", settings.JOB_TIMEOUT)
    logger.info("  MCP SSE transport: GET /mcp/sse (standard clients)")
    logger.info("  MCP JSON-RPC direct: POST /mcp/sse (SimTheory)")

    yield

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
    while True:
        try:
            await asyncio.sleep(3600)

            from app.engine.job_manager import job_manager

            count = await job_manager.cleanup_old_jobs()
            if count:
                logger.info("Periodic cleanup: removed %s old jobs", count)

            try:
                await _cleanup_expired_sandbox_files()
            except Exception as e:
                logger.error("Sandbox file cleanup error: %s", e)

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error("Cleanup error: %s", e)


async def _cleanup_expired_sandbox_files():
    try:
        from sqlalchemy import delete

        from app.database import get_session_factory
        from app.models import SandboxFile

        factory = get_session_factory()
        async with factory() as session:
            result = await session.execute(
                delete(SandboxFile).where(
                    SandboxFile.expires_at != None,
                    SandboxFile.expires_at < datetime.utcnow(),
                )
            )
            deleted = result.rowcount
            if deleted:
                await session.commit()
                logger.info("Cleaned up %s expired sandbox files", deleted)
    except Exception as e:
        logger.error("Failed to clean expired sandbox files: %s", e)


app = FastAPI(
    title="Power Interpreter MCP",
    description=(
        "General-purpose sandboxed Python execution engine. "
        "Execute code, manage files, query large datasets, "
        "and run long-running analysis jobs without timeouts. "
        "Generated files get persistent download URLs via /dl/{file_id}. "
        "Charts served at /charts/{session_id}/{filename}. "
        "Microsoft OneDrive + SharePoint integration."
    ),
    version="3.0.3",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/charts/{session_id}/{filename}")
async def serve_chart(session_id: str, filename: str):
    logger.info("Chart request: session=%s filename=%s", session_id, filename)

    try:
        from sqlalchemy import select

        from app.database import get_session_factory
        from app.models import SandboxFile

        factory = get_session_factory()
        async with factory() as db_session:
            result = await db_session.execute(
                select(SandboxFile)
                .where(SandboxFile.session_id == session_id)
                .where(SandboxFile.filename == filename)
                .order_by(SandboxFile.created_at.desc())
                .limit(1)
            )
            file_record = result.scalar_one_or_none()

            if not file_record:
                logger.warning("Chart not found: session=%s filename=%s", session_id, filename)
                return JSONResponse(
                    status_code=404,
                    content={
                        "error": f"Chart not found: {session_id}/{filename}",
                        "hint": "The chart may have expired or the session_id may be wrong.",
                    },
                )

            content_type = getattr(file_record, "content_type", None) or "application/octet-stream"
            fname_lower = filename.lower()
            if fname_lower.endswith(".png"):
                content_type = "image/png"
            elif fname_lower.endswith(".jpg") or fname_lower.endswith(".jpeg"):
                content_type = "image/jpeg"
            elif fname_lower.endswith(".svg"):
                content_type = "image/svg+xml"
            elif fname_lower.endswith(".gif"):
                content_type = "image/gif"
            elif fname_lower.endswith(".pdf"):
                content_type = "application/pdf"

            file_data = getattr(file_record, "file_data", None)
            if file_data is None:
                file_data = getattr(file_record, "data", None)
            if file_data is None:
                file_data = getattr(file_record, "content", None)

            if file_data is None:
                logger.error(
                    "Chart found but no binary data: session=%s filename=%s record_id=%s",
                    session_id,
                    filename,
                    getattr(file_record, "id", "?"),
                )
                return JSONResponse(
                    status_code=500,
                    content={"error": "File record found but binary data is missing"},
                )

            file_size = len(file_data)
            logger.info(
                "Chart served: session=%s filename=%s size=%s bytes content_type=%s",
                session_id,
                filename,
                file_size,
                content_type,
            )

            return Response(
                content=file_data,
                media_type=content_type,
                headers={
                    "Content-Disposition": f'inline; filename="{filename}"',
                    "Cache-Control": "public, max-age=3600",
                    "X-Power-Interpreter": "chart-serve-v3.0.3",
                },
            )

    except ImportError as e:
        logger.error("Chart serve import error: %s", e)
        return JSONResponse(
            status_code=503,
            content={"error": "Database not available"},
        )
    except Exception as e:
        logger.error(
            "Chart serve error: session=%s filename=%s: %s",
            session_id,
            filename,
            e,
            exc_info=True,
        )
        return JSONResponse(
            status_code=500,
            content={"error": f"Internal error serving chart: {e}"},
        )


def _build_tool_schema(tool) -> dict:
    if hasattr(tool, "parameters") and tool.parameters:
        return tool.parameters

    fn = tool.fn if hasattr(tool, "fn") else tool
    if not callable(fn):
        return {"type": "object", "properties": {}}

    sig = inspect.signature(fn)
    properties = {}
    required = []

    for param_name, param in sig.parameters.items():
        if param_name in ("self", "cls"):
            continue

        prop = {"type": "string"}
        if param.annotation != inspect.Parameter.empty:
            type_map = {
                int: "integer",
                float: "number",
                bool: "boolean",
                str: "string",
            }
            ann = param.annotation
            origin = getattr(ann, "__origin__", None)
            if origin is not None:
                args = getattr(ann, "__args__", ())
                if args:
                    ann = args[0]
            prop["type"] = type_map.get(ann, "string")

        if param.default != inspect.Parameter.empty:
            if param.default is not None:
                prop["default"] = param.default
        else:
            required.append(param_name)

        properties[param_name] = prop

    schema = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


def _get_tool_registry():
    base = {}
    try:
        if hasattr(mcp, "_tool_manager") and hasattr(mcp._tool_manager, "_tools"):
            base = mcp._tool_manager._tools
        elif hasattr(mcp, "tools"):
            base = mcp.tools
        else:
            logger.warning("Could not find tool registry on MCP server")
    except Exception as e:
        logger.error("Error accessing tool registry: %s", e)

    if _skill_tools:
        merged = dict(base)
        merged.update(_skill_tools)
        return merged
    return base


def _get_tools_list() -> list:
    result = []
    registry = _get_tool_registry()

    for name, tool in registry.items():
        desc = ""
        if hasattr(tool, "description"):
            desc = tool.description or ""
        elif hasattr(tool, "fn") and tool.fn.__doc__:
            desc = tool.fn.__doc__.strip()

        result.append(
            {
                "name": name,
                "description": desc,
                "inputSchema": _build_tool_schema(tool),
            }
        )

    return result


def _validate_tool_args(fn, tool_args: dict, tool_name: str) -> str | None:
    try:
        sig = inspect.signature(fn)
        missing = []
        for param_name, param in sig.parameters.items():
            if param_name in ("self", "cls"):
                continue
            if param.default is inspect.Parameter.empty and param_name not in tool_args:
                missing.append(param_name)

        if missing:
            return (
                f"Missing required parameter(s) for '{tool_name}': {', '.join(missing)}. "
                f"Please provide: {', '.join(missing)}"
            )
    except Exception:
        pass

    return None


@app.post("/mcp/sse")
async def handle_mcp_jsonrpc(request: Request):
    try:
        body = await request.body()
        body_str = body.decode("utf-8", errors="replace")
        logger.info("MCP direct: received %s bytes", len(body))
        logger.info("MCP direct: body preview=%s", body_str[:160].replace("\n", "\\n"))
        data = _safe_json_loads(body_str)
    except Exception as e:
        logger.error("MCP direct: parse error: %s", e)
        return _jsonrpc_error(None, -32700, f"Parse error: {e}", status_code=400)

    if isinstance(data, list):
        logger.info("MCP direct: batch request, %s messages", len(data))
        responses = []
        for item in data:
            resp = await _handle_single_jsonrpc(item)
            if resp is not None:
                responses.append(resp)

        if not responses:
            return Response(status_code=204)

        return JSONResponse(content=responses)

    result = await _handle_single_jsonrpc(data)
    if result is None:
        return Response(status_code=204)
    return JSONResponse(content=result)


async def _handle_single_jsonrpc(data: dict):
    method = data.get("method", "")
    msg_id = data.get("id")
    params = data.get("params", {})

    logger.info("MCP direct: method=%s id=%s", method, msg_id)

    if msg_id is None or method.startswith("notifications/"):
        logger.info("MCP direct: notification '%s' ack", method)
        return None

    if method == "initialize":
        logger.info("MCP direct: -> initialize response")
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {
                    "tools": {"listChanged": False},
                },
                "serverInfo": {
                    "name": "Power Interpreter",
                    "version": "3.0.3",
                },
            },
        }

    if method == "ping":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {}}

    if method == "tools/list":
        tools = _get_tools_list()
        logger.info("MCP direct: -> %s tools", len(tools))
        for t in tools:
            logger.info("  tool: %s", t["name"])

        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {"tools": tools},
        }

    if method == "tools/call":
        tool_name = params.get("name", "")
        tool_args = params.get("arguments", {})

        if tool_name == "execute_code":
            args_preview = json.dumps(
                {
                    "session_id": tool_args.get("session_id", "default"),
                    "timeout": tool_args.get("timeout"),
                    "code_len": len(tool_args.get("code", "")),
                },
                default=str,
            )
        else:
            try:
                args_preview = json.dumps(tool_args, default=str)[:160].replace("\n", "\\n")
            except Exception:
                args_preview = str(tool_args)[:160].replace("\n", "\\n")

        logger.info("MCP direct: -> tools/call '%s' args=%s", tool_name, args_preview)

        registry = _get_tool_registry()
        if tool_name not in registry:
            logger.error(
                "MCP direct: tool '%s' not found. Available: %s",
                tool_name,
                list(registry.keys()),
            )
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": -32601, "message": f"Tool not found: {tool_name}"},
            }

        try:
            tool = registry[tool_name]
            fn = tool.fn if hasattr(tool, "fn") else tool

            validation_error = _validate_tool_args(fn, tool_args, tool_name)
            if validation_error:
                logger.warning(
                    "MCP direct: %s argument validation failed: %s | argument_keys=%s | arguments_empty=%s",
                    tool_name,
                    validation_error,
                    list(tool_args.keys()),
                    len(tool_args) == 0,
                )

                recovery_msg = get_empty_args_recovery_message(tool_name, tool_args)
                error_text = recovery_msg if recovery_msg else f"Error: {validation_error}"

                return {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "content": [{"type": "text", "text": error_text}],
                        "isError": True,
                    },
                }

            if tool_name == "execute_code" and "code" in tool_args:
                syntax_issue = check_syntax(tool_args["code"])
                if syntax_issue:
                    logger.warning(
                        "MCP direct: %s syntax guard caught truncated code (session=%s, code_len=%s)",
                        tool_name,
                        tool_args.get("session_id", "unknown"),
                        len(tool_args["code"]),
                    )
                    return {
                        "jsonrpc": "2.0",
                        "id": msg_id,
                        "result": {
                            "content": [{"type": "text", "text": syntax_issue}],
                            "isError": True,
                        },
                    }

            logger.info("MCP direct: invoking %s...", tool_name)
            result = await fn(**tool_args)
            result = enforce_response_budget(tool_name, result)

            result_str = str(result)
            original_len = len(result_str)

            logger.info("MCP direct: %s returned %s chars", tool_name, f"{original_len:,}")
            logger.info("MCP direct: result preview: %s", result_str[:240].replace("\n", "\\n"))

            effective_cap = get_effective_cap(tool_name, MCP_RESPONSE_MAX_CHARS)

            if original_len > effective_cap:
                from app.response_guard import smart_truncate

                truncated_result = smart_truncate(result_str[:effective_cap])
                content = [{"type": "text", "text": truncated_result}]
                logger.warning(
                    "MCP direct: %s response TRUNCATED %s -> %s chars (cap: %s)",
                    tool_name,
                    f"{original_len:,}",
                    f"{len(truncated_result):,}",
                    f"{effective_cap:,}",
                )
            elif isinstance(result, str):
                warned_result = maybe_add_pressure_warning(tool_name, result)
                content = [{"type": "text", "text": warned_result}]
            elif isinstance(result, dict):
                content = [{"type": "text", "text": json.dumps(result, default=str)}]
            elif isinstance(result, list):
                content = result
            else:
                content = [{"type": "text", "text": result_str}]

            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {"content": content, "isError": False},
            }

        except Exception as e:
            logger.error("MCP direct: %s error: %s", tool_name, e, exc_info=True)
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "content": [{"type": "text", "text": f"Error executing {tool_name}: {e}"}],
                    "isError": True,
                },
            }

    logger.warning("MCP direct: unknown method '%s'", method)
    return {
        "jsonrpc": "2.0",
        "id": msg_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    }


app.include_router(health.router, tags=["Health"])

app.include_router(
    download_router,
    prefix="/dl",
    tags=["Downloads"],
)

app.include_router(
    execute.router,
    prefix="/api",
    tags=["Execute"],
    dependencies=[Depends(verify_api_key)],
)
app.include_router(
    jobs.router,
    prefix="/api",
    tags=["Jobs"],
    dependencies=[Depends(verify_api_key)],
)
app.include_router(
    files.router,
    prefix="/api",
    tags=["Files"],
    dependencies=[Depends(verify_api_key)],
)
app.include_router(
    data.router,
    prefix="/api",
    tags=["Data"],
    dependencies=[Depends(verify_api_key)],
)
app.include_router(
    sessions.router,
    prefix="/api",
    tags=["Sessions"],
    dependencies=[Depends(verify_api_key)],
)

app.mount("/mcp", mcp.sse_app())
