"""Microsoft Graph API client for OneDrive and SharePoint operations.
All methods return structured dicts ready for MCP tool responses.

v1.9.3: Added save_to_sandbox parameter to onedrive_download and sharepoint_download.
v1.9.3a: Fixed sandbox path resolution for Railway (/app/sandbox_data).
v1.9.4: Added resolve_share_link() for SharePoint/OneDrive sharing URLs.
v2.9.4: Universal cursor pagination for all list/search endpoints.
         Added _get_url, _add_pagination, page_token support.
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
                "(Token may have expired and refresh failed.)")
        return {"Authorization": f"Bearer {token}"}

    async def _get(self, user_id, path, params=None):
        headers = await self._headers(user_id)
        resp = await self._http.get(f"{GRAPH_BASE}{path}", headers=headers, params=params)
        resp.raise_for_status()
        return resp.json()

    async def _get_url(self, user_id, url, params=None):
        """Fetch a full URL directly (for @odata.nextLink pagination).

        v2.9.4: Used by paginated methods when page_token is provided.
        The nextLink from Microsoft Graph is a complete URL including
        query parameters, so we hit it directly instead of building one.
        """
        headers = await self._headers(user_id)
        resp = await self._http.get(url, headers=headers, params=params)
        resp.raise_for_status()
        return resp.json()

    async def _get_bytes(self, user_id, path):
        headers = await self._headers(user_id)
        resp = await self._http.get(f"{GRAPH_BASE}{path}", headers=headers, follow_redirects=True)
        resp.raise_for_status()
        return resp.content

    async def _post(self, user_id, path, json_body=None):
        headers = await self._headers(user_id)
        resp = await self._http.post(f"{GRAPH_BASE}{path}", headers=headers, json=json_body)
        resp.raise_for_status()
        return resp.json()

    async def _put_bytes(self, user_id, path, content, content_type="application/octet-stream"):
        headers = await self._headers(user_id)
        headers["Content-Type"] = content_type
        resp = await self._http.put(f"{GRAPH_BASE}{path}", headers=headers, content=content)
        resp.raise_for_status()
        return resp.json()

    async def _delete(self, user_id, path):
        headers = await self._headers(user_id)
        resp = await self._http.delete(f"{GRAPH_BASE}{path}", headers=headers)
        return resp.status_code == 204

    async def _patch(self, user_id, path, json_body):
        headers = await self._headers(user_id)
        resp = await self._http.patch(f"{GRAPH_BASE}{path}", headers=headers, json=json_body)
        resp.raise_for_status()
        return resp.json()

    # ── PAGINATION HELPER (v2.9.4) ─────────────────────────────────

    def _add_pagination(self, result: dict, data: dict) -> dict:
        """Add universal pagination fields from Graph API response.

        Extracts @odata.nextLink and @odata.count from the raw
        Graph API response and adds has_more / next_page / total_count
        to the result dict. Every list/search method calls this.

        The LLM sees:
          "has_more": true,
          "next_page": "https://graph.microsoft.com/v1.0/..."

        And knows to call the tool again with page_token=next_page.
        """
        next_link = data.get("@odata.nextLink")
        result["has_more"] = next_link is not None
        if next_link:
            result["next_page"] = next_link
        total = data.get("@odata.count")
        if total is not None:
            result["total_count"] = total
        return result

    # ── SHARING URL RESOLVER (v1.9.4) ──────────────────────────────

    @staticmethod
    def _encode_sharing_url(sharing_url: str) -> str:
        """Encode a sharing URL for the Microsoft Graph /shares/ API.
        See: https://learn.microsoft.com/en-us/graph/api/shares-get"""
        encoded = base64.urlsafe_b64encode(sharing_url.encode("utf-8")).decode("utf-8")
        return f"u!{encoded.rstrip('=')}"

    async def resolve_share_link(self, user_id, sharing_url, save_to_sandbox=False, session_id="default"):
        """Resolve a SharePoint/OneDrive sharing URL to item metadata.
        Optionally downloads the file directly to the sandbox."""
        encoded = self._encode_sharing_url(sharing_url)
        try:
            item = await self._get(user_id, f"/shares/{encoded}/driveItem")
        except httpx.HTTPStatusError as e:
            logger.error(f"resolve_share_link failed: {e.response.status_code} {e.response.text[:300]}")
            return {"error": True, "status_code": e.response.status_code,
                    "message": f"Failed to resolve sharing link: {e.response.text[:200]}",
                    "sharing_url": sharing_url}
        result = self._format_item(item)
        result["sharing_url"] = sharing_url
        result["driveId"] = item.get("parentReference", {}).get("driveId")
        result["siteId"] = item.get("parentReference", {}).get("siteId")
        if save_to_sandbox and result.get("type") == "file":
            try:
                content = await self._get_bytes(user_id, f"/shares/{encoded}/driveItem/content")
                filename = result.get("name", "downloaded_file")
                filepath = self._write_to_sandbox(filename, content, session_id)
                result["sandbox_path"] = filepath
                result["saved_to_sandbox"] = True
                result["downloaded_size"] = len(content)
                result["message"] = f"File '{filename}' resolved and saved to sandbox. Use: pd.read_excel('{filename}')"
                logger.info(f"Share link -> sandbox: {filename} ({len(content):,} bytes)")
            except Exception as e:
                logger.error(f"Failed to download from share link: {e}", exc_info=True)
                result["saved_to_sandbox"] = False
                result["download_error"] = str(e)
                result["message"] = f"Resolved metadata but download failed: {e}. Try onedrive_download_file with item_id='{result.get('id')}'"
        return result

    # ── SANDBOX FILE BRIDGE ────────────────────────────────────────

    @staticmethod
    def _get_sandbox_dir(session_id="default"):
        sandbox_base = os.environ.get("SANDBOX_DIR", "")
        if not sandbox_base:
            for c in ["/app/sandbox_data", "/app/sandbox", "/tmp/sandbox"]:
                if os.path.isdir(c):
                    sandbox_base = c
                    break
            if not sandbox_base:
                sandbox_base = "/app/sandbox_data"
        if session_id and session_id != "default":
            sd = os.path.join(sandbox_base, session_id)
            if os.path.isdir(sd):
                return sd
        os.makedirs(sandbox_base, exist_ok=True)
        return sandbox_base

    @staticmethod
    def _write_to_sandbox(filename, content, session_id="default"):
        sandbox_dir = GraphClient._get_sandbox_dir(session_id)
        filepath = os.path.join(sandbox_dir, filename)
        with open(filepath, "wb") as f:
            f.write(content)
        logger.info(f"Sandbox write: {filepath} ({len(content):,} bytes)")
        return filepath

    # ── ITEM FORMATTER ───────────────────────────────────────────

    @staticmethod
    def _format_item(item):
        """Format a Graph drive item into a clean dict."""
        result = {
            "id": item.get("id"),
            "name": item.get("name"),
            "type": "folder" if "folder" in item else "file",
            "size": item.get("size", 0),
            "lastModified": item.get("lastModifiedDateTime"),
            "webUrl": item.get("webUrl"),
        }
        if "file" in item:
            result["mimeType"] = item["file"].get("mimeType")
        if "folder" in item:
            result["childCount"] = item["folder"].get("childCount", 0)
        parent = item.get("parentReference", {})
        if parent:
            result["parentPath"] = parent.get("path", "")
        return result

    # ── ONEDRIVE ───────────────────────────────────────────────

    async def onedrive_list_files(self, user_id, path="/", top=50, page_token=None):
        if page_token:
            data = await self._get_url(user_id, page_token)
        else:
            ep = "/me/drive/root/children" if path == "/" or not path else f"/me/drive/root:/{path.strip('/')}:/children"
            data = await self._get(user_id, ep, {"$top": top})
        result = {"count": len(data.get("value", [])), "items": [self._format_item(i) for i in data.get("value", [])]}
        return self._add_pagination(result, data)

    async def onedrive_get_file(self, user_id, item_id):
        return self._format_item(await self._get(user_id, f"/me/drive/items/{item_id}"))

    async def onedrive_download(self, user_id, item_id, save_to_sandbox=True, session_id="default"):
        meta = await self._get(user_id, f"/me/drive/items/{item_id}")
        content = await self._get_bytes(user_id, f"/me/drive/items/{item_id}/content")
        filename = meta.get("name", f"download_{item_id}")
        result = {
            "name": filename,
            "size": len(content),
            "mimeType": meta.get("file", {}).get("mimeType", "application/octet-stream"),
        }
        if save_to_sandbox:
            try:
                fp = self._write_to_sandbox(filename, content, session_id)
                result.update({
                    "sandbox_path": fp,
                    "saved_to_sandbox": True,
                    "message": f"File '{filename}' saved to sandbox. Use in execute_code with path: '{fp}'",
                })
            except Exception as e:
                logger.error(f"Sandbox write failed: {e}", exc_info=True)
                result.update({
                    "content_base64": base64.b64encode(content).decode(),
                    "saved_to_sandbox": False,
                    "sandbox_error": str(e),
                })
        else:
            result.update({
                "content_base64": base64.b64encode(content).decode(),
                "saved_to_sandbox": False,
            })
        return result

    async def onedrive_search(self, user_id, query, top=25, page_token=None):
        if page_token:
            data = await self._get_url(user_id, page_token)
        else:
            data = await self._get(user_id, f"/me/drive/root/search(q='{query}')", {"$top": top})
        result = {"count": len(data.get("value", [])), "query": query, "items": [self._format_item(i) for i in data.get("value", [])]}
        return self._add_pagination(result, data)

    async def onedrive_create_folder(self, user_id, name, parent_path=None):
        ep = "/me/drive/root/children" if not parent_path or parent_path == "/" else f"/me/drive/root:/{parent_path.strip('/')}:/children"
        body = {"name": name, "folder": {}, "@microsoft.graph.conflictBehavior": "rename"}
        return self._format_item(await self._post(user_id, ep, body))

    async def onedrive_upload(self, user_id, path, content_base64, content_type="application/octet-stream"):
        clean = path.strip("/")
        ep = f"/me/drive/root:/{clean}:/content"
        return self._format_item(await self._put_bytes(user_id, ep, base64.b64decode(content_base64), content_type))

    async def onedrive_delete(self, user_id, item_id):
        deleted = await self._delete(user_id, f"/me/drive/items/{item_id}")
        return {"deleted": deleted, "item_id": item_id}

    async def onedrive_move(self, user_id, item_id, dest_folder_id, new_name=None):
        body: Dict[str, Any] = {"parentReference": {"id": dest_folder_id}}
        if new_name: body["name"] = new_name
        return self._format_item(await self._patch(user_id, f"/me/drive/items/{item_id}", body))

    async def onedrive_copy(self, user_id, item_id, dest_folder_id, new_name=None):
        body: Dict[str, Any] = {"parentReference": {"id": dest_folder_id}}
        if new_name: body["name"] = new_name
        headers = await self._headers(user_id)
        resp = await self._http.post(f"{GRAPH_BASE}/me/drive/items/{item_id}/copy", headers=headers, json=body)
        return {"status": "copy_started", "item_id": item_id, "monitor_url": resp.headers.get("Location")}

    async def onedrive_share(self, user_id, item_id, share_type="view", scope="organization"):
        result = await self._post(user_id, f"/me/drive/items/{item_id}/createLink", {"type": share_type, "scope": scope})
        link = result.get("link", {})
        return {"item_id": item_id, "share_url": link.get("webUrl"), "type": link.get("type"), "scope": link.get("scope")}

    # ── SHAREPOINT ─────────────────────────────────────────────

    def _format_site(self, site):
        return {"id": site.get("id"), "name": site.get("displayName") or site.get("name"),
                "description": site.get("description"), "webUrl": site.get("webUrl")}

    async def sharepoint_list_sites(self, user_id, search=None, page_token=None):
        if page_token:
            data = await self._get_url(user_id, page_token)
        else:
            ep = f"/sites?search={search}" if search else "/sites?search=*"
            data = await self._get(user_id, ep)
        result = {"count": len(data.get("value", [])), "sites": [self._format_site(s) for s in data.get("value", [])]}
        return self._add_pagination(result, data)

    async def sharepoint_get_site(self, user_id, site_id):
        return self._format_site(await self._get(user_id, f"/sites/{site_id}"))

    async def sharepoint_list_drives(self, user_id, site_id):
        data = await self._get(user_id, f"/sites/{site_id}/drives")
        return {"count": len(data.get("value", [])), "site_id": site_id,
                "drives": [{"id": d.get("id"), "name": d.get("name"), "description": d.get("description"),
                            "webUrl": d.get("webUrl"), "driveType": d.get("driveType")} for d in data.get("value", [])]}

    async def sharepoint_list_files(self, user_id, site_id, drive_id=None, path="/", top=50, page_token=None):
        if page_token:
            data = await self._get_url(user_id, page_token)
        else:
            if drive_id:
                ep = f"/drives/{drive_id}/root/children" if path == "/" or not path else f"/drives/{drive_id}/root:/{path.strip('/')}:/children"
            else:
                ep = f"/sites/{site_id}/drive/root/children" if path == "/" or not path else f"/sites/{site_id}/drive/root:/{path.strip('/')}:/children"
            data = await self._get(user_id, ep, {"$top": top})
        result = {"count": len(data.get("value", [])), "site_id": site_id, "items": [self._format_item(i) for i in data.get("value", [])]}
        return self._add_pagination(result, data)

    async def sharepoint_download(self, user_id, site_id, item_id, drive_id=None, save_to_sandbox=True, session_id="default"):
        if drive_id:
            meta_ep, content_ep = f"/drives/{drive_id}/items/{item_id}", f"/drives/{drive_id}/items/{item_id}/content"
        else:
            meta_ep, content_ep = f"/sites/{site_id}/drive/items/{item_id}", f"/sites/{site_id}/drive/items/{item_id}/content"
        meta = await self._get(user_id, meta_ep)
        content = await self._get_bytes(user_id, content_ep)
        filename = meta.get("name", f"download_{item_id}")
        result = {"name": filename, "size": len(content), "mimeType": meta.get("file", {}).get("mimeType", "application/octet-stream")}
        if save_to_sandbox:
            try:
                fp = self._write_to_sandbox(filename, content, session_id)
                result.update({"sandbox_path": fp, "saved_to_sandbox": True, "message": f"File '{filename}' saved to sandbox."})
            except Exception as e:
                logger.error(f"Sandbox write failed: {e}", exc_info=True)
                result.update({"content_base64": base64.b64encode(content).decode(), "saved_to_sandbox": False, "sandbox_error": str(e)})
        else:
            result.update({"content_base64": base64.b64encode(content).decode(), "saved_to_sandbox": False})
        return result

    async def sharepoint_upload(self, user_id, site_id, path, content_base64, drive_id=None, content_type=None):
        clean = path.strip("/")
        ep = f"/drives/{drive_id}/root:/{clean}:/content" if drive_id else f"/sites/{site_id}/drive/root:/{clean}:/content"
        return self._format_item(await self._put_bytes(user_id, ep, base64.b64decode(content_base64), content_type or "application/octet-stream"))

    async def sharepoint_search(self, user_id, site_id, query, drive_id=None, top=25, page_token=None):
        if page_token:
            data = await self._get_url(user_id, page_token)
        else:
            ep = f"/drives/{drive_id}/root/search(q='{query}')" if drive_id else f"/sites/{site_id}/drive/root/search(q='{query}')"
            data = await self._get(user_id, ep, {"$top": top})
        result = {"count": len(data.get("value", [])), "query": query, "items": [self._format_item(i) for i in data.get("value", [])]}
        return self._add_pagination(result, data)

    async def sharepoint_list_lists(self, user_id, site_id, page_token=None):
        if page_token:
            data = await self._get_url(user_id, page_token)
        else:
            data = await self._get(user_id, f"/sites/{site_id}/lists")
        result = {"count": len(data.get("value", [])), "site_id": site_id,
                "lists": [{"id": l.get("id"), "name": l.get("displayName"), "description": l.get("description"),
                           "webUrl": l.get("webUrl"), "template": l.get("list", {}).get("template")} for l in data.get("value", [])]}
        return self._add_pagination(result, data)

    async def sharepoint_list_items(self, user_id, site_id, list_id, top=50, page_token=None):
        if page_token:
            data = await self._get_url(user_id, page_token)
        else:
            data = await self._get(user_id, f"/sites/{site_id}/lists/{list_id}/items", {"$top": top, "$expand": "fields"})
        result = {"count": len(data.get("value", [])), "list_id": list_id,
                "items": [{"id": i.get("id"), "fields": i.get("fields", {}), "webUrl": i.get("webUrl"),
                           "lastModified": i.get("lastModifiedDateTime")} for i in data.get("value", [])]}
        return self._add_pagination(result, data)
