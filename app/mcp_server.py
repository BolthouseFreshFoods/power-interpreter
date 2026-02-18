"""Power Interpreter - MCP Server Definition

Defines the MCP tools that SimTheory.ai can call.
This maps MCP tool calls to the FastAPI endpoints.

MCP Tools (11):
- execute_code: Run Python code (sync, <60s)
- submit_job: Submit long-running job (async)
- get_job_status: Check job progress
- get_job_result: Get completed job output
- upload_file: Upload a file (base64) to sandbox
- fetch_file: Download a file from URL to sandbox
- list_files: List sandbox files
- load_dataset: Load CSV into PostgreSQL
- query_dataset: SQL query against datasets
- list_datasets: List loaded datasets
- create_session: Create workspace session

Version: 1.6.0 - Auto File Handling: improved tool descriptions for
                 reliable multi-step chaining. AI now knows the correct
                 sequence for file → analysis workflows.

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
"""

from mcp.server.fastmcp import FastMCP
from typing import Optional, Dict
import httpx
import os
import json
import logging

logger = logging.getLogger(__name__)

# MCP Server
mcp = FastMCP(
    "Power Interpreter",
    description="General-purpose sandboxed Python execution engine with large dataset support"
)

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
    
    Content blocks:
    1. Text block with stdout (UNMODIFIED - includes any URLs the executor appended)
    2. Error block (if execution failed)
    3. Additional image blocks (if inline_images[] is populated in JSON)
    4. Additional download blocks (if download_urls[] is populated in JSON)
    5. Metadata block (execution time, variables)
    """
    try:
        data = json.loads(resp_text)
    except (json.JSONDecodeError, TypeError):
        return [{"type": "text", "text": resp_text}]
    
    blocks = []
    
    # ================================================================
    # Block 1: Main output (stdout) — PASSED THROUGH UNMODIFIED
    #
    # The executor appends download URLs and chart URLs to stdout.
    # This is the RELIABLE delivery path. Do NOT strip or modify.
    # ================================================================
    stdout = data.get('stdout', '').strip()
    
    if stdout:
        blocks.append({"type": "text", "text": stdout})
    
    # ================================================================
    # Block 2: Error information (if execution failed)
    # ================================================================
    if not data.get('success', False):
        error_msg = data.get('error_message', 'Unknown error')
        error_tb = data.get('error_traceback', '')
        error_text = f"Execution Error: {error_msg}"
        if error_tb:
            if len(error_tb) > 500:
                error_tb = "..." + error_tb[-500:]
            error_text += f"\n\nTraceback:\n{error_tb}"
        blocks.append({"type": "text", "text": error_text})
    
    # ================================================================
    # Block 3: Additional image blocks (belt-and-suspenders)
    #
    # If inline_images[] is populated in the JSON response, create
    # extra blocks. These may duplicate what's in stdout — that's OK.
    # Better to show the URL twice than zero times.
    # ================================================================
    inline_images = data.get('inline_images', [])
    
    for img in inline_images:
        alt = img.get('alt_text', 'Generated chart')
        url = img.get('url', '')
        if url:
            blocks.append({
                "type": "text",
                "text": f"Chart: {alt}\nImage URL: {url}"
            })
            logger.info(f"Content block (extra): chart '{alt}' -> {url}")
    
    # ================================================================
    # Block 4: Additional download blocks (belt-and-suspenders)
    #
    # If download_urls[] is populated, create extra blocks.
    # Skip images that were already handled above.
    # ================================================================
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
            blocks.append({
                "type": "text",
                "text": f"File: {filename} ({size})\nDownload URL: {url}"
            })
            logger.info(f"Content block (extra): download '{filename}' -> {url}")
    
    # ================================================================
    # Block 5: Metadata (execution info, variables)
    # ================================================================
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
        blocks.append({
            "type": "text",
            "text": " | ".join(meta_parts)
        })
    
    # ================================================================
    # Safety: ensure we always return at least one block
    # ================================================================
    if not blocks:
        blocks.append({
            "type": "text",
            "text": "Code executed successfully (no output)."
        })
    
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
       - User provided a URL? → Call fetch_file FIRST, then execute_code.
       - User attached/uploaded a file? → Call upload_file FIRST, then execute_code.
       - File already in sandbox? → Just reference it by filename: 'data.csv'
       Do NOT use pd.read_csv('https://...') — outbound HTTP from code is blocked.
    
    3. SESSION PERSISTENCE: This works like a Jupyter notebook. Variables, imports,
       and DataFrames persist across calls WITHIN the same session_id.
       ALWAYS use session_id="default" unless isolating separate projects.
       Do NOT create new session IDs per call — that destroys persistence.
       
       Example:
         Call 1: execute_code("import pandas as pd; df = pd.read_csv('data.csv')")
         Call 2: execute_code("print(df.shape)")  # df still exists!
         Call 3: execute_code("summary = df.describe()")  # builds on previous work
    
    4. CHARTS: matplotlib/plotly figures are auto-captured as PNG images.
       Download URLs appear in stdout. Present these to the user.
    
    5. GENERATED FILES: When code creates files (xlsx, csv, pdf), download
       URLs are appended to stdout. Present these as clickable links.
    
    STANDARD WORKFLOW:
      Step 1: Get file into sandbox (fetch_file or upload_file)
      Step 2: execute_code to load and analyze (pd.read_csv('filename.csv'))
      Step 3: execute_code for follow-up analysis (variables persist!)
    
    For large datasets (1M+ rows), consider:
      Step 1: fetch_file or upload_file
      Step 2: load_dataset (loads CSV into PostgreSQL)
      Step 3: query_dataset (fast SQL queries)
    
    Pre-installed: pandas, numpy, matplotlib, plotly, seaborn, scipy,
    scikit-learn, statsmodels, openpyxl, pdfplumber, reportlab, requests.
    
    Use for quick operations (<60s). For longer tasks, use submit_job.
    
    Args:
        code: Python code to execute. Use relative filenames only (e.g., 'data.csv').
        session_id: Session for state persistence. Use "default" for continuity.
        timeout: Max seconds (max 60 for sync)
    
    Returns:
        List of content blocks: stdout text (with URLs), and execution metadata.
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


@mcp.tool()
async def submit_job(
    code: str,
    session_id: str = "default",
    timeout: int = 600
) -> str:
    """Submit a long-running job for async execution.
    
    IMPORTANT: Same file rules as execute_code — files must be in the
    sandbox first. Use fetch_file or upload_file before submitting.
    
    SESSION PERSISTENCE: Uses the same kernel as execute_code when
    session_id matches. Variables created in execute_code are available
    in the job, and vice versa.
    
    Returns immediately with a job_id. Poll with get_job_status,
    then retrieve output with get_job_result.
    
    Use for:
    - Large data processing (1M+ rows)
    - Complex analysis (>60 seconds)
    - Report generation
    
    Args:
        code: Python code to execute. Use relative filenames only.
        session_id: Session for state persistence. Use "default" to share
                    state with execute_code calls.
        timeout: Max seconds (default 600 = 10 min)
    
    Returns:
        Job ID and status
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
    
    Returns:
        Job status with timing info
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
async def get_job_result(job_id: str) -> str:
    """Get the full result of a completed job.
    
    Includes stdout, stderr, result data, files created, execution time.
    If the job generated charts, image URLs will be in the output.
    
    Args:
        job_id: The job ID from submit_job
    
    Returns:
        Full job result with image URLs and download links
    """
    url = f"{API_BASE}/api/jobs/{job_id}/result"
    logger.info(f"get_job_result: GET {url}")
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(url, headers=_headers())
            blocks = _build_content_blocks(resp.text)
            return blocks
    except Exception as e:
        logger.error(f"get_job_result: error: {e}", exc_info=True)
        return f"Error calling get_job_result API: {e}"


# ============================================================
# FILE MANAGEMENT TOOLS
# ============================================================

@mcp.tool()
async def upload_file(
    filename: str,
    content_base64: str,
    session_id: str = "default"
) -> str:
    """Upload a file to the sandbox using base64-encoded content.
    
    WHEN TO USE THIS:
    - User ATTACHED or PASTED a file in the conversation (you have the content)
    - Small files under 10MB
    
    WHEN TO USE fetch_file INSTEAD:
    - User provided a URL (you do NOT have the file content, only a link)
    - Large files over 10MB
    
    AFTER UPLOADING — your next step depends on the goal:
    
    For Python analysis (pandas, charts, etc.):
      1. upload_file("data.csv", "<base64>", session_id="default")
      2. execute_code("import pandas as pd; df = pd.read_csv('data.csv'); print(df.head())", session_id="default")
    
    For SQL analysis (large datasets, complex queries):
      1. upload_file("data.csv", "<base64>", session_id="default")
      2. load_dataset("data.csv", "my_data", session_id="default")
      3. query_dataset("SELECT * FROM my_data LIMIT 10")
    
    CRITICAL: Use the SAME session_id across all calls so files are accessible.
    
    Args:
        filename: Name for the file (e.g., 'invoices.csv', 'report.xlsx')
        content_base64: Base64-encoded file content
        session_id: Session for file isolation (default: 'default')
    
    Returns:
        Confirmation with file path, size, and preview info
    """
    url = f"{API_BASE}/api/files/upload"
    logger.info(f"upload_file: POST {url} filename={filename}")
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                url,
                headers=_headers(),
                json={
                    "filename": filename,
                    "content_base64": content_base64,
                    "session_id": session_id
                }
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
    
    WHEN TO USE THIS:
    - User provided a URL to a file (CSV, Excel, PDF, etc.)
    - User shared a Google Drive, Dropbox, or S3 link
    - Any file available at a public URL
    
    WHEN TO USE upload_file INSTEAD:
    - User attached a file directly in the conversation (you have the content)
    
    SUPPORTED URL FORMATS:
    - Direct links: https://example.com/data.csv
    - Google Drive: https://drive.google.com/uc?export=download&id=FILE_ID
    - Dropbox: Change dl=0 to dl=1 in the sharing URL
    - S3 pre-signed URLs
    
    AFTER FETCHING — your next step depends on the goal:
    
    For Python analysis (pandas, charts, etc.):
      1. fetch_file(url="https://...", filename="data.csv", session_id="default")
      2. execute_code("import pandas as pd; df = pd.read_csv('data.csv'); print(df.head())", session_id="default")
    
    For SQL analysis (large datasets, 100K+ rows):
      1. fetch_file(url="https://...", filename="data.csv", session_id="default")
      2. load_dataset("data.csv", "my_data", session_id="default")
      3. query_dataset("SELECT * FROM my_data LIMIT 10")
    
    CRITICAL: Use session_id="default" so the file is accessible to execute_code.
    
    Args:
        url: Public URL to download from
        filename: What to name the file in the sandbox (e.g., 'sales.csv')
        session_id: Session for file isolation (default: 'default')
    
    Returns:
        Confirmation with file path, size, type detection, and preview
    """
    api_url = f"{API_BASE}/api/files/fetch"
    logger.info(f"fetch_file: POST {api_url} url={url[:80]}... filename={filename}")
    try:
        async with httpx.AsyncClient(timeout=300) as client:
            resp = await client.post(
                api_url,
                headers=_headers(),
                json={
                    "url": url,
                    "filename": filename,
                    "session_id": session_id
                }
            )
            logger.info(f"fetch_file: response status={resp.status_code}")
            return resp.text
    except Exception as e:
        logger.error(f"fetch_file: error: {e}", exc_info=True)
        return f"Error calling fetch_file API: {e}"


@mcp.tool()
async def list_files(session_id: str = None) -> str:
    """List files in the sandbox.
    
    Use this to check what files are available before running code.
    Helpful when you're unsure if a file was already uploaded or fetched.
    
    Args:
        session_id: Optional session filter (use "default" to see main workspace)
    
    Returns:
        List of files with metadata
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
    
    PREREQUISITE: The file MUST already be in the sandbox. Get it there first:
      - From a URL: fetch_file(url="...", filename="data.csv")
      - From user upload: upload_file("data.csv", "<base64>")
    
    WHEN TO USE THIS vs. just execute_code with pandas:
    - Dataset has 100K+ rows → use load_dataset + query_dataset (faster)
    - You need complex SQL joins, GROUP BY, window functions → load_dataset
    - Quick look at a small file → just use execute_code with pandas
    
    COMPLETE WORKFLOW:
      1. fetch_file(url="https://...", filename="big_data.csv", session_id="default")
      2. load_dataset("big_data.csv", "sales", session_id="default")
      3. query_dataset("SELECT region, SUM(revenue) FROM sales GROUP BY region")
    
    Args:
        file_path: Filename in sandbox (e.g., 'data.csv' — NOT a URL or local path)
        dataset_name: Table name for SQL queries (e.g., 'sales', 'invoices')
        session_id: Session for file isolation (default: 'default')
        delimiter: CSV delimiter (default comma)
    
    Returns:
        Dataset info with row count, columns, and preview
    """
    url = f"{API_BASE}/api/data/load-csv"
    logger.info(f"load_dataset: POST {url}")
    try:
        async with httpx.AsyncClient(timeout=300) as client:
            resp = await client.post(
                url,
                headers=_headers(),
                json={
                    "file_path": file_path,
                    "dataset_name": dataset_name,
                    "session_id": session_id,
                    "delimiter": delimiter
                }
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
    
    PREREQUISITE: Dataset must be loaded first with load_dataset.
    Use list_datasets to see available table names.
    
    Only SELECT queries allowed. Results are paginated.
    
    Args:
        sql: SQL SELECT query (e.g., "SELECT * FROM sales WHERE revenue > 1000")
        limit: Max rows returned (default 1000)
        offset: Row offset for pagination
    
    Returns:
        Query results with columns and data
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
    
    Shows dataset names, table names, row counts, and sizes.
    Use the table_name in SQL queries with query_dataset.
    
    Args:
        session_id: Optional session filter
    
    Returns:
        List of datasets with metadata
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
    
    You almost NEVER need this. The "default" session is automatically
    available and should be used for all normal work.
    
    Only create a new session when you need to completely isolate
    files and data between unrelated projects running simultaneously.
    
    Args:
        name: Session name (e.g., 'vestis-audit', 'financial-model')
        description: Optional description
    
    Returns:
        Session ID and details
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
