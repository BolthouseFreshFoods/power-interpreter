"""Skills Integration — wires skills into the MCP server.

Updated to work directly with FastMCP's internal tool registry,
matching the patterns in app/main.py's _get_tool_registry() and
_build_tool_list().

Integration requires 3 small changes to app/main.py:
  1. Add `_skill_tools: dict = {}` after imports
  2. Call `initialize_skills(mcp)` in lifespan()
  3. Merge _skill_tools in _get_tool_registry()

See SKILLS_GUIDE.md for full integration instructions.
"""

import logging
from typing import Any, Callable, Dict, Optional

from .skills import create_skill_engine, SkillEngine

logger = logging.getLogger(__name__)


class SkillToolWrapper:
    """Wraps a skill as a tool-like object for the MCP registry.

    Mimics the interface expected by _get_tool_registry(),
    _build_tool_list(), and _handle_mcp_direct() in main.py:
      - .fn:          async callable(**kwargs) -> str
      - .description:  str
      - .parameters:   dict  (JSON Schema)
      - .inputSchema:  dict  (alias used by some FastMCP versions)
      - .name:         str
    """

    def __init__(
        self,
        name: str,
        description: str,
        input_schema: dict,
        handler: Callable,
    ):
        self.name = name
        self.description = description
        self.parameters = input_schema
        self.inputSchema = input_schema  # alias
        self.fn = handler


async def initialize_skills(mcp_server) -> Dict[str, SkillToolWrapper]:
    """Initialize the skills engine and return tool wrappers.

    This is called once during app startup in lifespan().
    It creates the SkillEngine, wires existing MCP tool handlers
    into it, and returns SkillToolWrapper objects that can be
    merged into _get_tool_registry() results.

    Args:
        mcp_server: The FastMCP instance from app.mcp_server

    Returns:
        Dict mapping skill_name -> SkillToolWrapper.
        Store this in the module-level _skill_tools dict.
    """
    # Create engine with all registered skills
    engine = create_skill_engine()

    # Extract existing tool handlers from MCP registry
    registry = _get_mcp_registry(mcp_server)
    if not registry:
        logger.warning("[Skills] Empty MCP registry — skills disabled")
        return {}

    # Wire required tool handlers
    required_tools = ["ms_auth", "onedrive", "execute_code", "list_files"]
    wired = 0
    for tool_name in required_tools:
        tool = registry.get(tool_name)
        if tool:
            fn = tool.fn if hasattr(tool, "fn") else tool
            engine.register_tool_handler(tool_name, fn)
            wired += 1
            logger.debug(f"[Skills] Wired tool handler: {tool_name}")
        else:
            logger.warning(
                f"[Skills] Required tool handler not found: {tool_name}"
            )

    # Wire optional handlers (future skills may use these)
    optional_tools = [
        "fetch_file", "sharepoint", "resolve_share_link",
        "submit_job", "get_job_status", "get_job_result",
    ]
    for tool_name in optional_tools:
        tool = registry.get(tool_name)
        if tool:
            fn = tool.fn if hasattr(tool, "fn") else tool
            engine.register_tool_handler(tool_name, fn)

    if wired < len(required_tools):
        logger.warning(
            f"[Skills] Only {wired}/{len(required_tools)} required "
            f"handlers wired — some skills may fail at runtime"
        )

    # Build SkillToolWrapper for each skill
    skill_tools: Dict[str, SkillToolWrapper] = {}

    for skill_info in engine.list_skills():
        skill_name = skill_info["name"]

        # Create async handler closure
        handler = _make_skill_handler(engine, skill_name)

        wrapper = SkillToolWrapper(
            name=skill_name,
            description=skill_info["description"],
            input_schema=skill_info["input_schema"],
            handler=handler,
        )
        skill_tools[skill_name] = wrapper
        logger.info(f"[Skills] Registered skill tool: {skill_name}")

    logger.info(
        f"[Skills] Engine ready: {len(skill_tools)} skills, "
        f"{wired} tool handlers wired"
    )
    return skill_tools


def _make_skill_handler(
    engine: SkillEngine, skill_name: str
) -> Callable:
    """Create an async handler closure for a skill.

    The returned function matches the signature expected by
    _handle_mcp_direct():  async fn(**kwargs) -> str

    We use **kwargs so the function accepts any arguments the
    model passes, matching how other MCP tools are invoked
    in the direct handler.
    """

    async def handler(**kwargs) -> str:
        session = kwargs.get("session_name", "default")
        result = await engine.run(skill_name, kwargs, session)
        return result.summary()

    # Metadata for introspection / logging
    handler.__name__ = skill_name
    handler.__doc__ = f"Skill: {skill_name}"
    return handler


def _get_mcp_registry(mcp_server) -> dict:
    """Extract the tool registry from a FastMCP server instance.

    Mirrors the same lookup logic used in main.py's
    _get_tool_registry() to ensure consistency.
    """
    try:
        if (
            hasattr(mcp_server, "_tool_manager")
            and hasattr(mcp_server._tool_manager, "_tools")
        ):
            return mcp_server._tool_manager._tools
        elif hasattr(mcp_server, "tools"):
            return mcp_server.tools
        else:
            logger.warning(
                "[Skills] Could not find tool registry on MCP server"
            )
            return {}
    except Exception as e:
        logger.error(f"[Skills] Error accessing MCP registry: {e}")
        return {}
