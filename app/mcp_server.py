"""
mcp_server.py
─────────────
FastMCP server — exposes all Power Interpreter tools over MCP/SSE.

Tools exposed (12 total):
  execute_code        — run Python in sandboxed kernel
  submit_job          — async job submission
  get_job_status      — poll async job
  get_job_result      — retrieve async result
  upload_file         — upload file via base64
  fetch_file          — retrieve generated file
  fetch_from_url      — ★ NEW: load file from CDN/URL directly into sandbox
  list_files          — list sandbox files
  load_dataset        — load dataset into named variable
  query_dataset       — SQL-style query on loaded dataset
  list_datasets       — list loaded datasets
  create_session      — create named kernel session
"""

from __future__ import annotations

import base64
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any

import httpx
from fastapi import Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastmcp import FastMCP

from app.fetch_from_url import fetch_from_url as _fetch_from_url_impl

logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
EXECUTOR_URL = os.environ.get("EXECUTOR_URL", "http://127.0.0.1:8080")
SANDBOX_BASE = Path(os.environ.get("SANDBOX_DATA_DIR", "/app/sandbox_data"))
MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_MB", "500")) * 1024 * 1024

mcp = FastMCP("power-interpreter")

# ── Helpers ───────────────────────────────────────────────────────────────────

def _session_dir(session_id: str = "default") -> Path:
    p = SANDBOX_BASE / session_id
    p.mkdir(parents=True, exist_ok=True)
    return p


async def _execute(code: str, session_id: str = "default", timeout: int = 120) -> dict:
    """POST code to the local executor and return the result dict."""
    logger.info(f"execute_code: POST http://127.0.0.1:8080/api/execute")
    async with httpx.AsyncClient(timeout=timeout + 5) as client:
        resp = await client.post(
            f"{EXECUTOR_URL}/api/execute",
            json={"code": code, "session_id": session_id, "timeout": timeout},
        )
        logger.info(f"execute_code: response status={resp.status_code}")
        return resp.json()


def _build_content_blocks(result: dict) -> list[dict]:
    """Convert executor result into MCP content blocks."""
    blocks: list[dict] = []

    if result.get("error"):
        blocks.append({"type": "text", "text": f"Execution Error:\n{result['error']}\n\nTraceback:\n{result.get('traceback', '')}"})
        return blocks

    if result.get("stdout"):
        blocks.append({"type": "text", "text": result["stdout"]})

    if result.get("stderr"):
        blocks.append({"type": "text", "text": f"[stderr]\n{result['stderr']}"})

    for chart in result.get("charts", []):
        blocks.append({"type": "image", "data": chart["data"], "mimeType": chart.get("mime", "image/png")})

    if not blocks:
        blocks.append({"type": "text", "text": "(no output)"})

    return blocks


# ── Tool: execute_code ────────────────────────────────────────────────────────

@mcp.tool()
async def execute_code(code: str, session_id: str = "default", timeout: int = 120) -> list[dict]:
    """
    Execute Python code in a sandboxed kernel session.

    Args:
        code:       Python source code to execute.
        session_id: Named kernel session (default: "default"). Up to 6 concurrent.
        timeout:    Max execution seconds (default: 120, max: 300).

    Returns:
        List of MCP content blocks (text output, charts as images).

    Capabilities:
        - Full pandas / numpy / scipy / scikit-learn / statsmodels stack
        - matplotlib, seaborn, plotly chart generation (returned as PNG)
        - openpyxl, xlsxwriter, xlrd for Excel I/O
        - duckdb for in-process SQL on DataFrames
        - pyarrow / parquet for columnar data
        - sympy for symbolic math
        - Variables persist across calls within the same session_id
        - Files written to /app/sandbox_data/{session_id}/ are accessible
    """
    logger.info(f"MCP execute_code: session={session_id}, timeout={timeout}, code_len={len(code)}")
    timeout = min(int(timeout), 300)
    result = await _execute(code, session_id=session_id, timeout=timeout)
    blocks = _build_content_blocks(result)
    logger.info(f"execute_code: returning {len(blocks)} content blocks")
    return blocks


# ── Tool: fetch_from_url ──────────────────────────────────────────────────────

@mcp.tool()
async def fetch_from_url(
    url: str,
    filename: str | None = None,
    session_id: str = "default",
) -> list[dict]:
    """
    Fetch a file from any accessible URL (Cloudinary CDN, S3, HTTPS) and save
    it directly into the sandbox so execute_code can open it immediately.

    This is the PRIMARY way to load files into Power Interpreter.
    SimTheory uploads files to Cloudinary — pass that CDN URL here.

    Args:
        url:        Full HTTPS URL to the file (xlsx, csv, json, pdf, etc.)
        filename:   Filename to save as. Inferred from URL if omitted.
        session_id: Sandbox session directory. Defaults to "default".

    Returns:
        Success message with the exact sandbox path to use in execute_code.

    Example workflow:
        1. Call fetch_from_url(url="https://cdn.simtheory.ai/.../data.xlsx")
        2. Call execute_code(code="import pandas as pd; df = pd.read_excel('/app/sandbox_data/default/data.xlsx'); print(df.head())")

    Supported formats:
        xlsx, xls, csv, tsv, json, jsonl, parquet, pdf, txt, png, jpg, zip, db, sqlite
    """
    logger.info(f"MCP fetch_from_url: url={url!r}, filename={filename!r}, session={session_id!r}")

    result = _fetch_from_url_impl(url=url, filename=filename, session_id=session_id)

    if result["success"]:
        text = (
            f"✅ File downloaded successfully!\n\n"
            f"  Filename : {result['filename']}\n"
            f"  Size     : {result['size_bytes']:,} bytes ({result['size_bytes']/1024:.1f} KB)\n"
            f"  Path     : {result['path']}\n"
            f"  Session  : {result['session_id']}\n\n"
            f"Ready to use in execute_code:\n"
            f"  import pandas as pd\n"
            f"  df = pd.read_excel('{result['path']}')\n"
            f"  print(df.shape, df.columns.tolist())"
        )
    else:
        text = f"❌ fetch_from_url failed:\n  {result['error']}"

    return [{"type": "text", "text": text}]


# ── Tool: upload_file ─────────────────────────────────────────────────────────

@mcp.tool()
async def upload_file(
    filename: str,
    content_base64: str,
    session_id: str = "default",
) -> list[dict]:
    """
    Upload a file to the sandbox via base64-encoded content.

    NOTE: For large files, prefer fetch_from_url() which streams directly
    from a CDN URL without base64 overhead.

    Args:
        filename:       Destination filename in the sandbox.
        content_base64: Base64-encoded file bytes.
        session_id:     Target session directory.
    """
    logger.info(f"MCP upload_file: filename={filename!r}, session={session_id!r}, b64_len={len(content_base64)}")

    try:
        raw = base64.b64decode(content_base64)
    except Exception as e:
        return [{"type": "text", "text": f"❌ Base64 decode failed: {e}"}]

    if len(raw) > MAX_UPLOAD_BYTES:
        return [{"type": "text", "text": f"❌ File too large: {len(raw):,} bytes (max {MAX_UPLOAD_BYTES // (1024*1024)}MB)"}]

    dest = _session_dir(session_id) / Path(filename).name
    dest.write_bytes(raw)

    return [{"type": "text", "text": (
        f"✅ File uploaded: {dest}\n"
        f"  Size: {len(raw):,} bytes\n"
        f"  Use in execute_code: open('{dest}', 'rb') or pd.read_excel('{dest}')"
    )}]


# ── Tool: fetch_file ──────────────────────────────────────────────────────────

@mcp.tool()
async def fetch_file(filename: str, session_id: str = "default") -> list[dict]:
    """
    Retrieve a file generated by execute_code from the sandbox.

    Args:
        filename:   Name of the file in the sandbox.
        session_id: Session directory to look in.

    Returns:
        Base64-encoded file content.
    """
    path = _session_dir(session_id) / Path(filename).name
    if not path.exists():
        return [{"type": "text", "text": f"❌ File not found: {path}"}]

    raw = path.read_bytes()
    encoded = base64.b64encode(raw).decode()
    return [{"type": "text", "text": f"FILE:{filename}:BASE64:{encoded}"}]


# ── Tool: list_files ──────────────────────────────────────────────────────────

@mcp.tool()
async def list_files(session_id: str = "default") -> list[dict]:
    """List all files currently in the sandbox for a given session."""
    d = _session_dir(session_id)
    files = sorted(d.iterdir()) if d.exists() else []
    if not files:
        return [{"type": "text", "text": f"No files in sandbox session '{session_id}'."}]

    lines = [f"Files in sandbox '{session_id}':"]
    for f in files:
        size = f.stat().st_size
        lines.append(f"  {f.name}  ({size:,} bytes)")
    return [{"type": "text", "text": "\n".join(lines)}]


# ── Tool: submit_job ──────────────────────────────────────────────────────────

_jobs: dict[str, dict] = {}


@mcp.tool()
async def submit_job(code: str, session_id: str = "default", timeout: int = 120) -> list[dict]:
    """
    Submit a long-running Python job asynchronously.
    Returns a job_id immediately. Poll with get_job_status / get_job_result.

    Use this for jobs that may exceed 30 seconds (large datasets, ML training, etc.)
    """
    import asyncio

    job_id = str(uuid.uuid4())[:8]
    _jobs[job_id] = {"status": "running", "submitted_at": time.time(), "session_id": session_id}

    async def _run():
        try:
            result = await _execute(code, session_id=session_id, timeout=timeout)
            _jobs[job_id]["status"] = "done"
            _jobs[job_id]["result"] = result
        except Exception as e:
            _jobs[job_id]["status"] = "error"
            _jobs[job_id]["error"] = str(e)

    asyncio.create_task(_run())

    return [{"type": "text", "text": f"✅ Job submitted: job_id={job_id}\nPoll with get_job_status(job_id='{job_id}')"}]


# ── Tool: get_job_status ──────────────────────────────────────────────────────

@mcp.tool()
async def get_job_status(job_id: str) -> list[dict]:
    """Check the status of an async job submitted via submit_job."""
    job = _jobs.get(job_id)
    if not job:
        return [{"type": "text", "text": f"❌ Job not found: {job_id}"}]

    elapsed = time.time() - job["submitted_at"]
    return [{"type": "text", "text": f"Job {job_id}: status={job['status']}, elapsed={elapsed:.1f}s"}]


# ── Tool: get_job_result ──────────────────────────────────────────────────────

@mcp.tool()
async def get_job_result(job_id: str) -> list[dict]:
    """Retrieve the result of a completed async job."""
    job = _jobs.get(job_id)
    if not job:
        return [{"type": "text", "text": f"❌ Job not found: {job_id}"}]
    if job["status"] == "running":
        return [{"type": "text", "text": f"Job {job_id} is still running. Try again shortly."}]
    if job["status"] == "error":
        return [{"type": "text", "text": f"❌ Job {job_id} failed: {job.get('error')}"}]

    return _build_content_blocks(job["result"])


# ── Tool: load_dataset ────────────────────────────────────────────────────────

@mcp.tool()
async def load_dataset(
    filename: str,
    dataset_name: str,
    session_id: str = "default",
    sheet_name: str | None = None,
) -> list[dict]:
    """
    Load a file from the sandbox into a named pandas DataFrame.
    Supports: xlsx, xls, csv, tsv, json, jsonl, parquet.

    After loading, use query_dataset to run SQL-style queries on it.
    """
    path = _session_dir(session_id) / Path(filename).name
    if not path.exists():
        return [{"type": "text", "text": f"❌ File not found: {path}\nUse fetch_from_url or upload_file first."}]

    ext = path.suffix.lower()
    if ext in (".xlsx", ".xls"):
        sheet_arg = f', sheet_name={sheet_name!r}' if sheet_name else ""
        code = f"import pandas as pd; {dataset_name} = pd.read_excel('{path}'{sheet_arg}); print(f'Loaded {dataset_name}: {{len({dataset_name})}} rows x {{{dataset_name}.shape[1]}} cols\\nColumns: {{{dataset_name}.columns.tolist()}}')"
    elif ext == ".csv":
        code = f"import pandas as pd; {dataset_name} = pd.read_csv('{path}'); print(f'Loaded {dataset_name}: {{len({dataset_name})}} rows x {{{dataset_name}.shape[1]}} cols')"
    elif ext == ".parquet":
        code = f"import pandas as pd; {dataset_name} = pd.read_parquet('{path}'); print(f'Loaded {dataset_name}: {{len({dataset_name})}} rows x {{{dataset_name}.shape[1]}} cols')"
    elif ext == ".json":
        code = f"import pandas as pd; {dataset_name} = pd.read_json('{path}'); print(f'Loaded {dataset_name}: {{len({dataset_name})}} rows')"
    else:
        return [{"type": "text", "text": f"❌ Unsupported format: {ext}"}]

    result = await _execute(code, session_id=session_id)
    return _build_content_blocks(result)


# ── Tool: query_dataset ───────────────────────────────────────────────────────

@mcp.tool()
async def query_dataset(
    query: str,
    session_id: str = "default",
) -> list[dict]:
    """
    Run a SQL query against any loaded DataFrame using DuckDB.

    DataFrames loaded via load_dataset are available as SQL table names.
    Example: SELECT vendor, SUM(amount) FROM invoices GROUP BY vendor ORDER BY 2 DESC

    Args:
        query:      SQL query string.
        session_id: Session containing the loaded DataFrames.
    """
    code = f"""
import duckdb
_result = duckdb.query('''{query}''').df()
print(f"Query returned {{len(_result)}} rows x {{_result.shape[1]}} cols")
print(_result.to_string(index=False, max_rows=50))
"""
    result = await _execute(code, session_id=session_id)
    return _build_content_blocks(result)


# ── Tool: list_datasets ───────────────────────────────────────────────────────

@mcp.tool()
async def list_datasets(session_id: str = "default") -> list[dict]:
    """List all DataFrames currently loaded in the session."""
    code = """
import pandas as pd
_dfs = {k: v for k, v in globals().items() if isinstance(v, pd.DataFrame)}
if _dfs:
    for name, df in _dfs.items():
        print(f"  {name}: {df.shape[0]} rows x {df.shape[1]} cols | cols: {df.columns.tolist()}")
else:
    print("No DataFrames loaded. Use load_dataset() or execute_code() to create one.")
"""
    result = await _execute(code, session_id=session_id)
    return _build_content_blocks(result)


# ── Tool: create_session ──────────────────────────────────────────────────────

@mcp.tool()
async def create_session(session_id: str) -> list[dict]:
    """
    Create a named sandbox session directory.
    Sessions isolate files and kernel state. Up to 6 concurrent sessions supported.
    """
    d = _session_dir(session_id)
    return [{"type": "text", "text": f"✅ Session '{session_id}' ready at {d}"}]


# ── MCP SSE endpoint (wired in main.py) ──────────────────────────────────────

def get_mcp_app():
    return mcp
