# Power Interpreter MCP

**Sandboxed Python Code Execution Server** — Model Context Protocol (MCP) compatible

A secure, persistent, database-backed Python execution environment designed for AI agents. Executes code in a controlled sandbox with resource limits, restricted imports, auto-captured charts, and file storage with download URLs.

> **Owner:** BolthouseFreshFoods  
> **Runtime:** Railway (Docker)  
> **Database:** PostgreSQL 17 (Railway-managed)  
> **Version:** v1.8.1 (server) / v2.8.4 (executor)

---

## Table of Contents

- [Architecture](#architecture)
- [Features](#features)
- [MCP Tools](#mcp-tools)
- [Environment Variables](#environment-variables)
- [Deployment](#deployment)
- [Sandbox Security Model](#sandbox-security-model)
- [Allowed Libraries](#allowed-libraries)
- [Changelog](#changelog)
- [License](#license)

---

## Architecture

```
┌─────────────────────────────────────────────────┐
│  AI Agent (e.g., SimTheory, Claude, etc.)       │
│  Connects via MCP SSE or JSON-RPC POST          │
└──────────────────┬──────────────────────────────┘
                   │ HTTPS + API_KEY
                   ▼
┌─────────────────────────────────────────────────┐
│  Power Interpreter MCP Server (v1.8.1)          │
│  ┌─────────────┐  ┌──────────────────────────┐  │
│  │ MCP Router   │  │ Uvicorn (port 8080)      │  │
│  │ SSE + POST   │  │ FastAPI                  │  │
│  └──────┬──────┘  └──────────────────────────┘  │
│         │                                        │
│  ┌──────▼──────────────────────────────────────┐ │
│  │ Sandbox Executor (v2.8.4)                   │ │
│  │ • Import allowlist + lazy loading           │ │
│  │ • Path normalization + /tmp/ interception   │ │
│  │ • Chart auto-capture (matplotlib/plotly)    │ │
│  │ • File I/O sandboxing                       │ │
│  │ • Persistent kernel sessions                │ │
│  └──────┬──────────────────────────────────────┘ │
│         │                                        │
│  ┌──────▼──────────────────────────────────────┐ │
│  │ PostgreSQL 17                               │ │
│  │ • Session state persistence                 │ │
│  │ • File storage with download URLs           │ │
│  │ • Job queue (async execution)               │ │
│  │ • Dataset storage + querying                │ │
│  │ • Execution audit trail                     │ │
│  └─────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────┘
```

---

## Features

| Feature | Description |
|---------|-------------|
| **Sandboxed Execution** | Code runs in a restricted environment with blocked builtins (`eval`, `exec`, `compile`, `__import__`, etc.) |
| **Persistent Sessions** | Variables survive across calls within a session (kernel manager) |
| **Import Allowlist** | Only whitelisted libraries can be imported (see [Allowed Libraries](#allowed-libraries)) |
| **Auto Chart Capture** | `plt.show()` interception, `savefig()` tracking, and post-execution figure sweep |
| **File Storage** | Generated files (xlsx, csv, pdf, png, etc.) auto-stored in Postgres with public download URLs |
| **Path Normalization** | Handles `/tmp/` paths, doubled session prefixes, Windows paths — all redirected to sandbox |
| **Upload Access** | Read-only access to uploaded files from external directories |
| **Resource Limits** | Configurable timeout, memory limits, max file size |
| **Job Queue** | Async job submission for long-running tasks |
| **Dataset Management** | Load, query, and list datasets stored in Postgres |
| **MCP Compatible** | Full MCP protocol support — SSE transport + JSON-RPC direct POST |

---

## MCP Tools

Power Interpreter exposes **13 tools** via the MCP protocol:

| Tool | Description |
|------|-------------|
| `execute_code` | Execute Python code in the sandbox |
| `fetch_from_url` | Download a file from a URL into the sandbox |
| `upload_file` | Upload a file (base64) into the sandbox |
| `fetch_file` | Retrieve a file from the sandbox |
| `list_files` | List files in the sandbox directory |
| `submit_job` | Submit code for async background execution |
| `get_job_status` | Check status of an async job |
| `get_job_result` | Retrieve results of a completed job |
| `load_dataset` | Load a dataset into Postgres for querying |
| `query_dataset` | Run SQL-like queries against loaded datasets |
| `list_datasets` | List all loaded datasets |
| `create_session` | Create a new isolated execution session |

### Connection Endpoints

| Transport | Endpoint | Use Case |
|-----------|----------|----------|
| SSE | `GET /mcp/sse` | Standard MCP clients |
| JSON-RPC | `POST /mcp/sse` | SimTheory and direct integrations |
| Health | `GET /health` | Railway health checks |

---

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `DATABASE_URL` | Yes | — | PostgreSQL connection string. Enables sessions, jobs, datasets, file storage |
| `API_KEY` | Yes | — | Authentication key for MCP endpoint access. All requests must include this |
| `PORT` | No | `8080` | Server port (Railway sets this automatically) |
| `MAX_EXECUTION_TIME` | No | `600` | Default code execution timeout in seconds |
| `MAX_MEMORY_MB` | No | `4096` | Maximum memory per execution |
| `MAX_CONCURRENT_KERNELS` | No | `6` | Maximum concurrent session kernels |
| `SANDBOX_FILE_MAX_MB` | No | `50` | Maximum file size for Postgres storage |
| `SANDBOX_FILE_TTL_HOURS` | No | `72` | File expiration time (0 = no expiry) |
| `MAX_CONCURRENT_JOBS` | No | `4` | Maximum concurrent async jobs |
| `JOB_TIMEOUT` | No | `1800` | Async job timeout in seconds |
| `PUBLIC_BASE_URL` | No | auto-detected | Base URL for generated download links |

---

## Deployment

### Railway (Production)

This repository auto-deploys to Railway on push to `main`.

**Prerequisites:**
1. Railway project with this repo connected
2. PostgreSQL service added to the same project
3. `DATABASE_URL` linked via Railway reference variable
4. `API_KEY` set as a service variable

**Deploy flow:**
```
git push origin main → Railway detects → Docker build → Deploy → Health check
```

### Local Development

```bash
# Clone
git clone https://github.com/BolthouseFreshFoods/power-interpreter.git
cd power-interpreter

# Copy environment
cp .env.example .env
# Edit .env with your DATABASE_URL and API_KEY

# Run with Docker
docker-compose up --build

# Or run directly
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8080
```

---

## Sandbox Security Model

### Blocked Builtins
```
eval, exec, compile, __import__, globals, locals,
exit, quit, breakpoint, input
```

### File Access
- **Write:** Only within the session sandbox directory (`/app/sandbox_data/{session_id}/`)
- **Read:** Session directory + allowed upload paths (`/home/ubuntu/uploads/`, `/app/uploads/`, `/app/sandbox_data/`)
- **Blocked:** All other filesystem access

### Path Interception
Absolute paths commonly generated by AI are intercepted and redirected:
- `/tmp/file.csv` → `file.csv` (in sandbox)
- `/var/tmp/file.csv` → `file.csv` (in sandbox)
- `C:\Users\...\file.csv` → `file.csv` (in sandbox)
- `default/file.csv` → `file.csv` (when cwd is already the session dir)

Upload paths and `/app/sandbox_data/` paths are passed through for read-only access.

### Import Control
All imports are intercepted by `_preprocess_code()` and routed through `_lazy_import()`. Only whitelisted modules are loaded. Unrecognized imports are commented out with a `[sandbox] BLOCKED` annotation.

---

## Allowed Libraries

### Pre-loaded (always available)
| Library | Alias | Notes |
|---------|-------|-------|
| `pandas` | `pd` | DataFrame operations |
| `numpy` | `np` | Numerical computing |
| `json` | — | JSON parsing |
| `csv` | — | CSV parsing |
| `math` | — | Math functions |
| `statistics` | — | Statistical functions |
| `datetime` | — | **Module** (v2.8.4 fix) + `timedelta`, `timezone`, `date` aliases |
| `collections` | — | Counter, defaultdict, etc. |
| `itertools` | — | Iterator utilities |
| `functools` | — | Function utilities |
| `re` | — | Regular expressions |
| `io` | — | I/O streams |
| `copy` | — | Deep/shallow copy |
| `hashlib` | — | Hashing |
| `base64` | — | Base64 encoding |
| `pathlib.Path` | `Path` | Path operations |
| `Decimal` | — | Decimal arithmetic |
| `Fraction` | — | Fraction arithmetic |
| `typing` | — | Type hints (Dict, List, Optional, etc.) |

### Lazy-loaded (imported on first use)
| Library | Alias | Notes |
|---------|-------|-------|
| `matplotlib` | `plt` | Charts (Agg backend, auto-capture) |
| `seaborn` | `sns` | Statistical visualization |
| `plotly` | `px`, `go` | Interactive charts |
| `scipy` | — | Scientific computing (stats, optimize, interpolate) |
| `sklearn` | — | Machine learning |
| `statsmodels` | `sm` | Statistical models |
| `openpyxl` | — | Excel read/write (styles, charts, tables, formatting) |
| `xlsxwriter` | — | Excel write |
| `pdfplumber` | — | PDF text extraction |
| `reportlab` | — | PDF generation (platypus, pdfgen, canvas) |
| `requests` | — | HTTP requests |
| `tabulate` | — | Table formatting |
| `textwrap` | — | Text wrapping |
| `string` | — | String constants |
| `struct` | — | Binary data |
| `decimal` | — | Decimal module |
| `fractions` | — | Fractions module |
| `random` | — | Random number generation |
| `time` | — | Time functions |
| `calendar` | — | Calendar functions |
| `pprint` | — | Pretty printing |
| `dataclasses` | — | Data classes |
| `pathlib` | — | Full pathlib module |
| `os` | — | OS interface |
| `urllib` | — | URL handling |
| `shutil` | — | File operations |
| `glob` | — | File pattern matching |

---

## Changelog

### v2.8.4 — datetime module injection fix (2026-02-28)
**Root cause:** `_build_safe_globals` correctly injected the datetime MODULE, but `_preprocess_code`'s from-import handler rewrote `from datetime import datetime` to `datetime = datetime.datetime`, overwriting the module reference with the class. Since kernels are persistent, the module was permanently destroyed for the session.

**Fix:**
- Added `datetime` to `_lazy_import()` with explicit module handling
- Added `timedelta`, `timezone`, `date` as top-level convenience aliases
- Added guard in `_preprocess_code` from-import handler to prevent overwriting module with its own class
- `_lazy_import` returns `True` for datetime, so preprocessor comments out the import instead of rewriting destructively

### v2.8.3 — Sandbox path recognition
- Added `/app/sandbox_data` to `ALLOWED_READ_PATHS` and `LEGITIMATE_READ_PREFIXES`
- Absolute sandbox paths passed through unchanged

### v2.8.2 — Read-only upload access
- `ALLOWED_READ_PATHS` for uploaded files outside sandbox
- `safe_open()` permits read-only access to upload directories
- Write operations still restricted to session directory

### v2.8.1 — /tmp/ path interception
- Intercept `/tmp/`, `/var/tmp/`, `/temp/` paths and redirect to sandbox
- Windows-style paths (`C:\...`) also intercepted
- Pandas write hooks (`to_csv`, `to_excel`, `to_json`, `to_parquet`) added

### v2.8.0 — Defensive path normalization
- `_normalize_path()` helper strips doubled session prefix
- `safe_open()` auto-normalizes before resolving
- Pandas read hooks auto-normalize paths

### v2.7.0 — reportlab + PDF backend
- Added `reportlab` to sandbox allowlist
- Added `matplotlib.backends.backend_pdf` import for PdfPages support

### v2.6 — Inline chart rendering
- `plt.show()` replacement captures all open figures as PNG
- `Figure.savefig()` wrapper tracks explicitly saved images
- Post-execution sweep captures unclosed figures
- **Critical fix:** `import matplotlib.pyplot as plt` alias override bug

### v2.1 — Auto file storage
- Generated files auto-stored in Postgres with download URLs
- Storable extensions: xlsx, xls, csv, tsv, json, pdf, png, jpg, jpeg, svg, html, txt, md, zip, parquet

### v2.0 — Persistent session state
- Variables survive across calls within a session
- Kernel manager with configurable max concurrent kernels

---

## Repository Structure

```
power-interpreter/
├── app/
│   ├── main.py              # FastAPI app + MCP server setup
│   ├── config.py            # Settings and environment variables
│   ├── database.py          # PostgreSQL connection + table creation
│   ├── models.py            # SQLAlchemy models (SandboxFile, Job, Dataset)
│   ├── engine/
│   │   ├── executor.py      # Sandbox executor (v2.8.4) — the core
│   │   ├── kernel_manager.py # Persistent session kernel management
│   │   └── mcp_server.py    # MCP protocol handler
│   └── routes/
│       └── files.py         # File download routes (/dl/{id}/{filename})
├── Dockerfile               # Production Docker image
├── docker-compose.yml       # Local development setup
├── requirements.txt         # Python dependencies
├── .env.example             # Environment variable template
└── README.md                # This file
```

---

## License

Private repository. All rights reserved by Bolthouse Fresh Foods.
