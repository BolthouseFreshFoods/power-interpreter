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
- fetch_from_url: ★ NEW — load file from CDN/URL directly into sandbox
- list_files: List sandbox files
- load_dataset: Load CSV into PostgreSQL
- query_dataset: SQL query against datasets
- list_datasets: List loaded datasets
- create_session: Create workspace session

Version: 1.7.1 - fix: remove unsupported 'description' kwarg from FastMCP()

HISTORY:
  v1.2.0: Response was a JSON blob. URLs lived in stdout. AI parsed them. WORKED.
  v1.5.0: Introduced content blocks. Stripped URLs from stdout, tried to rebuild
           from inline_images[]/download_urls[] arrays. Those arrays were empty
           due to parallel execution race in executor.py. URLs LOST.
  v1.5.1: Switched from markdown to plain text URLs. Still stripped stdout. Still broken.
  v1.5.2: Stop stripping stdout. Pass URLs through as-is. Belt-and-suspenders:
           also create extra blocks from JSON arrays if populated.
  v1.6.0: Auto File Handling. Rewrote tool descriptions so the AI reliably
           chains fetch_file → execute_code, upload_file → execute_code,
           and fetch_file → load_dataset → query_dataset. No logic changes.
  v1.7.0: fetch_from_url tool added. Streams files directly from any HTTPS URL
           (Cloudinary CDN, S3, public URLs) into sandbox. Fixes Priority 1
           file upload blocker — no base64 overhead, no SimTheory encoding bug.
  v1.7.1: Fix TypeError — FastMCP() does not accept 'description' kwarg in the
           installed version. Removed it. App now starts cleanly.
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
    timeout: int = 30
) -> list:
    """Execute Python code in a persistent sandboxed environment.

    IMPORTANT — READ BEFORE CALLING:

    1. REMOTE EXECUTION: Code runs on a REMOTE server, NOT locally.
       You CANNOT use local file paths like /home/ubuntu/... or /tmp/uploads/...

    2. FILE ACCESS: To work with files, you MUST get them into the sandbox first:
       - User provided a URL? → Call fetch_from_url FIRST, then execute_code.
       - User attached/uploaded a file? → Call upload_file FIRST, then execute_code.
       - File already in sandbox? → Just reference it by filename: 'data.csv'
       Do NOT use pd.read_csv('https://...') — outbound HTTP from code is blocked.

    3. SESSION PERSISTENCE: Variables, imports, and DataFrames persist across
       calls WITHIN the same session_id. ALWAYS use session_id="default" unless
       isolating separate projects.

    4. CHARTS: matplotlib/plotly figures are auto-captured as PNG images.
       Download URLs appear in stdout. Present these to the user.

    5. GENERATED FILES: When code creates files (xlsx, csv, pdf), download
       URLs are appended to stdout. Present these as clickable links.

    Pre-installed: pandas, numpy, matplotlib, plotly, seaborn, scipy,
    scikit-learn, statsmodels, openpyxl, pdfplumber, reportlab, requests,
    xgboost, lightgbm, sympy, duckdb, pyarrow, Pillow, beautifulsoup4.

    Args:
        code: Python code to execute. Use relative filenames only (e.g., 'data.csv').
        session_id: Session for state persistence. Use "default" for continuity.
        timeout: Max seconds (max 60 for sync)
    """
    url = f"{API_BASE}/api/execute"
    logger.info(f"execute_code: POST {url}")
    try:
        async with httpx.AsyncClient(timeout=70) as client:
            resp = await client.post(
                url,
                headers=_headers(),
                json={"code": code, "session_id": session_id, "timeout": timeout}
            )
            logger.info(f"execute_code: response status={resp.status_code}")
            blocks = _build_content_blocks(resp.text)
            logger.info(f"execute_code: returning {len(blocks)} content blocks")
            return blocks
    except Exception as e:
        logger.error(f"execute_code: error: {e}", exc_info=True)
        return [{"type": "text", "text": f"Error calling execute API: {e}"}]


# ============================================================
# FILE MANAGEMENT TOOLS
# ============================================================

@mcp.tool()
async def fetch_from_url(
    url: str,
    filename: Optional[str] = None,
    session_id: str = "default",
) -> list:
    """Fetch a file from any accessible URL and save it directly into the sandbox.

    ★ THIS IS THE PRIMARY WAY TO LOAD FILES INTO POWER INTERPRETER ★

    SimTheory uploads files to Cloudinary CDN. Pass that CDN URL here and
    the file will be streamed directly into the sandbox — no base64 encoding,
    no size limits from encoding overhead.

    AFTER FETCHING — standard workflow:
      1. fetch_from_url(url="https://cdn.simtheory.ai/.../data.xlsx", session_id="default")
      2. execute_code("import pandas as pd; df = pd.read_excel('data.xlsx'); print(df.head())", session_id="default")

    Supported file types: xlsx, xls, csv, tsv, json, jsonl, parquet,
                          pdf, txt, png, jpg, zip, db, sqlite

    Args:
        url:        Full HTTPS URL to the file
        filename:   Filename to save as in sandbox. Inferred from URL if omitted.
        session_id: Sandbox session. Use "default" to share with execute_code.
    """
    api_url = f"{API_BASE}/api/files/fetch-from-url"
    logger.info(f"fetch_from_url: POST {api_url} url={url[:80]} filename={filename}")
    try:
        async with httpx.AsyncClient(timeout=300) as client:
            resp = await client.post(
                api_url,
                headers=_headers(),
                json={"url": url, "filename": filename, "session_id": session_id}
            )
            logger.info(f"fetch_from_url: response status={resp.status_code}")
            try:
                data = resp.json()
                if data.get("success") or resp.status_code == 200:
                    saved_name = data.get("filename", filename or "file")
                    size_bytes = data.get("size_bytes", 0)
                    size_kb = size_bytes / 1024 if size_bytes else 0
                    text = (
                        f"✅ File downloaded successfully!\n\n"
                        f"  Filename : {saved_name}\n"
                        f"  Size     : {size_bytes:,} bytes ({size_kb:.1f} KB)\n"
                        f"  Session  : {session_id}\n\n"
                        f"Ready to use in execute_code:\n"
                        f"  import pandas as pd\n"
                        f"  df = pd.read_excel('{saved_name}')  # or read_csv, read_json, etc.\n"
                        f"  print(df.shape, df.columns.tolist())"
                    )
                else:
                    error = data.get("error", data.get("detail", resp.text))
                    text = f"❌ fetch_from_url failed (HTTP {resp.status_code}):\n  {error}"
            except Exception:
                text = resp.text
            return [{"type": "text", "text": text}]
    except Exception as e:
        logger.error(f"fetch_from_url: error: {e}", exc_info=True)
        return [{"type": "text", "text": f"Error calling fetch_from_url: {e}"}]


@mcp.tool()
async def upload_file(
    filename: str,
    content_base64: str,
    session_id: str = "default"
) -> str:
    """Upload a file to the sandbox using base64-encoded content.

    For large files or CDN URLs, prefer fetch_from_url instead.

    Args:
        filename: Name for the file (e.g., 'invoices.csv', 'report.xlsx')
        content_base64: Base64-encoded file content
        session_id: Session for file isolation (default: 'default')
    """
    url = f"{API_BASE}/api/files/upload"
    logger.info(f"upload_file: POST {url} filename={filename}")
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                url,
                headers=_headers(),
                json={"filename": filename, "content_base64": content_base64, "session_id": session_id}
            )
            logger.info(f"upload_file: response status={resp.status_code}")
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
    """Download a file from a URL into the sandbox. Supports up to 500MB.

    Args:
        url: Public URL to download from
        filename: What to name the file in the sandbox (e.g., 'sales.csv')
        session_id: Session for file isolation (default: 'default')
    """
    api_url = f"{API_BASE}/api/files/fetch"
    logger.info(f"fetch_file: POST {api_url} url={url[:80]}... filename={filename}")
    try:
        async with httpx.AsyncClient(timeout=300) as client:
            resp = await client.post(
                api_url,
                headers=_headers(),
                json={"url": url, "filename": filename, "session_id": session_id}
            )
            logger.info(f"fetch_file: response status={resp.status_code}")
            return resp.text
    except Exception as e:
        logger.error(f"fetch_file: error: {e}", exc_info=True)
        return f"Error calling fetch_file API: {e}"


@mcp.tool()
async def list_files(session_id: str = None) -> str:
    """List files in the sandbox.

    Args:
        session_id: Optional session filter (use "default" to see main workspace)
    """
    params = {}
    if session_id:
        params["session_id"] = session_id
    url = f"{API_BASE}/api/files"
    logger.info(f"list_files: GET {url}")
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, headers=_headers(), params=params)
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
