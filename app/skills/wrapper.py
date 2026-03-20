"""Skill Tool Wrapper — Bridges skills into the MCP tool registry.

Makes a skill look like a regular MCP tool so it appears in tools/list
and can be invoked via tools/call through the standard JSON-RPC handler.
"""

import logging
import inspect

logger = logging.getLogger(__name__)


class SkillToolWrapper:
    """Wraps a skill definition as an MCP-compatible tool object.

    The MCP JSON-RPC handler in main.py expects tools to have:
    - .fn: async callable
    - .description: string
    - .parameters: JSON Schema dict
    """

    def __init__(self, name: str, skill_def: dict, engine):
        """Initialize the wrapper.

        Args:
            name: Tool name as it will appear in MCP
            skill_def: The skill definition dict
            engine: The SkillEngine instance for execution
        """
        self.name = name
        self.skill_def = skill_def
        self.engine = engine
        self.description = skill_def.get('description', '')

        # Build parameters schema from skill definition
        self.parameters = self._build_parameters()

        # Create the callable fn
        self.fn = self._make_fn()

    def _build_parameters(self) -> dict:
        """Build JSON Schema for the skill's input parameters."""
        params = self.skill_def.get('parameters', {})
        if params:
            return params

        # Fall back to inspecting the execute function signature
        execute_fn = self.skill_def.get('execute')
        if not execute_fn or not callable(execute_fn):
            return {"type": "object", "properties": {}}

        sig = inspect.signature(execute_fn)
        properties = {}
        required = []

        for param_name, param in sig.parameters.items():
            if param_name in ('self', 'cls', 'engine'):
                continue

            prop = {"type": "string"}
            if param.annotation != inspect.Parameter.empty:
                type_map = {
                    int: "integer",
                    float: "number",
                    bool: "boolean",
                    str: "string",
                }
                prop["type"] = type_map.get(param.annotation, "string")

            if param.default != inspect.Parameter.empty:
                if param.default is not None:
                    prop["default"] = param.default
            else:
                required.append(param_name)

            properties[param_name] = prop

        schema = {"type": "object", "properties": properties}
        if required:
            schema["required"] = required
        return schema

    def _make_fn(self):
        """Create an async callable that matches MCP tool invocation pattern."""
        engine = self.engine
        skill_name = self.name

        async def skill_executor(**kwargs):
            return await engine.execute(skill_name, **kwargs)

        # Copy parameter info so _validate_tool_args works correctly
        execute_fn = self.skill_def.get('execute')
        if execute_fn and callable(execute_fn):
            sig = inspect.signature(execute_fn)
            new_params = [
                p for name, p in sig.parameters.items()
                if name not in ('self', 'cls', 'engine')
            ]
            skill_executor.__signature__ = sig.replace(parameters=new_params)

        skill_executor.__doc__ = self.description
        skill_executor.__name__ = skill_name

        return skill_executor
