"""Power Interpreter MCP - MCP Server Definition

Defines the MCP tools that SimTheory.ai can call.
Maps MCP tool calls to internal FastAPI endpoints.

Version: 3.0.3

Changes:
- structured tool logging
- truthful execute_code status logging based on logical execution success
- reduced request/code logging noise
"""

import base64
import hashlib
import json
import logging
import os
import re
import time
from typing import Dict, List, Optional, Tuple

import httpx
from mcp.server.fastmcp import FastMCP


logger = logging.getLogger(__name__)

mcp = FastMCP("Power Interpreter")
_ms_auth, _ms_graph = None, None

_default_base = "http://127.0.0.1:8080"
API_BASE = os.getenv("API_BASE_URL", _default_base)
API_KEY = os.getenv("API_KEY", "")

MAX_IMAGE_BASE64_BYTES = 5 * 1024 * 1024

_DL_IMAGE_URL_RE = re.compile(
    r'(https?://[^\s\)]+/dl/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/([^\s\)\]]+\.(?:png|jpg|jpeg|svg|gif)))',
    re.IGNORECASE,
)

_MARKDOWN_IMAGE_RE = re.compile(
    r'!\[[^\]]*\]\([^\)]*\.(?:png|jpg|jpeg|svg|gif)\)',
    re.IGNORECASE,
)
_GENERATED_CHARTS_RE = re.compile(
    r"Generated charts?:\s*\n*",
    re.IGNORECASE,
)

logger.info("MCP Server: API_BASE=%s", API_BASE)
logger.info("MCP Server: API_KEY=%s", "***configured***" if API_KEY else "NOT SET")


def _headers() -> Dict[str, str]:
    return {
        "X-API-Key": API_KEY,
        "Content-Type": "application/json",
    }


def _safe_json_loads(text: str) -> Optional[Dict]:
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def log_tool_call(
    session_id: Optional[str],
    user_email: Optional[str],
    tool_name: str,
    status: str,
    duration_ms: float,
    error_code: Optional[int] = None,
    extra: Optional[Dict] = None,
) -> None:
    entry = {
        "event": "tool_call",
        "session_id": (session_id or "default")[:12],
        "user": user_email or "anonymous",
        "tool": tool_name,
        "status": status,
        "duration_ms": round(duration_ms, 1),
    }

    if error_code is not None:
        entry["error_code"] = error_code

    if extra:
        entry.update(extra)

    logger.info(json.dumps(entry, default=str))


async def _request_json(
    method: str,
    url: str,
    *,
    timeout: int,
    json_body: Optional[Dict] = None,
    params: Optional[Dict] = None,
) -> httpx.Response:
    logger.info("%s %s", method.upper(), url)

    async with httpx.AsyncClient(timeout=timeout) as client:
        return await client.request(
            method=method.upper(),
            url=url,
            headers=_headers(),
            json=json_body,
            params=params,
        )


async def _fetch_image_base64(file_id: str, filename: str) -> Optional[Dict]:
    from urllib.parse import quote

    encoded_filename = quote(filename)
    internal_url = f"{API_BASE}/dl/{file_id}/{encoded_filename}"

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(internal_url)

            if resp.status_code != 200:
                logger.warning("Image fetch failed: %s -> HTTP %s", internal_url, resp.status_code)
                return None

            if len(resp.content) > MAX_IMAGE_BASE64_BYTES:
                logger.warning(
                    "Image too large for base64: %s (%s bytes)",
                    filename,
                    len(resp.content),
                )
                return None

            content_type = resp.headers.get("content-type", "")
            if "png" in content_type or filename.lower().endswith(".png"):
                mime = "image/png"
            elif "jpeg" in content_type or "jpg" in content_type:
                mime = "image/jpeg"
            elif "svg" in content_type:
                mime = "image/svg+xml"
            else:
                mime = content_type.split(";")[0].strip() or "image/png"

            b64 = base64.b64encode(resp.content).decode("utf-8")
            logger.info(
                "Image base64 encoded: %s (%s bytes -> %s chars, %s)",
                filename,
                len(resp.content),
                len(b64),
                mime,
            )

            return {"type": "image", "data": b64, "mimeType": mime}

    except Exception as e:
        logger.warning("Image base64 fetch failed for %s: %s", filename, e)
        return None


def _extract_image_urls_from_stdout(stdout: str) -> List[Tuple[str, str, str]]:
    matches = _DL_IMAGE_URL_RE.findall(stdout)
    if matches:
        logger.info("Found %s image URL(s) in stdout via regex", len(matches))
    return matches


def _strip_image_markdown_from_text(text: str) -> str:
    cleaned = _MARKDOWN_IMAGE_RE.sub("", text)
    cleaned = _GENERATED_CHARTS_RE.sub("", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned


async def _enrich_blocks_with_images(blocks: list, resp_text: str) -> list:
    data = _safe_json_loads(resp_text)
    if not data:
        return blocks

    inline_images = data.get("inline_images", [])
    download_urls = data.get("download_urls", [])
    stdout = data.get("stdout", "")

    image_blocks = []
    fallback_blocks = []
    images_found = False

    if inline_images:
        logger.info("Path A: %s inline_images in JSON", len(inline_images))
        images_found = True

        file_id_map = {}
        for dl in download_urls:
            if dl.get("is_image"):
                file_id_map[dl.get("filename", "")] = {
                    "file_id": dl.get("file_id", ""),
                    "url": dl.get("url", ""),
                }

        for img in inline_images:
            filename = img.get("filename", "")
            alt_text = img.get("alt_text", "Generated chart")
            dl_info = file_id_map.get(filename, {})
            file_id = dl_info.get("file_id", "")
            public_url = dl_info.get("url", "") or img.get("url", "")

            if file_id:
                block = await _fetch_image_base64(file_id, filename)
                if block:
                    image_blocks.append(block)
                    continue

            if public_url:
                fallback_blocks.append(
                    {
                        "type": "text",
                        "text": f"Chart: {alt_text}\nImage URL: {public_url}",
                    }
                )

    if not images_found and stdout:
        url_matches = _extract_image_urls_from_stdout(stdout)

        if url_matches:
            images_found = True
            for full_url, file_id, filename in url_matches:
                block = await _fetch_image_base64(file_id, filename)
                if block:
                    image_blocks.append(block)
                else:
                    fallback_blocks.append(
                        {
                            "type": "text",
                            "text": f"Chart: {filename}\nImage URL: {full_url}",
                        }
                    )

    if image_blocks and blocks:
        for i, block in enumerate(blocks):
            if block.get("type") == "text":
                original_text = block["text"]
                cleaned_text = _strip_image_markdown_from_text(original_text)
                if cleaned_text != original_text:
                    if cleaned_text:
                        blocks[i] = {"type": "text", "text": cleaned_text}
                    else:
                        blocks[i] = None
                break

        blocks = [b for b in blocks if b is not None]

    if image_blocks or fallback_blocks:
        insert_pos = 0
        for i, block in enumerate(blocks):
            if block.get("type") == "text":
                insert_pos = i + 1
                break

        for j, block in enumerate(image_blocks + fallback_blocks):
            blocks.insert(insert_pos + j, block)

    return blocks


def _build_content_blocks(resp_text: str) -> list:
    data = _safe_json_loads(resp_text)
    if not data:
        return [{"type": "text", "text": resp_text}]

    blocks = []

    stdout = data.get("stdout", "").strip()
    if stdout:
        blocks.append({"type": "text", "text": stdout})

    if not data.get("success", False):
        error_msg = data.get("error_message", "Unknown error")
        error_tb = data.get("error_traceback", "")
        error_text = f"Execution Error: {error_msg}"
        if error_tb:
            if len(error_tb) > 500:
                error_tb = "..." + error_tb[-500:]
            error_text += f"\n\nTraceback:\n{error_tb}"
        blocks.append({"type": "text", "text": error_text})

    download_urls = data.get("download_urls", [])
    non_image_downloads = [d for d in download_urls if not d.get("is_image", False)]
    for info in non_image_downloads:
        filename = info.get("filename", "file")
        url = info.get("url", "")
        size = info.get("size", "")
        if url:
            blocks.append(
                {
                    "type": "text",
                    "text": f"File: {filename} ({size})\nDownload URL: {url}",
                }
            )

    meta_parts = []
    exec_time = data.get("execution_time_ms", 0)
    if exec_time:
        meta_parts.append(f"Execution: {exec_time}ms")

    kernel_info = data.get("kernel_info", {})
    if kernel_info.get("session_persisted"):
        var_count = kernel_info.get("variable_count", 0)
        exec_count = kernel_info.get("execution_count", 0)
        meta_parts.append(f"Session: {var_count} variables persisted (call #{exec_count})")

    if meta_parts:
        blocks.append({"type": "text", "text": " | ".join(meta_parts)})

    if not blocks:
        blocks.append({"type": "text", "text": "Code executed successfully (no output)."})

    logger.info("Built %s content blocks for MCP response", len(blocks))
    return blocks


@mcp.tool()
async def execute_code(
    code: str,
    session_id: str = "default",
    timeout: int = 55,
) -> list:
    """Execute Python in a persistent sandbox. Variables, imports, and files persist across calls."""
    url = f"{API_BASE}/api/execute"
    logger.info(
        "execute_code: POST %s session=%s code_len=%s timeout=%s",
        url,
        session_id,
        len(code),
        timeout,
    )
    started = time.perf_counter()

    try:
        async with httpx.AsyncClient(timeout=timeout + 5) as client:
            resp = await client.post(
                url,
                headers=_headers(),
                json={"code": code, "session_id": session_id, "timeout": timeout},
            )

            data = _safe_json_loads(resp.text) or {}
            exec_success = bool(data.get("success", resp.status_code < 400))

            blocks = _build_content_blocks(resp.text)
            blocks = await _enrich_blocks_with_images(blocks, resp.text)

            log_tool_call(
                session_id=session_id,
                user_email=None,
                tool_name="execute_code",
                status="success" if exec_success else "error",
                duration_ms=(time.perf_counter() - started) * 1000,
                error_code=None if exec_success else resp.status_code,
                extra={
                    "http_status": resp.status_code,
                    "code_len": len(code),
                },
            )
            return blocks

    except Exception as e:
        log_tool_call(
            session_id=session_id,
            user_email=None,
            tool_name="execute_code",
            status="error",
            duration_ms=(time.perf_counter() - started) * 1000,
            extra={
                "exception": type(e).__name__,
                "code_len": len(code),
            },
        )
        logger.error("execute_code: error: %s", e, exc_info=True)
        return [{"type": "text", "text": f"Error calling execute_code API: {e}"}]


@mcp.tool()
async def fetch_from_url(
    url: str,
    filename: Optional[str] = None,
    session_id: str = "default",
) -> list:
    """Download a file from an HTTPS URL into the sandbox."""
    started = time.perf_counter()

    if not filename:
        from urllib.parse import urlparse

        parsed = urlparse(url)
        filename = parsed.path.split("/")[-1].split("?")[0] or "downloaded_file"

    api_url = f"{API_BASE}/api/files/fetch"
    logger.info("fetch_from_url: POST %s filename=%s", api_url, filename)

    try:
        resp = await _request_json(
            "POST",
            api_url,
            timeout=120,
            json_body={"url": url, "filename": filename, "session_id": session_id},
        )

        log_tool_call(
            session_id=session_id,
            user_email=None,
            tool_name="fetch_from_url",
            status="success" if resp.status_code < 400 else "error",
            duration_ms=(time.perf_counter() - started) * 1000,
            error_code=resp.status_code if resp.status_code >= 400 else None,
            extra={
                "filename": filename,
                "url_hash": _hash_text(url),
            },
        )

        if resp.status_code == 200:
            data = resp.json()
            return [
                {
                    "type": "text",
                    "text": (
                        f"File fetched successfully!\n"
                        f"  Filename : {data.get('filename')}\n"
                        f"  Size     : {data.get('size_human')}\n"
                        f"  Path     : {data.get('path')}\n"
                        f"  Session  : {data.get('session_id')}\n"
                        f"  Preview  : {data.get('preview', 'N/A')}\n\n"
                        f"Now call execute_code to work with this file."
                    ),
                }
            ]

        return [{"type": "text", "text": f"fetch_from_url failed (HTTP {resp.status_code}): {resp.text[:300]}"}]

    except Exception as e:
        log_tool_call(
            session_id=session_id,
            user_email=None,
            tool_name="fetch_from_url",
            status="error",
            duration_ms=(time.perf_counter() - started) * 1000,
            extra={
                "exception": type(e).__name__,
                "filename": filename,
                "url_hash": _hash_text(url),
            },
        )
        logger.error("fetch_from_url: error: %s", e, exc_info=True)
        return [{"type": "text", "text": f"fetch_from_url error: {e}"}]


@mcp.tool()
async def upload_file(
    filename: str,
    content_base64: str,
    session_id: str = "default",
) -> str:
    """Upload a base64-encoded file to the sandbox."""
    url = f"{API_BASE}/api/files/upload"
    logger.info("upload_file: POST %s filename=%s", url, filename)
    started = time.perf_counter()

    try:
        resp = await _request_json(
            "POST",
            url,
            timeout=60,
            json_body={
                "filename": filename,
                "content_base64": content_base64,
                "session_id": session_id,
            },
        )

        log_tool_call(
            session_id=session_id,
            user_email=None,
            tool_name="upload_file",
            status="success" if resp.status_code < 400 else "error",
            duration_ms=(time.perf_counter() - started) * 1000,
            error_code=resp.status_code if resp.status_code >= 400 else None,
            extra={"filename": filename},
        )
        return resp.text
    except Exception as e:
        log_tool_call(
            session_id=session_id,
            user_email=None,
            tool_name="upload_file",
            status="error",
            duration_ms=(time.perf_counter() - started) * 1000,
            extra={"exception": type(e).__name__, "filename": filename},
        )
        logger.error("upload_file: error: %s", e, exc_info=True)
        return f"Error calling upload_file API: {e}"


@mcp.tool()
async def fetch_file(
    url: str,
    filename: str,
    session_id: str = "default",
) -> str:
    """Download a file from a URL into the sandbox."""
    api_url = f"{API_BASE}/api/files/fetch"
    logger.info("fetch_file: POST %s filename=%s", api_url, filename)
    started = time.perf_counter()

    try:
        resp = await _request_json(
            "POST",
            api_url,
            timeout=120,
            json_body={"url": url, "filename": filename, "session_id": session_id},
        )
        log_tool_call(
            session_id=session_id,
            user_email=None,
            tool_name="fetch_file",
            status="success" if resp.status_code < 400 else "error",
            duration_ms=(time.perf_counter() - started) * 1000,
            error_code=resp.status_code if resp.status_code >= 400 else None,
            extra={"filename": filename, "url_hash": _hash_text(url)},
        )
        return resp.text
    except Exception as e:
        log_tool_call(
            session_id=session_id,
            user_email=None,
            tool_name="fetch_file",
            status="error",
            duration_ms=(time.perf_counter() - started) * 1000,
            extra={"exception": type(e).__name__, "filename": filename, "url_hash": _hash_text(url)},
        )
        logger.error("fetch_file: error: %s", e, exc_info=True)
        return f"Error calling fetch_file API: {e}"


@mcp.tool()
async def list_files(session_id: Optional[str] = "default") -> str:
    """List files in a sandbox session."""
    url = f"{API_BASE}/api/files"
    logger.info("list_files: GET %s session=%s", url, session_id)
    started = time.perf_counter()

    try:
        resp = await _request_json(
            "GET",
            url,
            timeout=10,
            params={"session_id": session_id},
        )
        log_tool_call(
            session_id=session_id,
            user_email=None,
            tool_name="list_files",
            status="success" if resp.status_code < 400 else "error",
            duration_ms=(time.perf_counter() - started) * 1000,
            error_code=resp.status_code if resp.status_code >= 400 else None,
        )
        return resp.text
    except Exception as e:
        log_tool_call(
            session_id=session_id,
            user_email=None,
            tool_name="list_files",
            status="error",
            duration_ms=(time.perf_counter() - started) * 1000,
            extra={"exception": type(e).__name__},
        )
        logger.error("list_files: error: %s", e, exc_info=True)
        return f"Error calling list_files API: {e}"


@mcp.tool()
async def submit_job(
    code: str,
    session_id: str = "default",
    timeout: int = 600,
) -> str:
    """Submit a long-running job for async execution."""
    url = f"{API_BASE}/api/jobs/submit"
    logger.info("submit_job: POST %s session=%s code_len=%s", url, session_id, len(code))
    started = time.perf_counter()

    try:
        resp = await _request_json(
            "POST",
            url,
            timeout=10,
            json_body={"code": code, "session_id": session_id, "timeout": timeout},
        )
        log_tool_call(
            session_id=session_id,
            user_email=None,
            tool_name="submit_job",
            status="success" if resp.status_code < 400 else "error",
            duration_ms=(time.perf_counter() - started) * 1000,
            error_code=resp.status_code if resp.status_code >= 400 else None,
            extra={"code_len": len(code)},
        )
        return resp.text
    except Exception as e:
        log_tool_call(
            session_id=session_id,
            user_email=None,
            tool_name="submit_job",
            status="error",
            duration_ms=(time.perf_counter() - started) * 1000,
            extra={"exception": type(e).__name__, "code_len": len(code)},
        )
        logger.error("submit_job: error: %s", e, exc_info=True)
        return f"Error calling submit_job API: {e}"


@mcp.tool()
async def get_job_status(job_id: str) -> str:
    """Check async job status."""
    url = f"{API_BASE}/api/jobs/{job_id}/status"
    logger.info("get_job_status: GET %s", url)
    started = time.perf_counter()

    try:
        resp = await _request_json("GET", url, timeout=10)
        log_tool_call(
            session_id="default",
            user_email=None,
            tool_name="get_job_status",
            status="success" if resp.status_code < 400 else "error",
            duration_ms=(time.perf_counter() - started) * 1000,
            error_code=resp.status_code if resp.status_code >= 400 else None,
            extra={"job_id": job_id},
        )
        return resp.text
    except Exception as e:
        log_tool_call(
            session_id="default",
            user_email=None,
            tool_name="get_job_status",
            status="error",
            duration_ms=(time.perf_counter() - started) * 1000,
            extra={"exception": type(e).__name__, "job_id": job_id},
        )
        logger.error("get_job_status: error: %s", e, exc_info=True)
        return f"Error calling get_job_status API: {e}"


@mcp.tool()
async def get_job_result(job_id: str) -> list:
    """Get the full result of a completed job."""
    url = f"{API_BASE}/api/jobs/{job_id}/result"
    logger.info("get_job_result: GET %s", url)
    started = time.perf_counter()

    try:
        resp = await _request_json("GET", url, timeout=30)
        data = _safe_json_loads(resp.text) or {}
        logical_success = bool(data.get("success", resp.status_code < 400))

        blocks = _build_content_blocks(resp.text)
        blocks = await _enrich_blocks_with_images(blocks, resp.text)

        log_tool_call(
            session_id="default",
            user_email=None,
            tool_name="get_job_result",
            status="success" if logical_success else "error",
            duration_ms=(time.perf_counter() - started) * 1000,
            error_code=None if logical_success else resp.status_code,
            extra={"job_id": job_id, "http_status": resp.status_code},
        )
        return blocks
    except Exception as e:
        log_tool_call(
            session_id="default",
            user_email=None,
            tool_name="get_job_result",
            status="error",
            duration_ms=(time.perf_counter() - started) * 1000,
            extra={"exception": type(e).__name__, "job_id": job_id},
        )
        logger.error("get_job_result: error: %s", e, exc_info=True)
        return [{"type": "text", "text": f"Error calling get_job_result API: {e}"}]


@mcp.tool()
async def load_dataset(
    file_path: str,
    dataset_name: str,
    session_id: str = "default",
    delimiter: str = ",",
) -> str:
    """Load a file from sandbox into PostgreSQL."""
    url = f"{API_BASE}/api/data/load-csv"
    logger.info("load_dataset: POST %s dataset=%s", url, dataset_name)
    started = time.perf_counter()

    try:
        resp = await _request_json(
            "POST",
            url,
            timeout=300,
            json_body={
                "file_path": file_path,
                "dataset_name": dataset_name,
                "session_id": session_id,
                "delimiter": delimiter,
            },
        )
        log_tool_call(
            session_id=session_id,
            user_email=None,
            tool_name="load_dataset",
            status="success" if resp.status_code < 400 else "error",
            duration_ms=(time.perf_counter() - started) * 1000,
            error_code=resp.status_code if resp.status_code >= 400 else None,
            extra={"dataset_name": dataset_name, "file_path": file_path},
        )
        return resp.text
    except Exception as e:
        log_tool_call(
            session_id=session_id,
            user_email=None,
            tool_name="load_dataset",
            status="error",
            duration_ms=(time.perf_counter() - started) * 1000,
            extra={"exception": type(e).__name__, "dataset_name": dataset_name, "file_path": file_path},
        )
        logger.error("load_dataset: error: %s", e, exc_info=True)
        return f"Error calling load_dataset API: {e}"


@mcp.tool()
async def query_dataset(
    sql: str,
    limit: int = 1000,
    offset: int = 0,
) -> str:
    """Execute a SQL query against loaded datasets."""
    url = f"{API_BASE}/api/data/query"
    logger.info("query_dataset: POST %s sql_len=%s", url, len(sql))
    started = time.perf_counter()

    try:
        resp = await _request_json(
            "POST",
            url,
            timeout=60,
            json_body={"sql": sql, "limit": limit, "offset": offset},
        )
        log_tool_call(
            session_id="default",
            user_email=None,
            tool_name="query_dataset",
            status="success" if resp.status_code < 400 else "error",
            duration_ms=(time.perf_counter() - started) * 1000,
            error_code=resp.status_code if resp.status_code >= 400 else None,
            extra={"sql_len": len(sql), "limit": limit, "offset": offset},
        )
        return resp.text
    except Exception as e:
        log_tool_call(
            session_id="default",
            user_email=None,
            tool_name="query_dataset",
            status="error",
            duration_ms=(time.perf_counter() - started) * 1000,
            extra={"exception": type(e).__name__, "sql_len": len(sql), "limit": limit, "offset": offset},
        )
        logger.error("query_dataset: error: %s", e, exc_info=True)
        return f"Error calling query_dataset API: {e}"


@mcp.tool()
async def list_datasets(session_id: str = None) -> str:
    """List loaded datasets."""
    params = {}
    if session_id:
        params["session_id"] = session_id

    url = f"{API_BASE}/api/data/datasets"
    logger.info("list_datasets: GET %s session=%s", url, session_id or "default")
    started = time.perf_counter()

    try:
        resp = await _request_json("GET", url, timeout=10, params=params)
        log_tool_call(
            session_id=session_id or "default",
            user_email=None,
            tool_name="list_datasets",
            status="success" if resp.status_code < 400 else "error",
            duration_ms=(time.perf_counter() - started) * 1000,
            error_code=resp.status_code if resp.status_code >= 400 else None,
        )
        return resp.text
    except Exception as e:
        log_tool_call(
            session_id=session_id or "default",
            user_email=None,
            tool_name="list_datasets",
            status="error",
            duration_ms=(time.perf_counter() - started) * 1000,
            extra={"exception": type(e).__name__},
        )
        logger.error("list_datasets: error: %s", e, exc_info=True)
        return f"Error calling list_datasets API: {e}"


@mcp.tool()
async def create_session(
    name: str,
    description: str = "",
) -> str:
    """Create an isolated workspace session."""
    url = f"{API_BASE}/api/sessions"
    logger.info("create_session: POST %s name=%s", url, name)
    started = time.perf_counter()

    try:
        resp = await _request_json(
            "POST",
            url,
            timeout=10,
            json_body={"name": name, "description": description},
        )

        from app.engine.user_tracker import UserTracker

        UserTracker().register_session(name)

        log_tool_call(
            session_id="default",
            user_email=None,
            tool_name="create_session",
            status="success" if resp.status_code < 400 else "error",
            duration_ms=(time.perf_counter() - started) * 1000,
            error_code=resp.status_code if resp.status_code >= 400 else None,
            extra={"session_name": name},
        )
        return resp.text
    except Exception as e:
        log_tool_call(
            session_id="default",
            user_email=None,
            tool_name="create_session",
            status="error",
            duration_ms=(time.perf_counter() - started) * 1000,
            extra={"exception": type(e).__name__, "session_name": name},
        )
        logger.error("create_session: error: %s", e, exc_info=True)
        return f"Error calling create_session API: {e}"


@mcp.tool()
async def delete_session(session_id: str) -> str:
    """Deactivate a session."""
    url = f"{API_BASE}/api/sessions/{session_id}"
    logger.info("delete_session: DELETE %s", url)
    started = time.perf_counter()

    try:
        resp = await _request_json("DELETE", url, timeout=10)
        log_tool_call(
            session_id=session_id,
            user_email=None,
            tool_name="delete_session",
            status="success" if resp.status_code < 400 else "error",
            duration_ms=(time.perf_counter() - started) * 1000,
            error_code=resp.status_code if resp.status_code >= 400 else None,
        )
        return resp.text
    except Exception as e:
        log_tool_call(
            session_id=session_id,
            user_email=None,
            tool_name="delete_session",
            status="error",
            duration_ms=(time.perf_counter() - started) * 1000,
            extra={"exception": type(e).__name__},
        )
        logger.error("delete_session: error: %s", e, exc_info=True)
        return f"Error calling delete_session API: {e}"


try:
    from app.microsoft.bootstrap import init_microsoft_tools

    _ms_auth, _ms_graph = init_microsoft_tools(mcp)
    if _ms_auth:
        logger.info(
            "Microsoft OneDrive + SharePoint integration: ENABLED (4 consolidated + 2 admin tools)"
        )
    else:
        logger.info(
            "Microsoft OneDrive + SharePoint integration: SKIPPED (no Azure credentials)"
        )
except Exception as e:
    logger.error("Microsoft integration failed to initialize: %s", e, exc_info=True)
    logger.info("Continuing with base tools — Microsoft tools unavailable")
    _ms_auth, _ms_graph = None, None
