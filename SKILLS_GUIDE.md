# Skills Guide

This document explains how Skills work in **Power Interpreter**, why they exist, and how to safely add new ones.

## What is a Skill?

A **Skill** is a composed, multi-step workflow that orchestrates existing MCP tools with guardrails enforced in code.

Skills are intentionally different from atomic tools:
- **Tools** do one thing.
- **Skills** chain tools together to complete a real workflow.

Example workflow:

`Download from OneDrive → process with pandas/openpyxl → save in sandbox → deliver file`

## Why Skills Exist (Historical Failure Modes)

Skills were introduced to prevent recurring production failures:

1. **Routing Enforcement**
   - Prevents model drift into external libraries like `urllib`, `requests`, `httpx`, `subprocess` when internal MCP tools are required.

2. **Path Enforcement**
   - Forces output into the correct sandbox location:
   - `/app/sandbox_data/{session}/`

3. **Sandbox Runtime Limits**
   - Avoids fragile patterns that crash in constrained environments.
   - Example: Prefer `ws.append()` for Excel writes.

4. **Output Flooding Prevention**
   - Restricts unbounded stdout/log output from generated code.

5. **Authentication Session Integrity**
   - Enforces Power Interpreter’s own `ms_auth` path so internal session records are created.

6. **Completion Criteria & Retry Controls**
   - Explicit retry logic prevents infinite loops and incomplete delivery states.

---

## Critical Auth Gotcha: `ForeignKeyViolationError`

### Root Cause

The most important failure pattern is using an **external Microsoft auth flow** instead of Power Interpreter’s internal `ms_auth` tool.

When external auth is used:
- Power Interpreter session tracking is bypassed.
- No corresponding `Session` row is created in Power Interpreter’s database.

### Observable Failure

Subsequent `submit_job` requests fail with database integrity errors because the `session_id` does not exist in the `sessions` table.

Typical symptom:
- `sqlalchemy.exc.IntegrityError`
- `asyncpg.exceptions.ForeignKeyViolationError`
- `500 Internal Server Error`

### Rule (Non-Negotiable)

For any workflow that touches Power Interpreter execution, authentication **must** run through Power Interpreter’s own `ms_auth` tool (directly or via skill orchestration).

---

## Architecture

```text
SimTheory Model
   -> SKILLS ENGINE (guardrails + orchestration)
      -> MCP TOOL LAYER (ms_auth, onedrive, execute_code, list_files, etc.)
```

The Skills Engine is a strict control layer between model intent and tool execution.

---

## Required `app/main.py` Integration

To enable Skills without breaking existing behavior, apply these **three changes**:

1. **Module-level registry cache**
   - Add a module-level variable:
   - `_skill_tools: dict = {}`

2. **Initialize skills in `lifespan()`**
   - In a guarded `try/except`, call:
   - `app.skills_integration.initialize_skills(mcp)`
   - Store results in `_skill_tools`.

3. **Merge skill tools into runtime registry**
   - Update `_get_tool_registry()` to merge base MCP tools + `_skill_tools`.

This makes skills discoverable by `tools/list` and callable via `tools/call` automatically.

---

## Adding a New Skill

1. Create a new class in:
   - `app/skills/definitions/`
2. Extend the `Skill` base class.
3. Implement `execute()` using chained `ctx.call_tool()` calls.
4. Register in:
   - `app/skills/registry.py` via `create_skill_engine()`.

---

## Reference Skill: `skill_consolidate_files`

### Purpose
Download selected OneDrive files and consolidate into one Excel workbook.

### Parameters
- `user_email` (required, string)
- `folder_path` (required, string)
- `file_names` (optional, string[])
- `output_filename` (optional, default: `Consolidated_Output.xlsx`)
- `session_name` (optional, default: `consolidate`)

### Guardrails
- Block external libs/routes: `urllib`, `requests`, `httpx`, `subprocess`, direct Graph URL usage.
- Prefer `ws.append()` for Excel writing.
- Limit generated code chunk size (e.g., line cap) and print count.
- Enforce sandbox output path.
- Limit delivery retries.

---

## Operational Checklist

Before promoting a skill:
- [ ] Uses `ms_auth` for PI-bound workflows.
- [ ] Never writes outside `/app/sandbox_data/{session}/`.
- [ ] No direct external HTTP library calls when MCP tools exist.
- [ ] Handles retries with explicit upper bounds.
- [ ] Emits bounded stdout.
- [ ] Registers cleanly in skill registry.

If all items pass, the skill is production-ready.
