"""Power Interpreter Skills Package.

Skills are multi-step workflows that orchestrate MCP tools
with built-in validation, error handling, and retry logic.

Modules:
    engine.py              - SkillEngine core (registration + execution)
    wrapper.py             - SkillToolWrapper (MCP tool bridge)
    consolidate_files.py   - OneDrive folder -> Excel inventory
    ocr_pdf_to_excel.py    - PDF -> OCR -> structured Excel
    data_to_report.py      - Data file -> Excel report + chart
    batch_ocr_pipeline.py  - OneDrive folder -> batch OCR -> combined Excel

Initialization is handled by app/skills_integration.py,
called from main.py during lifespan startup.

Skill count: 4
Total tool handlers wired: ~13
"""
