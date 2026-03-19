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
| `user_email` | string | Yes | Microsoft 365 email |
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

## Integration

See `app/skills_integration.py` for wiring into `main.py`.

```python
from app.skills_integration import register_skill_tools

skill_engine = await register_skill_tools(
    tool_handlers={
        "ms_auth": handle_ms_auth,
        "onedrive": handle_onedrive,
        "execute_code": handle_execute_code,
        "list_files": handle_list_files,
    },
    register_fn=register_tool,
)
```

After integration, the model sees `skill_consolidate_files` as a single MCP tool. One call, deterministic execution, guardrailed pipeline.
