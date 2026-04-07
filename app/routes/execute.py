"""Power Interpreter - Code Execution Routes

Sync execution for quick snippets (<30s).
For longer operations, use the Jobs API.

Version: 3.0.2 — Added sandbox queue backpressure (Change 9)
            + stdout truncation at source (Fix 2)
            + pre-execution syntax guard (Fix 5)
"""

from typing import Dict, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.context_guard import truncate_stdout
from app.database import ensure_session_exists
from app.engine.executor import executor
from app.engine.sandbox_queue import sandbox_queue
from app.syntax_guard import check_syntax


router = APIRouter()


class ExecuteRequest(BaseModel):
    """Request to execute Python code."""

    code: str = Field(..., description="Python code to execute")
    session_id: str = Field(default="default", description="Session ID for file isolation")
    timeout: Optional[int] = Field(
        default=30,
        description="Max execution time in seconds (max 60 for sync)",
    )
    context: Optional[Dict] = Field(
        default=None,
        description="Variables to inject into sandbox",
    )


class ExecuteResponse(BaseModel):
    """Response from code execution."""

    success: bool
    stdout: str
    stderr: str
    result: Optional[object] = None
    error_message: Optional[str] = None
    error_traceback: Optional[str] = None
    execution_time_ms: int
    memory_used_mb: float
    files_created: list
    variables: Dict[str, str]


@router.post("/execute", response_model=ExecuteResponse)
async def execute_code(request: ExecuteRequest):
    """Execute Python code synchronously for quick operations.

    Use this for:
    - Quick calculations
    - Small data transformations
    - File generation
    - Anything that completes in <60 seconds

    For longer operations, use POST /api/jobs/submit instead.
    """
    timeout = min(request.timeout or 30, 60)

    if not request.code.strip():
        raise HTTPException(status_code=400, detail="No code provided")

    # ── Fix 5: Pre-execution syntax guard ─────────────────────────
    syntax_issue = check_syntax(request.code)
    if syntax_issue:
        return {
            "success": False,
            "stdout": "",
            "stderr": syntax_issue,
            "result": None,
            "error_message": syntax_issue,
            "error_traceback": None,
            "execution_time_ms": 0,
            "memory_used_mb": 0.0,
            "files_created": [],
            "variables": {},
        }

    await ensure_session_exists(request.session_id)

    result = await sandbox_queue.run(
        executor.execute,
        code=request.code,
        session_id=request.session_id,
        timeout=timeout,
        context=request.context,
    )

    # ── Fix 2: Truncate stdout at the source ──────────────────────
    if hasattr(result, "stdout") and result.stdout:
        result.stdout = truncate_stdout(result.stdout)

    return result.to_dict()


@router.post("/execute/quick")
async def execute_quick(code: str):
    """Ultra-quick execution endpoint (10s max).

    Convenience endpoint for simple expressions and calculations.
    """
    if not code.strip():
        raise HTTPException(status_code=400, detail="No code provided")

    syntax_issue = check_syntax(code)
    if syntax_issue:
        return {
            "success": False,
            "output": syntax_issue,
            "result": None,
        }

    await ensure_session_exists("quick")

    result = await sandbox_queue.run(
        executor.execute,
        code=code,
        session_id="quick",
        timeout=10,
    )

    if hasattr(result, "stdout") and result.stdout:
        result.stdout = truncate_stdout(result.stdout)

    return {
        "success": result.success,
        "output": result.stdout.strip() if result.success else result.error_message,
        "result": result.result,
    }
