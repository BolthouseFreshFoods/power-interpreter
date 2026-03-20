# Power Interpreter MCP

**General-purpose sandboxed Python execution engine for AI agents.**
Designed for [SimTheory.ai](https://simtheory.ai) MCP integration.

> Version: **3.0.1** · Executor: **v2.11.0** · Skills Engine: **v1.0.0**
> Deploy target: [Railway](https://railway.app)

---

## What It Does

Power Interpreter gives AI agents a full Python runtime — sandboxed, persistent, and wired into Microsoft 365. Agents can execute code, process files, query datasets, generate charts, run OCR on scanned documents, and orchestrate multi-step workflows through the MCP (Model Context Protocol) interface.

### Key Capabilities

- **Sandboxed Python execution** with persistent session state (kernel architecture)
- **40+ pre-installed libraries** — pandas, numpy, scipy, scikit-learn, matplotlib, plotly, openpyxl, reportlab, Pillow, and more
- **OCR & PDF-to-Image** — pytesseract, pdf2image, pypdfium2 for handwritten/scanned document processing
- **Async job queue** for long-running operations (no timeouts)
- **Large dataset support** — 1.5M+ rows via PostgreSQL + DuckDB
- **Auto file storage** — generated files stored in Postgres with public download URLs
- **Chart auto-capture** — matplotlib/plotly figures saved and served automatically
- **Microsoft OneDrive + SharePoint integration** — per-user delegated auth
- **Skills Layer** — multi-step workflow orchestration via registered skills

---

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                   SimTheory.ai                       │
│              (MCP Client / AI Agent)                 │
└──────────────┬──────────────────────┬───────────────┘
               │ GET /mcp/sse         │ POST /mcp/sse
               │ (standard SSE)       │ (JSON-RPC direct)
               ▼                      ▼
┌─────────────────────────────────────────────────────┐
│              Power Interpreter MCP                   │
│                  (FastAPI + FastMCP)                  │
├─────────────────────────────────────────────────────┤
│  19 MCP Tools                                        │
│  ├── 12 Core (execute, files, jobs, sessions, data) │
│  ├──  4 Microsoft (ms_auth, onedrive, sharepoint,   │
│  │       resolve_share_link) — user_id REQUIRED      │
│  ├──  2 Admin (ms_auth_clear, ms_auth_list_users)   │
│  └──  1 Skill (skill_consolidate_files)             │
├─────────────────────────────────────────────────────┤
│  Guards                                              │
│  ├── Syntax Guard (pre-execution validation)        │
│  ├── Context Pressure Guard (per-tool response caps)│
│  └── Response Guard (smart truncation)              │
├─────────────────────────────────────────────────────┤
│  Engine                                              │
│  ├── SandboxExecutor (v2.11.0)                      │
│  ├── KernelManager (persistent sessions)            │
│  ├── JobManager (async queue)                       │
│  ├── SkillEngine (workflow orchestration)           │
│  └── UserTracker (per-session identity)             │
├─────────────────────────────────────────────────────┤
│  Storage                                             │
│  ├── PostgreSQL (sessions, files, tokens, datasets) │
│  └── /app/sandbox_data (ephemeral file system)      │
└─────────────────────────────────────────────────────┘
```

---

## MCP Tools (19 total)

### Core Tools (12)

| Tool | Description |
|------|-------------|
| `execute_code` | Run Python code in sandboxed session with persistent state |
| `create_session` | Create a new execution session |
| `delete_session` | Soft-delete a session (set is_active = False) |
| `list_files` | List files in a session's sandbox directory |
| `upload_file` | Upload a file to the sandbox |
| `fetch_file` | Download a file from the sandbox |
| `fetch_from_url` | Fetch a file from a URL into the sandbox |
| `submit_job` | Submit a long-running job to the async queue |
| `get_job_status` | Check status of an async job |
| `create_dataset` | Create a large dataset in PostgreSQL |
| `query_dataset` | Query a dataset with SQL |
| `list_datasets` | List available datasets |

### Microsoft Tools (4) — `user_id` REQUIRED

| Tool | Description |
|------|-------------|
| `ms_auth` | Authenticate with Microsoft 365 (device code flow) |
| `onedrive` | List, download, upload files to OneDrive |
| `sharepoint` | Access SharePoint sites and document libraries |
| `resolve_share_link` | Resolve a OneDrive/SharePoint sharing link |

> **Multi-user safety:** All Microsoft tools require `user_id` (Microsoft 365 email). Requests without `user_id` are rejected with a validation error.

### Admin Tools (2)

| Tool | Description |
|------|-------------|
| `ms_auth_clear` | Clear cached tokens for a user |
| `ms_auth_list_users` | List all authenticated users |

### Skill Tools (1)

| Tool | Description |
|------|-------------|
| `skill_consolidate_files` | Consolidate OneDrive folder contents into an Excel workbook. Multi-step workflow: list files → create session → execute consolidation → return download URL. |

---

## Skills Layer

Skills are server-side multi-step workflows that orchestrate existing MCP tools with built-in validation, error handling, and retry logic.

```
app/
├── skills_integration.py    # Bridge: main.py → SkillEngine
└── skills/
    ├── __init__.py
    ├── engine.py            # SkillEngine (registration + execution)
    ├── wrapper.py           # SkillToolWrapper (MCP tool bridge)
    └── consolidate_files.py # First production skill
```

### How Skills Work

1. **Registration** — Skill definitions are registered with the `SkillEngine` during startup
2. **Wrapping** — Each skill is wrapped as a `SkillToolWrapper` that implements the MCP tool interface (`.fn`, `.description`, `.parameters`)
3. **Merging** — Skill tools are merged into the MCP tool registry via `_get_tool_registry()` in `main.py`
4. **Execution** — When invoked via `tools/call`, the wrapper delegates to `SkillEngine.execute()`, which calls the skill's `execute` function with access to `engine.call_tool()` for invoking other MCP tools

---

## OCR & PDF-to-Image Support (v2.11.0)

Power Interpreter can process handwritten, image-based scanned PDFs natively.

### 3-Layer Implementation

| Layer | File | What |
|-------|------|------|
| System | `Dockerfile` | `tesseract-ocr`, `tesseract-ocr-eng`, `poppler-utils` |
| Python | `requirements.txt` | `pytesseract>=0.3.10`, `pdf2image>=1.17.0`, `pypdfium2>=4.0.0` |
| Sandbox | `executor.py` | `PIL`, `pytesseract`, `pdf2image`, `pypdfium2` in `_lazy_import` allowlist |

### Example Usage

```python
from pdf2image import convert_from_path
import pytesseract
from PIL import Image

# Convert PDF pages to images
images = convert_from_path("/app/sandbox_data/scanned_document.pdf")

# OCR each page
for i, img in enumerate(images):
    text = pytesseract.image_to_string(img)
    print(f"--- Page {i+1} ---")
    print(text)
```

---

## Sandbox Allowlist

The executor uses a whitelist-based import system. Only libraries with an `elif` block in `_lazy_import()` are permitted. Current allowlist:

| Category | Libraries |
|----------|-----------|
| **Data** | pandas, numpy, scipy, scikit-learn, xgboost, lightgbm, statsmodels, pingouin |
| **Visualization** | matplotlib, seaborn, plotly |
| **Excel/Docs** | openpyxl, xlsxwriter, python-docx, reportlab |
| **PDF** | pdfplumber, PyPDF2, pypdfium2 |
| **OCR/Image** | Pillow (PIL), pytesseract, pdf2image |
| **Standard Library** | os, re, json, csv, math, statistics, datetime, collections, itertools, functools, io, copy, hashlib, base64, pathlib, random, time, calendar, string, struct, textwrap, pprint, dataclasses, typing, glob, shutil, zipfile, warnings, abc, enum, weakref, importlib, pkgutil |
| **XML/HTML** | lxml, xml, beautifulsoup4 |
| **Other** | tabulate, requests, sympy, decimal, fractions, urllib |

---

## Configuration

### Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `DATABASE_URL` | Yes | PostgreSQL connection string |
| `API_KEY` | Yes | API key for authenticated routes |
| `RAILWAY_PUBLIC_DOMAIN` | Auto | Set by Railway for public URL generation |
| `PORT` | Auto | Set by Railway (default: 8080) |
| `MS_CLIENT_ID` | For MS tools | Azure AD application client ID |
| `MS_CLIENT_SECRET` | For MS tools | Azure AD application client secret |
| `MS_TENANT_ID` | For MS tools | Azure AD tenant ID |
| `LOG_LEVEL` | No | Logging level (default: INFO) |

### Operational Limits

| Limit | Value |
|-------|-------|
| Max file size | 50 MB |
| File TTL | 72 hours |
| Max execution time | 300s (floor: 100s) |
| Max memory | 16,384 MB |
| Max concurrent jobs | 4 |
| Job timeout | 1,800s |
| MCP response cap | 50,000 chars |

---

## Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/health` | Public | Health check |
| `GET` | `/mcp/sse` | Public | MCP SSE transport (standard clients) |
| `POST` | `/mcp/sse` | Public | MCP JSON-RPC direct (SimTheory) |
| `GET` | `/dl/{file_id}/{filename}` | Public | File download |
| `GET` | `/charts/{session_id}/{filename}` | Public | Chart image serving |
| `*` | `/api/*` | API Key | REST API routes |
| `GET` | `/docs` | Public | OpenAPI documentation |

---

## Deployment

Power Interpreter is deployed on Railway, linked to this repository.

```bash
# Railway auto-deploys on push to main
git push origin main
```

### Docker Build

```dockerfile
FROM python:3.12-slim
# Includes: gcc, libpq-dev, curl, tesseract-ocr, poppler-utils
```

### Startup Sequence

1. Database initialization (PostgreSQL)
2. Microsoft token table setup
3. Skills layer initialization
4. Periodic cleanup task (hourly)
5. Uvicorn on `0.0.0.0:$PORT`

### Deploy Verification

Watch for these lines in Railway logs:

```
Skills layer: 1 skill tools registered          main.py:111
Power Interpreter ready!                         main.py:127
Application startup complete.
GET /health 200 OK
```

---

## Version History

| Version | Date | Changes |
|---------|------|---------|
| v3.0.1 | 2026-03-18 | Pre-execution syntax guard, context pressure guard |
| v3.0.0 | 2026-03-17 | Context pressure guard, per-tool response caps |
| v2.11.0 | 2026-03-20 | OCR support (PIL, pytesseract, pdf2image, pypdfium2) |
| v2.10.0 | 2026-03-19 | Namespace preamble, skills layer integration |
| v2.9.2 | 2026-03-16 | Response guardrails, smart truncation |
| v2.9.1 | 2026-03-15 | Empty args recovery for model-agnostic error handling |
| v2.9.0 | 2026-03-14 | Token optimization (~57% tool description reduction) |
| v2.8.6 | 2026-03-13 | Timeout floor enforcement (100s minimum) |
| v2.8.5 | 2026-03-12 | python-docx + transitive dependency support |
| v2.8.4 | 2026-03-11 | datetime module injection fix |
| v2.8.3 | 2026-03-10 | Sandbox path recognition (/app/sandbox_data) |
| v2.8.2 | 2026-03-09 | Read-only upload access |
| v2.8.1 | 2026-03-08 | /tmp/ path interception |
| v2.8.0 | 2026-03-07 | Defensive path normalization |
| v1.9.0 | 2026-02-20 | Microsoft OneDrive + SharePoint integration |

---

## Author

Built by **MCA (Model Context Architect)** for **Timothy Escamilla**, Bolthouse Fresh Foods.

Powered by [SimTheory.ai](https://simtheory.ai) · Deployed on [Railway](https://railway.app)
