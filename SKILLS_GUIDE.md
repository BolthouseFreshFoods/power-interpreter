# Power Interpreter — Skills Layer

## What Are Skills?

Skills are **composed, multi-step workflows** that orchestrate existing MCP tools with guardrails enforced at the code level.

| Layer | What It Is | Example |
|-------|-----------|--------|
| **Tool** | Single atomic function | `onedrive(action="download")` |
| **Skill** | Multi-step pipeline | "Download from OneDrive → process with pandas → save to sandbox → deliver download URL" |

A tool is a screwdriver. A skill is "assemble the bookshelf."

## Why Skills Exist

Every skill was born from a real production failure:

| Failure | Root Cause | Skill Fix |
|---------|-----------|----------|
| Model uses `urllib` instead of `onedrive` tool | No routing enforcement | Skill hardcodes: onedrive for files |
| Model saves to `/tmp/` | No path enforcement | Skill saves to `/app/sandbox_data/{session}/` |
| `dataframe_to_rows` crashes | Sandbox limitation | Skill uses `ws.append()` |
| Model loops on file delivery | No completion criteria | Skill retries once, then stops |
| 400K+ chars flood stdout | No output limits | Skill only prints summaries |

## Architecture

```
┌─────────────────────────────────┐
│        SimTheory Model          │
│  (calls skill_consolidate_files │
│   instead of individual tools)  │
└──────────────┬──────────────────┘
               │
┌──────────────▼──────────────────┐
│     SKILLS ENGINE               │
│  ┌───────────────────────────┐  │
│  │ ConsolidateFilesSkill     │  │
│  │  Step 1: ms_auth          │  │
│  │  Step 2: onedrive → list  │  │
│  │  Step 3: onedrive → dl    │  │
│  │  Step 4: execute_code     │  │
│  │  Step 5: list_files → URL │  │
│  │  GUARDRAILS: no urllib,   │  │
│  │  no /tmp, no data dumps   │  │
│  └───────────────────────────┘  │
└──────────────┬──────────────────┘
               │
┌──────────────▼──────────────────┐
│        MCP TOOL LAYER           │
│  onedrive | execute_code |      │
│  ms_auth | list_files | ...     │
└─────────────────────────────────┘
```

## Available Skills

### `skill_consolidate_files`

Downloads files from OneDrive and consolidates into one Excel workbook.

**Parameters:**
| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `user_email` | string | Yes | Microsoft 365 email (e.g., user@bolthousefresh.com) |
| `folder_path` | string | Yes | OneDrive folder path |
| `file_names` | string[] | No | Specific files (default: all) |
| `output_filename` | string | No | Output name (default: Consolidated_Output.xlsx) |
| `session_name` | string | No | Session for storage (default: consolidate) |

**Guardrails:**
- Blocked: urllib, requests, httpx, subprocess
- Blocked: graph.microsoft.com URLs in code
- Blocked: dataframe_to_rows (use ws.append)
- Max code lines: 40 per execute_code call
- Max print statements: 10 per call
- Output path: forced to `/app/sandbox_data/{session}/`
- Delivery: one retry max, then stops

## Integration with app/main.py

The skills layer requires **3 small changes** to `app/main.py`.
These are designed to be non-breaking — if the skills module
is missing, the app runs exactly as before.

---

### Change 1: Add module-level variable

**Location:** After the imports block, near `MCP_RESPONSE_MAX_CHARS`

```python
# ── Skills Layer ──
_skill_tools: dict = {}
```

---

### Change 2: Initialize in lifespan()

**Location:** Inside `lifespan()`, after the Microsoft token
persistence block (`await _ms_auth.ensure_db_table()`)

```python
    # ── Skills Layer Integration ──
    try:
        from app.skills_integration import initialize_skills
        _skills_result = await initialize_skills(mcp)
        if _skills_result:
            global _skill_tools
            _skill_tools = _skills_result
            logger.info(
                f"Skills layer: {len(_skill_tools)} skill tools registered"
            )
        else:
            logger.info("Skills layer: no skills registered")
    except ImportError:
        logger.info("Skills layer: module not found, skipping")
    except Exception as e:
        logger.warning(f"Skills layer initialization failed: {e}")
```

---

### Change 3: Merge skills into tool registry

**Location:** Replace the existing `_get_tool_registry()` function

```python
def _get_tool_registry():
    """Get the tool registry from the MCP server, including skills."""
    base = {}
    try:
        if hasattr(mcp, '_tool_manager') and hasattr(mcp._tool_manager, '_tools'):
            base = mcp._tool_manager._tools
        elif hasattr(mcp, 'tools'):
            base = mcp.tools
        else:
            logger.warning("Could not find tool registry on MCP server")
    except Exception as e:
        logger.error(f"Error accessing tool registry: {e}")

    # Merge skill tools if the skills layer is active
    if _skill_tools:
        merged = dict(base)
        merged.update(_skill_tools)
        return merged
    return base
```

---

### Why This Works

The `SkillToolWrapper` class (in `skills_integration.py`) provides
the same interface as FastMCP's internal tool objects:
- `.fn` — the async handler called by `_handle_mcp_direct()`
- `.description` — shown in `tools/list` response
- `.parameters` / `.inputSchema` — the JSON Schema
- `.name` — the tool name

Because `_handle_mcp_direct()` uses `_get_tool_registry()` for
both `tools/list` and `tools/call`, merging skill wrappers into
that registry means skills automatically appear as MCP tools —
no changes needed to the request handling logic.

The design is **fail-safe**: if the skills module is missing or
crashes during init, `_skill_tools` stays empty and the app
runs exactly as v3.0.1.

## Adding a New Skill

1. Create `app/skills/definitions/your_skill.py`
2. Extend the `Skill` base class
3. Implement `execute()` with step methods
4. Register in `app/skills/registry.py`

```python
from app.skills.base import Skill, SkillResult, StepResult, StepStatus
from app.skills.engine import SkillContext

class YourSkill(Skill):
    name = "skill_your_name"
    description = "What it does"
    input_schema = { ... }

    async def execute(self, params, ctx: SkillContext) -> SkillResult:
        # Step 1
        result = await ctx.call_tool("onedrive", {...})
        # Step 2
        result = await ctx.call_tool("execute_code", {...})
        # Return structured result
        return SkillResult(...)
```

Then register in `app/skills/registry.py`:

```python
from .definitions.your_skill import YourSkill

def create_skill_engine() -> SkillEngine:
    engine = SkillEngine()
    engine.register_skill(ConsolidateFilesSkill())
    engine.register_skill(YourSkill())  # <-- add here
    return engine
```

The skill automatically becomes a new MCP tool on next deploy.
