"""MCP Tool registrations for OneDrive & SharePoint.

Version: 2.9.4 — universal cursor pagination for all list/search tools

HISTORY:
  v1.9.4: Made user_id optional, added resolve_share_link (22 tools).
  v2.8.6: Version unification across all files.
  v2.9.0: Trimmed all 22 tool descriptions to reduce token overhead.
  v2.9.1: Version string alignment across all modules.
  v2.9.4: Added page_token parameter to all list/search tools.
          Enables cursor pagination via @odata.nextLink.
"""

import json
import logging

logger = logging.getLogger(__name__)


def register_microsoft_tools(mcp, graph_client, auth_manager):
    """Register all Microsoft OneDrive + SharePoint tools on the MCP server."""

    async def _resolve_user(user_id: str = None) -> str:
        """Resolve user_id: use provided value or fall back to last authenticated user."""
        if user_id:
            return user_id

        default = await auth_manager.get_default_user_id_async()
        if default:
            logger.info(f"Auto-resolved user_id to: {default}")
            return default

        raise ValueError(
            "No user_id provided and no authenticated user found. "
            "Please authenticate first with ms_auth_start(user_id='your@email.com')"
        )

    # ── AUTH TOOLS ──────────────────────────────────────────────

    @mcp.tool()
    async def ms_auth_status(user_id: str = None) -> str:
        """Check Microsoft 365 authentication status.

        Args:
            user_id: Microsoft email. Optional if already authenticated.
        """
        try:
            uid = await _resolve_user(user_id)
            authenticated = await auth_manager.is_authenticated_async(uid)
            return json.dumps({
                "authenticated": authenticated,
                "user_id": uid,
                "message": (
                    "Authenticated and ready for OneDrive/SharePoint."
                    if authenticated
                    else "Not authenticated. Use ms_auth_start to begin sign-in."
                ),
            }, indent=2)
        except ValueError as e:
            return json.dumps({
                "authenticated": False,
                "error": str(e),
                "message": "No authenticated user. Use ms_auth_start(user_id='your@email.com') first.",
            }, indent=2)
        except Exception as e:
            logger.error(f"ms_auth_status error: {e}", exc_info=True)
            return json.dumps({
                "authenticated": False,
                "error": str(e),
                "message": "Error checking auth status. Use ms_auth_start to re-authenticate.",
            }, indent=2)

    @mcp.tool()
    async def ms_auth_start(user_id: str) -> str:
        """Start Microsoft device login. Returns a code and URL. Call ms_auth_poll after user enters the code.

        Args:
            user_id: Microsoft email (e.g. timothy.escamilla@bolthousefresh.com).
        """
        try:
            result = await auth_manager.start_device_login(user_id)
            return json.dumps(result, indent=2)
        except Exception as e:
            logger.error(f"ms_auth_start error for {user_id}: {e}", exc_info=True)
            return json.dumps({
                "status": "error",
                "message": f"Failed to start Microsoft login: {str(e)}",
            }, indent=2)

    @mcp.tool()
    async def ms_auth_poll(user_id: str = None) -> str:
        """Complete Microsoft device login after user has entered the code from ms_auth_start.

        Args:
            user_id: Microsoft email. Optional if only one pending login.
        """
        try:
            uid = user_id
            if not uid:
                for u, data in auth_manager._tokens.items():
                    if data.get("status") == "pending":
                        uid = u
                        break
            if not uid:
                return json.dumps({
                    "status": "error",
                    "message": "No pending login found. Call ms_auth_start first.",
                }, indent=2)
            result = await auth_manager.poll_device_login(uid)
            return json.dumps(result, indent=2)
        except Exception as e:
            logger.error(f"ms_auth_poll error: {e}", exc_info=True)
            return json.dumps({
                "status": "error",
                "message": f"Polling failed: {str(e)}",
            }, indent=2)

    # ── SHARING URL RESOLVER ─────────────────────────────────────

    @mcp.tool()
    async def resolve_share_link(
        sharing_url: str,
        save_to_sandbox: bool = True,
        user_id: str = None,
        session_id: str = "default",
    ) -> str:
        """Resolve a SharePoint/OneDrive sharing URL to file metadata. Downloads to sandbox by default for immediate use.

        Args:
            sharing_url: Full sharing URL from SharePoint/OneDrive.
            save_to_sandbox: Download file to sandbox (default True).
            user_id: Microsoft email. Optional if already authenticated.
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
        except ValueError as e:
            return json.dumps({
                "error": True,
                "message": str(e),
            }, indent=2)
        except Exception as e:
            logger.error(f"resolve_share_link error: {e}", exc_info=True)
            return json.dumps({
                "error": True,
                "message": f"Failed to resolve sharing link: {str(e)}",
                "sharing_url": sharing_url,
            }, indent=2)

    # ── ONEDRIVE TOOLS ──────────────────────────────────────────

    @mcp.tool()
    async def onedrive_list_files(
        path: str = "/",
        top: int = 50,
        page_token: str = None,
        user_id: str = None,
    ) -> str:
        """List files in a OneDrive folder. Returns has_more and next_page for pagination.

        Args:
            path: Folder path (default root).
            top: Max items per page (default 50, max 200).
            page_token: Pagination cursor from previous response's next_page field.
            user_id: Microsoft email. Optional if already authenticated.
        """
        try:
            uid = await _resolve_user(user_id)
            result = await graph_client.onedrive_list_files(uid, path, top, page_token=page_token)
            return json.dumps(result, indent=2)
        except ValueError as e:
            return json.dumps({"error": True, "message": str(e)}, indent=2)

    @mcp.tool()
    async def onedrive_get_file(
        item_id: str,
        user_id: str = None,
    ) -> str:
        """Get metadata for a specific OneDrive file or folder.

        Args:
            item_id: File/folder item ID.
            user_id: Microsoft email. Optional if already authenticated.
        """
        try:
            uid = await _resolve_user(user_id)
            result = await graph_client.onedrive_get_file(uid, item_id)
            return json.dumps(result, indent=2)
        except ValueError as e:
            return json.dumps({"error": True, "message": str(e)}, indent=2)

    @mcp.tool()
    async def onedrive_download_file(
        item_id: str,
        user_id: str = None,
        session_id: str = "default",
    ) -> str:
        """Download a file from OneDrive to the sandbox for analysis.

        Args:
            item_id: File item ID.
            user_id: Microsoft email. Optional if already authenticated.
            session_id: Sandbox session ID.
        """
        try:
            uid = await _resolve_user(user_id)
            result = await graph_client.onedrive_download(
                uid, item_id, save_to_sandbox=True, session_id=session_id
            )
            return json.dumps(result, indent=2)
        except ValueError as e:
            return json.dumps({"error": True, "message": str(e)}, indent=2)

    @mcp.tool()
    async def onedrive_search(
        query: str,
        top: int = 25,
        page_token: str = None,
        user_id: str = None,
    ) -> str:
        """Search OneDrive files by name or content. Returns has_more and next_page for pagination.

        Args:
            query: Search query.
            top: Max results per page (default 25).
            page_token: Pagination cursor from previous response's next_page field.
            user_id: Microsoft email. Optional if already authenticated.
        """
        try:
            uid = await _resolve_user(user_id)
            result = await graph_client.onedrive_search(uid, query, top, page_token=page_token)
            return json.dumps(result, indent=2)
        except ValueError as e:
            return json.dumps({"error": True, "message": str(e)}, indent=2)

    @mcp.tool()
    async def onedrive_create_folder(
        name: str,
        parent_path: str = None,
        user_id: str = None,
    ) -> str:
        """Create a new folder in OneDrive.

        Args:
            name: Folder name.
            parent_path: Parent folder path (default root).
            user_id: Microsoft email. Optional if already authenticated.
        """
        try:
            uid = await _resolve_user(user_id)
            result = await graph_client.onedrive_create_folder(uid, name, parent_path)
            return json.dumps(result, indent=2)
        except ValueError as e:
            return json.dumps({"error": True, "message": str(e)}, indent=2)

    @mcp.tool()
    async def onedrive_upload_file(
        path: str,
        content_base64: str,
        content_type: str = "application/octet-stream",
        user_id: str = None,
    ) -> str:
        """Upload a file to OneDrive (max 4MB).

        Args:
            path: Destination path (e.g. /Reports/report.xlsx).
            content_base64: File content as base64.
            content_type: MIME type.
            user_id: Microsoft email. Optional if already authenticated.
        """
        try:
            uid = await _resolve_user(user_id)
            result = await graph_client.onedrive_upload(uid, path, content_base64, content_type)
            return json.dumps(result, indent=2)
        except ValueError as e:
            return json.dumps({"error": True, "message": str(e)}, indent=2)

    @mcp.tool()
    async def onedrive_delete_item(
        item_id: str,
        user_id: str = None,
    ) -> str:
        """Delete a OneDrive file or folder.

        Args:
            item_id: Item ID to delete.
            user_id: Microsoft email. Optional if already authenticated.
        """
        try:
            uid = await _resolve_user(user_id)
            result = await graph_client.onedrive_delete(uid, item_id)
            return json.dumps(result, indent=2)
        except ValueError as e:
            return json.dumps({"error": True, "message": str(e)}, indent=2)

    @mcp.tool()
    async def onedrive_move_item(
        item_id: str,
        dest_folder_id: str,
        new_name: str = None,
        user_id: str = None,
    ) -> str:
        """Move a OneDrive item to a different folder.

        Args:
            item_id: Item ID to move.
            dest_folder_id: Destination folder ID.
            new_name: Optional new name.
            user_id: Microsoft email. Optional if already authenticated.
        """
        try:
            uid = await _resolve_user(user_id)
            result = await graph_client.onedrive_move(uid, item_id, dest_folder_id, new_name)
            return json.dumps(result, indent=2)
        except ValueError as e:
            return json.dumps({"error": True, "message": str(e)}, indent=2)

    @mcp.tool()
    async def onedrive_copy_item(
        item_id: str,
        dest_folder_id: str,
        new_name: str = None,
        user_id: str = None,
    ) -> str:
        """Copy a OneDrive item to another folder.

        Args:
            item_id: Item ID to copy.
            dest_folder_id: Destination folder ID.
            new_name: Optional new name for the copy.
            user_id: Microsoft email. Optional if already authenticated.
        """
        try:
            uid = await _resolve_user(user_id)
            result = await graph_client.onedrive_copy(uid, item_id, dest_folder_id, new_name)
            return json.dumps(result, indent=2)
        except ValueError as e:
            return json.dumps({"error": True, "message": str(e)}, indent=2)

    @mcp.tool()
    async def onedrive_share_item(
        item_id: str,
        share_type: str = "view",
        scope: str = "organization",
        user_id: str = None,
    ) -> str:
        """Create a sharing link for a OneDrive item.

        Args:
            item_id: Item ID to share.
            share_type: Link type (view/edit).
            scope: Sharing scope (anonymous/organization).
            user_id: Microsoft email. Optional if already authenticated.
        """
        try:
            uid = await _resolve_user(user_id)
            result = await graph_client.onedrive_share(uid, item_id, share_type, scope)
            return json.dumps(result, indent=2)
        except ValueError as e:
            return json.dumps({"error": True, "message": str(e)}, indent=2)

    # ── SHAREPOINT TOOLS ────────────────────────────────────────

    @mcp.tool()
    async def sharepoint_list_sites(
        search: str = None,
        page_token: str = None,
        user_id: str = None,
    ) -> str:
        """List accessible SharePoint sites. Returns has_more and next_page for pagination.

        Args:
            search: Optional search query to filter sites.
            page_token: Pagination cursor from previous response's next_page field.
            user_id: Microsoft email. Optional if already authenticated.
        """
        try:
            uid = await _resolve_user(user_id)
            result = await graph_client.sharepoint_list_sites(uid, search, page_token=page_token)
            return json.dumps(result, indent=2)
        except ValueError as e:
            return json.dumps({"error": True, "message": str(e)}, indent=2)

    @mcp.tool()
    async def sharepoint_get_site(
        site_id: str,
        user_id: str = None,
    ) -> str:
        """Get details about a specific SharePoint site.

        Args:
            site_id: SharePoint site ID.
            user_id: Microsoft email. Optional if already authenticated.
        """
        try:
            uid = await _resolve_user(user_id)
            result = await graph_client.sharepoint_get_site(uid, site_id)
            return json.dumps(result, indent=2)
        except ValueError as e:
            return json.dumps({"error": True, "message": str(e)}, indent=2)

    @mcp.tool()
    async def sharepoint_list_drives(
        site_id: str,
        user_id: str = None,
    ) -> str:
        """List document libraries in a SharePoint site.

        Args:
            site_id: SharePoint site ID.
            user_id: Microsoft email. Optional if already authenticated.
        """
        try:
            uid = await _resolve_user(user_id)
            result = await graph_client.sharepoint_list_drives(uid, site_id)
            return json.dumps(result, indent=2)
        except ValueError as e:
            return json.dumps({"error": True, "message": str(e)}, indent=2)

    @mcp.tool()
    async def sharepoint_list_files(
        site_id: str,
        drive_id: str = None,
        path: str = "/",
        top: int = 50,
        page_token: str = None,
        user_id: str = None,
    ) -> str:
        """List files in a SharePoint document library. Returns has_more and next_page for pagination.

        Args:
            site_id: SharePoint site ID.
            drive_id: Optional drive/library ID.
            path: Folder path within the library.
            top: Max items per page (default 50).
            page_token: Pagination cursor from previous response's next_page field.
            user_id: Microsoft email. Optional if already authenticated.
        """
        try:
            uid = await _resolve_user(user_id)
            result = await graph_client.sharepoint_list_files(uid, site_id, drive_id, path, top, page_token=page_token)
            return json.dumps(result, indent=2)
        except ValueError as e:
            return json.dumps({"error": True, "message": str(e)}, indent=2)

    @mcp.tool()
    async def sharepoint_download_file(
        site_id: str,
        item_id: str,
        drive_id: str = None,
        user_id: str = None,
        session_id: str = "default",
    ) -> str:
        """Download a file from SharePoint to the sandbox.

        Args:
            site_id: SharePoint site ID.
            item_id: File item ID.
            drive_id: Optional drive ID.
            user_id: Microsoft email. Optional if already authenticated.
            session_id: Sandbox session ID.
        """
        try:
            uid = await _resolve_user(user_id)
            result = await graph_client.sharepoint_download(
                uid, site_id, item_id, drive_id,
                save_to_sandbox=True,
                session_id=session_id,
            )
            return json.dumps(result, indent=2)
        except ValueError as e:
            return json.dumps({"error": True, "message": str(e)}, indent=2)

    @mcp.tool()
    async def sharepoint_upload_file(
        site_id: str,
        path: str,
        content_base64: str,
        drive_id: str = None,
        content_type: str = "application/octet-stream",
        user_id: str = None,
    ) -> str:
        """Upload a file to SharePoint (max 4MB).

        Args:
            site_id: SharePoint site ID.
            path: Destination path (e.g. /General/report.xlsx).
            content_base64: File content as base64.
            drive_id: Optional drive ID.
            content_type: MIME type.
            user_id: Microsoft email. Optional if already authenticated.
        """
        try:
            uid = await _resolve_user(user_id)
            result = await graph_client.sharepoint_upload(
                uid, site_id, path, content_base64, drive_id, content_type
            )
            return json.dumps(result, indent=2)
        except ValueError as e:
            return json.dumps({"error": True, "message": str(e)}, indent=2)

    @mcp.tool()
    async def sharepoint_search(
        site_id: str,
        query: str,
        drive_id: str = None,
        top: int = 25,
        page_token: str = None,
        user_id: str = None,
    ) -> str:
        """Search for files in a SharePoint site. Returns has_more and next_page for pagination.

        Args:
            site_id: SharePoint site ID.
            query: Search query.
            drive_id: Optional drive ID to scope search.
            top: Max results per page (default 25).
            page_token: Pagination cursor from previous response's next_page field.
            user_id: Microsoft email. Optional if already authenticated.
        """
        try:
            uid = await _resolve_user(user_id)
            result = await graph_client.sharepoint_search(uid, site_id, query, drive_id, top, page_token=page_token)
            return json.dumps(result, indent=2)
        except ValueError as e:
            return json.dumps({"error": True, "message": str(e)}, indent=2)

    @mcp.tool()
    async def sharepoint_list_lists(
        site_id: str,
        page_token: str = None,
        user_id: str = None,
    ) -> str:
        """List SharePoint lists in a site. Returns has_more and next_page for pagination.

        Args:
            site_id: SharePoint site ID.
            page_token: Pagination cursor from previous response's next_page field.
            user_id: Microsoft email. Optional if already authenticated.
        """
        try:
            uid = await _resolve_user(user_id)
            result = await graph_client.sharepoint_list_lists(uid, site_id, page_token=page_token)
            return json.dumps(result, indent=2)
        except ValueError as e:
            return json.dumps({"error": True, "message": str(e)}, indent=2)

    @mcp.tool()
    async def sharepoint_list_items(
        site_id: str,
        list_id: str,
        top: int = 50,
        page_token: str = None,
        user_id: str = None,
    ) -> str:
        """List items in a SharePoint list. Returns has_more and next_page for pagination.

        Args:
            site_id: SharePoint site ID.
            list_id: SharePoint list ID.
            top: Max items per page (default 50).
            page_token: Pagination cursor from previous response's next_page field.
            user_id: Microsoft email. Optional if already authenticated.
        """
        try:
            uid = await _resolve_user(user_id)
            result = await graph_client.sharepoint_list_items(uid, site_id, list_id, top, page_token=page_token)
            return json.dumps(result, indent=2)
        except ValueError as e:
            return json.dumps({"error": True, "message": str(e)}, indent=2)

    # v2.9.4: Universal cursor pagination for all list/search tools
    logger.info("Microsoft OneDrive + SharePoint tools registered (22 tools, v2.9.4)")
