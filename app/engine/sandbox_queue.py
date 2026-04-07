"""Power Interpreter - Sandbox Queue

Provides async backpressure for sandbox execution.
Instead of failing immediately when execution capacity is saturated,
requests wait briefly for a slot and then return a structured 503 if busy.
"""

import asyncio
import logging
from typing import Any, Awaitable, Callable

from fastapi import HTTPException, status

from app.config import settings


logger = logging.getLogger(__name__)


class SandboxQueue:
    """Simple async backpressure queue using a semaphore."""

    def __init__(
        self,
        max_concurrent: int,
        acquire_timeout: float = 30.0,
    ) -> None:
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._max_concurrent = max_concurrent
        self._acquire_timeout = acquire_timeout

    async def run(
        self,
        func: Callable[..., Awaitable[Any]],
        *args,
        **kwargs,
    ) -> Any:
        try:
            await asyncio.wait_for(
                self._semaphore.acquire(),
                timeout=self._acquire_timeout,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "Sandbox queue busy: all %s slots occupied after waiting %ss",
                self._max_concurrent,
                self._acquire_timeout,
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "error": "sandbox_busy",
                    "message": (
                        f"All {self._max_concurrent} sandbox slots are occupied. "
                        f"Request waited {self._acquire_timeout:.0f}s. Please retry."
                    ),
                    "retry_after": 5,
                },
            )

        try:
            return await func(*args, **kwargs)
        finally:
            self._semaphore.release()

    @property
    def max_concurrent(self) -> int:
        return self._max_concurrent


sandbox_queue = SandboxQueue(
    max_concurrent=settings.MAX_CONCURRENT_JOBS,
    acquire_timeout=30.0,
)
