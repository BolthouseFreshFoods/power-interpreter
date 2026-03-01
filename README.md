# Power Interpreter MCP

**Sandboxed Python Code Execution + Data Engine + Microsoft 365 Integration** — Model Context Protocol (MCP) compatible

A secure, persistent, database-backed Python execution environment designed for AI agents. Executes code in a controlled sandbox with resource limits, restricted imports, auto-captured charts, file storage with download URLs, SQL-queryable dataset management, and full Microsoft OneDrive/SharePoint integration.

> **Owner:** BolthouseFreshFoods  
> **Runtime:** Railway (Docker)  
> **Database:** PostgreSQL 17 (Railway-managed)  
> **Version:** v1.9.1 (server) / v2.8.4 (executor)

---

## Table of Contents

- [Architecture](#architecture)
- [Features](#features)
- [MCP Tools](#mcp-tools)
- [Data Pipeline](#data-pipeline)
- [Microsoft 365 Integration](#microsoft-365-integration)
- [Environment Variables](#environment-variables)
- [Deployment](#deployment)
- [Sandbox Security Model](#sandbox-security-model)
- [Allowed Libraries](#allowed-libraries)
- [Changelog](#changelog)
- [Repository Structure](#repository-structure)
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
│  Power Interpreter MCP Server (v1.9.1)          │
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
│  │ Data Manager (v1.9.1)                       │ │
│  │ • Multi-format loading (CSV, Excel, PDF,    │ │
│  │   JSON, Parquet)                            │ │
│  │ • Chunked loading (50K rows/chunk)          │ │
│  │ • Auto-indexing (date + ID columns)         │ │
│  │ • SQL query engine (SELECT only)            │ │
│  └──────┬──────────────────────────────────────┘ │
│         │                                        │
│  ┌──────▼──────────────────────────────────────┐ │
│  │ Microsoft 365 Integration (v1.9.0)          │ │
│  │ • OneDrive file management (20 tools)       │ │
│  │ • SharePoint sites, libraries, lists        │ │
│  │ • Device code OAuth flow                    │ │
│  │ • Token persistence in PostgreSQL           │ │
│  └──────┬──────────────────────────────────────┘ │
│         │                                        │
│  ┌──────▼──────────────────────────────────────┐ │
│  │ PostgreSQL 17                               │ │
│  │ • Session state persistence                 │ │
│  │ • File storage with download URLs           │ │
│  │ • Job queue (async execution)               │ │
│  │ • Dataset storage + SQL querying            │ │
│  │ • Microsoft OAuth token persistence         │ │
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
| **Auto Chart Capture** | `plt.show()` interception, `savefig()` tracking, and post-execution figure sweep. Charts returned as inline base64 ImageContent blocks |
| **File Storage** | Generated files (xlsx, csv, pdf, png, etc.) auto-stored in Postgres with public download URLs |
| **Path Normalization** | Handles `/tmp/` paths, doubled session prefixes, Windows paths — all redirected to sandbox |
| **Upload Access** | Read-only access to uploaded files from external directories |
| **Resource Limits** | Configurable timeout, memory limits, max file size |
| **Job Queue** | Async job submission for long-running tasks |
| **Multi-Format Data Loading** | Load CSV, Excel, PDF (table extraction), JSON, and Parquet into PostgreSQL for SQL querying. Handles 1.5M+ rows via chunked loading |
| **SQL Query Engine** | Execute SELECT queries against loaded datasets with pagination, auto-indexing on date/ID columns |
| **Microsoft 365** | Full OneDrive + SharePoint integration — browse, upload, download, search, share files across Microsoft 365 |
| **MCP Compatible** | Full MCP protocol support — SSE transport + JSON-RPC direct POST (32 tools) |

---

## MCP Tools

Power Interpreter exposes **32 tools** via the MCP protocol:

### Core Tools (12)

| Tool | Description |
|------|-------------|
| `execute_code` | Execute Python code in the sandbox (sync, <60s) |
| `submit_job` | Submit code for async background execution |
| `get_job_status` | Check status of an async job |
| `get_job_result` | Retrieve results of a completed job |
| `upload_file` | Upload a file (base64) into the sandbox |
| `fetch_file` | Retrieve a file from the sandbox |
| `fetch_from_url` | Download a file from any HTTPS URL into the sandbox |
| `list_files` | List files in the sandbox directory |
| `load_dataset` | Load a data file into PostgreSQL (auto-detects format) |
| `query_dataset` | Execute SQL SELECT queries against loaded datasets |
| `list_datasets` | List all loaded datasets |
| `create_session` | Create a new isolated execution session |

### Microsoft 365 Tools (20)

| Tool | Description |
|------|-------------|
| `ms_auth_status` | Check Microsoft 365 authentication status |
| `ms_auth_start` | Start Microsoft device code login flow |
| `onedrive_list_files` | List files/folders in OneDrive |
| `onedrive_search` | Search OneDrive by name or content |
| `onedrive_download_file` | Download file from OneDrive (base64) |
| `onedrive_upload_file` | Upload file to OneDrive |
| `onedrive_create_folder` | Create folder in OneDrive |
| `onedrive_delete_item` | Delete file/folder from OneDrive |
| `onedrive_move_item` | Move item in OneDrive |
| `onedrive_copy_item` | Copy item in OneDrive |
| `onedrive_share_item` | Create sharing link for OneDrive item |
| `sharepoint_list_sites` | List/search SharePoint sites |
| `sharepoint_get_site` | Get SharePoint site details |
| `sharepoint_list_drives` | List document libraries in a SharePoint site |
| `sharepoint_list_files` | List files in a SharePoint library |
| `sharepoint_download_file` | Download file from SharePoint (base64) |
| `sharepoint_upload_file` | Upload file to SharePoint |
| `sharepoint_search` | Search within a SharePoint site |
| `sharepoint_list_lists` | List SharePoint lists |
| `sharepoint_list_items` | List items in a SharePoint list |

### Connection Endpoints

| Transport | Endpoint | Use Case |
|-----------|----------|----------|
| SSE | `GET /mcp/sse` | Standard MCP clients |
| JSON-RPC | `POST /mcp/sse` | SimTheory and direct integrations |
| Health | `GET /health` | Railway health checks |

---

## Data Pipeline

The data pipeline enables AI agents to load, query, and analyze structured data at scale using SQL.

### Supported Formats

| Format | Extensions | Loading Method |
|--------|-----------|----------------|
| CSV / TSV | `.csv`, `.tsv`, `.txt` | Chunked (50K rows/chunk) |
| Excel | `.xlsx`, `.xls`, `.xlsm`, `.xlsb` | Full read → chunked insert |
| PDF | `.pdf` | Table extraction via pdfplumber → chunked insert |
| JSON | `.json` | Array of objects or records → chunked insert |
| Parquet | `.parquet`, `.pq` | Full read → chunked insert |

### Workflow

```
Step 1: fetch_from_url(url="https://example.com/data.xlsx", filename="data.xlsx")
Step 2: load_dataset(file_path="data.xlsx", dataset_name="sales")
Step 3: query_dataset(sql="SELECT region, SUM(revenue) FROM data_xxx GROUP BY region")
```

### Features

- **Auto-format detection** from file extension
- **Chunked loading** — handles 1.5M+ rows without memory issues
- **Auto-indexing** — creates indexes on date and ID columns
- **SQL safety** — only SELECT queries allowed; DROP/DELETE/UPDATE/INSERT blocked
- **Pagination** — configurable LIMIT/OFFSET for large result sets
- **Session isolation** — datasets can be scoped to sessions (UUID or default)

---

## Microsoft 365 Integration

Full OneDrive and SharePoint integration via Microsoft Graph API.

### Setup

1. Register an Azure AD application with the following API permissions:
   - `Files.ReadWrite.All`
   - `Sites.ReadWrite.All`
   - `User.Read`
2. Set environment variables: `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET`
3. Use `ms_auth_start` tool to initiate device code login flow
4. Tokens are persisted to PostgreSQL — survives container restarts

### Capabilities

- Browse, search, upload, download, move, copy, delete files in OneDrive
- Browse SharePoint sites, document libraries, and lists
- Upload/download files to/from SharePoint
- Create sharing links for collaboration
- Graceful degradation — if Azure credentials are not configured, the 12 core tools still work

---

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `DATABASE_URL` | Yes | — | PostgreSQL connection string |
| `API_KEY` | Yes | — | Authentication key for MCP endpoint access |
| `PORT` | No | `8080` | Server port (Railway sets automatically) |
| `MAX_EXECUTION_TIME` | No | `600` | Default code execution timeout (seconds) |
| `MAX_MEMORY_MB` | No | `4096` | Maximum memory per execution |
| `MAX_CONCURRENT_KERNELS` | No | `6` | Maximum concurrent session kernels |
| `SANDBOX_FILE_MAX_MB` | No | `50` | Maximum file size for Postgres storage |
| `SANDBOX_FILE_TTL_HOURS` | No | `72` | File expiration time (0 = no expiry) |
| `MAX_CONCURRENT_JOBS` | No | `4` | Maximum concurrent async jobs |
| `JOB_TIMEOUT` | No | `1800` | Async job timeout (seconds) |
| `PUBLIC_BASE_URL` | No | auto-detected | Base URL for generated download links |
| `AZURE_TENANT_ID` | No | — | Azure AD tenant ID (for Microsoft 365) |
| `AZURE_CLIENT_ID` | No | — | Azure AD application client ID |
| `AZURE_CLIENT_SECRET` | No | — | Azure AD application client secret |

---

## Deployment

### Railway (Production)

This repository auto-deploys to Railway on push to `main`.

**Prerequisites:**
1. Railway project with this repo connected
2. PostgreSQL service added to the same project
3. `DATABASE_URL` linked via Railway reference variable
4. `API_KEY` set as a service variable
5. (Optional) Azure AD credentials for Microsoft 365 integration

**Deploy flow:**
```
git push origin main → Railway detects → Docker build → Deploy → Health check (GET /health → 200)
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

### SQL Safety
Dataset queries are restricted to `SELECT` statements only. The following operations are explicitly blocked: `DROP`, `DELETE`, `UPDATE`, `INSERT`, `ALTER`, `TRUNCATE`, `GRANT`.

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
| `pdfplumber` | — | PDF text/table extraction |
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

### v1.9.1 — Safe Microsoft init + UUID session fix (2026-03-01)

**Server changes:**
- **FIX:** Microsoft bootstrap moved to BOTTOM of `mcp_server.py` (after all 12 base tool registrations). Previously at top of file — if Microsoft import failed, zero tools registered. Now Microsoft failure can never take down core tools.
- **FIX:** `_safe_parse_uuid()` helper added to `app/engine/data_manager.py`. The MCP server passes `session_id="default"` when no explicit session is created. `uuid.UUID("default")` was crashing with "badly formed hexadecimal UUID string". Now gracefully returns `None` for non-UUID session IDs.
- **FIX:** `psycopg2-binary` added to `requirements.txt`. Was missing — caused `ModuleNotFoundError` when data_manager tried to connect to PostgreSQL via sync engine for `pandas.to_sql()`.

**Bug chain (all three were masked):**
1. `psycopg2` missing → import crash before reaching UUID code
2. UUID parse crash → hidden behind psycopg2 crash
3. Wrong file patched (`app/data_manager.py` vs `app/engine/data_manager.py`) → route imports from `app/engine/`

### v1.9.0 — Microsoft OneDrive + SharePoint integration (2026-02-28)
- 20 new MCP tools for OneDrive and SharePoint via Microsoft Graph API
- Device code OAuth flow for authentication
- Token persistence to PostgreSQL (survives container restarts)
- Safe initialization — skips gracefully if Azure credentials not configured
- Environment variables: `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET`

### v1.8.2 — Universal data loading (2026-02-28)
- `load_dataset` tool updated to auto-detect file format from extension
- Backend `data_manager.py` uses `detect_format()` + format-specific readers
- Supported: CSV, Excel (.xlsx/.xls), PDF (table extraction), JSON, Parquet
- `/api/data/load-csv` endpoint preserved as backwards-compatible alias

### v1.8.1 — Inline chart rendering fix (2026-02-27)
- When `inline_images[]` is empty (common case due to executor race condition), scan stdout for `/dl/{uuid}/{filename}` URLs via regex
- Fetch bytes from internal `/dl/` route, base64 encode, return as MCP ImageContent block
- Strip markdown image syntax from stdout text block to prevent SimTheory URL rewriting (→ 404)

### v1.8.0 — Chart ImageContent blocks (2026-02-27)
- Charts rendered inline via base64 ImageContent blocks
- Relied on `inline_images[]` JSON array (empty due to executor race — fixed in v1.8.1)

### v1.7.2 — fetch_from_url route fix (2026-02-27)
- Fixed 404: was POSTing to `/api/files/fetch-from-url` (doesn't exist). Correct route: `/api/files/fetch`

### v1.7.1 — FastMCP constructor fix (2026-02-27)
- Removed unsupported `description` kwarg from `FastMCP()` constructor

### v1.7.0 — fetch_from_url tool (2026-02-27)
- New tool: stream files directly from any HTTPS URL into sandbox
- Fixes file upload blocker — no base64 overhead, no SimTheory encoding bug

### v1.6.0 — Auto file handling (2026-02-26)
- Rewrote tool descriptions for reliable AI chaining: `fetch_file` → `execute_code`, `upload_file` → `execute_code`, `fetch_file` → `load_dataset` → `query_dataset`

### v2.8.4 — datetime module injection fix (2026-02-28)
**Executor change:**
- Added `datetime` to `_lazy_import()` with explicit module handling
- Added `timedelta`, `timezone`, `date` as top-level convenience aliases
- Added guard in `_preprocess_code` from-import handler to prevent overwriting module with its own class

### v2.8.3 — Sandbox path recognition
- Added `/app/sandbox_data` to `ALLOWED_READ_PATHS` and `LEGITIMATE_READ_PREFIXES`

### v2.8.2 — Read-only upload access
- `ALLOWED_READ_PATHS` for uploaded files outside sandbox
- `safe_open()` permits read-only access to upload directories

### v2.8.1 — /tmp/ path interception
- Intercept `/tmp/`, `/var/tmp/`, `/temp/` paths and redirect to sandbox
- Windows-style paths (`C:\...`) also intercepted

### v2.8.0 — Defensive path normalization
- `_normalize_path()` helper strips doubled session prefix

### v2.7.0 — reportlab + PDF backend
- Added `reportlab` to sandbox allowlist
- Added `matplotlib.backends.backend_pdf` import for PdfPages support

### v2.6 — Inline chart rendering
- `plt.show()` replacement captures all open figures as PNG
- `Figure.savefig()` wrapper tracks explicitly saved images
- Post-execution sweep captures unclosed figures

### v2.1 — Auto file storage
- Generated files auto-stored in Postgres with download URLs

### v2.0 — Persistent session state
- Variables survive across calls within a session
- Kernel manager with configurable max concurrent kernels

---

## Repository Structure

```
power-interpreter/
├── app/
│   ├── main.py                # FastAPI app + MCP server setup
│   ├── config.py              # Settings and environment variables
│   ├── database.py            # PostgreSQL connection + table creation
│   ├── models.py              # SQLAlchemy models (SandboxFile, Job, Dataset)
│   ├── engine/
│   │   ├── executor.py        # Sandbox executor (v2.8.4) — the core
│   │   ├── kernel_manager.py  # Persistent session kernel management
│   │   ├── data_manager.py    # Dataset loading + SQL query engine (v1.9.1)
│   │   └── mcp_server.py      # MCP protocol handler (32 tools)
│   ├── microsoft/
│   │   ├── bootstrap.py       # Microsoft 365 initialization
│   │   ├── auth.py            # OAuth device code flow + token management
│   │   ├── graph.py           # Microsoft Graph API client
│   │   └── mcp_tools.py       # 20 OneDrive/SharePoint MCP tools
│   └── routes/
│       ├── files.py           # File download routes (/dl/{id}/{filename})
│       └── data.py            # Data loading + query routes (/api/data/*)
├── Dockerfile                 # Production Docker image
├── docker-compose.yml         # Local development setup
├── requirements.txt           # Python dependencies
├── .env.example               # Environment variable template
└── README.md                  # This file
```

---

## License

Private repository. All rights reserved by Bolthouse Fresh Foods.
