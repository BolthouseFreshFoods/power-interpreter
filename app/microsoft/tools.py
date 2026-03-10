"""MCP Tool registrations for OneDrive & SharePoint.

Version: 2.9.8 — batch_download + clearer download responses

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

    async def _resolve_user(user_id: str = None) -> str:
        if user_id:
            return user_id
        default = await auth_manager.get_default_user_id_async()
        if default:
            logger.info(f"Auto-resolved user_id to: {default}")
            return default
        raise ValueError(
            "No user_id provided and no authenticated user found. "
            "Use ms_auth(action='start', user_id='your@email.com')"
        )

    def _error_response(e: Exception, tool_name: str) -> str:
        """Central error handler. Returns clean JSON for any exception."""
        if isinstance(e, PermissionError):
            logger.warning(f"{tool_name}: auth failed \u2014 {e}")
            return json.dumps({
                "error": True, "error_type": "auth_expired",
                "message": str(e),
                "action": "Use ms_auth(action='start', user_id='your@email.com') to re-authenticate.",
            }, indent=2)
        elif isinstance(e, httpx.HTTPStatusError):
            status = e.response.status_code
            body = e.response.text[:300] if e.response.text else ""
            logger.warning(f"{tool_name}: Graph API HTTP {status} \u2014 {body}")
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
            logger.error(f"{tool_name}: unexpected error \u2014 {e}", exc_info=True)
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

        # Extract items from result (handle different key names)
        items = result.get("items", result.get("sites", result.get("lists", [])))
        item_count = len(items) if isinstance(items, list) else 0
        file_size = os.path.getsize(filepath)

        logger.info(
            f"save_to_sandbox: {filename} \u2014 {item_count} items, "
            f"{file_size:,} bytes in {sandbox_dir}"
        )

        return {
            "saved_to_sandbox": True,
            "file": filename,
            "path": filepath,
            "page": page_num,
            "items_count": item_count,
            "file_size_bytes": file_size,
            "has_more": result.get("has_more", False),
            "next_page": result.get("next_page"),
            "tip": f"Use execute_code: data = json.load(open('{filepath}'))"
        }

    # \u2500\u2500 CONSOLIDATED AUTH TOOL \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500

    @mcp.tool()
    async def ms_auth(action: str, user_id: str = None) -> str:
        """Microsoft 365 authentication.

        Args:
            action: status | start | poll
            user_id: Email (e.g. timothy.escamilla@bolthousefresh.com). Required for start.

        Actions:
            status: Check auth state.
            start: Begin device login. Requires user_id.
            poll: Complete login after entering code.

        Note: For admin ops use ms_auth_clear or ms_auth_list_users.
        """
        try:
            if action == "status":
                try:
                    uid = await _resolve_user(user_id)
                    authed = await auth_manager.is_authenticated_async(uid)
                    return json.dumps({
                        "authenticated": authed, "user_id": uid,
                        "message": "Ready." if authed else "Not authenticated. Use ms_auth(action='start').",
                    }, indent=2)
                except ValueError as e:
                    return json.dumps({
                        "authenticated": False, "error": str(e),
                        "message": "Use ms_auth(action='start', user_id='your@email.com').",
                    }, indent=2)

            elif action == "start":
                if not user_id:
                    return _missing("user_id", "start")
                result = await auth_manager.start_device_login(user_id)
                return json.dumps(result, indent=2)

            elif action == "poll":
                uid = user_id
                if not uid:
                    for u, data in auth_manager._tokens.items():
                        if data.get("status") == "pending":
                            uid = u
                            break
                if not uid:
                    return json.dumps({
                        "status": "error",
                        "message": "No pending login. Use ms_auth(action='start') first.",
                    }, indent=2)
                result = await auth_manager.poll_device_login(uid)
                return json.dumps(result, indent=2)

            else:
                return json.dumps({
                    "error": True,
                    "message": f"Unknown action '{action}'. Valid: status, start, poll. For admin: use ms_auth_clear or ms_auth_list_users.",
                }, indent=2)

        except Exception as e:
            return _error_response(e, f"ms_auth.{action}")

    # \u2500\u2500 CONSOLIDATED ONEDRIVE TOOL \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500

    @mcp.tool()
    async def onedrive(
        action: str,
        path: str = None,
        item_id: str = None,
        item_ids: str = None,
        query: str = None,
        name: str = None,
        parent_path: str = None,
        top: int = 50,
        page_token: str = None,
        save_to_sandbox: bool = False,
        content_base64: str = None,
        content_type: str = "application/octet-stream",
        dest_folder_id: str = None,
        new_name: str = None,
        share_type: str = "view",
        scope: str = "organization",
        session_id: str = "default",
        user_id: str = None,
    ) -> str:
        """OneDrive file operations. Paginated results include has_more/next_page.

        Args:
            action: list_files | get_file | download | batch_download | search | create_folder | upload | delete | move | copy | share
            path: Folder path (list_files) or dest path (upload).
            item_id: Item ID (get_file/download/delete/move/copy/share).
            item_ids: JSON array of item IDs for batch_download. Max 25 per call.
            query: Search text (search).
            name: Folder name (create_folder).
            parent_path: Parent folder (create_folder).
            top: Page size, default 50 (list_files/search).
            page_token: Cursor from previous next_page.
            save_to_sandbox: Save full JSON to sandbox for execute_code (list_files/search). Returns summary only.
            content_base64: Base64 file data (upload).
            content_type: MIME type (upload).
            dest_folder_id: Target folder (move/copy).
            new_name: Rename (move/copy).
            share_type: view|edit (share).
            scope: anonymous|organization (share).
            session_id: Sandbox session (download/batch_download/save_to_sandbox).
            user_id: Microsoft email. Optional if authenticated.

        batch_download: Downloads up to 25 files in parallel (5 concurrent).
            Files are saved directly to sandbox. Returns summary with paths.
            Use execute_code to process files after download.
        """
        try:
            uid = await _resolve_user(user_id)

            if action == "list_files":
                result = await graph_client.onedrive_list_files(
                    uid, path or "/", top, page_token=page_token)
                if save_to_sandbox:
                    summary = _save_result_to_sandbox(result, session_id, "onedrive_list")
                    return json.dumps(summary, indent=2)

            elif action == "get_file":
                if not item_id: return _missing("item_id", action)
                result = await graph_client.onedrive_get_file(uid, item_id)

            elif action == "download":
                if not item_id: return _missing("item_id", action)
                result = await graph_client.onedrive_download(
                    uid, item_id, save_to_sandbox=True, session_id=session_id)
                # v2.9.8: Make it unmistakable the file is already on disk
                sp = result.get("sandbox_path", "")
                if sp:
                    result["_note"] = (
                        f"FILE ALREADY SAVED to sandbox at: {sp} \u2014 "
                        f"Use execute_code: open('{sp}', 'rb') or pdfplumber.open('{sp}'). "
                        f"Do NOT re-download via URL (will fail with 401)."
                    )

            elif action == "batch_download":
                # v2.9.8: Download up to 25 files in one tool call
                if not item_ids:
                    return _missing("item_ids", action)
                try:
                    ids = json.loads(item_ids) if isinstance(item_ids, str) else item_ids
                except (json.JSONDecodeError, TypeError):
                    return json.dumps({
                        "error": True, "error_type": "validation_error",
                        "message": "item_ids must be a JSON array of ID strings, e.g. '[\"id1\", \"id2\"]'",
                    }, indent=2)

                if not isinstance(ids, list) or len(ids) == 0:
                    return json.dumps({
                        "error": True, "error_type": "validation_error",
                        "message": "item_ids must be a non-empty JSON array.",
                    }, indent=2)

                if len(ids) > _MAX_BATCH_SIZE:
                    return json.dumps({
                        "error": True, "error_type": "validation_error",
                        "message": f"Max {_MAX_BATCH_SIZE} files per batch. Got {len(ids)}. Split into multiple calls.",
                    }, indent=2)

                semaphore = asyncio.Semaphore(_DOWNLOAD_CONCURRENCY)

                async def _download_one(iid: str) -> dict:
                    async with semaphore:
                        try:
                            r = await graph_client.onedrive_download(
                                uid, iid, save_to_sandbox=True, session_id=session_id)
                            return {
                                "item_id": iid,
                                "status": "ok",
                                "filename": r.get("filename", ""),
                                "sandbox_path": r.get("sandbox_path", ""),
                                "size": r.get("size", r.get("file_size", 0)),
                            }
                        except Exception as ex:
                            logger.warning(f"batch_download: {iid} failed \u2014 {ex}")
                            return {
                                "item_id": iid,
                                "status": "failed",
                                "error": str(ex)[:200],
                            }

                tasks = [_download_one(iid) for iid in ids]
                results = await asyncio.gather(*tasks)

                succeeded = [r for r in results if r["status"] == "ok"]
                failed = [r for r in results if r["status"] == "failed"]

                logger.info(
                    f"batch_download: {len(succeeded)}/{len(ids)} succeeded, "
                    f"{len(failed)} failed, session={session_id}"
                )

                return json.dumps({
                    "batch_download": True,
                    "total": len(ids),
                    "succeeded": len(succeeded),
                    "failed": len(failed),
                    "files": list(results),
                    "_note": (
                        f"{len(succeeded)} files saved to sandbox. "
                        f"Process with execute_code: pdfplumber.open(sandbox_path) or open(sandbox_path, 'rb'). "
                        f"Do NOT re-download via URLs."
                    ),
                }, indent=2)

            elif action == "search":
                if not query: return _missing("query", action)
                result = await graph_client.onedrive_search(
                    uid, query, top, page_token=page_token)
                if save_to_sandbox:
                    summary = _save_result_to_sandbox(result, session_id, "onedrive_search")
                    return json.dumps(summary, indent=2)

            elif action == "create_folder":
                if not name: return _missing("name", action)
                result = await graph_client.onedrive_create_folder(
                    uid, name, parent_path)

            elif action == "upload":
                if not path: return _missing("path", action)
                if not content_base64: return _missing("content_base64", action)
                result = await graph_client.onedrive_upload(
                    uid, path, content_base64, content_type)

            elif action == "delete":
                if not item_id: return _missing("item_id", action)
                result = await graph_client.onedrive_delete(uid, item_id)

            elif action == "move":
                if not item_id: return _missing("item_id", action)
                if not dest_folder_id: return _missing("dest_folder_id", action)
                result = await graph_client.onedrive_move(
                    uid, item_id, dest_folder_id, new_name)

            elif action == "copy":
                if not item_id: return _missing("item_id", action)
                if not dest_folder_id: return _missing("dest_folder_id", action)
                result = await graph_client.onedrive_copy(
                    uid, item_id, dest_folder_id, new_name)

            elif action == "share":
                if not item_id: return _missing("item_id", action)
                result = await graph_client.onedrive_share(
                    uid, item_id, share_type, scope)

            else:
                return json.dumps({
                    "error": True,
                    "message": f"Unknown action '{action}'. Valid: list_files, get_file, download, batch_download, search, create_folder, upload, delete, move, copy, share",
                }, indent=2)

            return json.dumps(result, indent=2)
        except Exception as e:
            return _error_response(e, f"onedrive.{action}")

    # \u2500\u2500 CONSOLIDATED SHAREPOINT TOOL \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500

    @mcp.tool()
    async def sharepoint(
        action: str,
        site_id: str = None,
        drive_id: str = None,
        list_id: str = None,
        item_id: str = None,
        path: str = None,
        query: str = None,
        search: str = None,
        top: int = 50,
        page_token: str = None,
        save_to_sandbox: bool = False,
        content_base64: str = None,
        content_type: str = "application/octet-stream",
        session_id: str = "default",
        user_id: str = None,
    ) -> str:
        """SharePoint operations. Paginated results include has_more/next_page.

        Args:
            action: list_sites | get_site | list_drives | list_files | download | upload | search | list_lists | list_items
            site_id: Site ID (required for most actions).
            drive_id: Document library ID.
            list_id: List ID (list_items).
            item_id: File item ID (download).
            path: Folder path (list_files) or dest path (upload).
            query: Search text (search).
            search: Filter text (list_sites).
            top: Page size, default 50.
            page_token: Cursor from previous next_page.
            save_to_sandbox: Save full JSON to sandbox for execute_code. Returns summary only.
            content_base64: Base64 file data (upload).
            content_type: MIME type (upload).
            session_id: Sandbox session (download/save_to_sandbox).
            user_id: Microsoft email. Optional if authenticated.
        """
        try:
            uid = await _resolve_user(user_id)

            if action == "list_sites":
                result = await graph_client.sharepoint_list_sites(
                    uid, search, page_token=page_token)
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
                        f"FILE ALREADY SAVED to sandbox at: {sp} \u2014 "
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

    # \u2500\u2500 SHARE LINK RESOLVER \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500

    @mcp.tool()
    async def resolve_share_link(
        sharing_url: str,
        save_to_sandbox: bool = True,
        user_id: str = None,
        session_id: str = "default",
    ) -> str:
        """Resolve SharePoint/OneDrive sharing URL. Downloads to sandbox by default.

        Args:
            sharing_url: Full sharing URL.
            save_to_sandbox: Download to sandbox (default True).
            user_id: Microsoft email. Optional if authenticated.
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

    # v2.9.8: batch_download + clear download responses
    logger.info("Microsoft tools registered (4 consolidated tools, v2.9.8)")
