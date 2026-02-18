# Power Interpreter MCP

> A production-grade sandboxed Python execution engine with persistent sessions, inline chart rendering, and automatic file delivery — built for [SimTheory.ai](https://simtheory.ai)

[![Deployed on Railway](https://img.shields.io/badge/Deployed%20on-Railway-blueviolet)](https://railway.app)
[![MCP Protocol](https://img.shields.io/badge/Protocol-MCP%201.6-green)](https://modelcontextprotocol.io)
[![Python 3.12](https://img.shields.io/badge/Python-3.12-blue)](https://python.org)

## What It Does

Power Interpreter gives AI agents (like Kaffer on SimTheory) the ability to **write and execute Python code** in a secure sandbox — with results, charts, and files delivered back to the user inline in the chat.

Think of it as **Code Interpreter, but self-hosted**, with persistent state, large dataset support, and domain-customizable tools.

### Key Capabilities

| Capability | Description |
|---|---|
| **Sandboxed Execution** | Python code runs in a restricted environment with resource limits, import whitelisting, and filesystem isolation |
| **Persistent Sessions** | Variables, DataFrames, and imports survive across multiple code executions within a session — like a Jupyter notebook |
| **Inline Chart Rendering** | matplotlib/seaborn charts are auto-captured as PNG and rendered directly in the chat |
| **File Downloads** | Generated files (Excel, CSV, PDF, etc.) are stored in PostgreSQL and served via authenticated download URLs |
| **Large Dataset Ingestion** | Load 500K+ row CSV/Excel files into PostgreSQL via chunked ingestion, then query with SQL |
| **Async Job Queue** | Long-running operations (30+ minute timeout) run in the background with polling for status |
| **MCP Protocol** | Native integration with any MCP-compatible client (SimTheory, Claude Desktop, etc.) |

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  SimTheory.ai / Claude Desktop / Any MCP Client                │
└──────────────────────────┬──────────────────────────────────────┘
                           │ MCP Protocol (JSON-RPC over SSE)
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│  main.py — MCP Direct Transport (Streamable HTTP)              │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  POST /mcp/sse → JSON-RPC dispatch                      │   │
│  │  11 tools: execute_code, submit_job, upload_file, etc.  │   │
│  └─────────────────────────────────────────────────────────┘   │
└──────────────────────────┬──────────────────────────────────────┘
                           │ HTTP (internal)
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│  FastAPI Application (app/)                                     │
│                                                                 │
│  ┌──────────────┐  ┌──────────────┐  ┌───────────────────────┐ │
│  │ /api/execute  │  │ /api/jobs/*  │  │ /api/data/*           │ │
│  │ Sync execute  │  │ Async queue  │  │ Dataset load/query    │ │
│  └──────┬───────┘  └──────┬───────┘  └───────────┬───────────┘ │
│         │                 │                       │             │
│         ▼                 ▼                       ▼             │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  SandboxExecutor (engine/executor.py)                   │   │
│  │  • Import preprocessing & whitelisting                  │   │
│  │  • Code compilation & exec() in isolated globals        │   │
│  │  • Chart auto-capture (plt.show, savefig, unclosed)     │   │
│  │  • File detection & Postgres storage                    │   │
│  │  • Resource limits (time, memory)                       │   │
│  └──────┬──────────────────────────────────┬───────────────┘   │
│         │                                  │                    │
│         ▼                                  ▼                    │
│  ┌──────────────────┐  ┌──────────────────────────────────┐    │
│  │  KernelManager   │  │  PostgreSQL                      │    │
│  │  (kernel_mgr.py) │  │  • sandbox_files (binary blobs)  │    │
│  │  • Per-session    │  │  • datasets (chunked ingestion)  │    │
│  │    globals dict   │  │  • jobs (async queue state)      │    │
│  │  • Idle timeout   │  │                                  │    │
│  │  • Max 6 kernels  │  │  /dl/{file_id}/{filename}        │    │
│  └──────────────────┘  │  → Authenticated file downloads   │    │
│                         └──────────────────────────────────┘    │
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  Sandbox Directory (/tmp/sandbox/{session_id}/)         │   │
│  │  • Isolated per session                                 │   │
│  │  • Charts saved as PNG                                  │   │
│  │  • Generated files (xlsx, csv, pdf, etc.)               │   │
│  └─────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

## How It Works

### 1. Persistent Sessions (Kernel Manager)

Each session maintains a **persistent Python namespace** (`sandbox_globals` dict) that survives across multiple `execute_code` calls:

```
Call 1: df = pd.DataFrame(...)     → kernel CREATED, df stored in globals
Call 2: df.plot(kind='bar')        → kernel REUSED, df still available
Call 3: df.to_excel('report.xlsx') → kernel REUSED, df still available
```

Sessions expire after idle timeout and are cleaned up automatically. Max 6 concurrent kernels.

### 2. Chart Auto-Capture

Three mechanisms ensure charts are always captured:

1. **`plt.show()` interception** — replaced with a function that saves all open figures as PNG
2. **`Figure.savefig()` tracking** — wraps savefig to track explicitly saved images
3. **Post-execution sweep** — captures any unclosed figures as a safety net

Captured PNGs are stored in PostgreSQL and served via `/dl/` URLs that render inline in SimTheory.

### 3. File Storage Pipeline

```
Code generates file → executor detects new files in sandbox dir
  → files stored in PostgreSQL (sandbox_files table)
  → authenticated /dl/{uuid}/{filename} URL generated
  → URL appended to stdout → passed through MCP to client
  → SimTheory renders download link / inline image
```

Supported file types: `.xlsx`, `.xls`, `.csv`, `.tsv`, `.json`, `.pdf`, `.png`, `.jpg`, `.jpeg`, `.svg`, `.html`, `.txt`, `.md`, `.zip`, `.parquet`

### 4. Import Sandboxing

The executor uses a **whitelist-based import system**. All `import` and `from ... import` statements are preprocessed:

- **Allowed modules** are lazily loaded into the sandbox globals
- **Blocked modules** are commented out with `# [sandbox] BLOCKED`
- Common aliases are auto-resolved (`plt`, `sns`, `go`, `px`, `sm`, etc.)

## Deployment

### Railway Setup

1. Create new project in [Railway](https://railway.app)
2. Connect this GitHub repo
3. Add PostgreSQL plugin
4. Set environment variables (see below)
5. Deploy — Railway auto-builds from `Dockerfile`

### Environment Variables

| Variable | Description | Default |
|---|---|---|
| `API_KEY` | API authentication key | *(required)* |
| `DATABASE_URL` | PostgreSQL connection string | *(auto from Railway)* |
| `PUBLIC_BASE_URL` | Public URL for file downloads (e.g., `https://your-app.up.railway.app`) | *(required for file URLs)* |
| `MAX_EXECUTION_TIME` | Max sync execution (seconds) | `300` |
| `MAX_MEMORY_MB` | Memory limit per execution | `4096` |
| `MAX_FILE_SIZE_MB` | Max upload file size | `500` |
| `MAX_CONCURRENT_JOBS` | Parallel job limit | `4` |
| `JOB_TIMEOUT` | Max async job time (seconds) | `600` |
| `SANDBOX_FILE_MAX_MB` | Max file size for Postgres storage | `50` |
| `SANDBOX_FILE_TTL_HOURS` | File expiration time | `24` |

## MCP Tools

Power Interpreter exposes **11 tools** via the MCP protocol:

### Code Execution
| Tool | Description | Mode |
|---|---|---|
| `execute_code` | Run Python code with persistent session state | Sync |
| `submit_job` | Submit long-running code (up to 30 min) | Async |
| `get_job_status` | Check async job progress | Sync |
| `get_job_result` | Retrieve completed job output | Sync |

### File Management
| Tool | Description | Mode |
|---|---|---|
| `upload_file` | Upload a file to the sandbox | Sync |
| `fetch_file` | Download a file from URL/Google Drive into sandbox | Sync |
| `list_files` | List files in the sandbox directory | Sync |

### Data Management
| Tool | Description | Mode |
|---|---|---|
| `load_dataset` | Load CSV/Excel into PostgreSQL (chunked) | Async |
| `query_dataset` | Run SQL queries against loaded datasets | Sync |
| `list_datasets` | List all loaded datasets | Sync |

### Session Management
| Tool | Description | Mode |
|---|---|---|
| `create_session` | Create a named workspace with isolated file space | Sync |

## Pre-installed Libraries

### Data Analysis
| Library | Version | Description |
|---|---|---|
| pandas | 2.2.3 | DataFrames, data manipulation |
| numpy | 2.2.1 | Numerical computing |
| openpyxl | 3.1.5 | Excel read/write (.xlsx) |
| xlsxwriter | 3.2.0 | Excel write with formatting |
| pdfplumber | 0.11.4 | PDF text/table extraction |
| tabulate | 0.9.0 | Pretty-print tables |

### PDF Generation
| Library | Version | Description |
|---|---|---|
| reportlab | 4.1.0 | Professional PDF creation with tables, styles, headers |

### Visualization
| Library | Version | Description |
|---|---|---|
| matplotlib | 3.10.0 | Charts, plots (auto-captured as PNG) |
| seaborn | 0.13.2 | Statistical visualizations |
| plotly | 5.24.1 | Interactive charts |

### Statistics & Machine Learning
| Library | Version | Description |
|---|---|---|
| scipy | 1.15.1 | Scientific computing |
| scikit-learn | 1.6.1 | Machine learning |
| statsmodels | 0.14.4 | Statistical models |

### Standard Library (Available in Sandbox)
`math`, `statistics`, `datetime`, `collections`, `itertools`, `functools`, `re`, `json`, `csv`, `io`, `pathlib`, `copy`, `hashlib`, `base64`, `decimal`, `fractions`, `random`, `time`, `calendar`, `pprint`, `dataclasses`, `typing`, `textwrap`, `string`, `struct`, `os`, `urllib`

## API Endpoints

### Quick Execution
```bash
curl -X POST https://your-app.up.railway.app/api/execute \
  -H "X-API-Key: your-key" \
  -H "Content-Type: application/json" \
  -d '{"code": "import pandas as pd; print(pd.__version__)", "session_id": "default", "timeout": 30}'
```

### File Downloads
```
GET /dl/{file_id}/{filename}
```
No authentication required — file IDs are UUIDs (unguessable). Files expire after `SANDBOX_FILE_TTL_HOURS`.

### Async Jobs
```bash
# Submit
POST /api/jobs/submit
{"code": "...", "timeout": 600}
→ {"job_id": "abc-123", "status": "pending"}

# Poll
GET /api/jobs/abc-123/status
→ {"status": "running", "elapsed_ms": 5000}

# Retrieve
GET /api/jobs/abc-123/result
→ {"status": "completed", "stdout": "...", "result": {...}}
```

### Dataset Management
```bash
# Load CSV into PostgreSQL
POST /api/data/load-csv
{"file_path": "data.csv", "dataset_name": "my_data"}

# Query with SQL
POST /api/data/query
{"sql": "SELECT * FROM data_xxx WHERE amount > 100", "limit": 1000}
```

### Health Check
```
GET /health
→ {"status": "ok", "version": "2.6.0"}
```

## Security

| Layer | Protection |
|---|---|
| **Authentication** | API key via `X-API-Key` header (MCP and REST) |
| **Import Sandboxing** | Whitelist-only imports — blocked modules are commented out at preprocessing |
| **Filesystem Isolation** | `safe_open()` restricts file I/O to session sandbox directory |
| **Resource Limits** | Time limits (configurable), memory limits via `resource.setrlimit` |
| **SQL Safety** | Dataset queries restricted to SELECT only |
| **File Downloads** | UUID-based URLs (unguessable), TTL expiration |

## Comparison: Power Interpreter vs. Code Interpreter

| Capability | OpenAI Code Interpreter | Power Interpreter |
|---|---|---|
| Dataset size | ~100MB, crashes on large files | 500K+ rows via chunked PostgreSQL |
| Data persistence | Dies with session | PostgreSQL — survives restarts |
| Async jobs | 60-second hard timeout | 30-minute timeout, background execution |
| External data | Upload through chat UI only | `fetch_file` from URLs, Google Drive |
| Infrastructure | Black box | Full Railway logs, real-time debug |
| MCP integration | Not available | Native MCP protocol |
| Session isolation | Single sandbox | Named sessions with isolated file spaces |
| Inline charts | ✅ Built-in | ✅ Auto-captured matplotlib/seaborn |
| File downloads | ✅ Built-in | ✅ Postgres-backed with authenticated URLs |
| Session state | ✅ Persistent | ✅ Persistent (KernelManager) |
| Custom domain tools | ❌ Not possible | ✅ Extensible MCP tools |

## Development Roadmap

| Priority | Feature | Status |
|---|---|---|
| ~~P1~~ | ~~File Output & Visualization~~ | ✅ **Shipped** — Charts render inline, files download |
| ~~P2~~ | ~~Persistent Python Kernel~~ | ✅ **Shipped** — KernelManager with session state |
| P3 | Structured Error Handling & Retry | 🟡 Partial — AI self-corrects from tracebacks |
| P4 | Auto File Handling from Chat | 🟡 Partial — AI routes to upload_file |
| P5 | Domain-Specific Financial Tools | ❌ Not started — **the competitive moat** |

## Version History

| Version | Date | Changes |
|---|---|---|
| 2.6.0 | Feb 2026 | Fix import alias override bug, robust chart capture, reportlab support |
| 2.5.0 | Feb 2026 | Stop stripping URLs from stdout, fix inline chart rendering |
| 2.1.0 | Feb 2026 | Auto file storage in Postgres, download URLs |
| 2.0.0 | Feb 2026 | Persistent kernel sessions (KernelManager) |
| 1.0.0 | Jan 2026 | Initial release — sandboxed execution, async jobs, dataset support |

## Author

Built by **Kaffer AI** for **Timothy Escamilla**

## License

Private — All rights reserved
