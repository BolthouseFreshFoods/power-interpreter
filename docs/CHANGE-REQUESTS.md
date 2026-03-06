# Power Interpreter MCP — Change Requests

> **Source:** Production log analysis, 2026-03-06 (UTC 17:56 – 18:56)  
> **Analyzed by:** Model Context Architect (MCA)  
> **Status:** Staged — awaiting team smoke test completion before implementation  
> **Branch:** `main`

---

## Summary

| # | Change | Type | Impact | Effort | Priority |
|---|--------|------|--------|--------|----------|
| 1 | [stderr → stdout logging fix](#1-fix-redirect-python-logging-from-stderr-to-stdout) | Bug Fix | All users | Low | High |
| 2 | [Single-call file download](#2-perf-collapse-resolve_share_link-to-single-graph-api-call) | Performance | ~400ms/download | Low | High |
| 3 | [Trim response payload](#3-perf-trim-resolve_share_link-response-payload) | Performance | Faster LLM turnaround | Low | High |
| 4 | [httpx connection pooling](#4-perf-session-scoped-httpxasyncclient-with-connection-pooling) | Performance | ~50-100ms/call | Medium | Medium |
| 5 | [Cache tools/list manifest](#5-perf-pre-serialize-and-cache-toolslist-manifest) | Performance | Faster handshake | Low | Low |
| 6 | [Consolidate SSE response](#6-perf-consolidate-execute_code-response-into-single-sse-message) | Performance | 1 fewer RT/call | Medium | Medium |
| 7 | [Batch file processing](#7-feature-batch-file-processing-for-bulk-analysis-workflows) | Feature | **30-40x speedup** | Medium | Critical |
| 8 | [Structured request logging](#8-observability-structured-request-logging-with-usersession-attribution) | Observability | Multi-user debugging | Low-Medium | High |
| 9 | [Sandbox resilience & request queuing](#9-stability-sandbox-resilience-request-queuing-and-retry) | Stability | **Eliminates 500s** | Medium | **Critical** |

### Suggested Shipping Strategy

- **Release 1 (Quick wins):** Items 1, 2, 3, 5, 8 — low effort, no architectural changes, immediate value
- **Release 2 (Stability):** Item 9 — critical for multi-user reliability, directly unblocks Marie
- **Release 3 (Infrastructure):** Items 4, 6 — transport and HTTP client layer changes, test carefully
- **Release 4 (Feature):** Item 7 — highest-impact single change, needs design decisions

---

## 1. [FIX] Redirect Python logging from stderr to stdout

**Type:** Bug Fix  
**Effort:** Low  
**Priority:** High — affects all users' observability

### Problem

Railway's log collector assigns `severity: "error"` to all output written to `stderr`. Python's `logging` module and `rich.Console` write to `stderr` by default. This causes every normal `INFO`-level log to appear as a red error in the Railway dashboard — making it impossible to distinguish real errors from routine operational messages.

**Observed:** 100% of application `INFO` logs tagged `severity: "error"` in Railway. Only uvicorn access logs (which use `stdout`) are correctly tagged `severity: "info"`.

### Solution

**Python `logging` module:**
```python
import sys
import logging

logging.basicConfig(
    stream=sys.stdout,  # Redirect from stderr (default) to stdout
    level=logging.INFO,
)
```

**Rich Console (if applicable):**
```python
from rich.console import Console

console = Console(stderr=False)  # Forces output to stdout
```

### Impact
- Clean Railway dashboard for all teams using Power Interpreter
- Ability to filter genuine errors from operational noise
- Zero functional change to application behavior

---

## 2. [PERF] Collapse resolve_share_link to single Graph API call

**Type:** Performance  
**Effort:** Low  
**Priority:** High

### Problem

The `resolve_share_link` tool currently makes **3 sequential HTTP round-trips** to Microsoft Graph to download a single file:

| Step | Call | Latency |
|------|------|---------|
| 1 | `GET /v1.0/shares/{encoded}/driveItem` (metadata) | ~416ms |
| 2 | `GET /v1.0/shares/{encoded}/driveItem/content` (redirect) | ~355ms |
| 3 | `GET /sharepoint.com/.../download.aspx` (actual bytes) | ~270ms |
| | **Total** | **~1,041ms** |

Step 1 (metadata) is unnecessary when the goal is to download the file content. The metadata fetch adds ~400ms of latency per file download.

### Solution

Collapse to a single call that retrieves content directly:

```python
# Before (3 calls):
response = await client.get(f"/v1.0/shares/{share_id}/driveItem")       # metadata
response = await client.get(f"/v1.0/shares/{share_id}/driveItem/content") # redirect
response = await client.get(redirect_url)                                 # download

# After (1 call — Graph follows redirect automatically):
response = await client.get(
    f"/v1.0/shares/{share_id}/driveItem/content",
    follow_redirects=True
)
```

If metadata is still needed for the response payload (see Change #3), fetch it in parallel:

```python
metadata, content = await asyncio.gather(
    client.get(f"/v1.0/shares/{share_id}/driveItem"),
    client.get(f"/v1.0/shares/{share_id}/driveItem/content", follow_redirects=True)
)
```

### Impact
- **~400ms saved per file download** (serial elimination)
- Directly benefits Marie's 200+ file workflows and any user resolving sharing links

---

## 3. [PERF] Trim resolve_share_link response payload

**Type:** Performance  
**Effort:** Low  
**Priority:** High

### Problem

After `resolve_share_link` downloads a file, the tool response returns the **full driveItem metadata blob** back to the LLM. This includes properties the LLM never uses (eTag, cTag, parentReference, file hashes, createdBy/lastModifiedBy user objects, etc.).

**Observed:** There is a consistent **~2 minute dead zone** between a successful file download and the first `execute_code` call. The LLM spends significant time parsing a large token payload before it can formulate its first pandas command.

### Solution

Trim the response to only what the LLM needs to proceed:

```python
# Before (full metadata blob — hundreds of tokens):
return {
    "status": "success",
    "driveItem": { ...full Graph API response... },
    "download_path": "/tmp/...",
    "message": "File downloaded successfully"
}

# After (minimal — ~30 tokens):
return {
    "status": "downloaded",
    "local_path": "/tmp/filename.xlsx",
    "filename": "filename.xlsx",
    "size_mb": 2.1
}
```

### Impact
- Reduces LLM token parsing overhead between download and first code execution
- Compresses the ~2 min dead zone observed in production sessions
- Every user benefits on every file download

---

## 4. [PERF] Session-scoped httpx.AsyncClient with connection pooling

**Type:** Performance  
**Effort:** Medium  
**Priority:** Medium

### Problem

Log analysis indicates each Microsoft Graph API call may instantiate a per-request HTTP client (`_client.py:1740` logged per request). This means every call to `graph.microsoft.com` incurs full TCP + TLS handshake overhead (~50-100ms per handshake).

In Marie's session alone, there were 27+ code executions plus multiple Graph API calls for file access — each potentially paying the TLS negotiation cost.

### Solution

Ensure a single `httpx.AsyncClient` instance is created per user session (or globally) and reused with HTTP keep-alive:

```python
# Before (per-request — suspected):
async def call_graph(endpoint):
    async with httpx.AsyncClient() as client:
        return await client.get(endpoint)

# After (session-scoped with pooling):
class GraphClient:
    def __init__(self):
        self._client = httpx.AsyncClient(
            base_url="https://graph.microsoft.com/v1.0",
            limits=httpx.Limits(
                max_connections=10,
                max_keepalive_connections=5,
                keepalive_expiry=300  # 5 min
            ),
            timeout=httpx.Timeout(30.0)
        )

    async def get(self, endpoint, **kwargs):
        return await self._client.get(endpoint, **kwargs)

    async def close(self):
        await self._client.aclose()
```

### Verification
After implementation, Graph API calls should reuse existing TCP connections. Look for absence of repeated TLS handshakes in verbose logging and reduced per-call latency on 2nd+ Graph call in a session.

### Impact
- Reduced TCP/TLS overhead on every Graph API call
- Compounding benefit during bulk file operations (200+ files)
- Estimated ~50-100ms saved per Graph call after the first

---

## 5. [PERF] Pre-serialize and cache tools/list manifest

**Type:** Performance  
**Effort:** Low  
**Priority:** Low

### Problem

The MCP server registers **34 tools** and serves the full manifest on every `tools/list` request. Currently, the tool list appears to be rebuilt (serialized to JSON) on each new client connection during the MCP handshake.

With growing team adoption (multiple concurrent users observed: Marie, Sherrill, and others), this manifest is served multiple times per minute.

### Solution

Pre-serialize the `tools/list` response once at server startup and cache it in memory:

```python
import json

class MCPServer:
    def __init__(self):
        # Build and cache at startup — never rebuild
        self._tools_manifest = self._build_tools_manifest()
        self._tools_manifest_json = json.dumps(self._tools_manifest)

    def _build_tools_manifest(self) -> dict:
        # ... existing tool registration logic ...
        return {"tools": [...]}

    async def handle_tools_list(self, request_id: int) -> str:
        # Return pre-serialized response — zero computation
        return f'{{"jsonrpc":"2.0","id":{request_id},"result":{self._tools_manifest_json}}}'
```

### Impact
- Faster MCP handshake for every new connection
- Eliminates redundant serialization of 34 tool definitions
- Marginal per-connection (~5-10ms), but compounds across concurrent users

---

## 6. [PERF] Consolidate execute_code response into single SSE message

**Type:** Performance  
**Effort:** Medium  
**Priority:** Medium

### Problem

Every `execute_code` call currently generates **5 HTTP round-trips** through the SSE transport layer:

```
POST /mcp/sse → 200  (request in)
POST /mcp/sse → 204  (acknowledgment)
POST /api/execute → 200  (kernel execution)
POST /mcp/sse → 200  (result part 1)
POST /mcp/sse → 200  (result part 2)   ← extra round-trip
```

The kernel result is being sent back in **two chunked SSE messages** instead of one. This adds an unnecessary HTTP round-trip per code execution.

**Observed in production:** Marie's session had 27+ `execute_code` calls = 27+ unnecessary HTTP round-trips just for result chunking.

### Solution

Consolidate the `execute_code` result into a single SSE response message:

```python
# Before (chunked — two SSE events):
async def send_result(result, session):
    await session.send_sse(result[:MAX_CHUNK])   # Part 1
    await session.send_sse(result[MAX_CHUNK:])   # Part 2

# After (single consolidated payload):
async def send_result(result, session):
    await session.send_sse(result)  # Complete result in one message
```

If the chunking exists to handle large outputs, implement a truncation/summary strategy instead:

```python
async def send_result(result, session):
    if len(result) > MAX_RESPONSE_SIZE:
        result = truncate_with_summary(result)
    await session.send_sse(result)  # Always single message
```

### Impact
- Eliminates 1 HTTP round-trip per `execute_code` call
- At Marie's usage level (27 calls/session): 27 fewer round-trips per session
- Reduces cumulative SSE transport overhead for all users

---

## 7. [FEATURE] Batch file processing for bulk analysis workflows

**Type:** Feature  
**Effort:** Medium  
**Priority:** Critical — active production use case

### Problem

Marie is reviewing **200+ files** using Power Interpreter. The current workflow requires one LLM round-trip per file:

```
LLM → execute_code(process file 1) → result → LLM thinks → execute_code(process file 2) → ... × 200
```

**Observed production metrics:**

| Metric | Value |
|--------|-------|
| Average gap between `execute_code` calls | ~22 seconds |
| Server-side execution time | sub-second |
| LLM reasoning + SSE transport overhead | ~21 seconds per cycle |
| **Projected time for 200 files** | **2.5 – 3.5 hours** |

The MCP server is fast (sub-second execution). The bottleneck is the **LLM round-trip loop** — 21 seconds of overhead per file that is entirely outside the server's control.

### Solution

Enable batch processing patterns that let the kernel iterate over all files in a **single `execute_code` call**, returning a consolidated summary.

**Option A: Enhanced kernel with pre-loaded file list**
```python
# Single execute_code call processes all files:
import pandas as pd
import os

results = []
for f in os.listdir('/tmp/downloads/'):
    df = pd.read_excel(f'/tmp/downloads/{f}')
    results.append({
        'file': f,
        'rows': len(df),
        'columns': list(df.columns),
        'summary': df.describe().to_dict()
    })

summary_df = pd.DataFrame(results)
print(summary_df.to_string())
```

**Option B: New `batch_file_analysis` tool**

A purpose-built MCP tool that accepts a folder path and analysis template, iterates internally, and returns a consolidated result — all within a single tool call.

**Option C: `onedrive_download_folder` tool**

A new tool that downloads all files from a OneDrive folder into the sandbox in one operation, enabling the kernel to iterate locally without repeated Graph API calls.

### Performance Projection

| Approach | LLM Round-trips | Estimated Time |
|----------|----------------|----------------|
| Current (1 file per call) | ~400-600 | **2.5 – 3.5 hours** |
| Batch (all files in 1 call) | ~5-10 | **2 – 5 minutes** |

**~30-40x speedup** for bulk file workflows.

### Design Decisions Needed
1. New tool vs. enhanced kernel behavior?
2. How to handle partial failures (file 147 of 200 is corrupt)?
3. Output format: full results vs. summary with drill-down?
4. Memory limits: can the sandbox hold 200 Excel files simultaneously?

### Impact
- **Highest-impact single change** for bulk file workflows
- Directly unblocks Marie's 200+ file review process
- Benefits any team member doing batch analysis

---

## 8. [OBSERVABILITY] Structured request logging with user/session attribution

**Type:** Observability  
**Effort:** Low-Medium  
**Priority:** High — required for multi-user debugging

### Problem

Railway HTTP-level logs provide **no user context**. The only information logged per request is:

```
INFO:     100.64.0.13:17452 - "POST /mcp/sse HTTP/1.1" 200 OK
```

This tells us: source IP (Railway's internal load balancer, not the actual user), HTTP method, path, and status code. It does **not** tell us:

- **Which authenticated user's session** generated the request
- **Which tool** was called (`resolve_share_link` vs `execute_code`)
- **What parameters** were passed
- A **session or correlation ID** to group related requests

### Real-World Failure Case

On 2026-03-06 at `18:34:23 UTC`, a `resolve_share_link` call returned `403 Forbidden` on a SharePoint path containing `/p/sherrill_reed/`. Without user attribution in the logs, it was **impossible to determine** whether:

- **Marie's session** tried to access Sherrill's file (a permissions issue)
- **Sherrill's own session** failed on an expired or malformed link
- A **third user's session** triggered the call entirely
- The **LLM hallucinated** a sharing link URL that doesn't exist

The root cause attribution was based on **timing proximity between requests** — which is guesswork, not engineering.

### Solution

Add structured, tool-level logging that includes user and session context on every tool invocation:

```python
import hashlib
import logging
import json

logger = logging.getLogger("power_interpreter")

def log_tool_call(
    session_id: str,
    user_email: str | None,
    tool_name: str,
    status: str,
    duration_ms: float,
    error_code: int | None = None,
    extra: dict | None = None
):
    """Structured log entry for every tool invocation."""
    entry = {
        "event": "tool_call",
        "session_id": session_id[:8],  # Short ID for readability
        "user": user_email or "anonymous",
        "tool": tool_name,
        "status": status,  # "success", "error", "timeout"
        "duration_ms": round(duration_ms, 1),
    }
    if error_code:
        entry["error_code"] = error_code
    if extra:
        entry.update(extra)

    logger.info(json.dumps(entry))
```

### Example Output (What Railway Would Show)

**Success case:**
```json
{"event": "tool_call", "session_id": "a8f3c1d2", "user": "marie.ludy@bolthousefresh.com", "tool": "execute_code", "status": "success", "duration_ms": 342.1}
```

**Error case (the 403 that caused confusion):**
```json
{"event": "tool_call", "session_id": "b7e2f4a1", "user": "sherrill_reed@bolthousefresh.com", "tool": "resolve_share_link", "status": "error", "error_code": 403, "duration_ms": 416.0, "share_url_hash": "a3f91a26c4b2"}
```

With this, the 403 investigation becomes: *"Sherrill's own session tried to resolve a link and got a 403 — expired link, not a cross-user access issue."* — **instant root cause, zero guessing.**

### Privacy Considerations

- **User email:** Already present in the authenticated session; logging it is consistent with the per-user auth model
- **Share URLs:** Hashed (SHA-256 prefix) to enable correlation without exposing actual file paths
- **Code content:** Never logged — only tool name, status, and duration
- **Credentials:** Never logged — masked in existing startup logs (`***configured***`)

### Implementation Scope

Files to modify:
- `mcp_server.py` — Add `log_tool_call()` wrapper around tool dispatch
- `tools.py` — Pass session context to each tool handler
- `main.py` — Ensure session ID and user email are propagated from SSE connection

### Impact
- **Instant root cause attribution** for any tool failure in multi-user environment
- **Session-level request grouping** — see all actions by a single user in sequence
- **Performance monitoring** — per-tool duration tracking without external APM
- **Audit trail** — who accessed what, when (privacy-safe)
- Directly prevents the guesswork that occurred during the 403 investigation

---

## 9. [STABILITY] Sandbox resilience: request queuing and retry

**Type:** Stability  
**Effort:** Medium  
**Priority:** Critical — users blocked by 500 errors

### Problem

On 2026-03-06 at approximately 11:30 AM Pacific, Marie experienced **repeated 500 Internal Server Errors** when performing basic sandbox operations (listing files, checking sandbox status). The failures were **intermittent** — one call would succeed, the next would fail with "Internal Server Error", then the next would succeed again.

**User-visible behavior:**
```
✓ Listed all PDF files in the utility_analysis directory        → Success
✗ Listed files in utility_analysis sandbox_data directory       → Internal Server Error
✗ Checked if the code execution sandbox is active               → Internal Server Error
✓ Listed all PDF files in the utility_analysis directory        → Success
✗ Next call                                                     → Internal Server Error
```

The LLM assistant was forced to tell Marie: *"I'm going to be straight with you — the sandbox is experiencing heavy intermittent server errors right now."*

**This is a production-blocking issue.** Marie was unable to complete her work.

### Root Cause Analysis

The sandbox is configured with:
- **Max concurrent jobs:** 4
- **Job timeout:** 1,800 seconds (30 minutes)
- **Max memory:** 16,384 MB

With Marie, Sherrill, and potentially other users active simultaneously at 11:30 AM, the likely causes are:

1. **Concurrent job slot exhaustion** — All 4 job slots occupied. New requests get an immediate 500 instead of waiting for a slot to free up. No queuing, no backpressure, no retry.
2. **Kernel crash without recovery** — If the Python kernel process crashes (OOM, segfault, or unhandled exception), subsequent requests to that kernel fail until the process is manually or automatically restarted. There is no health check → auto-restart cycle.
3. **Database connection pool exhaustion** — Under concurrent load, PostgreSQL connections may max out, causing internal failures on token lookups or session management.
4. **Sandbox file system pressure** — Multiple users accumulating files in `/app/sandbox_data` with 72-hour TTL can fill available disk space.

### Solution

#### A. Request Queue with Backpressure

Instead of rejecting requests immediately when all job slots are full, implement an async queue that holds requests and processes them as slots become available:

```python
import asyncio
from dataclasses import dataclass
from typing import Any

@dataclass
class QueuedJob:
    """A job waiting for a sandbox slot."""
    session_id: str
    tool_name: str
    params: dict
    future: asyncio.Future
    enqueued_at: float

class SandboxJobQueue:
    """
    Async job queue with backpressure for sandbox execution.
    Instead of returning 500 when slots are full, jobs wait in a
    bounded queue with a configurable timeout.
    """

    def __init__(
        self,
        max_concurrent: int = 4,
        max_queue_size: int = 20,
        queue_timeout: float = 30.0,  # Max seconds to wait in queue
    ):
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._queue: asyncio.Queue[QueuedJob] = asyncio.Queue(maxsize=max_queue_size)
        self._max_concurrent = max_concurrent
        self._queue_timeout = queue_timeout
        self._active_jobs = 0
        self._total_queued = 0
        self._total_processed = 0
        self._total_timeout = 0

    async def submit(self, func, *args, **kwargs) -> Any:
        """
        Submit a job for execution. Waits for an available slot
        up to queue_timeout seconds before raising TimeoutError.
        """
        try:
            # Try to acquire a slot immediately
            acquired = self._semaphore._value > 0
            if not acquired:
                self._total_queued += 1
                logger.info(json.dumps({
                    "event": "job_queued",
                    "active_jobs": self._active_jobs,
                    "queue_depth": self._queue.qsize(),
                    "max_concurrent": self._max_concurrent,
                }))

            # Wait for a slot with timeout
            async with asyncio.timeout(self._queue_timeout):
                await self._semaphore.acquire()

            self._active_jobs += 1
            try:
                result = await func(*args, **kwargs)
                self._total_processed += 1
                return result
            finally:
                self._active_jobs -= 1
                self._semaphore.release()

        except TimeoutError:
            self._total_timeout += 1
            logger.warning(json.dumps({
                "event": "job_queue_timeout",
                "active_jobs": self._active_jobs,
                "queue_depth": self._queue.qsize(),
                "timeout_seconds": self._queue_timeout,
            }))
            raise HTTPException(
                status_code=503,
                detail={
                    "error": "sandbox_busy",
                    "message": f"All {self._max_concurrent} sandbox slots are occupied. "
                               f"Request waited {self._queue_timeout}s. Please retry.",
                    "retry_after": 5,
                    "active_jobs": self._active_jobs,
                }
            )

    @property
    def stats(self) -> dict:
        return {
            "active_jobs": self._active_jobs,
            "max_concurrent": self._max_concurrent,
            "total_processed": self._total_processed,
            "total_queued": self._total_queued,
            "total_timeouts": self._total_timeout,
        }
```

**Key behaviors:**
- Requests **wait** for a slot instead of failing immediately
- Bounded queue (20) prevents unbounded memory growth
- Configurable timeout (30s) — if a slot doesn't free up in 30 seconds, return `503 Service Unavailable` (not `500 Internal Server Error`) with a `retry_after` hint
- The LLM receives a structured, actionable error instead of an opaque 500

#### B. Kernel Health Check and Auto-Recovery

Add a heartbeat mechanism that detects a dead kernel and restarts it before the next request:

```python
import asyncio
import time

class KernelHealthMonitor:
    """
    Monitors the sandbox kernel process and auto-restarts
    if it becomes unresponsive.
    """

    def __init__(self, kernel_manager, check_interval: float = 10.0):
        self._kernel = kernel_manager
        self._check_interval = check_interval
        self._last_healthy = time.monotonic()
        self._restart_count = 0
        self._running = False

    async def start(self):
        """Start the background health check loop."""
        self._running = True
        asyncio.create_task(self._health_loop())

    async def _health_loop(self):
        while self._running:
            try:
                is_alive = await self._check_kernel_health()
                if is_alive:
                    self._last_healthy = time.monotonic()
                else:
                    await self._restart_kernel(reason="health_check_failed")
            except Exception as e:
                logger.error(json.dumps({
                    "event": "kernel_health_error",
                    "error": str(e),
                }))
                await self._restart_kernel(reason="health_check_exception")

            await asyncio.sleep(self._check_interval)

    async def _check_kernel_health(self) -> bool:
        """Send a lightweight probe to the kernel."""
        try:
            result = await asyncio.wait_for(
                self._kernel.execute("1+1"),  # Minimal probe
                timeout=5.0
            )
            return result is not None
        except (asyncio.TimeoutError, Exception):
            return False

    async def _restart_kernel(self, reason: str):
        """Kill and restart the kernel process."""
        self._restart_count += 1
        logger.warning(json.dumps({
            "event": "kernel_restart",
            "reason": reason,
            "restart_count": self._restart_count,
            "seconds_since_healthy": round(time.monotonic() - self._last_healthy, 1),
        }))
        await self._kernel.restart()
        self._last_healthy = time.monotonic()

    async def ensure_healthy(self):
        """
        Called before each tool execution.
        If kernel is dead, restart it immediately rather than
        letting the request fail with 500.
        """
        is_alive = await self._check_kernel_health()
        if not is_alive:
            await self._restart_kernel(reason="pre_request_check")
```

#### C. Graceful 503 Instead of 500

Change error responses from opaque `500 Internal Server Error` to structured `503 Service Unavailable` with retry guidance:

```python
# Before (opaque 500 — LLM can't reason about it):
HTTP 500 Internal Server Error
{"detail": "Internal server error"}

# After (structured 503 — LLM can wait and retry):
HTTP 503 Service Unavailable
{
    "error": "sandbox_busy",
    "message": "All 4 sandbox slots are occupied. Request waited 30s. Please retry.",
    "retry_after": 5,
    "active_jobs": 4,
    "queue_stats": {
        "total_processed": 142,
        "total_queued": 23,
        "total_timeouts": 1
    }
}
```

This lets the LLM tell the user: *"The sandbox is busy with other requests. I'll retry in a few seconds."* instead of *"Internal Server Error."*

#### D. Integration — Request Flow

```
User request arrives
        │
        ▼
┌─────────────────┐
│ SandboxJobQueue  │  Is a slot available?
│   .submit()      │
└────────┬────────┘
         │
    ┌────┴────┐
    │ YES     │ NO → Wait up to 30s → Timeout? → 503 + retry_after
    │         │
    ▼         │
┌─────────────┴───┐
│ KernelHealth     │  Is the kernel alive?
│ .ensure_healthy()│
└────────┬────────┘
         │
    ┌────┴────┐
    │ YES     │ NO → Auto-restart kernel → Proceed
    │         │
    ▼         ▼
┌─────────────────┐
│ Execute tool     │  Run the actual operation
│ (execute_code,   │
│  list_files, etc)│
└────────┬────────┘
         │
         ▼
    Return result
```

### Implementation Scope

Files to modify:
- `main.py` — Initialize `SandboxJobQueue` and `KernelHealthMonitor` at startup
- `mcp_server.py` — Wrap tool dispatch in `queue.submit()` and `health.ensure_healthy()`
- New file: `queue.py` — `SandboxJobQueue` class
- New file: `health.py` — `KernelHealthMonitor` class

### Configuration (Environment Variables)

| Variable | Default | Description |
|----------|---------|-------------|
| `SANDBOX_MAX_CONCURRENT` | 4 | Max simultaneous sandbox jobs |
| `SANDBOX_QUEUE_SIZE` | 20 | Max jobs waiting in queue |
| `SANDBOX_QUEUE_TIMEOUT` | 30 | Seconds to wait for a slot |
| `KERNEL_HEALTH_INTERVAL` | 10 | Seconds between health checks |
| `KERNEL_HEALTH_TIMEOUT` | 5 | Seconds before probe is considered failed |

### Testing Strategy

1. **Simulate full slots:** Start 4 long-running `execute_code` jobs, send a 5th — verify it queues and completes when a slot frees
2. **Simulate queue timeout:** Fill all slots and queue, send requests that exceed `queue_timeout` — verify 503 with retry guidance
3. **Simulate kernel death:** Kill the kernel process, send a request — verify auto-restart and successful execution
4. **Concurrent user simulation:** Run 3 simulated user sessions with interleaved tool calls — verify no 500s

### Impact
- **Eliminates the 500 Internal Server Errors** that blocked Marie
- Users see *"busy, retrying"* instead of *"error"*
- Kernel crashes are self-healing — no manual intervention needed
- Queue stats provide visibility into concurrency pressure
- LLM receives structured errors it can reason about and retry

### Risk Assessment
- **Low risk:** Queue is bounded (20 max), timeout is bounded (30s), no unbounded resource consumption
- **Backward compatible:** Requests that previously succeeded immediately still succeed immediately — the queue only activates when slots are full
- **Observable:** All queue events and kernel restarts are logged (pairs with Change #8)

---

## Appendix: Log Evidence

- **Session date:** 2026-03-06
- **Log window analyzed:** 11:30 AM Pacific (user report) + 17:56 – 23:14 UTC (full 3-hour production window)
- **Active users observed:** Marie (via `marie.ludy@bolthousefresh.com`), Sherrill Reed (via `sherrill_reed@bolthousefresh.com`), plus additional concurrent sessions
- **Total `execute_code` calls (Marie):** 27+ over ~15 minutes
- **Success rate (code execution):** 100% — zero application errors
- **500 errors (sandbox operations):** Multiple intermittent failures at ~11:30 AM Pacific on file listing and sandbox status checks (see Change #9)
- **403 incident:** `resolve_share_link` on SharePoint path `/p/sherrill_reed/` — **user attribution unknown** due to lack of structured logging (see Change #8)
- **Railway severity misclassification:** 100% of Python `INFO` logs tagged as `error` due to stderr routing (see Change #1)
- **Deployment swap:** `948bd420` → `7a0f7d17` → `e1d924b7` — zero downtime, clean handoffs
