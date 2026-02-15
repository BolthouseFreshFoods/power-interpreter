"""Power Interpreter - Async Job Routes

Submit/poll/result pattern for long-running operations.
This ensures NO MCP call ever times out.

Pattern:
  1. POST /api/jobs/submit -> returns job_id immediately
  2. GET /api/jobs/{id}/status -> check progress
  3. GET /api/jobs/{id}/result -> get full output when complete
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Optional, Dict, List

from app.engine.job_manager import job_manager

router = APIRouter()


class JobSubmitRequest(BaseModel):
    """Request to submit a job"""
    code: str = Field(..., description="Python code to execute")
    session_id: Optional[str] = Field(default=None, description="Session ID")
    timeout: Optional[int] = Field(default=None, description="Max execution time in seconds")
    context: Optional[Dict] = Field(default=None, description="Variables to inject")
    metadata: Optional[Dict] = Field(default=None, description="Job metadata")


class JobSubmitResponse(BaseModel):
    """Response from job submission"""
    job_id: str
    status: str = "pending"
    message: str = "Job submitted successfully. Poll /api/jobs/{job_id}/status for progress."


@router.post("/jobs/submit", response_model=JobSubmitResponse)
async def submit_job(request: JobSubmitRequest):
    """Submit a long-running job for async execution
    
    Returns immediately with a job_id.
    Use GET /api/jobs/{job_id}/status to check progress.
    Use GET /api/jobs/{job_id}/result to get output when complete.
    
    Use this for:
    - Large data processing (1.5M+ rows)
    - Complex analysis that takes >60 seconds
    - File generation (Excel reports, charts)
    - Any operation that might timeout with sync execution
    """
    if not request.code.strip():
        raise HTTPException(status_code=400, detail="No code provided")
    
    job_id = await job_manager.submit_job(
        code=request.code,
        session_id=request.session_id,
        timeout=request.timeout,
        context=request.context,
        metadata=request.metadata
    )
    
    return JobSubmitResponse(job_id=job_id)


@router.get("/jobs/{job_id}/status")
async def get_job_status(job_id: str):
    """Check the status of a submitted job
    
    Returns:
    - pending: Job is queued
    - running: Job is executing
    - completed: Job finished successfully
    - failed: Job encountered an error
    - cancelled: Job was cancelled
    - timeout: Job exceeded time limit
    """
    status = await job_manager.get_job_status(job_id)
    if not status:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    return status


@router.get("/jobs/{job_id}/result")
async def get_job_result(job_id: str):
    """Get the full result of a completed job
    
    Includes stdout, stderr, result data, files created, etc.
    """
    result = await job_manager.get_job_result(job_id)
    if not result:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    
    if result['status'] in ('pending', 'running'):
        raise HTTPException(
            status_code=202, 
            detail=f"Job is still {result['status']}. Poll /api/jobs/{job_id}/status"
        )
    
    return result


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(job_id: str):
    """Cancel a running job"""
    cancelled = await job_manager.cancel_job(job_id)
    if not cancelled:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found or already completed")
    return {"job_id": job_id, "status": "cancelled"}


@router.get("/jobs")
async def list_jobs(
    session_id: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 50
):
    """List jobs with optional filters"""
    jobs = await job_manager.list_jobs(
        session_id=session_id,
        status=status,
        limit=limit
    )
    return {"jobs": jobs, "count": len(jobs)}
