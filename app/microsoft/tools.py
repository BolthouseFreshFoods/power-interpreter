"""MCP Tool registrations for OneDrive & SharePoint.
Import and call register_microsoft_tools(mcp, graph_client, auth_manager)
from your existing mcp_server.py

v1.9.2: Fixed auth status to check Postgres. Replaced fire-and-forget polling
         with explicit two-step flow (ms_auth_start + ms_auth_poll).
         Added try/except to all auth tools.
v1.9.3: Updated onedrive_download_file and sharepoint_download_file to save
         files directly to sandbox. Added session_id parameter.
         Response now includes sandbox_path instead of bloated base64.
"""

import json
import logging

logger = logging.getLogger(__name__)


def register_microsoft_tools(mcp, graph_client, auth_manager):
    """Register all Microsoft OneDrive + SharePoint tools on the MCP server."""

    # ── AUTH TOOLS ──────────────────────────────────────────────────

    @mcp.tool()
    async def ms_auth_status(user_id: str) -> str:
        """Check Microsoft 365 authentication status for OneDrive/SharePoint access.

        Args:
            user_id: Microsoft email (e.g. tescamilla@bolthousefresh.com)
        """
        try:
            # Full check: memory + Postgres + token refresh
            authenticated = await auth_manager.is_authenticated_async(user_id)
            return json.dumps({
                "authenticated": authenticated,
                "user_id": user_id,
                "message": (
                    "Authenticated and ready for OneDrive/SharePoint."
                    if authenticated
                    else "Not authenticated. Use ms_auth_start to begin sign-in."
                ),
            }, indent=2)
        except Exception as e:
            logger.error(f"ms_auth_status error for {user_id}: {e}", exc_info=True)
            return json.dumps({
                "authenticated": False,
                "user_id": user_id,
                "error": str(e),
                "message": "Error checking auth status. Use ms_auth_start to re-authenticate.",
            }, indent=2)

    @mcp.tool()
    async def ms_auth_start(user_id: str) -> str:
        """Start Microsoft device login for OneDrive/SharePoint access.

        Returns a device code and URL. The user must visit the URL and enter
        the code. After entering the code, call ms_auth_poll to complete login.

        Args:
            user_id: Microsoft email (e.g. tescamilla@bolthousefresh.com)
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
    async def ms_auth_poll(user_id: str) -> str:
        """Complete Microsoft device login after user has entered the code.

        Call this AFTER ms_auth_start, once the user confirms they have
        visited the URL and entered the device code.

        Args:
            user_id: Microsoft email (e.g. tescamilla@bolthousefresh.com)
        """
        try:
            result = await auth_manager.poll_device_login(user_id)
            return json.dumps(result, indent=2)
        except Exception as e:
            logger.error(f"ms_auth_poll error for {user_id}: {e}", exc_info=True)
            return json.dumps({
                "status": "error",
                "message": f"Polling failed: {str(e)}",
            }, indent=2)

    # ── ONEDRIVE TOOLS ──────────────────────────────────────────────

    @mcp.tool()
    async def onedrive_list_files(
        user_id: str,
        path: str = "/",
        top: int = 50,
    ) -> str:
        """List files and folders in OneDrive.

        Args:
            user_id: Microsoft email (e.g. tescamilla@bolthousefresh.com)
            path: Folder path (/ for root, or /Documents/subfolder)
            top: Max items to return (default 50)
        """
        result = await graph_client.onedrive_list(user_id, path, top)
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def onedrive_search(
        user_id: str,
        query: str,
        top: int = 25,
    ) -> str:
        """Search for files in OneDrive by name or content.

        Args:
            user_id: Microsoft email
            query: Search query string
            top: Max results (default 25)
        """
        result = await graph_client.onedrive_search(user_id, query, top)
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def onedrive_download_file(
        user_id: str,
        item_id: str,
        session_id: str = "default",
    ) -> str:
        """Download a file from OneDrive and save it to the sandbox.

        The file is written directly to the sandbox filesystem so that
        execute_code can immediately access it (e.g. pd.read_excel('filename.xlsx')).

        Returns metadata including the sandbox_path where the file was saved.

        Args:
            user_id: Microsoft email
            item_id: The file's OneDrive item ID (from list or search results)
            session_id: Sandbox session ID (default 'default')
        """
        result = await graph_client.onedrive_download(
            user_id, item_id,
            save_to_sandbox=True,
            session_id=session_id,
        )
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def onedrive_upload_file(
        user_id: str,
        path: str,
        content_base64: str,
        content_type: str = "application/octet-stream",
    ) -> str:
        """Upload a file to OneDrive (max 4MB for simple upload).

        Args:
            user_id: Microsoft email
            path: Destination path (e.g. /Documents/report.xlsx)
            content_base64: File content as base64-encoded string
            content_type: MIME type (default application/octet-stream)
        """
        result = await graph_client.onedrive_upload(
            user_id, path, content_base64, content_type
        )
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def onedrive_create_folder(
        user_id: str,
        folder_name: str,
        parent_path: str = "/",
    ) -> str:
        """Create a new folder in OneDrive.

        Args:
            user_id: Microsoft email
            folder_name: Name of the new folder
            parent_path: Parent folder path (/ for root)
        """
        result = await graph_client.onedrive_create_folder(
            user_id, parent_path, folder_name
        )
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def onedrive_delete_item(
        user_id: str,
        item_id: str,
    ) -> str:
        """Delete a file or folder from OneDrive.

        Args:
            user_id: Microsoft email
            item_id: The item's OneDrive ID
        """
        result = await graph_client.onedrive_delete(user_id, item_id)
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def onedrive_move_item(
        user_id: str,
        item_id: str,
        dest_folder_id: str,
        new_name: str = None,
    ) -> str:
        """Move a file or folder to a different OneDrive location.

        Args:
            user_id: Microsoft email
            item_id: Item to move
            dest_folder_id: Destination folder ID
            new_name: Optional new name for the item
        """
        result = await graph_client.onedrive_move(
            user_id, item_id, dest_folder_id, new_name
        )
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def onedrive_copy_item(
        user_id: str,
        item_id: str,
        dest_folder_id: str,
        new_name: str = None,
    ) -> str:
        """Copy a file or folder to a different OneDrive location.

        Args:
            user_id: Microsoft email
            item_id: Item to copy
            dest_folder_id: Destination folder ID
            new_name: Optional new name for the copy
        """
        result = await graph_client.onedrive_copy(
            user_id, item_id, dest_folder_id, new_name
        )
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def onedrive_share_item(
        user_id: str,
        item_id: str,
        share_type: str = "view",
        scope: str = "organization",
    ) -> str:
        """Create a sharing link for a OneDrive file or folder.

        Args:
            user_id: Microsoft email
            item_id: Item to share
            share_type: 'view' or 'edit' (default 'view')
            scope: 'anonymous', 'organization', or 'users' (default 'organization')
        """
        result = await graph_client.onedrive_share(
            user_id, item_id, share_type, scope
        )
        return json.dumps(result, indent=2)

    # ── SHAREPOINT TOOLS ────────────────────────────────────────────

    @mcp.tool()
    async def sharepoint_list_sites(
        user_id: str,
        search: str = None,
    ) -> str:
        """List or search SharePoint sites accessible to the user.

        Args:
            user_id: Microsoft email
            search: Optional search query to filter sites
        """
        result = await graph_client.sharepoint_list_sites(user_id, search)
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def sharepoint_get_site(
        user_id: str,
        site_id: str,
    ) -> str:
        """Get details of a specific SharePoint site.

        Args:
            user_id: Microsoft email
            site_id: SharePoint site ID
        """
        result = await graph_client.sharepoint_get_site(user_id, site_id)
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def sharepoint_list_drives(
        user_id: str,
        site_id: str,
    ) -> str:
        """List document libraries (drives) in a SharePoint site.

        Args:
            user_id: Microsoft email
            site_id: SharePoint site ID
        """
        result = await graph_client.sharepoint_list_drives(user_id, site_id)
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def sharepoint_list_files(
        user_id: str,
        site_id: str,
        drive_id: str = None,
        path: str = "/",
        top: int = 50,
    ) -> str:
        """List files in a SharePoint document library.

        Args:
            user_id: Microsoft email
            site_id: SharePoint site ID
            drive_id: Optional specific drive/library ID
            path: Folder path within the library
            top: Max items (default 50)
        """
        result = await graph_client.sharepoint_list_files(
            user_id, site_id, drive_id, path, top
        )
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def sharepoint_download_file(
        user_id: str,
        site_id: str,
        item_id: str,
        drive_id: str = None,
        session_id: str = "default",
    ) -> str:
        """Download a file from SharePoint and save it to the sandbox.

        The file is written directly to the sandbox filesystem so that
        execute_code can immediately access it.

        Args:
            user_id: Microsoft email
            site_id: SharePoint site ID
            item_id: File item ID
            drive_id: Optional drive ID
            session_id: Sandbox session ID (default 'default')
        """
        result = await graph_client.sharepoint_download(
            user_id, site_id, item_id, drive_id,
            save_to_sandbox=True,
            session_id=session_id,
        )
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def sharepoint_upload_file(
        user_id: str,
        site_id: str,
        path: str,
        content_base64: str,
        drive_id: str = None,
        content_type: str = "application/octet-stream",
    ) -> str:
        """Upload a file to SharePoint (max 4MB simple upload).

        Args:
            user_id: Microsoft email
            site_id: SharePoint site ID
            path: Destination path (e.g. /General/report.xlsx)
            content_base64: File content as base64
            drive_id: Optional drive ID
            content_type: MIME type
        """
        result = await graph_client.sharepoint_upload(
            user_id, site_id, path, content_base64, drive_id, content_type
        )
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def sharepoint_search(
        user_id: str,
        site_id: str,
        query: str,
        drive_id: str = None,
        top: int = 25,
    ) -> str:
        """Search for files within a SharePoint site.

        Args:
            user_id: Microsoft email
            site_id: SharePoint site ID
            query: Search query
            drive_id: Optional drive ID to scope search
            top: Max results (default 25)
        """
        result = await graph_client.sharepoint_search(
            user_id, site_id, query, drive_id, top
        )
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def sharepoint_list_lists(
        user_id: str,
        site_id: str,
    ) -> str:
        """List SharePoint lists in a site.

        Args:
            user_id: Microsoft email
            site_id: SharePoint site ID
        """
        result = await graph_client.sharepoint_list_lists(user_id, site_id)
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def sharepoint_list_items(
        user_id: str,
        site_id: str,
        list_id: str,
        top: int = 50,
    ) -> str:
        """List items in a SharePoint list.

        Args:
            user_id: Microsoft email
            site_id: SharePoint site ID
            list_id: SharePoint list ID
            top: Max items (default 50)
        """
        result = await graph_client.sharepoint_list_items(
            user_id, site_id, list_id, top
        )
        return json.dumps(result, indent=2)

    # v1.9.3: 21 tools (updated download tools to save to sandbox)
    logger.info("Microsoft OneDrive + SharePoint tools registered (21 tools, v1.9.3)")
