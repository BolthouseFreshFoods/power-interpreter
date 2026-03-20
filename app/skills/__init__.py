"""Power Interpreter Skills Package.

Skills are multi-step workflows that orchestrate MCP tools
with built-in validation, error handling, and retry logic.

Modules:
    engine.py            - SkillEngine core (registration + execution)
    wrapper.py           - SkillToolWrapper (MCP tool bridge)
    consolidate_files.py - First production skill (OneDrive -> Excel)

Initialization is handled by app/skills_integration.py,
called from main.py during lifespan startup.
"""
