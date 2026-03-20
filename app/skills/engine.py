"""Skills Engine — Server-side workflow orchestration.

The SkillEngine manages skill registration and execution.
Skills are multi-step workflows that orchestrate existing MCP tools
with built-in validation, error handling, and retry logic.
"""

import logging
import json
import asyncio
from datetime import datetime

logger = logging.getLogger(__name__)


class SkillEngine:
    """Core engine for registering and executing skills."""

    def __init__(self, mcp_server):
        """Initialize with reference to the MCP server for tool access.

        Args:
            mcp_server: The FastMCP server instance
        """
        self.mcp_server = mcp_server
        self.skills: dict = {}
        self._execution_log: list = []

    def register(self, skill_def: dict):
        """Register a skill definition.

        Args:
            skill_def: Dict with keys: name, description, execute

        Raises:
            ValueError: If skill definition is invalid or duplicate
        """
        required_keys = ['name', 'description', 'execute']
        for key in required_keys:
            if key not in skill_def:
                raise ValueError(f"Skill definition missing required key: '{key}'")

        name = skill_def['name']
        if name in self.skills:
            raise ValueError(f"Skill '{name}' is already registered")

        self.skills[name] = skill_def
        logger.info(f"SkillEngine: registered '{name}'")

    async def execute(self, skill_name: str, **kwargs) -> str:
        """Execute a skill by name with the given arguments.

        Args:
            skill_name: Name of the registered skill
            **kwargs: Arguments to pass to the skill's execute function

        Returns:
            String result from the skill execution
        """
        if skill_name not in self.skills:
            return f"Error: Skill '{skill_name}' not found. Available: {list(self.skills.keys())}"

        skill = self.skills[skill_name]
        execute_fn = skill['execute']

        start_time = datetime.utcnow()
        logger.info(f"SkillEngine: executing '{skill_name}' with args: {list(kwargs.keys())}")

        try:
            result = await execute_fn(self, **kwargs)
            elapsed = (datetime.utcnow() - start_time).total_seconds()
            logger.info(f"SkillEngine: '{skill_name}' completed in {elapsed:.1f}s")

            self._execution_log.append({
                'skill': skill_name,
                'status': 'success',
                'elapsed': elapsed,
                'timestamp': start_time.isoformat(),
            })

            return result

        except Exception as e:
            elapsed = (datetime.utcnow() - start_time).total_seconds()
            logger.error(
                f"SkillEngine: '{skill_name}' failed after {elapsed:.1f}s: {e}",
                exc_info=True,
            )

            self._execution_log.append({
                'skill': skill_name,
                'status': 'error',
                'error': str(e),
                'elapsed': elapsed,
                'timestamp': start_time.isoformat(),
            })

            return f"Skill '{skill_name}' failed: {e}"

    async def call_tool(self, tool_name: str, **kwargs) -> str:
        """Call an MCP tool by name. Used by skills to invoke other tools.

        Args:
            tool_name: Name of the MCP tool to call
            **kwargs: Arguments for the tool

        Returns:
            String result from the tool
        """
        try:
            registry = {}
            if hasattr(self.mcp_server, '_tool_manager') and hasattr(
                self.mcp_server._tool_manager, '_tools'
            ):
                registry = self.mcp_server._tool_manager._tools
            elif hasattr(self.mcp_server, 'tools'):
                registry = self.mcp_server.tools

            if tool_name not in registry:
                return f"Error: Tool '{tool_name}' not found in registry"

            tool = registry[tool_name]
            fn = tool.fn if hasattr(tool, 'fn') else tool

            logger.info(f"SkillEngine: calling tool '{tool_name}'")
            result = await fn(**kwargs)
            return str(result)

        except Exception as e:
            logger.error(f"SkillEngine: tool '{tool_name}' call failed: {e}")
            return f"Error calling {tool_name}: {e}"
