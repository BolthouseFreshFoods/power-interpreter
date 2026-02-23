"""Power Interpreter MCP - MCP Server Definition

Defines the MCP tools that SimTheory.ai can call.
This maps MCP tool calls to the FastAPI endpoints.

MCP Tools (12):
- execute_code: Run Python code (sync, <60s)
- submit_job: Submit long-running job (async)
- get_job_status: Check job progress
- get_job_result: Get completed job output
- upload_file: Upload a file (base64) to sandbox
- fetch_file: Download a file from URL to sandbox
- fetch_from_url: ★ Load file from CDN/URL directly into sandbox
- list_files: List sandbox files
- load_dataset: Load CSV into PostgreSQL
- query_dataset: SQL query against datasets
- list_datasets: List loaded datasets
- create_session: Create workspace session

Version: 1.7.2 - fix: fetch_from_url was calling /api/files/fetch-from-url (404).
                       Corrected to /api/files/fetch which is the actual registered
                       FastAPI route in files.py. One word difference, total blocker.

HISTORY:
  v1.2.0: Response was a JSON blob. URLs lived in stdout. AI parsed them. WORKED.
  v1.5.0: Introduced content blocks. Stripped URLs from stdout, tried to rebuild
           from inline_images[]/download_urls[] arrays. Those arrays were empty
           due to parallel execution race in executor.py. URLs LOST.
  v1.5.1: Switched from markdown to plain text URLs. Still stripped stdout. Still broken.
  v1.5.2: Stop stripping stdout. Pass URLs through as-is. Belt-and-suspenders:
           also create extra blocks from JSON arrays if populated.
  v1.6.0: Auto File Handling. Rewrote tool descriptions so the AI reliably
           chains fetch_file -> execute_code, upload_file -> execute_code,
           and fetch_file -> load_dataset -> query_dataset. No logic changes.
  v1.7.0: fetch_from_url tool added. Streams files directly from any HTTPS URL
           (Cloudinary CDN, S3, public URLs) into sandbox. Fixes Priority 1
           file upload blocker — no base64 overhead, no SimTheory encoding bug.
  v1.7.1: Fix TypeError — FastMCP() does not accept 'description' kwarg in the
           installed version. Removed it. App now starts cleanly.
  v1.7.2: Fix 404 — fetch_from_url was POSTing to /api/files/fetch-from-url
           which does not exist. Correct route is /api/files/fetch. Fixed.
"""

from mcp.server.fastmcp import FastMCP
from typing import Optional, Dict
import httpx
import os
import json
import logging

logger = logging.getLogger(__name__)

# MCP Server — name only, no description kwarg (not supported in installed fastmcp version)
mcp = FastMCP("Power Interpreter")

# Internal API base URL
_default_base = "http://127.0.0.1:8080"
API_BASE = os.getenv("API_BASE_URL", _default_base)
API_KEY = os.getenv("API_KEY", "")

logger.info(f"MCP Server: API_BASE={API_BASE}")
logger.info(f"MCP Server: API_KEY={'***configured***' if API_KEY else 'NOT SET'}")


def _headers():
    return {"X-API-Key": API_KEY, "Content-Type": "application/json"}


def _build_content_blocks(resp_text: str) -> list:
    """Build MCP content blocks from execute_code API response.

    Returns a LIST of content blocks, not a JSON string.
    main.py's tools/call handler will use these directly:
      isinstance(result, list) -> content = result

    CRITICAL DESIGN DECISION (v1.5.2):
    We do NOT strip URLs from stdout. The executor appends download URLs
    and chart URLs to stdout, and that's the RELIABLE path. We pass
    stdout through as-is.

    If the JSON response also has populated inline_images[] or
    download_urls[] arrays, we create ADDITIONAL blocks for those
    (belt and suspenders). But we never remove URLs from stdout.
    """
    try:
        data = json.loads(resp_text)
    except (json.JSONDecodeError, TypeError):
        return [{"type": "text", "text": resp_text}]

    blocks = []

    # Block 1: stdout — PASSED THROUGH UNMODIFIED
    stdout = data.get('stdout', '').strip()
    if stdout:
        blocks.append({"type": "text", "text": stdout})

    # Block 2: Error information
    if not data.get('success', False):
        error_msg = data.get('error_message', 'Unknown error')
        error_tb = data.get('error_traceback', '')
        error_text = f"Execution Error: {error_msg}"
        if error_tb:
            if len(error_tb) > 500:
                error_tb = "..." + error_tb[-500:]
            error_text += f"\n\nTraceback:\n{error_tb}"
        blocks.append({"type": "text", "text": error_text})

    # Block 3: Additional image blocks (belt-and-suspenders)
    inline_images = data.get('inline_images', [])
    for img in inline_images:
        alt = img.get('alt_text', 'Generated chart')
        url = img.get('url', '')
        if url:
            blocks.append({"type": "text", "text": f"Chart: {alt}\nImage URL: {url}"})

    # Block 4: Additional download blocks (belt-and-suspenders)
    download_urls = data.get('download_urls', [])
    image_filenames = {img.get('filename', '') for img in inline_images}
    non_image_downloads = [
        d for d in download_urls
        if d.get('filename', '') not in image_filenames
        and not d.get('is_image', False)
    ]
    for info in non_image_downloads:
        filename = info.get('filename', 'file')
        url = info.get('url', '')
        size = info.get('size', '')
        if url:
            blocks.append({"type": "text", "text": f"File: {filename} ({size})\nDownload URL: {url}"})

    # Block 5: Metadata
    meta_parts = []
    exec_time = data.get('execution_time_ms', 0)
    if exec_time:
        meta_parts.append(f"Execution: {exec_time}ms")
    kernel_info = data.get('kernel_info', {})
    if kernel_info.get('session_persisted'):
        var_count = kernel_info.get('variable_count', 0)
        exec_count = kernel_info.get('execution_count', 0)
        meta_parts.append(f"Session: {var_count} variables persisted (call #{exec_count})")
    if meta_parts:
        blocks.append({"type": "text", "text": " | ".join(meta_parts)})

    # Safety: always return at least one block
    if not blocks:
        blocks.append({"type": "text", "text": "Code executed successfully (no output)."})

    logger.info(f"Built {len(blocks)} content blocks for MCP response")
    return blocks


# ============================================================
# CODE EXECUTION TOOLS
# ============================================================

@mcp.tool()
async def execute_code(
    code: str,
    session_id: str = "default",
    timeout: int = 55
) -> list:
    """Execute Python code in a persistent sandbox kernel.

    The kernel persists between calls — variables, imports, and loaded
    files are all available in subsequent execute_code calls.

    WORKFLOW — always follow this pattern:
      1. fetch_from_url(url, filename) — load a file from URL into sandbox
      2. execute_code("import pandas as pd; df = pd.read_excel('filename.xlsx')")
      3. execute_code("print(df.head())")  — variables persist!

    OUTPUT — stdout is returned as-is. Any URLs printed to stdout
    (chart URLs, download URLs) will be visible in the response.

    Args:
        code: Python code to execute. Multi-line strings work fine.
        session_id: Session for state persistence (default: 'default').
                    Use the same session_id across calls to share state.
        timeout: Max seconds before timeout (default 55, max 59).
    """
    url = f"{API_BASE}/api/execute"
    logger.info(f"execute_code: POST {url} session={session_id}")
    try:
        async with httpx.AsyncClient(timeout=timeout + 5) as client:
            resp = await client.post(
                url,
                headers=_headers(),
                json={"code": code, "session_id": session_id, "timeout": timeout}
            )
            return _build_content_blocks(resp.text)
    except Exception as e:
        logger.error(f"execute_code: error: {e}", exc_info=True)
        return [{"type": "text", "text": f"Error calling execute_code API: {e}"}]


# ============================================================
# FILE TOOLS
# ============================================================

@mcp.tool()
async def fetch_from_url(
    url: str,
    filename: Optional[str] = None,
    session_id: str = "default",
) -> list:
    """Fetch a file from any HTTPS URL directly into the sandbox.

    USE THIS to load files before running execute_code on them.

    Supports:
    - Cloudinary CDN URLs (SimTheory file attachments)
    - Google Sheets export URLs (?format=xlsx or ?format=csv)
    - S3 pre-signed URLs
    - Any public HTTPS download link

    WORKFLOW:
      1. fetch_from_url(url="https://...", filename="data.xlsx")
      2. execute_code("import pandas as pd; df = pd.read_excel('data.xlsx')")
      3. execute_code("print(df.describe())")

    Args:
        url: HTTPS URL to download from.
        filename: Name to save as in sandbox (e.g. 'invoices.xlsx').
                  If omitted, derived from the URL.
        session_id: Session for file isolation (default: 'default').
    """
    # Derive filename from URL if not provided
    if not filename:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        filename = parsed.path.split('/')[-1].split('?')[0] or 'downloaded_file'

    # FIXED v1.7.2: Call /api/files/fetch (correct route in files.py)
    # Previously called /api/files/fetch-from-url which returned 404.
    api_url = f"{API_BASE}/api/files/fetch"
    logger.info(f"fetch_from_url: POST {api_url} url={url[:80]} filename={filename}")

    try:
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                api_url,
                headers=_headers(),
                json={"url": url, "filename": filename, "session_id": session_id}
            )
            logger.info(f"fetch_from_url: response status={resp.status_code}")
            if resp.status_code == 200:
                data = resp.json()
                return [{
                    "type": "text",
                    "text": (
                        f"✅ File fetched successfully!\n"
                        f"  Filename : {data.get('filename')}\n"
                        f"  Size     : {data.get('size_human')}\n"
                        f"  Path     : {data.get('path')}\n"
                        f"  Session  : {data.get('session_id')}\n"
                        f"  Preview  : {data.get('preview', 'N/A')}\n\n"
                        f"Now call execute_code to work with this file."
                    )
                }]
            else:
                return [{"type": "text", "text": f"❌ fetch_from_url failed (HTTP {resp.status_code}):\n  {resp.text[:300]}"}]
    except Exception as e:
        logger.error(f"fetch_from_url: error: {e}", exc_info=True)
        return [{"type": "text", "text": f"❌ fetch_from_url error: {e}"}]


@mcp.tool()
async def upload_file(
    filename: str,
    content_base64: str,
    session_id: str = "default"
) -> str:
    """Upload a file to the sandbox via base64 encoding.

    Use for files under 10MB. For larger files or URL-accessible files,
    use fetch_from_url instead (no base64 overhead).

    After uploading, use execute_code to process the file:
      execute_code("import pandas as pd; df = pd.read_csv('filename.csv')")

    Args:
        filename: Name to save as (e.g., 'data.csv')
        content_base64: Base64-encoded file content
        session_id: Session for isolation (default: 'default')
    """
    url = f"{API_BASE}/api/files/upload"
    logger.info(f"upload_file: POST {url} filename={filename}")
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                url,
                headers=_headers(),
                json={"filename": filename, "content_base64": content_base64,
                      "session_id": session_id}
            )
            return resp.text
    except Exception as e:
        logger.error(f"upload_file: error: {e}", exc_info=True)
        return f"Error calling upload_file API: {e}"


@mcp.tool()
async def fetch_file(
    url: str,
    filename: str,
    session_id: str = "default"
) -> str:
    """Download a file from a URL into the sandbox.

    Alternative to fetch_from_url. Both call the same backend route.
    Use fetch_from_url for new code (better response formatting).

    Args:
        url: URL to download from
        filename: Name to save as in sandbox
        session_id: Session for isolation (default: 'default')
    """
    api_url = f"{API_BASE}/api/files/fetch"
    logger.info(f"fetch_file: POST {api_url}")
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                api_url,
                headers=_headers(),
                json={"url": url, "filename": filename, "session_id": session_id}
            )
            return resp.text
    except Exception as e:
        logger.error(f"fetch_file: error: {e}", exc_info=True)
        return f"Error calling fetch_file API: {e}"


@mcp.tool()
async def list_files(session_id: Optional[str] = "default") -> str:
    """List files currently in the sandbox.

    Shows filename, size, type, and a text preview for each file.
    Use this to confirm a file was successfully uploaded or fetched
    before trying to process it with execute_code.

    Args:
        session_id: Session to list files for (default: 'default')
    """
    url = f"{API_BASE}/api/files"
    logger.info(f"list_files: GET {url}")
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                url,
                headers=_headers(),
                params={"session_id": session_id}
            )
            return resp.text
    except Exception as e:
        logger.error(f"list_files: error: {e}", exc_info=True)
        return f"Error calling list_files API: {e}"


# ============================================================
# ASYNC JOB TOOLS
# ============================================================

@mcp.tool()
async def submit_job(
    code: str,
    session_id: str = "default",
    timeout: int = 600
) -> str:
    """Submit a long-running job for async execution. Returns a job_id immediately.

    Use for jobs that exceed 60 seconds. Poll with get_job_status,
    then retrieve output with get_job_result.

    Args:
        code: Python code to execute.
        session_id: Session for state persistence.
        timeout: Max seconds (default 600 = 10 min)
    """
    url = f"{API_BASE}/api/jobs/submit"
    logger.info(f"submit_job: POST {url}")
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                url,
                headers=_headers(),
                json={"code": code, "session_id": session_id, "timeout": timeout}
            )
            return resp.text
    except Exception as e:
        logger.error(f"submit_job: error: {e}", exc_info=True)
        return f"Error calling submit_job API: {e}"


@mcp.tool()
async def get_job_status(job_id: str) -> str:
    """Check the status of a submitted job.

    Status values: pending, running, completed, failed, cancelled, timeout

    Args:
        job_id: The job ID from submit_job
    """
    url = f"{API_BASE}/api/jobs/{job_id}/status"
    logger.info(f"get_job_status: GET {url}")
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, headers=_headers())
            return resp.text
    except Exception as e:
        logger.error(f"get_job_status: error: {e}", exc_info=True)
        return f"Error calling get_job_status API: {e}"


@mcp.tool()
async def get_job_result(job_id: str) -> list:
    """Get the full result of a completed job.

    Args:
        job_id: The job ID from submit_job
    """
    url = f"{API_BASE}/api/jobs/{job_id}/result"
    logger.info(f"get_job_result: GET {url}")
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(url, headers=_headers())
            return _build_content_blocks(resp.text)
    except Exception as e:
        logger.error(f"get_job_result: error: {e}", exc_info=True)
        return f"Error calling get_job_result API: {e}"


# ============================================================
# DATASET TOOLS
# ============================================================

@mcp.tool()
async def load_dataset(
    file_path: str,
    dataset_name: str,
    session_id: str = "default",
    delimiter: str = ","
) -> str:
    """Load a CSV file from the sandbox into PostgreSQL for fast SQL querying.

    Args:
        file_path: Filename in sandbox (e.g., 'data.csv' — NOT a URL or local path)
        dataset_name: Table name for SQL queries (e.g., 'sales', 'invoices')
        session_id: Session for file isolation (default: 'default')
        delimiter: CSV delimiter (default comma)
    """
    url = f"{API_BASE}/api/data/load-csv"
    logger.info(f"load_dataset: POST {url}")
    try:
        async with httpx.AsyncClient(timeout=300) as client:
            resp = await client.post(
                url,
                headers=_headers(),
                json={"file_path": file_path, "dataset_name": dataset_name,
                      "session_id": session_id, "delimiter": delimiter}
            )
            return resp.text
    except Exception as e:
        logger.error(f"load_dataset: error: {e}", exc_info=True)
        return f"Error calling load_dataset API: {e}"


@mcp.tool()
async def query_dataset(
    sql: str,
    limit: int = 1000,
    offset: int = 0
) -> str:
    """Execute a SQL query against datasets loaded into PostgreSQL.

    Args:
        sql: SQL SELECT query (e.g., "SELECT * FROM sales WHERE revenue > 1000")
        limit: Max rows returned (default 1000)
        offset: Row offset for pagination
    """
    url = f"{API_BASE}/api/data/query"
    logger.info(f"query_dataset: POST {url}")
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                url,
                headers=_headers(),
                json={"sql": sql, "limit": limit, "offset": offset}
            )
            return resp.text
    except Exception as e:
        logger.error(f"query_dataset: error: {e}", exc_info=True)
        return f"Error calling query_dataset API: {e}"


@mcp.tool()
async def list_datasets(session_id: str = None) -> str:
    """List all datasets loaded into PostgreSQL.

    Args:
        session_id: Optional session filter
    """
    params = {}
    if session_id:
        params["session_id"] = session_id
    url = f"{API_BASE}/api/data/datasets"
    logger.info(f"list_datasets: GET {url}")
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, headers=_headers(), params=params)
            return resp.text
    except Exception as e:
        logger.error(f"list_datasets: error: {e}", exc_info=True)
        return f"Error calling list_datasets API: {e}"


# ============================================================
# SESSION TOOLS
# ============================================================

@mcp.tool()
async def create_session(
    name: str,
    description: str = ""
) -> str:
    """Create a new workspace session for file/data isolation.

    The "default" session is automatically available for all normal work.
    Only create a new session to isolate unrelated projects.

    Args:
        name: Session name (e.g., 'vestis-audit', 'financial-model')
        description: Optional description
    """
    url = f"{API_BASE}/api/sessions"
    logger.info(f"create_session: POST {url}")
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                url,
                headers=_headers(),
                json={"name": name, "description": description}
            )
            return resp.text
    except Exception as e:
        logger.error(f"create_session: error: {e}", exc_info=True)
        return f"Error calling create_session API: {e}"
