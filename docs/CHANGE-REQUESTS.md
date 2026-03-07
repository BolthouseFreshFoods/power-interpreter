# Power Interpreter MCP — Change Requests

> **Source:** Production log analysis, 2026-03-06 (UTC 17:56 – 23:59+)  
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
| 10 | [Response size guardrails](#10-stability-response-size-guardrails--pagination-and-token-budget) | Stability | **Prevents context overflow** | Medium | **Critical** |
| 11 | [Chunked file transfer & smart format conversion](#11-data-handling-chunked-file-transfer--smart-format-conversion) | Data Handling | **Production-scale files** | Medium-High | **Critical** |

### Suggested Shipping Strategy

- **Release 1 (Quick wins):** Items 1, 2, 3, 5, 8 — low effort, no architectural changes, immediate value
- **Release 2 (Stability):** Items 9, 10 — critical for multi-user reliability and context overflow prevention
- **Release 3 (Data Handling):** Item 11 — chunked transfers and format conversion for production-scale files
- **Release 4 (Infrastructure):** Items 4, 6 — transport and HTTP client layer changes, test carefully
- **Release 5 (Feature):** Item 7 — highest-impact single change, needs design decisions

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
        queue_timeout: float = 30.0,
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
        try:
            acquired = self._semaphore._value > 0
            if not acquired:
                self._total_queued += 1

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
            raise HTTPException(
                status_code=503,
                detail={
                    "error": "sandbox_busy",
                    "message": f"All {self._max_concurrent} sandbox slots are occupied. "
                               f"Request waited {self._queue_timeout}s. Please retry.",
                    "retry_after": 5,
                }
            )
```

#### B. Kernel Health Check and Auto-Recovery

```python
class KernelHealthMonitor:
    def __init__(self, kernel_manager, check_interval: float = 10.0):
        self._kernel = kernel_manager
        self._check_interval = check_interval
        self._restart_count = 0

    async def ensure_healthy(self):
        is_alive = await self._check_kernel_health()
        if not is_alive:
            await self._restart_kernel(reason="pre_request_check")

    async def _check_kernel_health(self) -> bool:
        try:
            result = await asyncio.wait_for(
                self._kernel.execute("1+1"),
                timeout=5.0
            )
            return result is not None
        except (asyncio.TimeoutError, Exception):
            return False

    async def _restart_kernel(self, reason: str):
        self._restart_count += 1
        await self._kernel.restart()
```

#### C. Graceful 503 Instead of 500

```python
# Before: HTTP 500 Internal Server Error — opaque, session-killing
# After:  HTTP 503 Service Unavailable — structured, retryable
{
    "error": "sandbox_busy",
    "message": "All 4 sandbox slots are occupied. Request waited 30s. Please retry.",
    "retry_after": 5
}
```

### Configuration (Environment Variables)

| Variable | Default | Description |
|----------|---------|-------------|
| `SANDBOX_MAX_CONCURRENT` | 4 | Max simultaneous sandbox jobs |
| `SANDBOX_QUEUE_SIZE` | 20 | Max jobs waiting in queue |
| `SANDBOX_QUEUE_TIMEOUT` | 30 | Seconds to wait for a slot |
| `KERNEL_HEALTH_INTERVAL` | 10 | Seconds between health checks |
| `KERNEL_HEALTH_TIMEOUT` | 5 | Seconds before probe is considered failed |

### Impact
- **Eliminates the 500 Internal Server Errors** that blocked Marie
- Users see *"busy, retrying"* instead of *"error"*
- Kernel crashes are self-healing — no manual intervention needed

---

## 10. [STABILITY] Response size guardrails — pagination and token budget

**Type:** Stability  
**Effort:** Medium  
**Priority:** Critical — sessions killed by context overflow

### Problem

On 2026-03-06 at `23:59 UTC`, Marie's session connected and called a file listing tool that returned **all 200+ Power Usage Report PDFs** with full OneDrive metadata (item IDs, filenames, sizes). The tool response consumed approximately **204,516 tokens** — exceeding Anthropic's 200,000 token context window maximum.

**The Anthropic API rejected the prompt:**
```
Anthropic API error: Error code: 400 - {'type': 'error', 'error': {'type':
'invalid_request_error', 'message': 'prompt is too long: 204516 tokens > 200000
maximum'}, 'request_id': 'req_011CYnjbUpfA93hxF9iVZuHf'}
```

**This is a hard wall.** Once the context window fills, the entire session is dead. Marie can't even send a message to ask for help — the LLM cannot process any input.

### Root Cause

Tool responses are returned to the LLM **unbounded**. There is no:
- Maximum token budget per tool response
- Pagination for large result sets
- Summarization fallback for oversized responses
- Filtering of internal identifiers (OneDrive item IDs) that the LLM doesn't need

Each file entry in the listing included:
```
("01NBYD6HFM6AVWMHL7QFBYLWW224EDWSBD", "Power Usage Report - 10-14-25 7AM.pdf", 60838)
```

Multiply by 200+ files with full OneDrive IDs = ~200K+ tokens for a single tool call.

### Solution

#### A. Global Tool Response Token Budget

Every tool response passes through a guardrail before being returned to the LLM:

```python
import math

# Conservative: no single tool response should exceed 25% of context window
MAX_TOOL_RESPONSE_TOKENS = 50_000
CHARS_PER_TOKEN = 4  # rough estimate for English text
MAX_TOOL_RESPONSE_CHARS = MAX_TOOL_RESPONSE_TOKENS * CHARS_PER_TOKEN  # 200,000 chars

def enforce_response_budget(tool_name: str, result: str | dict) -> dict:
    """Enforce token budget on tool responses before returning to LLM."""
    
    if isinstance(result, dict):
        serialized = json.dumps(result)
    else:
        serialized = str(result)
    
    estimated_tokens = math.ceil(len(serialized) / CHARS_PER_TOKEN)
    
    if estimated_tokens <= MAX_TOOL_RESPONSE_TOKENS:
        return result  # Within budget, return as-is
    
    # Over budget — truncate with metadata
    return {
        "status": "truncated",
        "warning": f"Response truncated: {estimated_tokens:,} tokens exceeded "
                   f"{MAX_TOOL_RESPONSE_TOKENS:,} token budget",
        "tool": tool_name,
        "original_size_tokens": estimated_tokens,
        "truncated_to_tokens": MAX_TOOL_RESPONSE_TOKENS,
        "data": serialized[:MAX_TOOL_RESPONSE_CHARS],
        "message": "Use pagination parameters to retrieve remaining results."
    }
```

#### B. Paginated File Listings

The `list_files` and `list_onedrive_files` tools must support pagination:

```python
async def list_files(
    path: str = "/",
    page: int = 1,
    page_size: int = 50,
    sort_by: str = "name",
) -> dict:
    """List files with mandatory pagination."""
    
    all_files = await _get_directory_contents(path)
    total_files = len(all_files)
    total_pages = math.ceil(total_files / page_size)
    
    # Slice for requested page
    start = (page - 1) * page_size
    end = start + page_size
    page_files = all_files[start:end]
    
    return {
        "status": "success",
        "path": path,
        "files": [
            {
                "name": f.name,
                "size_kb": round(f.size / 1024, 1),
                "modified": f.modified.isoformat(),
                # NO OneDrive item IDs — LLM doesn't need them
            }
            for f in page_files
        ],
        "pagination": {
            "page": page,
            "page_size": page_size,
            "total_files": total_files,
            "total_pages": total_pages,
            "has_next": page < total_pages,
        },
        "summary": f"Showing {len(page_files)} of {total_files} files (page {page}/{total_pages})"
    }
```

#### C. Smart Summarization for Large Directories

When a directory has more than `page_size` files, the first response should include a summary:

```python
# Instead of dumping 200+ file entries:
{
    "status": "success",
    "summary": {
        "total_files": 237,
        "total_size_mb": 48.3,
        "file_types": {"pdf": 235, "json": 2},
        "date_range": "2025-10-01 to 2026-03-06",
        "largest_file": {"name": "Power Usage Report - 01-15-26 7AM.pdf", "size_mb": 2.1},
        "smallest_file": {"name": "Power Usage Report - 10-14-25 7AM.pdf", "size_kb": 59.4}
    },
    "files": [...first 50 files...],
    "pagination": {"page": 1, "total_pages": 5, "has_next": true},
    "message": "237 files found. Showing first 50. Use page=2 for next batch."
}
```

#### D. Strip Internal IDs from LLM Responses

OneDrive item IDs (`01NBYD6HFM6AVWMHL7QFBYLWW224EDWSBD`) are internal identifiers used by the Graph API. The LLM never needs to see them — they should be stored server-side in a session-scoped lookup table and referenced by index or filename.

```python
# Before (wastes ~40 tokens per file on opaque IDs):
("01NBYD6HFM6AVWMHL7QFBYLWW224EDWSBD", "Power Usage Report - 10-14-25 7AM.pdf", 60838)

# After (LLM-friendly, ~10 tokens per file):
{"name": "Power Usage Report - 10-14-25 7AM.pdf", "size_kb": 59.4}
```

The OneDrive ID is still stored server-side so the MCP can resolve it when the user asks to open or download a specific file by name.

### Configuration (Environment Variables)

| Variable | Default | Description |
|----------|---------|-------------|
| `MAX_TOOL_RESPONSE_TOKENS` | 50000 | Max tokens per tool response |
| `FILE_LIST_PAGE_SIZE` | 50 | Default files per page |
| `FILE_LIST_MAX_PAGE_SIZE` | 200 | Maximum files per page |

### Implementation Scope

Files to modify:
- `mcp_server.py` — Add `enforce_response_budget()` middleware around all tool responses
- `tools.py` — Add pagination to `list_files`, `list_onedrive_files`, `search_onedrive`
- New file: `response_budget.py` — Token budgeting and truncation logic

### Testing Strategy

1. **Simulate large listing:** Create 250 files, call `list_files()` — verify paginated response ≤ 50K tokens
2. **Simulate overflow:** Force a 200K+ token response — verify truncation with metadata
3. **Verify LLM can paginate:** Ensure paginated responses include clear navigation instructions
4. **Verify ID stripping:** Confirm OneDrive item IDs are absent from LLM-facing responses

### Impact
- **Prevents context window overflow** — no single tool response can kill a session
- **Reduces token waste** — stripping internal IDs saves ~30 tokens per file × 200+ files
- **Enables large directory workflows** — 200+ file directories work with pagination instead of exploding
- **Directly prevents** the exact failure Marie experienced at 23:59 UTC

---

## 11. [DATA HANDLING] Chunked file transfer & smart format conversion

**Type:** Data Handling  
**Effort:** Medium-High  
**Priority:** Critical — no chunking or format conversion exists in current code

### Problem

Code search across the entire Power Interpreter repository reveals **zero implementation** of:

| Feature | Search Results | Status |
|---------|---------------|--------|
| Chunked upload | 0 matches for `chunk`, `chunked`, `chunking` | **Not implemented** |
| Chunked download | Same — 0 matches | **Not implemented** |
| Excel → CSV conversion | 0 matches for `csv`, `xlsx`, `capacity` | **Not implemented** |
| File size guardrails | 0 matches for `MAX_FILE`, `max_size`, `truncat` | **Not implemented** |
| Response pagination | 0 matches for `pagina` | **Not implemented** |

The sandbox supports **16 GB of memory** and a **50 MB max file size**, but there is no intelligent handling of:

1. **Large file transfers** — A 45 MB Excel file is uploaded/downloaded as a single HTTP payload. No streaming, no chunking, no resume-on-failure.
2. **Excel row limits** — Microsoft Excel's `.xlsx` format has a hard limit of **1,048,576 rows**. If a dataset exceeds this, the file is silently truncated or the operation fails.
3. **Format optimization** — Large tabular datasets are stored as `.xlsx` (binary XML, larger) when `.csv` (plain text, smaller, no row limit) would be more appropriate.

### Real-World Implications

Bolthouse Fresh Foods processes production data daily. Scenarios where this matters:

- **Power usage reports**: 200+ daily PDFs, each containing tabular data that may be consolidated into large datasets
- **Production line data**: Sensor readings can exceed 1M+ rows over a reporting period
- **Financial reports**: Multi-sheet workbooks with cross-references
- **Bulk analysis**: Marie's 200+ file workflow requires downloading and processing all files

### Solution

#### A. Chunked File Transfer

Implement streaming upload and download with configurable chunk sizes:

```python
import aiofiles
import os
from typing import AsyncIterator

# Configurable chunk size (default 5 MB)
CHUNK_SIZE = int(os.getenv("FILE_CHUNK_SIZE", 5 * 1024 * 1024))  # 5 MB

async def chunked_upload(
    file_path: str,
    destination: str,
    chunk_size: int = CHUNK_SIZE,
) -> dict:
    """
    Upload a file in chunks with progress tracking.
    Supports resume on failure.
    """
    file_size = os.path.getsize(file_path)
    total_chunks = math.ceil(file_size / chunk_size)
    uploaded_bytes = 0
    
    async with aiofiles.open(file_path, 'rb') as f:
        for chunk_num in range(total_chunks):
            chunk = await f.read(chunk_size)
            
            # Upload chunk with retry
            for attempt in range(3):
                try:
                    await _upload_chunk(
                        destination=destination,
                        chunk=chunk,
                        chunk_num=chunk_num,
                        total_chunks=total_chunks,
                        offset=uploaded_bytes,
                    )
                    uploaded_bytes += len(chunk)
                    break
                except Exception as e:
                    if attempt == 2:
                        return {
                            "status": "partial",
                            "uploaded_bytes": uploaded_bytes,
                            "total_bytes": file_size,
                            "failed_at_chunk": chunk_num,
                            "error": str(e),
                            "message": "Upload can be resumed from failed chunk."
                        }
                    await asyncio.sleep(1 * (attempt + 1))  # backoff
    
    return {
        "status": "success",
        "file": os.path.basename(file_path),
        "size_mb": round(file_size / (1024 * 1024), 2),
        "chunks": total_chunks,
    }


async def chunked_download(
    source: str,
    destination: str,
    chunk_size: int = CHUNK_SIZE,
) -> AsyncIterator[bytes]:
    """
    Download a file in chunks with streaming.
    Memory-efficient — never loads full file into RAM.
    """
    async with aiofiles.open(destination, 'wb') as f:
        async for chunk in _stream_from_source(source, chunk_size):
            await f.write(chunk)
            yield chunk  # Allow progress tracking
```

#### B. Smart Format Detection and Conversion

Automatically detect when Excel format is inappropriate and convert to CSV:

```python
import pandas as pd
import os

# Excel hard limits
EXCEL_MAX_ROWS = 1_048_576
EXCEL_MAX_COLS = 16_384
EXCEL_PRACTICAL_SIZE_MB = 25  # Beyond this, Excel becomes sluggish

async def smart_file_output(
    df: pd.DataFrame,
    requested_filename: str,
    session_id: str,
) -> dict:
    """
    Intelligently choose output format based on data characteristics.
    Converts to CSV when Excel limits would be exceeded.
    """
    row_count = len(df)
    col_count = len(df.columns)
    base_name, requested_ext = os.path.splitext(requested_filename)
    
    # Decision: Excel or CSV?
    needs_csv = False
    conversion_reason = None
    
    if row_count > EXCEL_MAX_ROWS:
        needs_csv = True
        conversion_reason = (
            f"Dataset has {row_count:,} rows, exceeding Excel's "
            f"{EXCEL_MAX_ROWS:,} row limit"
        )
    elif col_count > EXCEL_MAX_COLS:
        needs_csv = True
        conversion_reason = (
            f"Dataset has {col_count:,} columns, exceeding Excel's "
            f"{EXCEL_MAX_COLS:,} column limit"
        )
    
    if needs_csv and requested_ext in ('.xlsx', '.xls'):
        # Auto-convert to CSV
        actual_filename = f"{base_name}.csv"
        output_path = f"/app/sandbox_data/{session_id}/{actual_filename}"
        df.to_csv(output_path, index=False)
        file_size = os.path.getsize(output_path)
        
        return {
            "status": "converted",
            "original_format": requested_ext,
            "actual_format": ".csv",
            "filename": actual_filename,
            "path": output_path,
            "rows": row_count,
            "columns": col_count,
            "size_mb": round(file_size / (1024 * 1024), 2),
            "reason": conversion_reason,
            "message": f"Automatically converted to CSV: {conversion_reason}. "
                       f"CSV has no row limit and is {round(file_size / (1024*1024), 1)} MB."
        }
    
    # Standard Excel output
    output_path = f"/app/sandbox_data/{session_id}/{requested_filename}"
    df.to_excel(output_path, index=False, engine='openpyxl')
    file_size = os.path.getsize(output_path)
    
    # Warn if Excel file is very large (will be slow to open)
    warning = None
    estimated_size_mb = file_size / (1024 * 1024)
    if estimated_size_mb > EXCEL_PRACTICAL_SIZE_MB:
        warning = (
            f"File is {estimated_size_mb:.1f} MB. Excel may be slow to open. "
            f"Consider requesting CSV format for better performance."
        )
    
    return {
        "status": "success",
        "filename": requested_filename,
        "path": output_path,
        "rows": row_count,
        "columns": col_count,
        "size_mb": round(estimated_size_mb, 2),
        "warning": warning,
    }
```

#### C. File Size Guardrails

Enforce limits at the transfer layer, not just configuration:

```python
MAX_UPLOAD_SIZE = int(os.getenv("MAX_UPLOAD_SIZE", 50 * 1024 * 1024))  # 50 MB
MAX_SANDBOX_STORAGE = int(os.getenv("MAX_SANDBOX_STORAGE", 2 * 1024 * 1024 * 1024))  # 2 GB per session

async def validate_file_transfer(
    file_path: str,
    session_id: str,
    direction: str = "upload",
) -> dict | None:
    """Validate file size and sandbox capacity before transfer."""
    
    file_size = os.path.getsize(file_path)
    
    # Check individual file size
    if file_size > MAX_UPLOAD_SIZE:
        return {
            "status": "rejected",
            "error": "file_too_large",
            "file_size_mb": round(file_size / (1024 * 1024), 2),
            "max_size_mb": round(MAX_UPLOAD_SIZE / (1024 * 1024), 2),
            "message": f"File is {file_size / (1024*1024):.1f} MB, "
                       f"exceeding the {MAX_UPLOAD_SIZE / (1024*1024):.0f} MB limit."
        }
    
    # Check sandbox capacity
    session_usage = await _get_session_disk_usage(session_id)
    if session_usage + file_size > MAX_SANDBOX_STORAGE:
        return {
            "status": "rejected",
            "error": "sandbox_full",
            "current_usage_mb": round(session_usage / (1024 * 1024), 2),
            "file_size_mb": round(file_size / (1024 * 1024), 2),
            "max_storage_mb": round(MAX_SANDBOX_STORAGE / (1024 * 1024), 2),
            "message": f"Sandbox storage is {session_usage / (1024*1024):.0f} MB / "
                       f"{MAX_SANDBOX_STORAGE / (1024*1024):.0f} MB. "
                       f"Clean up old files to free space."
        }
    
    return None  # Validation passed
```

### Configuration (Environment Variables)

| Variable | Default | Description |
|----------|---------|-------------|
| `FILE_CHUNK_SIZE` | 5242880 (5 MB) | Chunk size for file transfers |
| `MAX_UPLOAD_SIZE` | 52428800 (50 MB) | Max individual file size |
| `MAX_SANDBOX_STORAGE` | 2147483648 (2 GB) | Max storage per session |
| `EXCEL_AUTO_CONVERT_THRESHOLD` | 1048576 | Row count that triggers CSV conversion |
| `EXCEL_SIZE_WARNING_MB` | 25 | File size that triggers Excel performance warning |

### Implementation Scope

Files to modify:
- `tools.py` — Add chunked upload/download to file transfer tools
- New file: `file_handler.py` — `chunked_upload()`, `chunked_download()`, `smart_file_output()`, `validate_file_transfer()`
- New file: `format_converter.py` — Excel ↔ CSV conversion logic with limit detection
- `mcp_server.py` — Wire file handler into tool dispatch

### Decision Matrix: When to Use Each Format

| Condition | Action | Output Format |
|-----------|--------|---------------|
| Rows ≤ 1,048,576 AND size ≤ 25 MB | Keep as requested | `.xlsx` |
| Rows > 1,048,576 | Auto-convert + notify | `.csv` |
| Size > 25 MB AND rows ≤ limit | Warn user | `.xlsx` with warning |
| Size > 50 MB | Reject OR chunk transfer | Error / chunked `.csv` |
| User explicitly requests CSV | Honor request | `.csv` |
| Multi-sheet workbook | Keep as Excel | `.xlsx` |

### Testing Strategy

1. **Chunked upload**: Upload a 45 MB file — verify chunked transfer with progress
2. **Chunked download**: Download from sandbox — verify streaming chunks
3. **Resume on failure**: Kill connection mid-transfer — verify resume from last chunk
4. **Excel overflow**: Create 1.1M row DataFrame, request `.xlsx` — verify auto-CSV conversion with notification
5. **Size warning**: Create 30 MB Excel file — verify performance warning
6. **Sandbox capacity**: Fill sandbox to near limit — verify rejection with clear message

### Impact
- **Chunked transfer** prevents timeouts and enables large file workflows
- **Smart format conversion** prevents silent data truncation when Excel limits are exceeded
- **File size guardrails** enforced in code, not just configuration
- **Resume capability** prevents wasted time on failed transfers
- **Production-scale data** workflows become reliable for the entire team

---

## Appendix: Log Evidence

- **Session date:** 2026-03-06
- **Log window analyzed:** 11:30 AM Pacific (user report) + 17:56 – 23:59+ UTC (full production window)
- **Active users observed:** Marie (via `marie.ludy@bolthousefresh.com`), Sherrill Reed (via `sherrill_reed@bolthousefresh.com`), plus additional concurrent sessions
- **Total `execute_code` calls (Marie):** 27+ over ~15 minutes
- **Success rate (code execution):** 100% — zero application errors
- **500 errors (sandbox operations):** Multiple intermittent failures at ~11:30 AM Pacific on file listing and sandbox status checks (see Change #9)
- **403 incident:** `resolve_share_link` on SharePoint path `/p/sherrill_reed/` — **user attribution unknown** due to lack of structured logging (see Change #8)
- **Context overflow incident:** 204,516 tokens > 200,000 maximum at 23:59 UTC — session killed by unbounded file listing response (see Change #10)
- **Code search results:** Zero matches for `chunk`, `csv`, `xlsx`, `pagina`, `MAX_FILE`, or `truncat` in application code — confirming no chunking, format conversion, or pagination exists (see Change #11)
- **Railway severity misclassification:** 100% of Python `INFO` logs tagged as `error` due to stderr routing (see Change #1)
- **Deployment chain:** `948bd420` → `7a0f7d17` → `e1d924b7` → `365d8514` — zero downtime, all clean handoffs
