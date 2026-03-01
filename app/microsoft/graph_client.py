"""
Microsoft Graph API client for OneDrive and SharePoint operations.
All methods return structured dicts ready for MCP tool responses.

v1.9.3: Added save_to_sandbox parameter to onedrive_download and sharepoint_download.
         Improved _headers error message when refresh fails.
         Added _write_to_sandbox helper for persisting downloaded files to sandbox disk.
"""

import logging
import base64
import os
from typing import Optional, Dict, Any

import httpx

logger = logging.getLogger(__name__)

GRAPH_BASE = "https://graph.microsoft.com/v1.0"


class GraphClient:
    """Async Microsoft Graph API client for OneDrive + SharePoint."""

    def __init__(self, auth_manager):
        self._auth = auth_manager
        self._http = httpx.AsyncClient(timeout=60)

    async def _headers(self, user_id: str) -> Dict[str, str]:
        token = await self._auth.get_access_token(user_id)
        if not token:
            raise PermissionError(
                "Not authenticated. Use ms_auth_start to begin device login. "
                "(Token may have expired and refresh failed — check logs for details.)"
            )
        return {"Authorization": f"Bearer {token}"}

    async def _get(self, user_id: str, path: str, params: dict = None) -> dict:
        headers = await self._headers(user_id)
        resp = await self._http.get(
            f"{GRAPH_BASE}{path}", headers=headers, params=params
        )
        resp.raise_for_status()
        return resp.json()

    async def _get_bytes(self, user_id: str, path: str) -> bytes:
        headers = await self._headers(user_id)
        resp = await self._http.get(
            f"{GRAPH_BASE}{path}", headers=headers, follow_redirects=True
        )
        resp.raise_for_status()
        return resp.content

    async def _post(self, user_id: str, path: str, json_body: dict = None) -> dict:
        headers = await self._headers(user_id)
        resp = await self._http.post(
            f"{GRAPH_BASE}{path}", headers=headers, json=json_body
        )
        resp.raise_for_status()
        return resp.json()

    async def _put_bytes(self, user_id: str, path: str, content: bytes,
                         content_type: str = "application/octet-stream") -> dict:
        headers = await self._headers(user_id)
        headers["Content-Type"] = content_type
        resp = await self._http.put(
            f"{GRAPH_BASE}{path}", headers=headers, content=content
        )
        resp.raise_for_status()
        return resp.json()

    async def _delete(self, user_id: str, path: str) -> bool:
        headers = await self._headers(user_id)
        resp = await self._http.delete(f"{GRAPH_BASE}{path}", headers=headers)
        return resp.status_code == 204

    async def _patch(self, user_id: str, path: str, json_body: dict) -> dict:
        headers = await self._headers(user_id)
        resp = await self._http.patch(
            f"{GRAPH_BASE}{path}", headers=headers, json=json_body
        )
        resp.raise_for_status()
        return resp.json()

    # ═══════════════════════════════════════════════════════════════
    #  SANDBOX FILE BRIDGE (v1.9.3)
    # ═══════════════════════════════════════════════════════════════

    @staticmethod
    def _get_sandbox_dir(session_id: str = "default") -> str:
        """Get the sandbox working directory for a session.
        
        Checks common sandbox directory patterns used by Power Interpreter.
        Falls back to /tmp if nothing else is available.
        """
        # Check environment / settings for sandbox dir
        sandbox_base = os.environ.get("SANDBOX_DIR", "/tmp/sandbox")
        
        # Try session-specific directory first
        session_dir = os.path.join(sandbox_base, session_id)
        if os.path.isdir(session_dir):
            return session_dir
        
        # Try base sandbox dir
        if os.path.isdir(sandbox_base):
            return sandbox_base
        
        # Try common Railway paths
        for candidate in ["/app/sandbox", "/tmp/sandbox", "/tmp"]:
            if os.path.isdir(candidate):
                return candidate
        
        # Last resort: create it
        os.makedirs(sandbox_base, exist_ok=True)
        return sandbox_base

    @staticmethod
    def _write_to_sandbox(filename: str, content: bytes,
                          session_id: str = "default") -> str:
        """Write downloaded file bytes directly to the sandbox filesystem.
        
        Returns the full path where the file was saved.
        
        v1.9.3: This is the critical bridge between OneDrive download
        and the sandbox where execute_code runs.
        """
        sandbox_dir = GraphClient._get_sandbox_dir(session_id)
        filepath = os.path.join(sandbox_dir, filename)
        
        # Ensure directory exists
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        
        with open(filepath, "wb") as f:
            f.write(content)
        
        logger.info(
            f"Sandbox write: {filename} ({len(content):,} bytes) -> {filepath}"
        )
        return filepath

    # ═══════════════════════════════════════════════════════════════
    #  ONEDRIVE
    # ═══════════════════════════════════════════════════════════════

    def _format_item(self, item: dict) -> dict:
        """Normalize a Graph drive item to a clean dict."""
        return {
            "id": item.get("id"),
            "name": item.get("name"),
            "type": "folder" if "folder" in item else "file",
            "size": item.get("size", 0),
            "lastModified": item.get("lastModifiedDateTime"),
            "webUrl": item.get("webUrl"),
            "mimeType": item.get("file", {}).get("mimeType"),
            "parentPath": (
                item.get("parentReference", {}).get("path", "")
                .replace("/drive/root:", "")
            ),
        }

    async def onedrive_list(self, user_id: str, path: str = "/",
                            top: int = 50) -> dict:
        """List files/folders in OneDrive."""
        if path == "/" or not path:
            endpoint = "/me/drive/root/children"
        else:
            clean = path.strip("/")
            endpoint = f"/me/drive/root:/{clean}:/children"

        data = await self._get(user_id, endpoint, {"$top": top})
        items = [self._format_item(i) for i in data.get("value", [])]
        return {"count": len(items), "items": items}

    async def onedrive_search(self, user_id: str, query: str,
                              top: int = 25) -> dict:
        """Search OneDrive files."""
        endpoint = f"/me/drive/root/search(q='{query}')"
        data = await self._get(user_id, endpoint, {"$top": top})
        items = [self._format_item(i) for i in data.get("value", [])]
        return {"count": len(items), "query": query, "items": items}

    async def onedrive_download(self, user_id: str, item_id: str,
                                save_to_sandbox: bool = True,
                                session_id: str = "default") -> dict:
        """Download a file from OneDrive.
        
        v1.9.3: When save_to_sandbox=True (default), writes the file directly
        to the sandbox filesystem so execute_code can access it immediately.
        Returns metadata + sandbox_path instead of bloated base64 in the
        MCP JSON response.
        """
        meta = await self._get(user_id, f"/me/drive/items/{item_id}")
        content = await self._get_bytes(user_id, f"/me/drive/items/{item_id}/content")
        
        filename = meta.get("name", f"download_{item_id}")
        mime_type = meta.get("file", {}).get("mimeType", "application/octet-stream")
        
        result = {
            "name": filename,
            "size": len(content),
            "mimeType": mime_type,
        }
        
        if save_to_sandbox:
            try:
                filepath = self._write_to_sandbox(filename, content, session_id)
                result["sandbox_path"] = filepath
                result["saved_to_sandbox"] = True
                logger.info(
                    f"OneDrive download -> sandbox: {filename} "
                    f"({len(content):,} bytes) at {filepath}"
                )
            except Exception as e:
                logger.error(f"Failed to write {filename} to sandbox: {e}")
                # Fall back to base64 if sandbox write fails
                result["content_base64"] = base64.b64encode(content).decode("utf-8")
                result["saved_to_sandbox"] = False
                result["sandbox_error"] = str(e)
        else:
            result["content_base64"] = base64.b64encode(content).decode("utf-8")
            result["saved_to_sandbox"] = False
        
        return result

    async def onedrive_upload(self, user_id: str, path: str,
                              content_base64: str, content_type: str = None) -> dict:
        """Upload a file to OneDrive (< 4MB simple upload)."""
        clean = path.strip("/")
        endpoint = f"/me/drive/root:/{clean}:/content"
        content = base64.b64decode(content_base64)
        ct = content_type or "application/octet-stream"
        result = await self._put_bytes(user_id, endpoint, content, ct)
        return self._format_item(result)

    async def onedrive_create_folder(self, user_id: str, parent_path: str,
                                     folder_name: str) -> dict:
        """Create a folder in OneDrive."""
        if parent_path == "/" or not parent_path:
            endpoint = "/me/drive/root/children"
        else:
            clean = parent_path.strip("/")
            endpoint = f"/me/drive/root:/{clean}:/children"

        body = {
            "name": folder_name,
            "folder": {},
            "@microsoft.graph.conflictBehavior": "rename",
        }
        result = await self._post(user_id, endpoint, body)
        return self._format_item(result)

    async def onedrive_delete(self, user_id: str, item_id: str) -> dict:
        """Delete a file or folder from OneDrive."""
        success = await self._delete(user_id, f"/me/drive/items/{item_id}")
        return {"deleted": success, "item_id": item_id}

    async def onedrive_move(self, user_id: str, item_id: str,
                            dest_folder_id: str, new_name: str = None) -> dict:
        """Move an item to a different folder."""
        body: Dict[str, Any] = {"parentReference": {"id": dest_folder_id}}
        if new_name:
            body["name"] = new_name
        result = await self._patch(user_id, f"/me/drive/items/{item_id}", body)
        return self._format_item(result)

    async def onedrive_copy(self, user_id: str, item_id: str,
                            dest_folder_id: str, new_name: str = None) -> dict:
        """Copy an item to a different folder."""
        body: Dict[str, Any] = {"parentReference": {"id": dest_folder_id}}
        if new_name:
            body["name"] = new_name
        headers = await self._headers(user_id)
        resp = await self._http.post(
            f"{GRAPH_BASE}/me/drive/items/{item_id}/copy",
            headers=headers, json=body
        )
        return {
            "status": "copy_started",
            "item_id": item_id,
            "monitor_url": resp.headers.get("Location"),
        }

    async def onedrive_share(self, user_id: str, item_id: str,
                             share_type: str = "view",
                             scope: str = "organization") -> dict:
        """Create a sharing link for an item."""
        body = {"type": share_type, "scope": scope}
        result = await self._post(
            user_id, f"/me/drive/items/{item_id}/createLink", body
        )
        link = result.get("link", {})
        return {
            "item_id": item_id,
            "share_url": link.get("webUrl"),
            "type": link.get("type"),
            "scope": link.get("scope"),
        }

    # ═══════════════════════════════════════════════════════════════
    #  SHAREPOINT
    # ═══════════════════════════════════════════════════════════════

    def _format_site(self, site: dict) -> dict:
        return {
            "id": site.get("id"),
            "name": site.get("displayName") or site.get("name"),
            "description": site.get("description"),
            "webUrl": site.get("webUrl"),
        }

    async def sharepoint_list_sites(self, user_id: str,
                                    search: str = None) -> dict:
        """List or search SharePoint sites."""
        if search:
            endpoint = f"/sites?search={search}"
        else:
            endpoint = "/sites?search=*"
        data = await self._get(user_id, endpoint)
        sites = [self._format_site(s) for s in data.get("value", [])]
        return {"count": len(sites), "sites": sites}

    async def sharepoint_get_site(self, user_id: str, site_id: str) -> dict:
        """Get details of a specific SharePoint site."""
        data = await self._get(user_id, f"/sites/{site_id}")
        return self._format_site(data)

    async def sharepoint_list_drives(self, user_id: str, site_id: str) -> dict:
        """List document libraries in a SharePoint site."""
        data = await self._get(user_id, f"/sites/{site_id}/drives")
        drives = [
            {
                "id": d.get("id"),
                "name": d.get("name"),
                "description": d.get("description"),
                "webUrl": d.get("webUrl"),
                "driveType": d.get("driveType"),
            }
            for d in data.get("value", [])
        ]
        return {"count": len(drives), "site_id": site_id, "drives": drives}

    async def sharepoint_list_files(self, user_id: str, site_id: str,
                                    drive_id: str = None, path: str = "/",
                                    top: int = 50) -> dict:
        """List files in a SharePoint document library."""
        if drive_id:
            if path == "/" or not path:
                endpoint = f"/drives/{drive_id}/root/children"
            else:
                clean = path.strip("/")
                endpoint = f"/drives/{drive_id}/root:/{clean}:/children"
        else:
            if path == "/" or not path:
                endpoint = f"/sites/{site_id}/drive/root/children"
            else:
                clean = path.strip("/")
                endpoint = f"/sites/{site_id}/drive/root:/{clean}:/children"

        data = await self._get(user_id, endpoint, {"$top": top})
        items = [self._format_item(i) for i in data.get("value", [])]
        return {"count": len(items), "site_id": site_id, "items": items}

    async def sharepoint_download(self, user_id: str, site_id: str,
                                  item_id: str, drive_id: str = None,
                                  save_to_sandbox: bool = True,
                                  session_id: str = "default") -> dict:
        """Download a file from SharePoint.
        
        v1.9.3: When save_to_sandbox=True (default), writes the file directly
        to the sandbox filesystem so execute_code can access it immediately.
        """
        if drive_id:
            meta_ep = f"/drives/{drive_id}/items/{item_id}"
            content_ep = f"/drives/{drive_id}/items/{item_id}/content"
        else:
            meta_ep = f"/sites/{site_id}/drive/items/{item_id}"
            content_ep = f"/sites/{site_id}/drive/items/{item_id}/content"

        meta = await self._get(user_id, meta_ep)
        content = await self._get_bytes(user_id, content_ep)
        
        filename = meta.get("name", f"download_{item_id}")
        mime_type = meta.get("file", {}).get("mimeType", "application/octet-stream")
        
        result = {
            "name": filename,
            "size": len(content),
            "mimeType": mime_type,
        }
        
        if save_to_sandbox:
            try:
                filepath = self._write_to_sandbox(filename, content, session_id)
                result["sandbox_path"] = filepath
                result["saved_to_sandbox"] = True
                logger.info(
                    f"SharePoint download -> sandbox: {filename} "
                    f"({len(content):,} bytes) at {filepath}"
                )
            except Exception as e:
                logger.error(f"Failed to write {filename} to sandbox: {e}")
                result["content_base64"] = base64.b64encode(content).decode("utf-8")
                result["saved_to_sandbox"] = False
                result["sandbox_error"] = str(e)
        else:
            result["content_base64"] = base64.b64encode(content).decode("utf-8")
            result["saved_to_sandbox"] = False
        
        return result

    async def sharepoint_upload(self, user_id: str, site_id: str,
                                path: str, content_base64: str,
                                drive_id: str = None,
                                content_type: str = None) -> dict:
        """Upload a file to SharePoint (< 4MB simple upload)."""
        clean = path.strip("/")
        if drive_id:
            endpoint = f"/drives/{drive_id}/root:/{clean}:/content"
        else:
            endpoint = f"/sites/{site_id}/drive/root:/{clean}:/content"

        content = base64.b64decode(content_base64)
        ct = content_type or "application/octet-stream"
        result = await self._put_bytes(user_id, endpoint, content, ct)
        return self._format_item(result)

    async def sharepoint_search(self, user_id: str, site_id: str,
                                query: str, drive_id: str = None,
                                top: int = 25) -> dict:
        """Search files within a SharePoint site."""
        if drive_id:
            endpoint = f"/drives/{drive_id}/root/search(q='{query}')"
        else:
            endpoint = f"/sites/{site_id}/drive/root/search(q='{query}')"

        data = await self._get(user_id, endpoint, {"$top": top})
        items = [self._format_item(i) for i in data.get("value", [])]
        return {"count": len(items), "query": query, "items": items}

    async def sharepoint_list_lists(self, user_id: str, site_id: str) -> dict:
        """List SharePoint lists in a site."""
        data = await self._get(user_id, f"/sites/{site_id}/lists")
        lists_data = [
            {
                "id": lst.get("id"),
                "name": lst.get("displayName"),
                "description": lst.get("description"),
                "webUrl": lst.get("webUrl"),
                "template": lst.get("list", {}).get("template"),
            }
            for lst in data.get("value", [])
        ]
        return {"count": len(lists_data), "site_id": site_id, "lists": lists_data}

    async def sharepoint_list_items(self, user_id: str, site_id: str,
                                    list_id: str, top: int = 50) -> dict:
        """List items in a SharePoint list."""
        endpoint = f"/sites/{site_id}/lists/{list_id}/items"
        data = await self._get(user_id, endpoint, {
            "$top": top,
            "$expand": "fields",
        })
        items = [
            {
                "id": i.get("id"),
                "fields": i.get("fields", {}),
                "webUrl": i.get("webUrl"),
                "lastModified": i.get("lastModifiedDateTime"),
            }
            for i in data.get("value", [])
        ]
        return {"count": len(items), "list_id": list_id, "items": items}
