# Power Interpreter MCP

**A production Model Context Protocol (MCP) server that gives AI assistants the ability to execute Python code, access Microsoft OneDrive/SharePoint files, and perform data analysis — all within a secure, sandboxed environment.**

> Built by **GROW by Bolthouse Fresh** · Architected by MCA

---

## Overview

Power Interpreter bridges AI assistants (via Simtheory.ai or any MCP-compatible client) to a full Python execution environment with live Microsoft 365 file access. Users authenticate independently through device code flow, ensuring each person's session is scoped to their own permissions.

| | |
|---|---|
| **Version** | 2.9.1 |
| **Tools Registered** | 22 |
| **Runtime** | Python 3.x on Railway |
| **Transport** | MCP SSE + JSON-RPC direct |
| **Authentication** | Per-user device code flow (Microsoft Graph API) |
| **Database** | PostgreSQL (token persistence, session data) |

---

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│  Simtheory.ai / MCP Client                                   │
│  (POST /mcp/sse — JSON-RPC direct)                           │
└──────────────┬───────────────────────────────────────────────┘
               │
               ▼
┌──────────────────────────────────────────────────────────────┐
│  Power Interpreter MCP Server (FastAPI + Uvicorn)             │
│                                                               │
│  ┌─────────────┐  ┌──────────────┐  ┌─────────────────┐      │
│  │ mcp_server   │  │ tools        │  │ bootstrap       │      │
│  │ (22 tools)   │  │ (OneDrive/SP)│  │ (kernel init)   │      │
│  └──────┬──────┘  └──────┬───────┘  └────────┬────────┘      │
│         │                │                    │               │
│         ▼                ▼                    ▼               │
│  ┌────────────────────────────────────────────────────────┐   │
│  │  Response Budget Guard (Change #10)                    │   │
│  │  Max 50K tokens per tool response │ Pagination         │   │
│  └────────────────────────┬───────────────────────────────┘   │
│                           │                                   │
│                           ▼                                   │
│  ┌────────────────────────────────────────────────────────┐   │
│  │  SandboxJobQueue (Change #9)                           │   │
│  │  Async request queue with backpressure                 │   │
│  │  Max concurrent: 4  │  Queue: 20  │  Timeout: 30s     │   │
│  └────────────────────────┬───────────────────────────────┘   │
│                           │                                   │
│                           ▼                                   │
│  ┌────────────────────────────────────────────────────────┐   │
│  │  KernelHealthMonitor (Change #9)                       │   │
│  │  Heartbeat probe │ Auto-restart on failure             │   │
│  └────────────────────────┬───────────────────────────────┘   │
│                           │                                   │
│                           ▼                                   │
│  ┌────────────────────────────────────────────────────────┐   │
│  │  File Handler (Change #11)                             │   │
│  │  Chunked transfer │ Excel↔CSV │ Size guardrails        │   │
│  └────────────────────────┬───────────────────────────────┘   │
│                           │                                   │
│                           ▼                                   │
│  ┌────────────────────────────────────────────────────────┐   │
│  │  Python Sandbox Kernel (/usr/local/bin/python3)        │   │
│  │  Max Memory: 16 GB  │  Max Jobs: 4  │  Timeout: 30m   │   │
│  └────────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────┘
               │                    │
               ▼                    ▼
     ┌──────────────┐     ┌──────────────────┐
     │  PostgreSQL   │     │  Microsoft Graph  │
     │  (tokens,     │     │  API (per-user    │
     │   sessions)   │     │   device code)    │
     └──────────────┘     └──────────────────┘
```

---

## Authentication Model

Power Interpreter uses a **per-user, independent authentication model**. This is a deliberate design choice:

- Each user authenticates via **device code flow** against Microsoft Entra ID
- Graph API calls are made **using that user's token**, not an application-level token
- File access is scoped to **what that specific user has permission to see**
- A `403 Forbidden` means the file isn't shared with that user — not a system error

**Why not `Files.Read.All`?**  
Granting blanket tenant-wide file access to the application would allow any authenticated user to access any file through the MCP. The per-user model respects individual sharing permissions and avoids the security implications of global tenant access.

---

## Tools (22)

Power Interpreter registers 22 tools across three categories:

### Code Execution
| Tool | Description |
|------|-------------|
| `execute_code` | Execute Python code in the sandboxed kernel |
| `install_package` | Install pip packages into the sandbox |
| `list_files` | List files in the sandbox working directory |
| `upload_file` | Upload a file into the sandbox |
| `download_file` | Download a file from the sandbox |

### Microsoft OneDrive / SharePoint
| Tool | Description |
|------|-------------|
| `resolve_share_link` | Download a file from a OneDrive/SharePoint sharing link |
| `list_onedrive_files` | List files in a user's OneDrive |
| `search_onedrive` | Search OneDrive by filename or content |

### Authentication & Session
| Tool | Description |
|------|-------------|
| `ms_auth_poll` | Initiate or check device code authentication |
| `ms_auth_status` | Check current authentication status |

*Plus additional utility and management tools.*

---

## Pre-loaded Packages

The sandbox kernel comes pre-loaded with a comprehensive data science and document processing stack:

| Category | Packages |
|----------|----------|
| **Data Analysis** | pandas, numpy, scipy, scikit-learn, statsmodels |
| **Visualization** | matplotlib, seaborn |
| **Excel/Spreadsheet** | openpyxl, xlsxwriter |
| **PDF Processing** | pdfplumber, pypdf |
| **Document Creation** | python-docx, python-pptx |
| **Web/HTTP** | requests, httpx, beautifulsoup4, lxml |
| **Image Processing** | Pillow |
| **Utilities** | tqdm, tabulate, jinja2, regex, chardet, python-dateutil, pytz |

---

## Project Structure

```
power-interpreter/
├── main.py              # FastAPI app, Uvicorn server, startup configuration
├── mcp_server.py        # MCP protocol handler, tool dispatch, SSE transport
├── tools.py             # Tool implementations (OneDrive, SharePoint, execution)
├── bootstrap.py         # Kernel initialization, package pre-loading
├── sandbox_startup.py   # Sandbox environment setup
├── requirements.txt     # Python dependencies
├── pyproject.toml       # Project metadata
├── Dockerfile           # Production container (Railway)
├── .env.example         # Environment variable template
├── migrations/          # Database migrations (PostgreSQL)
└── docs/
    └── CHANGE-REQUESTS.md  # Staged improvements (11 items)
```

---

## Deployment

### Railway (Production)

The server is deployed on Railway with auto-deploy on push to `main`.

| Setting | Value |
|---------|-------|
| **URL** | `https://power-interpreter-production-6396.up.railway.app` |
| **Port** | `8080` (via `PORT` env var) |
| **Builder** | Nixpacks |
| **Health Check** | `GET /health` |
| **Database** | Railway-managed PostgreSQL |

### Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/health` | GET | Health check (Railway + monitoring) |
| `/mcp/sse` | GET | MCP SSE transport (standard clients) |
| `/mcp/sse` | POST | MCP JSON-RPC direct (Simtheory.ai) |
| `/api/execute` | POST | Internal kernel execution (localhost only) |
| `/dl/{file_id}` | GET | Public file download (no auth) |
| `/charts/{session_id}/{filename}` | GET | Public chart access (no auth) |

### Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `PORT` | Yes | Server port (Railway sets this automatically) |
| `DATABASE_URL` | Yes | PostgreSQL connection string |
| `API_KEY` | Yes | MCP server API key |
| `AZURE_CLIENT_ID` | Yes | Microsoft Entra app registration client ID |
| `AZURE_TENANT_ID` | Yes | Microsoft Entra tenant ID |
| `SIMTHEORY_AUTH_TOKEN` | No | Simtheory.ai MCP registration token |
| `SANDBOX_MAX_CONCURRENT` | No | Max simultaneous sandbox jobs (default: 4) |
| `SANDBOX_QUEUE_SIZE` | No | Max jobs waiting in queue (default: 20) |
| `SANDBOX_QUEUE_TIMEOUT` | No | Seconds to wait for a slot (default: 30) |
| `KERNEL_HEALTH_INTERVAL` | No | Seconds between health checks (default: 10) |
| `KERNEL_HEALTH_TIMEOUT` | No | Seconds before probe fails (default: 5) |
| `MAX_TOOL_RESPONSE_TOKENS` | No | Max tokens per tool response (default: 50000) |
| `FILE_LIST_PAGE_SIZE` | No | Default files per page (default: 50) |
| `FILE_CHUNK_SIZE` | No | Chunk size for file transfers (default: 5 MB) |
| `MAX_UPLOAD_SIZE` | No | Max individual file size (default: 50 MB) |
| `MAX_SANDBOX_STORAGE` | No | Max storage per session (default: 2 GB) |
| `EXCEL_AUTO_CONVERT_THRESHOLD` | No | Row count triggering CSV conversion (default: 1048576) |

---

## Sandbox Limits

| Limit | Value |
|-------|-------|
| Max file size | 50 MB (configurable) |
| File TTL | 72 hours |
| Max execution time | 300 seconds |
| Max memory | 16,384 MB (16 GB) |
| Max concurrent jobs | 4 (configurable) |
| Job queue depth | 20 (configurable) |
| Queue timeout | 30 seconds (configurable) |
| Job timeout | 1,800 seconds (30 minutes) |
| Max storage per session | 2 GB (configurable) |
| File chunk size | 5 MB (configurable) |
| Excel auto-convert threshold | 1,048,576 rows |
| Sandbox directory | `/app/sandbox_data` |

---

## Data Handling (Staged — Change #11)

Power Interpreter is designed to handle production-scale data files intelligently:

### Chunked File Transfer
- Large files are uploaded and downloaded in **configurable chunks** (default 5 MB)
- Transfers support **resume on failure** — no need to restart from scratch
- Memory-efficient streaming — never loads full file into RAM

### Smart Format Conversion (Excel ↔ CSV)

| Condition | Action | Output |
|-----------|--------|--------|
| Rows ≤ 1,048,576 | Keep as requested | `.xlsx` |
| Rows > 1,048,576 | Auto-convert + notify user | `.csv` |
| File > 25 MB | Warn about Excel performance | `.xlsx` with warning |
| File > 50 MB | Reject or chunk transfer | Error / chunked `.csv` |
| Multi-sheet workbook | Keep as Excel | `.xlsx` |

### File Size Guardrails
- Individual file size enforced at transfer layer (not just config)
- Per-session storage tracked and limited (default 2 GB)
- Clear, actionable error messages when limits are exceeded

---

## Resilience Model (Staged — Change #9)

Power Interpreter is designed to handle concurrent multi-user load gracefully:

```
Request arrives
      │
      ▼
┌─────────────────┐
│ SandboxJobQueue  │  Slot available?
└────────┬────────┘
    ┌────┴────┐
    │ YES     │ NO → Wait up to 30s → Timeout? → 503 + retry_after
    ▼         │
┌─────────────┴───┐
│ KernelHealth     │  Kernel alive?
└────────┬────────┘
    ┌────┴────┐
    │ YES     │ NO → Auto-restart → Proceed
    ▼         ▼
┌─────────────────┐
│ Execute tool     │
└────────┬────────┘
         ▼
    Return result
```

- **No more 500s** from slot exhaustion — requests queue and wait
- **Self-healing kernel** — crashed kernels restart automatically
- **Structured 503** — LLM receives actionable retry guidance instead of opaque errors

---

## Simtheory.ai Integration

Power Interpreter is registered as an MCP tool in the GROW by Bolthouse Fresh workspace on Simtheory.ai.

**Connection Configuration:**
- **SSE URL:** `https://power-interpreter-production-6396.up.railway.app/mcp/sse`
- **Transport:** JSON-RPC direct (POST to `/mcp/sse`)
- **Auth Token:** Set via workspace admin at `grow.bolthousefresh.com/chat/workspace/admin`

---

## Staged Improvements

See [`docs/CHANGE-REQUESTS.md`](docs/CHANGE-REQUESTS.md) for 11 staged performance, observability, stability, and data handling improvements identified from production log analysis:

| # | Change | Type | Priority |
|---|--------|------|----------|
| 1 | stderr → stdout logging fix | Bug Fix | High |
| 2 | Single-call file download | Performance | High |
| 3 | Trim response payload | Performance | High |
| 4 | httpx connection pooling | Performance | Medium |
| 5 | Cache tools/list manifest | Performance | Low |
| 6 | Consolidate SSE response | Performance | Medium |
| 7 | Batch file processing | Feature | Critical |
| 8 | Structured request logging | Observability | High |
| 9 | Sandbox resilience & request queuing | Stability | Critical |
| 10 | Response size guardrails & pagination | Stability | Critical |
| 11 | Chunked file transfer & smart format conversion | Data Handling | Critical |

### Shipping Strategy

| Release | Items | Focus |
|---------|-------|-------|
| **Release 1** | 1, 2, 3, 5, 8 | Quick wins — logging, download speed, observability |
| **Release 2** | 9, 10 | Stability — eliminates 500s and context overflow |
| **Release 3** | 11 | Data handling — chunked transfers, format conversion |
| **Release 4** | 4, 6 | Infrastructure — transport and HTTP client optimization |
| **Release 5** | 7 | Feature — batch processing (30-40x speedup) |

---

## License

Private — Bolthouse Fresh Foods. All rights reserved.
