"""Power Interpreter - Async Job Routes

Submit / poll / result pattern for long-running operations.
This ensures NO MCP call ever times out.

Pattern:
  1. POST /api/jobs/submit -> returns job_id immediately
  2. GET /api/jobs/{id}/status -> check progress
  3. GET /api/jobs/{id}/result -> get full output when complete
"""

from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field

from app.engine.job_manager import job_manager
from app.database import ensure_session_exists


router = APIRouter()

DEFAULT_SESSION_ID = "default"
DEFAULT_LIST_LIMIT = 50
MAX_LIST_LIMIT = 200
PENDING_STATES = {"pending", "running"}


class JobSubmitRequest(BaseModel):
    """Request to submit a job."""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(..., min_length=1, description="Python code to execute")
    session_id: Optional[str] = Field(default=None, description="Session ID")
    timeout: Optional[int] = Field(
        default=None,
        ge=1,
        le=3600,
        description="Max execution time in seconds",
    )
    context: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Variables to inject",
    )
    metadata: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Job metadata",
    )


class JobSubmitResponse(BaseModel):
    """Response from job submission."""

    job_id: str
    status: str = "pending"
    message: str = "Job submitted successfully. Poll /api/jobs/{job_id}/status for progress."


class JobCancelResponse(BaseModel):
    """Response from job cancellation."""

    job_id: str
    status: str = "cancelled"


class JobListResponse(BaseModel):
    """Response from job listing."""

    jobs: list[Dict[str, Any]]
    count: int


def _normalize_code(code: str) -> str:
    """Normalize submitted code and reject blank input."""
    normalized = code.strip()
    if not normalized:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No code provided",
        )
    return normalized


def _extract_status(payload: Dict[str, Any]) -> Optional[str]:
    """Safely extract job status from a payload."""
    value = payload.get("status")
    return str(value) if value is not None else None


@router.post("/jobs/submit", response_model=JobSubmitResponse, status_code=status.HTTP_202_ACCEPTED)
async def submit_job(request: JobSubmitRequest) -> JobSubmitResponse:
    """Submit a long-running job for async execution.

    Returns immediately with a job_id.
    Use GET /api/jobs/{job_id}/status to check progress.
    Use GET /api/jobs/{job_id}/result to get output when complete.

    Use this for:
    - Large data processing
    - Complex analysis that takes >60 seconds
    - File generation
    - Any operation that might timeout with sync execution
    """
    code = _normalize_code(request.code)

    await ensure_session_exists(request.session_id or DEFAULT_SESSION_ID)

    job_id = await job_manager.submit_job(
        code=code,
        session_id=request.session_id,
        timeout=request.timeout,
        context=request.context,
        metadata=request.metadata,
    )

    return JobSubmitResponse(job_id=job_id)


@router.get("/jobs/{job_id}/status")
async def get_job_status(job_id: str) -> Dict[str, Any]:
    """Check the status of a submitted job.

    Returns:
    - pending: Job is queued
    - running: Job is executing
    - completed: Job finished successfully
    - failed: Job encountered an error
    - cancelled: Job was cancelled
    - timeout: Job exceeded time limit
    """
    job_status = await job_manager.get_job_status(job_id)
    if not job_status:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job {job_id} not found",
        )
    return job_status


@router.get("/jobs/{job_id}/result")
async def get_job_result(job_id: str) -> Dict[str, Any]:
    """Get the full result of a completed job.

    Includes stdout, stderr, result data, files created, etc.
    """
    result = await job_manager.get_job_result(job_id)
    if not result:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job {job_id} not found",
        )

    result_status = _extract_status(result)
    if result_status in PENDING_STATES:
        raise HTTPException(
            status_code=status.HTTP_202_ACCEPTED,
            detail=f"Job is still {result_status}. Poll /api/jobs/{job_id}/status",
        )

    return result


@router.post("/jobs/{job_id}/cancel", response_model=JobCancelResponse)
async def cancel_job(job_id: str) -> JobCancelResponse:
    """Cancel a running job."""
    cancelled = await job_manager.cancel_job(job_id)
    if not cancelled:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job {job_id} not found or already completed",
        )
    return JobCancelResponse(job_id=job_id, status="cancelled")


@router.get("/jobs", response_model=JobListResponse)
async def list_jobs(
    session_id: Optional[str] = Query(default=None, description="Session ID filter"),
    status_filter: Optional[str] = Query(
        default=None,
        alias="status",
        description="Job status filter",
    ),
    limit: int = Query(
        default=DEFAULT_LIST_LIMIT,
        ge=1,
        le=MAX_LIST_LIMIT,
        description="Maximum number of jobs to return",
    ),
) -> JobListResponse:
    """List jobs with optional filters."""
    jobs = await job_manager.list_jobs(
        session_id=session_id,
        status=status_filter,
        limit=limit,
    )
    return JobListResponse(jobs=jobs, count=len(jobs))
