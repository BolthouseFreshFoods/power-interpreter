"""MCP Tool registrations for OneDrive & SharePoint.

Version: 2.10.1 — user_id REQUIRED on all data-access tools (multi-user safety)

HISTORY:
  v1.9.4: Made user_id optional, added resolve_share_link (22 tools).
  v2.8.6: Version unification across all files.
  v2.9.0: Trimmed all 22 tool descriptions to reduce token overhead.
  v2.9.1: Version string alignment across all modules.
  v2.9.4: Added page_token parameter to all list/search tools.
  v2.9.5: Broadened exception handling in all tools.
  v2.9.6: Consolidated 22 tools into 4 (ms_auth, onedrive,
          sharepoint, resolve_share_link). ~50% token reduction
          in LLM context. 36 total tools -> 18 total tools.
  v2.9.7: Added save_to_sandbox parameter to onedrive and sharepoint
          list/search actions. Writes full JSON to sandbox filesystem,
          returns lightweight summary to LLM.
  v2.9.8: batch_download action for onedrive (up to 25 files per call).
          Clearer download responses with _note confirming file is on disk.
          Prevents LLMs from re-downloading via urllib (401 errors).
  v2.10.1: BREAKING — user_id now REQUIRED on onedrive, sharepoint,
           resolve_share_link. Eliminates cross-user token fallback
           risk when multiple users are authenticated concurrently.
           _resolve_user() is now a strict validator, not a guesser.
           ms_auth still allows optional user_id for 'status' action.
"""

import asyncio
import json
import logging
import os
import glob
import httpx

logger = logging.getLogger(__name__)

# Sandbox base directory
_SANDBOX_DIR = os.getenv("SANDBOX_DIR", "/app/sandbox_data")

# Max files per batch_download call (rate-limit safety)
_MAX_BATCH_SIZE = 25

# Concurrent download limit (avoid Graph API throttling)
_DOWNLOAD_CONCURRENCY = 5


def register_microsoft_tools(mcp, graph_client, auth_manager):
    """Register consolidated Microsoft OneDrive + SharePoint tools."""

    # ── FIX (v2.10.1): Strict user validation — no fallback ─────────
    # Previously, _resolve_user() called get_default_user_id_async()
    # which uses _last_authenticated_user — a global singleton that
    # could return the WRONG user when multiple users are active.
    #
    # Now: user_id is REQUIRED. No guessing. No cross-user risk.
    # ────────────────────────────────────────────────────────────────
    async def _resolve_user(user_id: str = None) -> str:
        """Validate user_id — STRICT, no fallback (v2.10.1).

        v2.10.1: Removed get_default_user_id_async() fallback.
        If user_id is not provided, raise immediately.
        This eliminates the cross-user token risk entirely.
        """
        if user_id and user_id.strip():
            return user_id.strip()
        raise ValueError(
            "user_id is REQUIRED. Pass your Microsoft 365 email "
            "(e.g., user_id='user@bolthousefresh.com'). "
            "Since v2.10.1, auto-resolution is disabled for multi-user safety."
        )

    # ── Legacy fallback for ms_auth 'status' action only ────────────
    async def _resolve_user_optional(user_id: str = None) -> str:
        """Resolve user with fallback — ONLY for ms_auth status action."""
        if user_id and user_id.strip():
            return user_id.strip()
        default = await auth_manager.get_default_user_id_async()
        if default:
            logger.info(f"ms_auth status: auto-resolved user_id to {default}")
            return default
        raise ValueError(
            "No user_id provided and no authenticated user found. "
            "Use ms_auth(action='start', user_id='your@email.com')"
        )

    def _error_response(e: Exception, tool_name: str) -> str:
        """Central error handler. Returns clean JSON for any exception."""
        if isinstance(e, PermissionError):
            logger.warning(f"{tool_name}: auth failed — {e}")
            return json.dumps({
                "error": True, "error_type": "auth_expired",
                "message": str(e),
                "action": "Use ms_auth(action='start', user_id='your@email.com') to re-authenticate.",
            }, indent=2)
        elif isinstance(e, httpx.HTTPStatusError):
            status = e.response.status_code
            body = e.response.text[:300] if e.response.text else ""
            logger.warning(f"{tool_name}: Graph API HTTP {status} — {body}")
            return json.dumps({
                "error": True, "error_type": "graph_api_error",
                "status_code": status,
                "message": f"Microsoft Graph API returned HTTP {status}",
                "detail": body,
                "action": (
                    "Token expired. Use ms_auth(action='start') to re-authenticate."
                    if status == 401 else
                    "Access denied. Check permissions."
                    if status == 403 else
                    "Rate limited. Wait and retry."
                    if status == 429 else
                    f"Graph API error {status}."
                ),
            }, indent=2)
        elif isinstance(e, ValueError):
            return json.dumps({
                "error": True, "error_type": "validation_error",
                "message": str(e),
            }, indent=2)
        else:
            logger.error(f"{tool_name}: unexpected error — {e}", exc_info=True)
            return json.dumps({
                "error": True, "error_type": "unexpected_error",
                "message": str(e),
            }, indent=2)

    def _missing(param: str, action: str) -> str:
        """Return validation error for missing required param."""
        return json.dumps({
            "error": True, "error_type": "validation_error",
            "message": f"'{param}' is required for action '{action}'.",
        }, indent=2)

    def _save_result_to_sandbox(result: dict, session_id: str, prefix: str) -> dict:
        """Write full JSON result to sandbox filesystem, return summary.

        Eliminates LLM transcription bottleneck for large listings.
        The LLM gets a ~200 char summary; execute_code gets the full data.
        """
        sandbox_dir = os.path.join(_SANDBOX_DIR, session_id)
        os.makedirs(sandbox_dir, exist_ok=True)

        # Sequential page numbering based on existing files
        pattern = os.path.join(sandbox_dir, f"{prefix}_*.json")
        existing = sorted(glob.glob(pattern))
        page_num = len(existing) + 1
        filename = f"{prefix}_{page_num}.json"
        filepath = os.path.join(sandbox_dir, filename)

        # Write compact JSON (no indent = smaller file)
        with open(filepath, 'w') as f:
            json.dump(result, f)

        # Build lightweight summary
        items = result.get("items", result.get("files", result.get("value", [])))
        item_count = len(items) if isinstance(items, list) else 0
        next_page = result.get("page_token") or result.get("@odata.nextLink")

        summary = {
            "saved_to": filepath,
            "item_count": item_count,
            "has_more_pages": bool(next_page),
            "next_page_token": next_page,
            "hint": f"Full data saved to sandbox. Use execute_code to read: json.load(open('{filepath}'))",
        }
        if item_count > 0 and isinstance(items, list):
            preview = []
            for item in items[:5]:
                if isinstance(item, dict):
                    preview.append({
                        "name": item.get("name", item.get("displayName", "?")),
                        "id": item.get("id", "")[:12] + "...",
                    })
            summary["preview"] = preview

        return summary

    # ── MS AUTH ──────────────────────────────────────────────────────

    @mcp.tool()
    async def ms_auth(
        action: str,
        user_id: str = None,
    ) -> str:
        """Microsoft 365 authentication (Device Code Flow). Required before OneDrive/SharePoint.

        Args:
            action: 'start' | 'poll' | 'check' | 'status'.
            user_id: Microsoft email. REQUIRED for start/poll/check. Optional for status.
        """
        try:
            if not auth_manager.enabled:
                return json.dumps({
                    "error": True,
                    "message": "Microsoft integration not configured. Set AZURE_TENANT_ID and AZURE_CLIENT_ID.",
                }, indent=2)

            if action == "start":
                if not user_id:
                    return _missing("user_id", "start")
                result = await auth_manager.start_device_login(user_id)
                return json.dumps(result, indent=2)

            elif action == "poll":
                if not user_id:
                    return _missing("user_id", "poll")
                result = await auth_manager.poll_device_login(user_id)
                return json.dumps(result, indent=2)

            elif action == "check":
                if not user_id:
                    return _missing("user_id", "check")
                is_auth = await auth_manager.is_authenticated_async(user_id)
                return json.dumps({
                    "user_id": user_id,
                    "authenticated": is_auth,
                    "message": (
                        f"{user_id} is authenticated."
                        if is_auth else
                        f"{user_id} is NOT authenticated. Use action='start'."
                    ),
                }, indent=2)

            elif action == "status":
                # status is the ONLY action that allows optional user_id
                users = await auth_manager.list_authenticated_users()
                return json.dumps({
                    "authenticated_users": users,
                    "count": len(users),
                }, indent=2)

            else:
                return json.dumps({
                    "error": True,
                    "message": f"Unknown action '{action}'. Valid: start, poll, check, status",
                }, indent=2)

        except Exception as e:
            return _error_response(e, f"ms_auth.{action}")

    # ── ONEDRIVE ─────────────────────────────────────────────────────
    # v2.10.1: user_id is now REQUIRED (no default)

    @mcp.tool()
    async def onedrive(
        action: str,
        user_id: str,
        path: str = "/",
        item_id: str = None,
        query: str = None,
        content_base64: str = None,
        content_type: str = None,
        top: int = 50,
        page_token: str = None,
        save_to_sandbox: bool = False,
        session_id: str = "default",
        file_ids: str = None,
    ) -> str:
        """OneDrive file operations. REQUIRES user_id (Microsoft email).

        Args:
            action: 'list' | 'search' | 'download' | 'upload' | 'batch_download'.
            user_id: Microsoft email (REQUIRED, e.g. user@bolthousefresh.com).
            path: Folder path for list/upload (default '/').
            item_id: File ID for download.
            query: Search query text.
            content_base64: Base64-encoded file content for upload.
            content_type: MIME type for upload.
            top: Max items to return (default 50).
            page_token: Pagination token for next page.
            save_to_sandbox: Save list/search results to sandbox file.
            session_id: Sandbox session ID.
            file_ids: Comma-separated item IDs for batch_download.
        """
        try:
            uid = await _resolve_user(user_id)

            if action == "list":
                result = await graph_client.onedrive_list(uid, path, top, page_token=page_token)
                if save_to_sandbox:
                    summary = _save_result_to_sandbox(result, session_id, "od_list")
                    return json.dumps(summary, indent=2)

            elif action == "search":
                if not query: return _missing("query", action)
                result = await graph_client.onedrive_search(uid, query, top, page_token=page_token)
                if save_to_sandbox:
                    summary = _save_result_to_sandbox(result, session_id, "od_search")
                    return json.dumps(summary, indent=2)

            elif action == "download":
                if not item_id and path == "/":
                    return _missing("item_id or path", action)
                result = await graph_client.onedrive_download(
                    uid, item_id=item_id, path=path if path != "/" else None,
                    save_to_sandbox=True, session_id=session_id)
                # v2.9.8: Make it unmistakable the file is already on disk
                sp = result.get("sandbox_path", "")
                if sp:
                    result["_note"] = (
                        f"FILE ALREADY SAVED to sandbox at: {sp} — "
                        f"Use execute_code: open('{sp}', 'rb') or pd.read_csv('{sp}'). "
                        f"Do NOT re-download via URL (will fail with 401)."
                    )

            elif action == "upload":
                if not path or path == "/": return _missing("path", action)
                if not content_base64: return _missing("content_base64", action)
                result = await graph_client.onedrive_upload(
                    uid, path, content_base64, content_type)

            elif action == "batch_download":
                if not file_ids: return _missing("file_ids", action)
                ids = [fid.strip() for fid in file_ids.split(",") if fid.strip()]
                if len(ids) > _MAX_BATCH_SIZE:
                    return json.dumps({
                        "error": True,
                        "message": f"Max {_MAX_BATCH_SIZE} files per batch. Got {len(ids)}.",
                    }, indent=2)

                sem = asyncio.Semaphore(_DOWNLOAD_CONCURRENCY)
                results_list = []

                async def _dl_one(fid: str):
                    async with sem:
                        try:
                            r = await graph_client.onedrive_download(
                                uid, item_id=fid,
                                save_to_sandbox=True, session_id=session_id)
                            sp = r.get("sandbox_path", "")
                            return {"item_id": fid, "status": "ok",
                                    "sandbox_path": sp,
                                    "name": r.get("name", fid)}
                        except Exception as ex:
                            return {"item_id": fid, "status": "error",
                                    "message": str(ex)}

                tasks = [_dl_one(fid) for fid in ids]
                results_list = await asyncio.gather(*tasks)

                ok = [r for r in results_list if r["status"] == "ok"]
                fail = [r for r in results_list if r["status"] == "error"]

                result = {
                    "total": len(ids),
                    "downloaded": len(ok),
                    "failed": len(fail),
                    "files": list(results_list),
                    "_note": (
                        f"{len(ok)} files saved to sandbox in session '{session_id}'. "
                        f"Use execute_code to read them. Do NOT re-download via URL."
                    ),
                }

            else:
                return json.dumps({
                    "error": True,
                    "message": f"Unknown action '{action}'. Valid: list, search, download, upload, batch_download",
                }, indent=2)

            return json.dumps(result, indent=2)
        except Exception as e:
            return _error_response(e, f"onedrive.{action}")

    # ── SHAREPOINT ───────────────────────────────────────────────────
    # v2.10.1: user_id is now REQUIRED (no default)

    @mcp.tool()
    async def sharepoint(
        action: str,
        user_id: str,
        site_id: str = None,
        drive_id: str = None,
        path: str = None,
        item_id: str = None,
        list_id: str = None,
        query: str = None,
        content_base64: str = None,
        content_type: str = None,
        top: int = 50,
        page_token: str = None,
        save_to_sandbox: bool = False,
        session_id: str = "default",
    ) -> str:
        """SharePoint operations. REQUIRES user_id (Microsoft email).

        Args:
            action: 'list_sites' | 'get_site' | 'list_drives' | 'list_files' | 'download' | 'upload' | 'search' | 'list_lists' | 'list_items'.
            user_id: Microsoft email (REQUIRED, e.g. user@bolthousefresh.com).
            site_id: SharePoint site ID.
            drive_id: Drive ID override.
            path: Folder path.
            item_id: Item/file ID.
            list_id: List ID.
            query: Search query.
            content_base64: Base64-encoded file for upload.
            content_type: MIME type for upload.
            top: Max items (default 50).
            page_token: Pagination token.
            save_to_sandbox: Save list/search results to sandbox file.
            session_id: Sandbox session ID.
        """
        try:
            uid = await _resolve_user(user_id)

            if action == "list_sites":
                result = await graph_client.sharepoint_list_sites(uid, page_token=page_token)
                if save_to_sandbox:
                    summary = _save_result_to_sandbox(result, session_id, "sp_sites")
                    return json.dumps(summary, indent=2)

            elif action == "get_site":
                if not site_id: return _missing("site_id", action)
                result = await graph_client.sharepoint_get_site(uid, site_id)

            elif action == "list_drives":
                if not site_id: return _missing("site_id", action)
                result = await graph_client.sharepoint_list_drives(uid, site_id)

            elif action == "list_files":
                if not site_id: return _missing("site_id", action)
                result = await graph_client.sharepoint_list_files(
                    uid, site_id, drive_id, path or "/", top,
                    page_token=page_token)
                if save_to_sandbox:
                    summary = _save_result_to_sandbox(result, session_id, "sp_list")
                    return json.dumps(summary, indent=2)

            elif action == "download":
                if not site_id: return _missing("site_id", action)
                if not item_id: return _missing("item_id", action)
                result = await graph_client.sharepoint_download(
                    uid, site_id, item_id, drive_id,
                    save_to_sandbox=True, session_id=session_id)
                # v2.9.8: Make it unmistakable the file is already on disk
                sp = result.get("sandbox_path", "")
                if sp:
                    result["_note"] = (
                        f"FILE ALREADY SAVED to sandbox at: {sp} — "
                        f"Use execute_code: open('{sp}', 'rb') or pdfplumber.open('{sp}'). "
                        f"Do NOT re-download via URL (will fail with 401)."
                    )

            elif action == "upload":
                if not site_id: return _missing("site_id", action)
                if not path: return _missing("path", action)
                if not content_base64: return _missing("content_base64", action)
                result = await graph_client.sharepoint_upload(
                    uid, site_id, path, content_base64, drive_id, content_type)

            elif action == "search":
                if not site_id: return _missing("site_id", action)
                if not query: return _missing("query", action)
                result = await graph_client.sharepoint_search(
                    uid, site_id, query, drive_id, top,
                    page_token=page_token)
                if save_to_sandbox:
                    summary = _save_result_to_sandbox(result, session_id, "sp_search")
                    return json.dumps(summary, indent=2)

            elif action == "list_lists":
                if not site_id: return _missing("site_id", action)
                result = await graph_client.sharepoint_list_lists(
                    uid, site_id, page_token=page_token)
                if save_to_sandbox:
                    summary = _save_result_to_sandbox(result, session_id, "sp_lists")
                    return json.dumps(summary, indent=2)

            elif action == "list_items":
                if not site_id: return _missing("site_id", action)
                if not list_id: return _missing("list_id", action)
                result = await graph_client.sharepoint_list_items(
                    uid, site_id, list_id, top,
                    page_token=page_token)
                if save_to_sandbox:
                    summary = _save_result_to_sandbox(result, session_id, "sp_items")
                    return json.dumps(summary, indent=2)

            else:
                return json.dumps({
                    "error": True,
                    "message": f"Unknown action '{action}'. Valid: list_sites, get_site, list_drives, list_files, download, upload, search, list_lists, list_items",
                }, indent=2)

            return json.dumps(result, indent=2)
        except Exception as e:
            return _error_response(e, f"sharepoint.{action}")

    # ── SHARE LINK RESOLVER ────────────────────────────────────────
    # v2.10.1: user_id is now REQUIRED (no default)

    @mcp.tool()
    async def resolve_share_link(
        sharing_url: str,
        user_id: str,
        save_to_sandbox: bool = True,
        session_id: str = "default",
    ) -> str:
        """Resolve SharePoint/OneDrive sharing URL. REQUIRES user_id. Downloads to sandbox by default.

        Args:
            sharing_url: Full sharing URL.
            user_id: Microsoft email (REQUIRED, e.g. user@bolthousefresh.com).
            save_to_sandbox: Download to sandbox (default True).
            session_id: Sandbox session ID.
        """
        try:
            uid = await _resolve_user(user_id)
            result = await graph_client.resolve_share_link(
                uid, sharing_url,
                save_to_sandbox=save_to_sandbox,
                session_id=session_id,
            )
            return json.dumps(result, indent=2)
        except Exception as e:
            return _error_response(e, "resolve_share_link")

    # v2.10.1: user_id required on data-access tools + multi-user safety
    logger.info("Microsoft tools registered (4 consolidated tools, v2.10.1 — user_id required)")
