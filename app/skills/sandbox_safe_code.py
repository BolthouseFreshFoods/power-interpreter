"""Skill: Sandbox Safe Code Rules

Returns canonical sandbox-safe coding guidelines for the Power Interpreter
environment. Call this skill before writing code to get up-to-date rules.
"""

import logging

logger = logging.getLogger(__name__)

RULES_MARKDOWN = """\
# Sandbox-Safe Coding Rules for Power Interpreter

## Blocked Libraries (NEVER use these)
- **PyPDF2** — deprecated, not installed. Use `matplotlib.backends.backend_pdf.PdfPages`
- **fpdf / fpdf2** — not installed. Use `matplotlib.backends.backend_pdf.PdfPages`
- **urllib / urllib.request** — use the `fetch_from_url` MCP tool instead
- **requests / httpx / aiohttp** — use MCP tools instead
- **subprocess / socket** — blocked for security

## Code Size Limits
- Max ~50 lines per `execute_code` call
- Max ~2048 characters per `execute_code` call
- Kernel persists variables between calls — chunk large work across multiple calls
- Max ~5000 chars stdout output

## File Paths
- ALWAYS use absolute paths: `/app/sandbox_data/{session_id}/filename`
- NEVER use `/tmp/` or relative paths
- Use `os.makedirs(path, exist_ok=True)` before writing

## PDF Generation
- Use `matplotlib.backends.backend_pdf.PdfPages` (available, works)
- NEVER use PyPDF2, fpdf, or fpdf2

## Excel Writing
- Use `ws.append(list(row))` with openpyxl
- NEVER use `dataframe_to_rows` (broken in sandbox)

## Authentication
- For M365 workflows, ALWAYS use the `ms_auth` MCP tool
- If M365 tools return 400 Bad Request, re-run `ms_auth` first
- Never build custom auth flows

## Error Handling
- NEVER wrap blocked imports in try/except (stripped imports leave empty blocks)
- Check tool results for "error" before proceeding
- Use bounded retries (max 3 attempts)

## Available MCP Tools (use these instead of libraries)
- `onedrive` — file operations (list, download, upload)
- `execute_code` — run Python in sandbox
- `list_files` — list sandbox files + get download URLs
- `ms_auth` — Microsoft authentication
- `fetch_from_url` — download from public URLs
- `create_session` / `delete_session` — session management
"""


async def execute_sandbox_safe_code(engine) -> str:
    """Return sandbox-safe coding rules.

    Args:
        engine: SkillEngine instance (injected automatically, not used)

    Returns:
        Formatted markdown string with all sandbox rules
    """
    logger.info("skill_sandbox_safe_code: returning rules")
    return RULES_MARKDOWN


SKILL_DEFINITION = {
    "name": "skill_sandbox_safe_code",
    "description": (
        "Returns canonical sandbox-safe coding rules and guardrails for the Power "
        "Interpreter environment. Call this skill BEFORE writing code to the sandbox "
        "to get up-to-date rules on: blocked libraries, path requirements, PDF generation, "
        "Excel writing patterns, authentication flows, and available MCP tools. "
        "This prevents common failure modes like PyPDF2 crashes, dataframe_to_rows errors, "
        "and auth bypass issues. No parameters required."
    ),
    "parameters": {
        "type": "object",
        "properties": {},
    },
    "tools": [],
    "execute": execute_sandbox_safe_code,
}
