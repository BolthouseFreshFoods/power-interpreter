"""Skills Layer Integration Bridge.

Connects the Skills Engine to the Power Interpreter MCP server.
Called from main.py during lifespan startup.

This module:
1. Initializes the SkillEngine
2. Registers all skill definitions
3. Returns wrapped skill tools for the MCP registry

v1.0.0: 1 skill  (consolidate_files)
v2.0.0: 4 skills (+ ocr_pdf_to_excel, data_to_report, batch_ocr_pipeline)
v2.1.0: 5 skills (+ sandbox_safe_code)
"""

import logging
from app.skills.engine import SkillEngine
from app.skills.wrapper import SkillToolWrapper
from app.skills.consolidate_files import SKILL_DEFINITION as CONSOLIDATE_SKILL
from app.skills.ocr_pdf_to_excel import SKILL_DEFINITION as OCR_SKILL
from app.skills.data_to_report import SKILL_DEFINITION as REPORT_SKILL
from app.skills.batch_ocr_pipeline import SKILL_DEFINITION as BATCH_OCR_SKILL
from app.skills.sandbox_safe_code import SKILL_DEFINITION as SANDBOX_SAFE_SKILL

logger = logging.getLogger(__name__)

# All skill definitions – add new skills here
ALL_SKILLS = [
    CONSOLIDATE_SKILL,
    OCR_SKILL,
    REPORT_SKILL,
    BATCH_OCR_SKILL,
    SANDBOX_SAFE_SKILL,
]


async def initialize_skills(mcp_server) -> dict:
    """Initialize the skills layer and return wrapped tool dict.

    Args:
        mcp_server: The FastMCP server instance (used by skills to call tools)

    Returns:
        Dict mapping tool_name -> SkillToolWrapper, or empty dict.
    """
    engine = SkillEngine(mcp_server)

    registered = 0
    for skill_def in ALL_SKILLS:
        try:
            engine.register(skill_def)
            registered += 1
            logger.info(f"Skills: registered '{skill_def['name']}'")
        except Exception as e:
            logger.warning(
                f"Skills: failed to register "
                f"'{skill_def.get('name', '?')}': {e}"
            )

    if registered == 0:
        return {}

    # Wrap registered skills as MCP-compatible tools
    tool_dict = {}
    for name, skill in engine.skills.items():
        wrapper = SkillToolWrapper(name, skill, engine)
        tool_dict[name] = wrapper
        logger.info(f"Skills: wrapped '{name}' as MCP tool")

    total_handlers = sum(
        len(s.get('tools', [])) for s in engine.skills.values()
    )
    logger.info(
        f"Skills: {len(tool_dict)} skill tools ready, "
        f"{total_handlers} tool handlers wired"
    )

    return tool_dict
