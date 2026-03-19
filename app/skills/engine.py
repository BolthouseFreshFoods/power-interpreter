"""Skill Engine — orchestrates skill registration and execution.

The engine is the bridge between the MCP tool layer and the skill
definitions. It provides SkillContext so skills can call underlying
tool handlers directly (server-side) without going back through
the model.
"""

import logging
import time
from typing import Any, Callable, Dict, List, Optional

from .base import Skill, SkillResult, StepStatus

logger = logging.getLogger(__name__)


class SkillContext:
    """Provides skills with access to underlying tool handlers.

    Instead of the model deciding which tool to call next,
    the skill calls tools directly through this context.
    This is what makes skills deterministic.
    """

    def __init__(
        self,
        tool_handlers: Dict[str, Callable],
        session_name: str = "default",
    ):
        self._handlers = tool_handlers
        self.session_name = session_name
        self.variables: Dict[str, Any] = {}  # Shared state between steps

    async def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Any:
        """Call an underlying MCP tool handler directly.

        This bypasses the model entirely — the skill controls
        the exact tool, exact arguments, exact order.
        """
        handler = self._handlers.get(tool_name)
        if not handler:
            available = list(self._handlers.keys())
            raise ValueError(
                f"Tool '{tool_name}' not found. "
                f"Available: {available}"
            )
        logger.info(
            f"[Skill] -> {tool_name}({list(arguments.keys())})"
        )
        start = time.time()
        result = await handler(arguments)
        elapsed = int((time.time() - start) * 1000)
        logger.info(f"[Skill] <- {tool_name} ({elapsed}ms)")
        return result

    def store(self, key: str, value: Any):
        """Store a value for use by later steps."""
        self.variables[key] = value

    def get(self, key: str, default: Any = None) -> Any:
        """Retrieve a stored value."""
        return self.variables.get(key, default)


class SkillEngine:
    """Central registry and executor for all skills."""

    def __init__(self):
        self._skills: Dict[str, Skill] = {}
        self._tool_handlers: Dict[str, Callable] = {}

    def register_tool_handler(self, name: str, handler: Callable):
        """Register an existing MCP tool handler for skills to use.

        Call this for each tool (onedrive, execute_code, ms_auth, etc.)
        so that skills can invoke them server-side.
        """
        self._tool_handlers[name] = handler
        logger.debug(f"[SkillEngine] Tool handler registered: {name}")

    def register_skill(self, skill: Skill):
        """Register a skill."""
        if skill.name in self._skills:
            logger.warning(
                f"[SkillEngine] Skill '{skill.name}' already registered, replacing"
            )
        self._skills[skill.name] = skill
        logger.info(f"[SkillEngine] Registered skill: {skill.name}")

    def get_skill(self, name: str) -> Optional[Skill]:
        """Get a skill by name."""
        return self._skills.get(name)

    def list_skills(self) -> List[Dict[str, Any]]:
        """List all registered skills with their schemas."""
        return [
            {
                "name": s.name,
                "description": s.description,
                "input_schema": s.input_schema,
            }
            for s in self._skills.values()
        ]

    async def run(
        self,
        skill_name: str,
        params: Dict[str, Any],
        session_name: str = "default",
    ) -> SkillResult:
        """Execute a skill with full orchestration.

        This is the main entry point. The model calls a skill tool,
        the MCP handler calls this, and the engine takes over.
        """
        skill = self._skills.get(skill_name)
        if not skill:
            return SkillResult(
                skill_name=skill_name,
                success=False,
                error=f"Unknown skill: {skill_name}. "
                f"Available: {list(self._skills.keys())}",
            )

        # Validate parameters
        validation_error = skill.validate_params(params)
        if validation_error:
            return SkillResult(
                skill_name=skill_name,
                success=False,
                error=f"Invalid parameters: {validation_error}",
            )

        # Create execution context
        ctx = SkillContext(
            self._tool_handlers,
            session_name=session_name,
        )

        logger.info(f"[SkillEngine] === Running skill: {skill_name} ===")
        start = time.time()

        try:
            result = await skill.execute(params, ctx)
            elapsed = int((time.time() - start) * 1000)
            logger.info(
                f"[SkillEngine] === Skill {skill_name} "
                f"completed in {elapsed}ms, "
                f"success={result.success} ==="
            )
            return result

        except Exception as e:
            elapsed = int((time.time() - start) * 1000)
            logger.error(
                f"[SkillEngine] === Skill {skill_name} "
                f"CRASHED after {elapsed}ms: {e} ===",
                exc_info=True,
            )
            return SkillResult(
                skill_name=skill_name,
                success=False,
                error=f"Skill crashed: {str(e)}",
            )
