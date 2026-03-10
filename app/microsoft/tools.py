"""MCP Tool registrations for OneDrive & SharePoint.

Version: 2.9.6 — consolidated tools for token optimization

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
          Descriptions trimmed for minimal token footprint.
"""

import json
import logging
import httpx

logger = logging.getLogger(__name__)


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
        query: str = None,
        name: str = None,
        parent_path: str = None,
        top: int = 50,
        page_token: str = None,
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
            action: list_files | get_file | download | search | create_folder | upload | delete | move | copy | share
            path: Folder path (list_files) or dest path (upload).
            item_id: Item ID (get_file/download/delete/move/copy/share).
            query: Search text (search).
            name: Folder name (create_folder).
            parent_path: Parent folder (create_folder).
            top: Page size, default 50 (list_files/search).
            page_token: Cursor from previous next_page.
            content_base64: Base64 file data (upload).
            content_type: MIME type (upload).
            dest_folder_id: Target folder (move/copy).
            new_name: Rename (move/copy).
            share_type: view|edit (share).
            scope: anonymous|organization (share).
            session_id: Sandbox session (download).
            user_id: Microsoft email. Optional if authenticated.
        """
        try:
            uid = await _resolve_user(user_id)

            if action == "list_files":
                result = await graph_client.onedrive_list_files(
                    uid, path or "/", top, page_token=page_token)

            elif action == "get_file":
                if not item_id: return _missing("item_id", action)
                result = await graph_client.onedrive_get_file(uid, item_id)

            elif action == "download":
                if not item_id: return _missing("item_id", action)
                result = await graph_client.onedrive_download(
                    uid, item_id, save_to_sandbox=True, session_id=session_id)

            elif action == "search":
                if not query: return _missing("query", action)
                result = await graph_client.onedrive_search(
                    uid, query, top, page_token=page_token)

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
                    "message": f"Unknown action '{action}'. Valid: list_files, get_file, download, search, create_folder, upload, delete, move, copy, share",
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
            content_base64: Base64 file data (upload).
            content_type: MIME type (upload).
            session_id: Sandbox session (download).
            user_id: Microsoft email. Optional if authenticated.
        """
        try:
            uid = await _resolve_user(user_id)

            if action == "list_sites":
                result = await graph_client.sharepoint_list_sites(
                    uid, search, page_token=page_token)

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

            elif action == "download":
                if not site_id: return _missing("site_id", action)
                if not item_id: return _missing("item_id", action)
                result = await graph_client.sharepoint_download(
                    uid, site_id, item_id, drive_id,
                    save_to_sandbox=True, session_id=session_id)

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

            elif action == "list_lists":
                if not site_id: return _missing("site_id", action)
                result = await graph_client.sharepoint_list_lists(
                    uid, site_id, page_token=page_token)

            elif action == "list_items":
                if not site_id: return _missing("site_id", action)
                if not list_id: return _missing("list_id", action)
                result = await graph_client.sharepoint_list_items(
                    uid, site_id, list_id, top,
                    page_token=page_token)

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

    # v2.9.6: Consolidated 22 tools -> 4 tools for ~50% token reduction
    logger.info("Microsoft tools registered (4 consolidated tools, v2.9.6)")
