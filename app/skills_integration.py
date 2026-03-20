"""Skills Layer Integration Bridge.

Connects the Skills Engine to the Power Interpreter MCP server.
Called from main.py during lifespan startup.

This module:
1. Initializes the SkillEngine
2. Registers all skill definitions
3. Returns wrapped skill tools for the MCP registry
"""

import logging
from app.skills.engine import SkillEngine
from app.skills.wrapper import SkillToolWrapper
from app.skills.consolidate_files import SKILL_DEFINITION

logger = logging.getLogger(__name__)


async def initialize_skills(mcp_server) -> dict:
    """Initialize the skills layer and return wrapped tool dict.

    Args:
        mcp_server: The FastMCP server instance (used by skills to call tools)

    Returns:
        Dict mapping tool_name -> SkillToolWrapper, or empty dict.
    """
    engine = SkillEngine(mcp_server)

    # Register all skill definitions
    skills = [SKILL_DEFINITION]

    registered = 0
    for skill_def in skills:
        try:
            engine.register(skill_def)
            registered += 1
            logger.info(f"Skills: registered '{skill_def['name']}'")
        except Exception as e:
            logger.warning(f"Skills: failed to register '{skill_def.get('name', '?')}': {e}")

    if registered == 0:
        return {}

    # Wrap registered skills as MCP-compatible tools
    tool_dict = {}
    for name, skill in engine.skills.items():
        wrapper = SkillToolWrapper(name, skill, engine)
        tool_dict[name] = wrapper
        logger.info(f"Skills: wrapped '{name}' as MCP tool")

    logger.info(
        f"Skills: {len(tool_dict)} skill tools ready, "
        f"{sum(len(s.get('tools', [])) for s in engine.skills.values())} tool handlers wired"
    )

    return tool_dict
