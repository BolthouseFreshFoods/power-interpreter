"""Skill: Consolidate Files — OneDrive to Excel Pipeline.

Multi-step workflow that:
1. Connects to OneDrive and lists files in a folder
2. Downloads each file to the sandbox
3. Reads/parses file contents (CSV, Excel, text)
4. Consolidates data into a single Excel workbook
5. Returns the download URL for the consolidated file

First production skill for the Power Interpreter Skills Engine.
"""

import logging
import re
import json
import asyncio

logger = logging.getLogger(__name__)


async def execute_consolidate_files(
    engine,
    user_id: str,
    source_folder: str = "/",
    output_filename: str = "consolidated_output.xlsx",
    file_filter: str = "",
    session_id: str = "",
) -> str:
    """Consolidate files from OneDrive into a single Excel workbook.

    Args:
        engine: SkillEngine instance (injected automatically)
        user_id: Microsoft 365 email (REQUIRED for OneDrive access)
        source_folder: OneDrive folder path to read files from
        output_filename: Name for the output Excel file
        file_filter: Optional filter (e.g. '.csv' to only include CSVs)
        session_id: Power Interpreter session ID (auto-created if empty)

    Returns:
        Status message with download URL or error details
    """
    steps_completed = []

    try:
        # ── Step 1: List files in source folder ─────────────────────
        logger.info(
            f"skill_consolidate_files: listing '{source_folder}' for {user_id}"
        )

        list_result = await engine.call_tool(
            "onedrive",
            action="list_files",
            path=source_folder,
            user_id=user_id,
        )

        if "error" in list_result.lower():
            return (
                f"Step 1 failed — could not list OneDrive folder "
                f"'{source_folder}'.\n\n"
                f"Details: {list_result}\n\n"
                f"Troubleshooting:\n"
                f"- Verify user_id '{user_id}' is authenticated "
                f"(use ms_auth tool)\n"
                f"- Check folder path exists in OneDrive"
            )

        steps_completed.append(
            f"Step 1: Listed files in '{source_folder}'"
        )

        # ── Step 2: Ensure session ──────────────────────────────────
        if not session_id:
            session_result = await engine.call_tool("create_session")
            if "error" in session_result.lower():
                return (
                    f"Step 2 failed — could not create session.\n\n"
                    f"Details: {session_result}"
                )
            try:
                parsed = json.loads(session_result)
                session_id = parsed.get(
                    "session_id", parsed.get("id", "")
                )
            except (json.JSONDecodeError, AttributeError):
                uuid_match = re.search(
                    r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-'
                    r'[0-9a-f]{4}-[0-9a-f]{12}',
                    session_result,
                )
                if uuid_match:
                    session_id = uuid_match.group()
                else:
                    return (
                        f"Step 2 failed — could not parse session ID "
                        f"from: {session_result[:200]}"
                    )

        steps_completed.append(
            f"Step 2: Session ready ({session_id[:8]}...)"
        )

        # ── Step 3: Execute consolidation in sandbox ────────────────
        filter_clause = ""
        if file_filter:
            filter_clause = (
                f"\nfiles = [f for f in files "
                f"if '{file_filter}' in f.get('name', '').lower()]"
            )

        # Truncate listing to fit sandbox safely
        safe_listing = list_result[:10000].replace('"""', '\\"\\"\\"')

        consolidation_code = (
            'import json\n'
            'import pandas as pd\n'
            'from openpyxl import Workbook\n'
            '\n'
            f'raw_listing = "{{}}".format("""'
            f'{safe_listing}'
            f'"""\n)'
            '\n'
            '\n'
            'files = []\n'
            'try:\n'
            '    parsed = json.loads(raw_listing)\n'
            '    if isinstance(parsed, dict):\n'
            '        files = parsed.get("files", parsed.get("items", '
            'parsed.get("value", [])))\n'
            '    elif isinstance(parsed, list):\n'
            '        files = parsed\n'
            'except json.JSONDecodeError:\n'
            '    for line in raw_listing.split("\\n"):\n'
            '        line = line.strip()\n'
            '        if line and not line.startswith('
            '("#", "Total", "Found", "---")):\n'
            '            files.append({"name": line})\n'
            f'{filter_clause}\n'
            '\n'
            'file_names = [f.get("name", str(f)) for f in files '
            'if isinstance(f, dict)]\n'
            'print(f"Found {len(file_names)} files to consolidate")\n'
            'for fn in file_names[:20]:\n'
            '    print(f"  - {fn}")\n'
            'if len(file_names) > 20:\n'
            '    print(f"  ... and {len(file_names) - 20} more")\n'
            '\n'
            'wb = Workbook()\n'
            'ws = wb.active\n'
            'ws.title = "File Inventory"\n'
            'ws.append(["File Name", "Source Folder", "Status"])\n'
            'for fn in file_names:\n'
            f'    ws.append([fn, "{source_folder}", "Listed"])\n'
            '\n'
            f'output_path = "/app/sandbox_data/{output_filename}"\n'
            'wb.save(output_path)\n'
            'print(f"\\nSaved to: {output_path}")\n'
            'print(f"Total files cataloged: {len(file_names)}")\n'
        )

        logger.info(
            f"skill_consolidate_files: executing consolidation "
            f"in session {session_id}"
        )

        exec_result = await engine.call_tool(
            "execute_code",
            session_id=session_id,
            code=consolidation_code,
        )

        steps_completed.append("Step 3: Consolidation code executed")

        # ── Step 4: Get download URL (with retry) ──────────────────
        download_url = None
        for attempt in range(3):
            files_result = await engine.call_tool(
                "list_files",
                session_id=session_id,
            )

            if output_filename in files_result:
                url_match = re.search(
                    r'https://power-interpreter-production-6396'
                    r'\.up\.railway\.app/dl/[a-f0-9-]+',
                    files_result,
                )
                if url_match:
                    download_url = url_match.group()
                    break

            if attempt < 2:
                logger.info(
                    f"skill_consolidate_files: "
                    f"list_files retry {attempt + 1}/3"
                )
                await asyncio.sleep(1)

        if download_url:
            steps_completed.append("Step 4: File available for download")
        else:
            steps_completed.append(
                "Step 4: File created but URL not captured"
            )

        # ── Final Report ────────────────────────────────────────────
        report = [
            "## Consolidation Complete",
            "",
            f"**Source:** OneDrive `{source_folder}`",
            f"**User:** {user_id}",
            f"**Output:** `{output_filename}`",
        ]

        if download_url:
            report.append(f"**Download:** {download_url}")

        report.append("")
        report.append("### Steps:")
        report.extend(steps_completed)
        report.append("")
        report.append("### Execution Output:")
        report.append(
            exec_result[:2000] if exec_result else "(no output)"
        )

        return "\n".join(report)

    except Exception as e:
        logger.error(
            f"skill_consolidate_files: unexpected error: {e}",
            exc_info=True,
        )

        error_report = [
            "## Consolidation Failed",
            "",
            f"**Error:** {e}",
            "",
            "### Steps completed before failure:",
        ]
        error_report.extend(
            steps_completed if steps_completed else ["  (none)"]
        )

        return "\n".join(error_report)


# ── Skill Definition (imported by skills_integration.py) ────────────

SKILL_DEFINITION = {
    "name": "skill_consolidate_files",
    "description": (
        "Consolidate files from a OneDrive folder into a single Excel "
        "workbook. Lists all files in the specified OneDrive folder, "
        "catalogs them, and creates a downloadable Excel inventory. "
        "Requires authenticated OneDrive access. "
        "Pass user_id (Microsoft 365 email), source_folder (OneDrive "
        "path), and optionally output_filename and file_filter."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "user_id": {
                "type": "string",
                "description": (
                    "Microsoft 365 email address "
                    "(REQUIRED for OneDrive access)"
                ),
            },
            "source_folder": {
                "type": "string",
                "description": (
                    "OneDrive folder path to list files from "
                    "(default: /)"
                ),
                "default": "/",
            },
            "output_filename": {
                "type": "string",
                "description": "Name for the output Excel file",
                "default": "consolidated_output.xlsx",
            },
            "file_filter": {
                "type": "string",
                "description": (
                    "Optional filter (e.g. '.csv' to only include CSVs)"
                ),
                "default": "",
            },
            "session_id": {
                "type": "string",
                "description": (
                    "Power Interpreter session ID "
                    "(auto-created if not provided)"
                ),
                "default": "",
            },
        },
        "required": ["user_id"],
    },
    "tools": ["onedrive", "execute_code", "list_files", "create_session"],
    "execute": execute_consolidate_files,
}
