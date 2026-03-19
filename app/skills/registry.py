"""Skill Registry — discovers and registers all available skills.

New skills are added here. The engine calls create_skill_engine()
at startup and all skills become available as MCP tools.

To add a new skill:
1. Create a class in app/skills/definitions/
2. Import it here
3. Add engine.register_skill(YourSkill()) below
"""

import logging
from .engine import SkillEngine
from .definitions.consolidate_files import ConsolidateFilesSkill

logger = logging.getLogger(__name__)


def create_skill_engine() -> SkillEngine:
    """Create and populate the skill engine with all skills."""
    engine = SkillEngine()

    # ── Register skills ──────────────────────────────────
    engine.register_skill(ConsolidateFilesSkill())

    # Future skills:
    # engine.register_skill(ExtractEmailAttachmentsSkill())
    # engine.register_skill(SearchAndSummarizeSkill())
    # engine.register_skill(GenerateReportSkill())

    logger.info(
        f"[SkillRegistry] {len(engine._skills)} skills registered: "
        f"{list(engine._skills.keys())}"
    )
    return engine
