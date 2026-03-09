"""Microsoft Auth Admin Tools — MCP tool registrations for auth management.

v2.9.3a: Initial release.

Provides:
  - ms_auth_clear: Remove stale/wrong email token entries
  - ms_auth_list_users: List all authenticated users

These tools are registered separately from the 22 OneDrive/SharePoint
data tools in tools.py. They only need auth_manager (no graph_client).

Wiring: Called from bootstrap.py after register_microsoft_tools().

Author: MCA for Timothy Escamilla / Bolthouse Fresh Foods
"""

import json
import logging

logger = logging.getLogger(__name__)


def register_auth_admin_tools(mcp, auth_manager):
    """Register Microsoft auth admin tools on the MCP server.

    Args:
        mcp: The MCP server instance.
        auth_manager: The MSAuthManager singleton.
    """

    @mcp.tool()
    async def ms_auth_clear(user_id: str) -> str:
        """Remove a user's Microsoft auth tokens from memory and database. Use to fix stale or wrong email entries.

        Args:
            user_id: The exact email to remove (e.g. old.wrong@bolthousefresh.com).
        """
        try:
            result = await auth_manager.clear_user_token(user_id)
            mem = "removed" if result["removed_from_memory"] else "not found"
            db = "removed" if result["removed_from_database"] else "not found"
            result["message"] = (
                f"Cleared tokens for {user_id}. "
                f"Memory: {mem}. Database: {db}. "
                f"To re-authenticate, use ms_auth_start(user_id='correct.email@bolthousefresh.com')."
            )
            return json.dumps(result, indent=2)
        except Exception as e:
            logger.error(f"ms_auth_clear error: {e}", exc_info=True)
            return json.dumps({
                "error": True,
                "message": f"Failed to clear tokens: {str(e)}",
            }, indent=2)

    @mcp.tool()
    async def ms_auth_list_users() -> str:
        """List all Microsoft-authenticated users. Shows who has active tokens in memory and database.

        Use to verify which email addresses are registered and find stale entries to clear.
        """
        try:
            users = await auth_manager.list_authenticated_users()
            if users:
                msg = (
                    f"Found {len(users)} authenticated user(s). "
                    "Use ms_auth_clear(user_id='...') to remove stale entries."
                )
            else:
                msg = (
                    "No authenticated users found. "
                    "Use ms_auth_start(user_id='your.email@bolthousefresh.com') to authenticate."
                )
            return json.dumps({
                "total_users": len(users),
                "users": users,
                "message": msg,
            }, indent=2)
        except Exception as e:
            logger.error(f"ms_auth_list_users error: {e}", exc_info=True)
            return json.dumps({
                "error": True,
                "message": f"Failed to list users: {str(e)}",
            }, indent=2)

    logger.info("MS Auth admin tools registered: ms_auth_clear, ms_auth_list_users")
    return 2  # Number of tools registered
