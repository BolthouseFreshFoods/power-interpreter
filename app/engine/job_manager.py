"""Power Interpreter - Async Job Manager

Manages long-running code execution jobs with:
- Submit/poll/result pattern (prevents timeouts)
- Concurrent job execution with limits
- Job status tracking in PostgreSQL
- Automatic cleanup of old jobs

Pattern:
  1. Client submits job -> gets job_id immediately
  2. Client polls job status -> gets progress
  3. Client gets result when complete -> gets full output

This ensures NO MCP call ever times out, even for 5-minute jobs.

v1.9.5: Fixed ValueError when non-UUID session_id strings are passed
         (e.g. 'default'). Added _safe_parse_session_id() helper.
"""

import asyncio
import logging
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import delete, select, update

from app.config import settings
from app.engine.executor import executor
from app.models import Job, JobStatus
from app.database import get_session_factory


logger = logging.getLogger(__name__)


def _safe_parse_session_id(session_id: Optional[str]) -> Optional[uuid.UUID]:
    """Safely parse a session_id string into a UUID.

    If the string is a valid UUID, parse it directly.
    If it's a friendly name like 'default', generate a deterministic UUID via uuid5.
    Returns None if session_id is falsy.

    v1.9.5: Fix for ValueError when non-UUID strings (e.g. 'default') are passed.
    """
    if not session_id:
        return None

    try:
        return uuid.UUID(session_id)
    except (ValueError, TypeError):
        return uuid.uuid5(uuid.NAMESPACE_DNS, str(session_id))


def _safe_parse_job_id(job_id: str) -> Optional[uuid.UUID]:
    """Safely parse a job_id into a UUID.

    Returns None if the value is invalid.
    """
    try:
        return uuid.UUID(job_id)
    except (ValueError, TypeError, AttributeError):
        return None


def _utcnow() -> datetime:
    """Return current UTC time."""
    return datetime.utcnow()


class JobManager:
    """Manages async code execution jobs."""

    def __init__(self) -> None:
        self._running_jobs: Dict[str, asyncio.Task] = {}
        self._semaphore = asyncio.Semaphore(settings.MAX_CONCURRENT_JOBS)

    async def submit_job(
        self,
        code: str,
        session_id: str = None,
        timeout: int = None,
        context: Dict[str, Any] = None,
        metadata: Dict[str, Any] = None,
    ) -> str:
        """Submit a job for async execution.

        Returns job_id immediately. Use get_job_status() to check progress.
        """
        job_uuid = uuid.uuid4()
        job_id = str(job_uuid)
        timeout = timeout or settings.JOB_TIMEOUT

        factory = get_session_factory()
        async with factory() as session:
            job = Job(
                id=job_uuid,
                session_id=_safe_parse_session_id(session_id),
                code=code,
                status=JobStatus.PENDING,
                submitted_at=_utcnow(),
                metadata_=metadata or {},
            )
            session.add(job)
            await session.commit()

        task = asyncio.create_task(
            self._execute_job(
                job_id=job_id,
                code=code,
                session_id=session_id or "default",
                timeout=timeout,
                context=context,
            )
        )
        self._running_jobs[job_id] = task
        task.add_done_callback(lambda _: self._running_jobs.pop(job_id, None))

        logger.info("Job %s submitted (session: %s)", job_id, session_id)
        return job_id

    async def _execute_job(
        self,
        job_id: str,
        code: str,
        session_id: str,
        timeout: int,
        context: Dict[str, Any] = None,
    ) -> None:
        """Execute a job with semaphore limiting."""
        job_uuid = _safe_parse_job_id(job_id)
        if job_uuid is None:
            logger.error("Cannot execute job with invalid job_id: %s", job_id)
            return

        async with self._semaphore:
            factory = get_session_factory()

            async with factory() as session:
                await session.execute(
                    update(Job)
                    .where(Job.id == job_uuid)
                    .values(
                        status=JobStatus.RUNNING,
                        started_at=_utcnow(),
                    )
                )
                await session.commit()

            logger.info("Job %s started execution", job_id)

            try:
                exec_result = await executor.execute(
                    code=code,
                    session_id=session_id,
                    timeout=timeout,
                    context=context,
                )

                async with factory() as session:
                    final_status = (
                        JobStatus.COMPLETED
                        if exec_result.success
                        else JobStatus.FAILED
                    )

                    await session.execute(
                        update(Job)
                        .where(Job.id == job_uuid)
                        .values(
                            status=final_status,
                            completed_at=_utcnow(),
                            execution_time_ms=exec_result.execution_time_ms,
                            stdout=(exec_result.stdout or "")[:100000],
                            stderr=(exec_result.stderr or "")[:100000],
                            result=exec_result.to_dict(),
                            error_message=exec_result.error_message,
                            error_traceback=exec_result.error_traceback,
                            memory_used_mb=exec_result.memory_used_mb,
                            files_created=exec_result.files_created,
                        )
                    )
                    await session.commit()

                logger.info(
                    "Job %s completed: %s (%sms)",
                    job_id,
                    final_status.value,
                    exec_result.execution_time_ms,
                )

            except asyncio.CancelledError:
                logger.info("Job %s received cancellation", job_id)

                async with factory() as session:
                    await session.execute(
                        update(Job)
                        .where(Job.id == job_uuid)
                        .values(
                            status=JobStatus.CANCELLED,
                            completed_at=_utcnow(),
                        )
                    )
                    await session.commit()

                raise

            except Exception as exc:
                logger.exception("Job %s failed with unexpected error", job_id)

                async with factory() as session:
                    await session.execute(
                        update(Job)
                        .where(Job.id == job_uuid)
                        .values(
                            status=JobStatus.FAILED,
                            completed_at=_utcnow(),
                            error_message=str(exc),
                        )
                    )
                    await session.commit()

    async def get_job_status(self, job_id: str) -> Optional[Dict[str, Any]]:
        """Get current status of a job."""
        job_uuid = _safe_parse_job_id(job_id)
        if job_uuid is None:
            return None

        factory = get_session_factory()
        async with factory() as session:
            result = await session.execute(
                select(Job).where(Job.id == job_uuid)
            )
            job = result.scalar_one_or_none()

            if not job:
                return None

            elapsed = None
            if job.started_at:
                end = job.completed_at or _utcnow()
                elapsed = int((end - job.started_at).total_seconds() * 1000)

            return {
                "job_id": str(job.id),
                "status": job.status.value,
                "submitted_at": job.submitted_at.isoformat() if job.submitted_at else None,
                "started_at": job.started_at.isoformat() if job.started_at else None,
                "completed_at": job.completed_at.isoformat() if job.completed_at else None,
                "elapsed_ms": elapsed,
                "execution_time_ms": job.execution_time_ms,
                "has_result": job.result is not None,
                "has_error": job.error_message is not None,
            }

    async def get_job_result(self, job_id: str) -> Optional[Dict[str, Any]]:
        """Get full result of a completed job."""
        job_uuid = _safe_parse_job_id(job_id)
        if job_uuid is None:
            return None

        factory = get_session_factory()
        async with factory() as session:
            result = await session.execute(
                select(Job).where(Job.id == job_uuid)
            )
            job = result.scalar_one_or_none()

            if not job:
                return None

            return {
                "job_id": str(job.id),
                "status": job.status.value,
                "code": job.code,
                "stdout": job.stdout,
                "stderr": job.stderr,
                "result": job.result,
                "error_message": job.error_message,
                "error_traceback": job.error_traceback,
                "execution_time_ms": job.execution_time_ms,
                "memory_used_mb": job.memory_used_mb,
                "files_created": job.files_created,
                "submitted_at": job.submitted_at.isoformat() if job.submitted_at else None,
                "started_at": job.started_at.isoformat() if job.started_at else None,
                "completed_at": job.completed_at.isoformat() if job.completed_at else None,
            }

    async def cancel_job(self, job_id: str) -> bool:
        """Cancel a running job."""
        job_uuid = _safe_parse_job_id(job_id)
        if job_uuid is None:
            return False

        task = self._running_jobs.get(job_id)
        if task and not task.done():
            task.cancel()

            factory = get_session_factory()
            async with factory() as session:
                await session.execute(
                    update(Job)
                    .where(Job.id == job_uuid)
                    .values(
                        status=JobStatus.CANCELLED,
                        completed_at=_utcnow(),
                    )
                )
                await session.commit()

            logger.info("Job %s cancelled", job_id)
            return True

        return False

    async def list_jobs(
        self,
        session_id: str = None,
        status: str = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """List jobs with optional filters."""
        factory = get_session_factory()
        async with factory() as session:
            query = select(Job).order_by(Job.submitted_at.desc()).limit(limit)

            if session_id:
                query = query.where(Job.session_id == _safe_parse_session_id(session_id))

            if status:
                try:
                    query = query.where(Job.status == JobStatus(status))
                except ValueError:
                    return []

            result = await session.execute(query)
            jobs = result.scalars().all()

            return [
                {
                    "job_id": str(job.id),
                    "status": job.status.value,
                    "submitted_at": job.submitted_at.isoformat() if job.submitted_at else None,
                    "execution_time_ms": job.execution_time_ms,
                    "has_error": job.error_message is not None,
                }
                for job in jobs
            ]

    async def cleanup_old_jobs(self, hours: int = None) -> int:
        """Delete jobs older than specified hours."""
        hours = hours or settings.JOB_CLEANUP_HOURS
        cutoff = _utcnow() - timedelta(hours=hours)

        factory = get_session_factory()
        async with factory() as session:
            result = await session.execute(
                delete(Job).where(Job.submitted_at < cutoff)
            )
            await session.commit()

            count = result.rowcount or 0
            if count > 0:
                logger.info("Cleaned up %s old jobs (older than %sh)", count, hours)

            return count

    @property
    def active_job_count(self) -> int:
        """Number of currently running jobs."""
        return len([task for task in self._running_jobs.values() if not task.done()])


# Singleton
job_manager = JobManager()
