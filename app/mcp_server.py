"""Power Interpreter - MCP Server Definition

Defines the MCP tools that SimTheory.ai can call.
This maps MCP tool calls to the FastAPI endpoints.

MCP Tools:
- execute_code: Run Python code (sync, <60s)
- submit_job: Submit long-running job (async)
- get_job_status: Check job progress
- get_job_result: Get completed job output
- upload_file: Upload a file to sandbox
- list_files: List sandbox files
- load_dataset: Load CSV into PostgreSQL
- query_dataset: SQL query against datasets
- list_datasets: List loaded datasets
- create_session: Create workspace session
"""

from mcp.server.fastmcp import FastMCP
from typing import Optional, Dict
import httpx
import os

# MCP Server
mcp = FastMCP(
    "Power Interpreter",
    description="General-purpose sandboxed Python execution engine with large dataset support"
)

# Internal API base URL
API_BASE = os.getenv("API_BASE_URL", "http://localhost:8000")
API_KEY = os.getenv("API_KEY", "")


def _headers():
    return {"X-API-Key": API_KEY, "Content-Type": "application/json"}


@mcp.tool()
async def execute_code(
    code: str,
    session_id: str = "default",
    timeout: int = 30
) -> str:
    """Execute Python code in a sandboxed environment.
    
    Pre-installed libraries: pandas, numpy, matplotlib, plotly, seaborn,
    scipy, scikit-learn, statsmodels, openpyxl, pdfplumber.
    
    Use for quick operations (<60s). For longer tasks, use submit_job.
    
    Args:
        code: Python code to execute
        session_id: Session ID for file isolation
        timeout: Max seconds (max 60 for sync)
    
    Returns:
        Execution result with stdout, result, errors, files created
    """
    async with httpx.AsyncClient(timeout=70) as client:
        resp = await client.post(
            f"{API_BASE}/api/execute",
            headers=_headers(),
            json={"code": code, "session_id": session_id, "timeout": timeout}
        )
        return resp.text


@mcp.tool()
async def submit_job(
    code: str,
    session_id: str = None,
    timeout: int = 600
) -> str:
    """Submit a long-running job for async execution.
    
    Returns immediately with a job_id. Use get_job_status to check progress.
    Use get_job_result to get output when complete.
    
    Use for:
    - Large data processing (1.5M+ rows)
    - Complex analysis (>60 seconds)
    - Report generation
    
    Args:
        code: Python code to execute
        session_id: Session ID for file isolation
        timeout: Max seconds (default 600 = 10 min)
    
    Returns:
        Job ID and status
    """
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            f"{API_BASE}/api/jobs/submit",
            headers=_headers(),
            json={"code": code, "session_id": session_id, "timeout": timeout}
        )
        return resp.text


@mcp.tool()
async def get_job_status(job_id: str) -> str:
    """Check the status of a submitted job.
    
    Status values: pending, running, completed, failed, cancelled, timeout
    
    Args:
        job_id: The job ID from submit_job
    
    Returns:
        Job status with timing info
    """
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            f"{API_BASE}/api/jobs/{job_id}/status",
            headers=_headers()
        )
        return resp.text


@mcp.tool()
async def get_job_result(job_id: str) -> str:
    """Get the full result of a completed job.
    
    Includes stdout, stderr, result data, files created, execution time.
    
    Args:
        job_id: The job ID from submit_job
    
    Returns:
        Full job result
    """
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            f"{API_BASE}/api/jobs/{job_id}/result",
            headers=_headers()
        )
        return resp.text


@mcp.tool()
async def list_files(session_id: str = None) -> str:
    """List files in the sandbox.
    
    Args:
        session_id: Optional session filter
    
    Returns:
        List of files with metadata
    """
    params = {}
    if session_id:
        params["session_id"] = session_id
    
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            f"{API_BASE}/api/files",
            headers=_headers(),
            params=params
        )
        return resp.text


@mcp.tool()
async def load_dataset(
    file_path: str,
    dataset_name: str,
    session_id: str = None,
    delimiter: str = ","
) -> str:
    """Load a CSV file into PostgreSQL for fast SQL querying.
    
    Handles 1.5M+ rows by loading in chunks.
    After loading, use query_dataset with SQL to analyze.
    
    Args:
        file_path: Path to CSV in sandbox
        dataset_name: Logical name (e.g., 'vestis_invoices')
        session_id: Optional session
        delimiter: CSV delimiter (default comma)
    
    Returns:
        Dataset info with row count, columns, preview
    """
    async with httpx.AsyncClient(timeout=300) as client:
        resp = await client.post(
            f"{API_BASE}/api/data/load-csv",
            headers=_headers(),
            json={
                "file_path": file_path,
                "dataset_name": dataset_name,
                "session_id": session_id,
                "delimiter": delimiter
            }
        )
        return resp.text


@mcp.tool()
async def query_dataset(
    sql: str,
    limit: int = 1000,
    offset: int = 0
) -> str:
    """Execute a SQL query against loaded datasets.
    
    Only SELECT queries allowed. Results paginated.
    
    Use list_datasets first to find table names.
    
    Args:
        sql: SQL SELECT query
        limit: Max rows (default 1000)
        offset: Row offset for pagination
    
    Returns:
        Query results with columns and data
    """
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            f"{API_BASE}/api/data/query",
            headers=_headers(),
            json={"sql": sql, "limit": limit, "offset": offset}
        )
        return resp.text


@mcp.tool()
async def list_datasets(session_id: str = None) -> str:
    """List all datasets loaded into PostgreSQL.
    
    Shows dataset names, table names, row counts, and sizes.
    Use the table_name in SQL queries.
    
    Args:
        session_id: Optional session filter
    
    Returns:
        List of datasets
    """
    params = {}
    if session_id:
        params["session_id"] = session_id
    
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            f"{API_BASE}/api/data/datasets",
            headers=_headers(),
            params=params
        )
        return resp.text


@mcp.tool()
async def create_session(
    name: str,
    description: str = ""
) -> str:
    """Create a new workspace session for file/data isolation.
    
    Use session_id in other tools to scope operations.
    
    Args:
        name: Session name (e.g., 'vestis-audit', 'financial-model')
        description: Optional description
    
    Returns:
        Session ID and details
    """
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            f"{API_BASE}/api/sessions",
            headers=_headers(),
            json={"name": name, "description": description}
        )
        return resp.text
