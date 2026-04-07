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
from pydantic import BaseModel, Field, ConfigDict

from app.database import ensure_session_exists
from app.engine.job_manager import job_manager


router = APIRouter()

DEFAULT_SESSION_ID = "default"
DEFAULT_LIST_LIMIT = 50
MAX_LIST_LIMIT = 200
PENDING_STATES = {"pending", "running"}


class JobSubmitRequest(BaseModel):
    """Request to submit a job for async execution."""

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
        description="Variables to inject into execution context",
    )
    metadata: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Job metadata",
    )


class JobSubmitResponse(BaseModel):
    """Response returned after successful job submission."""

    job_id: str
    status: str = "pending"
    message: str = (
        "Job submitted successfully. Poll /api/jobs/{job_id}/status for progress."
    )


class JobCancelResponse(BaseModel):
    """Response returned after a successful cancellation request."""

    job_id: str
    status: str = "cancelled"


class JobListResponse(BaseModel):
    """Response returned when listing jobs."""

    jobs: list[Dict[str, Any]]
    count: int


def _normalize_session_id(session_id: Optional[str]) -> str:
    """Normalize a possibly-empty session ID."""
    if session_id is None:
        return DEFAULT_SESSION_ID

    normalized = session_id.strip()
    return normalized or DEFAULT_SESSION_ID


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
    """Safely extract status from a job payload."""
    value = payload.get("status")
    return str(value) if value is not None else None


@router.post("/jobs/submit", response_model=JobSubmitResponse, status_code=status.HTTP_202_ACCEPTED)
async def submit_job(request: JobSubmitRequest) -> JobSubmitResponse:
    """Submit a long-running job for async execution.

    Returns immediately with a job_id.

    Use:
    - GET /api/jobs/{job_id}/status to check progress
    - GET /api/jobs/{job_id}/result to get output when complete

    Recommended for:
    - Large data processing
    - Complex analysis that takes >60 seconds
    - File/report generation
    - Any operation that may timeout with sync execution
    """
    code = _normalize_code(request.code)
    session_id = _normalize_session_id(request.session_id)

    await ensure_session_exists(session_id)

    job_id = await job_manager.submit_job(
        code=code,
        session_id=session_id,
        timeout=request.timeout,
        context=request.context,
        metadata=request.metadata,
    )

    return JobSubmitResponse(job_id=job_id)


@router.get("/jobs/{job_id}/status")
async def get_job_status(job_id: str) -> Dict[str, Any]:
    """Check the status of a submitted job.

    Expected states include:
    - pending
    - running
    - completed
    - failed
    - cancelled
    - timeout
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
    """Get the full result of a submitted job.

    Includes stdout, stderr, result data, files created, and related output.
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

    return JobCancelResponse(job_id=job_id)


@router.get("/jobs", response_model=JobListResponse)
async def list_jobs(
    session_id: Optional[str] = Query(default=None, description="Filter by session ID"),
    status_filter: Optional[str] = Query(
        default=None,
        alias="status",
        description="Filter by job status",
    ),
    limit: int = Query(
        default=DEFAULT_LIST_LIMIT,
        ge=1,
        le=MAX_LIST_LIMIT,
        description="Maximum number of jobs to return",
    ),
) -> JobListResponse:
    """List jobs with optional filters."""
    normalized_session_id = None
    if session_id is not None:
        stripped = session_id.strip()
        normalized_session_id = stripped or DEFAULT_SESSION_ID

    jobs = await job_manager.list_jobs(
        session_id=normalized_session_id,
        status=status_filter,
        limit=limit,
    )

    return JobListResponse(jobs=jobs
