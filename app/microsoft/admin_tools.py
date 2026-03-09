"""Microsoft Auth Admin Tools — Token Management

v2.9.3a: New tools for multi-user token lifecycle management.

Tools:
  ms_auth_clear      — Purge stale/incorrect tokens for a user
  ms_auth_list_users — List all authenticated users and status

Registered separately from the main 22 OneDrive/SharePoint tools.
Call register_admin_tools(mcp, auth_manager) from bootstrap.

Author: MCA for Timothy Escamilla / Bolthouse Fresh Foods
"""

import json
import logging

logger = logging.getLogger(__name__)


def register_admin_tools(mcp, auth_manager):
    """Register Microsoft auth admin/management tools on the MCP server."""

    @mcp.tool()
    async def ms_auth_clear(user_id: str) -> str:
        """Clear stored Microsoft authentication tokens for a user. Use to remove stale or incorrect logins so the user can re-authenticate.

        Args:
            user_id: Microsoft email to clear tokens for (e.g. jane.doe@bolthousefresh.com).
        """
        try:
            result = await auth_manager.clear_user_token(user_id)
            if result.get("cleared"):
                return json.dumps({
                    "status": "cleared",
                    "user_id": user_id,
                    "details": result,
                    "message": (
                        f"Authentication tokens cleared for {user_id}. "
                        f"Use ms_auth_start(user_id='{user_id}') to re-authenticate."
                    ),
                }, indent=2)
            else:
                return json.dumps({
                    "status": "not_found",
                    "user_id": user_id,
                    "details": result,
                    "message": (
                        f"No tokens found for {user_id}. "
                        f"Nothing to clear."
                    ),
                }, indent=2)
        except Exception as e:
            logger.error(f"ms_auth_clear error for {user_id}: {e}", exc_info=True)
            return json.dumps({
                "status": "error",
                "message": f"Error clearing tokens: {str(e)}",
            }, indent=2)

    @mcp.tool()
    async def ms_auth_list_users() -> str:
        """List all Microsoft-authenticated users and their token status. Shows who has active sessions and when they last authenticated."""
        try:
            users = await auth_manager.list_authenticated_users_async()
            if not users:
                return json.dumps({
                    "users": [],
                    "count": 0,
                    "message": (
                        "No authenticated users found. "
                        "Use ms_auth_start(user_id='your@email.com') to authenticate."
                    ),
                }, indent=2)
            return json.dumps({
                "users": users,
                "count": len(users),
                "message": f"{len(users)} user(s) with stored tokens.",
            }, indent=2)
        except Exception as e:
            logger.error(f"ms_auth_list_users error: {e}", exc_info=True)
            return json.dumps({
                "status": "error",
                "message": f"Error listing users: {str(e)}",
            }, indent=2)

    logger.info("MS Auth admin tools registered (ms_auth_clear, ms_auth_list_users)")
    return 2  # number of tools registered
