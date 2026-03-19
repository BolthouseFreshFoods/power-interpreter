"""Skills Integration — wires skills into the MCP server.

This module provides the bridge between the SkillEngine and
the existing MCP tool registration in main.py.

Usage in main.py:

    from app.skills_integration import register_skill_tools

    # After existing tool handlers are defined:
    skill_engine = await register_skill_tools(
        tool_handlers={
            "ms_auth": handle_ms_auth,
            "onedrive": handle_onedrive,
            "execute_code": handle_execute_code,
            "list_files": handle_list_files,
        },
        register_fn=register_tool,  # Your MCP tool registration function
    )
"""

import logging
from typing import Any, Callable, Dict

from .skills import create_skill_engine, SkillEngine

logger = logging.getLogger(__name__)


async def register_skill_tools(
    tool_handlers: Dict[str, Callable],
    register_fn: Callable,
) -> SkillEngine:
    """Register all skills as MCP tools.

    Args:
        tool_handlers: Dict mapping tool name -> async handler.
            Required keys: ms_auth, onedrive, execute_code, list_files.
            Additional handlers can be passed for future skills.
        register_fn: Function to register a new MCP tool.
            Signature: register_fn(name, description, input_schema, handler)

    Returns:
        The initialized SkillEngine instance.
    """
    # Validate required handlers
    required = {"ms_auth", "onedrive", "execute_code", "list_files"}
    missing = required - set(tool_handlers.keys())
    if missing:
        logger.warning(
            f"[SkillIntegration] Missing tool handlers: {missing}. "
            f"Some skills may fail."
        )

    # Create engine and wire up tool handlers
    engine = create_skill_engine()
    for name, handler in tool_handlers.items():
        engine.register_tool_handler(name, handler)

    # Register each skill as an MCP tool
    for skill_info in engine.list_skills():
        skill_name = skill_info["name"]

        # Create a closure that captures the skill name
        def make_handler(sn: str):
            async def handler(arguments: dict) -> str:
                session = arguments.get("session_name", "default")
                result = await engine.run(sn, arguments, session)
                return result.summary()
            return handler

        handler = make_handler(skill_name)

        register_fn(
            name=skill_name,
            description=skill_info["description"],
            input_schema=skill_info["input_schema"],
            handler=handler,
        )
        logger.info(
            f"[SkillIntegration] Registered MCP tool: {skill_name}"
        )

    logger.info(
        f"[SkillIntegration] {len(engine._skills)} skills "
        f"registered as MCP tools"
    )
    return engine
