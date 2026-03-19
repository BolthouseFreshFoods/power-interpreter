"""Guardrails for the Skills framework.

These are code-level enforcement rules that prevent the exact
failures observed in production:

- urllib.request bypassing onedrive tool
- requests/httpx for direct Graph API calls
- dataframe_to_rows (broken in sandbox)
- stdout flooding from print() loops
- context exhaustion from oversized code blocks

Every check maps to a real incident.
"""

import re
import logging
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

# ── Default blocked imports ──────────────────────────────────
# These are blocked across ALL skills by default.
# Individual skills can extend this list.
DEFAULT_BLOCKED_IMPORTS = [
    "urllib",
    "urllib.request",
    "requests",
    "http.client",
    "httpx",
    "aiohttp",
    "subprocess",
    "socket",
]

# ── Default blocked URL patterns ─────────────────────────────
# Prevents execute_code from reaching Microsoft APIs directly.
DEFAULT_BLOCKED_PATTERNS = [
    r"graph\.microsoft\.com",
    r"login\.microsoftonline\.com",
    r"management\.azure\.com",
    r"service\.flow\.microsoft\.com",
]

# ── Known broken imports ─────────────────────────────────────
# These exist in the stdlib/packages but crash in our sandbox.
KNOWN_BROKEN = {
    "dataframe_to_rows": (
        "dataframe_to_rows is not available in the sandbox. "
        "Use ws.append(list(row)) instead."
    ),
    "PyPDF2": (
        "PyPDF2 is not installed. Use pdfplumber instead."
    ),
}


def check_code_guardrails(
    code: str,
    blocked_imports: Optional[List[str]] = None,
    blocked_patterns: Optional[List[str]] = None,
    max_lines: int = 50,
    max_print_statements: int = 10,
) -> Tuple[bool, Optional[str]]:
    """Check code against guardrails before execution.

    Returns:
        (True, None) if code is safe.
        (False, "reason") if code violates a guardrail.
    """
    imports_to_check = blocked_imports or DEFAULT_BLOCKED_IMPORTS
    patterns_to_check = blocked_patterns or DEFAULT_BLOCKED_PATTERNS

    lines = code.strip().split("\n")

    # ── Line count ───────────────────────────────────────
    if len(lines) > max_lines:
        return False, (
            f"Code exceeds {max_lines} line limit "
            f"({len(lines)} lines). Split into smaller calls."
        )

    # ── Blocked imports ──────────────────────────────────
    for imp in imports_to_check:
        escaped = re.escape(imp)
        if re.search(rf"(?:^|\s)import\s+{escaped}", code, re.MULTILINE):
            return False, f"Blocked import: {imp}"
        if re.search(rf"from\s+{escaped}", code, re.MULTILINE):
            return False, f"Blocked import: from {imp}"

    # ── Blocked URL patterns ─────────────────────────────
    for pattern in patterns_to_check:
        if re.search(pattern, code, re.IGNORECASE):
            return False, (
                f"Blocked URL pattern: {pattern}. "
                f"Use the appropriate MCP tool instead of raw HTTP."
            )

    # ── Known broken imports ─────────────────────────────
    for broken_name, message in KNOWN_BROKEN.items():
        if broken_name in code:
            return False, message

    # ── Excessive print statements ───────────────────────
    print_count = len(re.findall(r"\bprint\s*\(", code))
    if print_count > max_print_statements:
        return False, (
            f"Too many print() calls ({print_count}, "
            f"max {max_print_statements}). "
            f"Only print summaries, not raw data."
        )

    # ── Catch file-to-self copies ────────────────────────
    copy_matches = re.findall(
        r"shutil\.copy2?\s*\(\s*[\"']([^\"']+)[\"']\s*,\s*[\"']([^\"']+)[\"']",
        code,
    )
    for src, dst in copy_matches:
        if src == dst:
            return False, (
                f"Copying file to itself: {src}. "
                f"This is a no-op."
            )

    return True, None
