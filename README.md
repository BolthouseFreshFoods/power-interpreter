# Power Interpreter MCP

**General-purpose sandboxed Python execution engine for AI agents.**  
Designed for [SimTheory.ai](https://simtheory.ai) MCP integration.

> Version: **3.0.3** · Executor: **v2.11.0** · Skills Engine: **v1.0.0**  
> Deploy target: [Railway](https://railway.app)

---

## What It Does

Power Interpreter gives AI agents a full Python runtime — sandboxed, persistent, and wired into Microsoft 365. Agents can execute code, process files, query datasets, generate charts, run OCR on scanned documents, and orchestrate multi-step workflows through the MCP (Model Context Protocol) interface.

### Key Capabilities

- **Sandboxed Python execution** with persistent session state (kernel architecture)
- **40+ pre-installed libraries** — pandas, numpy, scipy, scikit-learn, matplotlib, plotly, openpyxl, reportlab, Pillow, and more
- **OCR & PDF-to-Image** — pytesseract, pdf2image, pypdfium2 for handwritten/scanned document processing
- **Async job queue** for long-running operations
- **Sandbox backpressure guard** — queued execution with graceful `503 sandbox_busy` when saturated
- **Large dataset support** — 1.5M+ rows via PostgreSQL + DuckDB
- **Auto file storage** — generated files stored in Postgres with public download URLs
- **Chart auto-capture** — matplotlib/plotly figures saved and served automatically
- **Microsoft OneDrive + SharePoint integration** — per-user delegated auth
- **Skills Layer** — multi-step workflow orchestration via registered skills
- **Syntax Guard** — catches truncated/broken code before sandbox execution
- **Response Budget + Smart Truncation** — protects MCP sessions from oversized payloads
- **Structured MCP observability** — tool timing, session correlation, and status logging

---

## Architecture

```text
┌──────────────────────────────────────────────────────────────┐
│                        SimTheory.ai                           │
│                   (MCP Client / AI Agent)                    │
└────────────────┬──────────────────────────┬──────────────────┘
                 │ GET /mcp/sse             │ POST /mcp/sse
                 │ (standard SSE)           │ (JSON-RPC direct)
                 ▼                          ▼
┌──────────────────────────────────────────────────────────────┐
│                   Power Interpreter MCP                      │
│                    (FastAPI + FastMCP)                       │
├──────────────────────────────────────────────────────────────┤
│  23 MCP Tools                                                │
│  ├── 12 Core (execute, files, jobs, sessions, data)         │
│  ├──  4 Microsoft (ms_auth, onedrive, sharepoint,           │
│  │       resolve_share_link)                                 │
│  ├──  2 Admin (ms_auth_clear, ms_auth_list_users)           │
│  └──  4 Skill Tools                                          │
├──────────────────────────────────────────────────────────────┤
│  Guards                                                      │
│  ├── Syntax Guard                                            │
│  ├── Context Pressure Guard                                  │
│  ├── Response Guard / Smart Truncation                       │
│  └── Response Budget Enforcement                             │
├──────────────────────────────────────────────────────────────┤
│  Engine                                                      │
│  ├── SandboxExecutor (v2.11.0)                               │
│  ├── KernelManager (persistent sessions)                     │
│  ├── JobManager (async queue)                                │
│  ├── SandboxQueue (backpressure / graceful 503)              │
│  ├── SkillEngine (workflow orchestration)                    │
│  └── UserTracker (per-session identity)                      │
├──────────────────────────────────────────────────────────────┤
│  Storage                                                     │
│  ├── PostgreSQL (sessions, files, tokens, datasets)          │
│  └── /app/sandbox_data (ephemeral working filesystem)        │
└──────────────────────────────────────────────────────────────┘
```

---

## MCP Tools

## Total: **23 tools**

### Core Tools (12)

| Tool | Description |
|------|-------------|
| `execute_code` | Run Python code in a sandboxed session with persistent state |
| `create_session` | Create a new execution session |
| `delete_session` | Soft-delete a session |
| `list_files` | List files in a session sandbox |
| `upload_file` | Upload a file to the sandbox |
| `fetch_file` | Download/fetch a file into the sandbox |
| `fetch_from_url` | Fetch a file from a URL into the sandbox |
| `submit_job` | Submit a long-running async job |
| `get_job_status` | Check async job status |
| `get_job_result` | Retrieve final async job result |
| `load_dataset` | Load a dataset into PostgreSQL |
| `query_dataset` | Query a dataset with SQL |
| `list_datasets` | List available datasets |

> Note: the historical docs sometimes referenced `create_dataset`; current runtime logs show `load_dataset`, `query_dataset`, and `list_datasets`.

### Microsoft Tools (4)

| Tool | Description |
|------|-------------|
| `ms_auth` | Authenticate with Microsoft 365 |
| `onedrive` | List, download, and upload files to OneDrive |
| `sharepoint` | Access SharePoint sites and document libraries |
| `resolve_share_link` | Resolve OneDrive/SharePoint sharing links |

### Admin Tools (2)

| Tool | Description |
|------|-------------|
| `ms_auth_clear` | Clear cached Microsoft tokens for a user |
| `ms_auth_list_users` | List authenticated Microsoft users |

### Skill Tools (4)

| Tool | Description |
|------|-------------|
| `skill_consolidate_files` | Consolidate files into a workbook |
| `skill_ocr_pdf_to_excel` | OCR PDF workflow to Excel output |
| `skill_data_to_report` | Transform analyzed data into report output |
| `skill_batch_ocr_pipeline` | Batch OCR multi-step processing pipeline |

---

## Skills Layer

Skills are server-side multi-step workflows that orchestrate existing MCP tools with validation, retries, and error handling.

```text
app/
├── skills_integration.py
└── skills/
    ├── __init__.py
    ├── engine.py
    ├── wrapper.py
    ├── consolidate_files.py
    ├── ocr_pdf_to_excel.py
    ├── data_to_report.py
    └── batch_ocr_pipeline.py
```

### How Skills Work

1. **Registration** — skills are registered with the `SkillEngine` during startup
2. **Wrapping** — each skill is exposed as an MCP-compatible tool wrapper
3. **Merging** — skill tools are merged into the MCP tool registry in `main.py`
4. **Execution** — wrappers delegate to `SkillEngine.execute()` and can call other MCP tools internally

---

## OCR & PDF-to-Image Support

Power Interpreter can process scanned and handwritten PDFs natively.

### 3-Layer Implementation

| Layer | File | What |
|-------|------|------|
| System | `Dockerfile` | `tesseract-ocr`, `tesseract-ocr-eng`, `poppler-utils` |
| Python | `requirements.txt` | `pytesseract`, `pdf2image`, `pypdfium2` |
| Sandbox | `executor.py` | allowlisted OCR/PDF/image imports |

### Example Usage

```python
from pdf2image import convert_from_path
import pytesseract
from PIL import Image

images = convert_from_path("/app/sandbox_data/scanned_document.pdf")

for i, img in enumerate(images):
    text = pytesseract.image_to_string(img)
    print(f"--- Page {i+1} ---")
    print(text)
```

---

## Sandbox Allowlist

The executor uses a whitelist-based import system. Only libraries explicitly supported in `_lazy_import()` are permitted.

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

## Guards and Protections

### Syntax Guard
Pre-execution validation catches truncated or malformed Python before sandbox time is wasted.

### Context Pressure Guard
Applies per-tool response caps and recovery behavior to reduce MCP context overload.

### Response Guard
Uses smart truncation to preserve useful output when responses get too large.

### Response Budget
Applies a hard response budget before content shaping to reduce runaway MCP payloads.

### Sandbox Queue / Backpressure
Execution is routed through a queue layer. When all slots are occupied, requests wait briefly and then return a structured busy response instead of opaque failures.

---

## Observability

Power Interpreter now emits structured MCP tool logs for easier Railway diagnosis.

### Structured Log Fields

- `event`
- `tool`
- `session_id`
- `status`
- `duration_ms`
- optional metadata such as `code_len`, `http_status`, `job_id`

### Example

```json
{
  "event": "tool_call",
  "session_id": "default",
  "user": "anonymous",
  "tool": "execute_code",
  "status": "success",
  "duration_ms": 58.6,
  "code_len": 2881
}
```

---

## Configuration

### Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `DATABASE_URL` | Yes | PostgreSQL connection string |
| `API_KEY` | Yes | API key for authenticated routes |
| `RAILWAY_PUBLIC_DOMAIN` | Auto | Public domain set by Railway |
| `PORT` | Auto | Bound port for Railway |
| `MS_CLIENT_ID` | For MS tools | Azure AD client ID |
| `MS_CLIENT_SECRET` | For MS tools | Azure AD client secret |
| `MS_TENANT_ID` | For MS tools | Azure AD tenant ID |
| `LOG_LEVEL` | No | Logging level, default `INFO` |
| `API_BASE_URL` | No | Internal base URL override for MCP → FastAPI calls |

### Operational Limits

| Limit | Value |
|-------|-------|
| Max file size | 50 MB |
| File TTL | 72 hours |
| Max execution time | 300s |
| Sync execute route cap | 60s route-level clamp |
| Executor timeout floor | 100s internal floor in executor |
| Max memory | 16,384 MB |
| Max concurrent jobs | 4 |
| Job timeout | 1,800s |
| Response soft cap | 50,000 chars |
| Image inline cap | 5 MB |

---

## Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/health` | Public | Health check |
| `GET` | `/mcp/sse` | Public | Standard MCP SSE transport |
| `POST` | `/mcp/sse` | Public | Direct MCP JSON-RPC endpoint |
| `GET` | `/dl/{file_id}/{filename}` | Public | File download |
| `GET` | `/charts/{session_id}/{filename}` | Public | Chart image serving |
| `*` | `/api/*` | API Key | Internal REST routes |
| `GET` | `/docs` | Public | OpenAPI docs |

---

## Deployment

Power Interpreter is deployed on Railway, linked to this repository.

```bash
git push origin main
```

Railway auto-deploys on push to `main`.

### Docker Build

```dockerfile
FROM python:3.12-slim
```

System packages include support for:
- PostgreSQL client libs
- OCR
- PDF rendering
- image processing

### Startup Sequence

1. Database initialization
2. Microsoft token table setup
3. Skills layer initialization
4. Periodic cleanup task startup
5. Uvicorn bind on `0.0.0.0:$PORT`

---

## Deploy Verification

Watch for these lines in Railway logs:

```text
Power Interpreter MCP v3.0.3 starting...
Database initialized successfully
Microsoft token persistence: ENABLED (Postgres)
Skills layer: 4 skill tools registered
Power Interpreter ready!
Application startup complete.
GET /health 200 OK
```

For MCP runtime verification, look for:

```text
MCP direct: method=initialize id=0
MCP direct: -> initialize response
MCP direct: method=tools/list id=2
MCP direct: -> 23 tools
{"event":"tool_call","tool":"execute_code", ...}
```

---

## Notes on Runtime Behavior

### Execution Success vs HTTP Success
`/api/execute` may return HTTP `200` even if the sandboxed code itself failed logically. MCP content blocks and structured logs distinguish logical execution success from transport success.

### Persistent Session Reuse
Logs show active kernel reuse across repeated calls to `session_id="default"`.

### File/Chart Sharing
Use returned `download_url` values exactly as emitted by tools. Do not reconstruct file URLs manually.

---

## Version History

| Version | Changes |
|---------|---------|
| v3.0.3 | Corrected MCP observability, reduced log noise, truthful execute_code status logging |
| v3.0.2 | Stdout logging fix, response budget enforcement, structured MCP tool logging |
| v3.0.1 | Pre-execution syntax guard, context pressure guard |
| v3.0.0 | Context pressure guard, per-tool response caps |
| v2.11.0 | OCR support (PIL, pytesseract, pdf2image, pypdfium2) |
| v2.10.0 | Namespace preamble, skills layer integration |
| v2.9.2 | Response guardrails, smart truncation |
| v2.9.1 | Empty args recovery for model-agnostic handling |
| v2.9.0 | Token optimization (~57% tool description reduction) |
| v1.9.0 | Microsoft OneDrive + SharePoint integration |

---

## Author

Built by **MCA (Model Context Architect)** for **Timothy Escamilla**.

Powered by [SimTheory.ai](https://simtheory.ai) · Deployed on [Railway](https://railway.app)
