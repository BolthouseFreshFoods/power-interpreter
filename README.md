# 🔌 Power Interpreter MCP

**v2.9.1** — General-purpose sandboxed Python execution engine with PostgreSQL storage, async job queue, and large dataset support (1.5M+ rows). Deployed on Railway for SimTheory.ai integration.

> **Author:** Kaffer AI for Timothy Escamilla  
> **Organization:** Bolthouse Fresh Foods / New Carrot Farms LLC  
> **Platform:** [SimTheory.ai](https://simtheory.ai) MCP Integration

---

## Overview

Power Interpreter is a Model Context Protocol (MCP) server that provides AI assistants with Python code execution, file management, data analysis, and Microsoft 365 integration capabilities — all running in a secure sandboxed environment.

### Key Capabilities

| Category | Features |
|---|---|
| **Code Execution** | Sandboxed Python with pre-installed data science libraries |
| **Data** | PostgreSQL storage, 1.5M+ row datasets, pandas/numpy |
| **Files** | Upload, download, persistent storage with public URLs |
| **Jobs** | Async queue for long-running operations (no timeouts) |
| **Microsoft 365** | OneDrive + SharePoint integration (22 tools) |
| **Charts** | Auto-generated chart serving with persistent URLs |
| **MCP** | Full MCP protocol support (SSE + JSON-RPC) |

---

## Architecture

```
SimTheory.ai ──► Power Interpreter MCP (Railway)
                    ├── Sandbox Executor (Python)
                    ├── Kernel Manager (persistent sessions)
                    ├── Job Manager (async queue)
                    ├── File Manager (Postgres + /dl/ URLs)
                    ├── Data Manager (1.5M+ row datasets)
                    ├── Microsoft Graph Client (OneDrive/SharePoint)
                    └── PostgreSQL (storage, tokens, sessions)
```

---

## MCP Tool Registry (34 tools)

All tools below are registered as MCP tools and callable by AI agents via the MCP protocol.

### Core MCP Tools — 12 tools (mcp_server.py)

| # | Tool | Description |
|---|---|---|
| 1 | `execute_code` | Execute Python in persistent sandbox |
| 2 | `fetch_from_url` | Download file from HTTPS URL into sandbox |
| 3 | `upload_file` | Upload base64-encoded file to sandbox |
| 4 | `fetch_file` | Download file from URL (alias for fetch_from_url) |
| 5 | `list_files` | List files in sandbox with size/type info |
| 6 | `submit_job` | Submit long-running async job (up to 30 min) |
| 7 | `get_job_status` | Check async job status |
| 8 | `get_job_result` | Retrieve completed job results |
| 9 | `load_dataset` | Load file into PostgreSQL for SQL querying |
| 10 | `query_dataset` | Execute SQL against loaded datasets |
| 11 | `list_datasets` | List datasets in PostgreSQL |
| 12 | `create_session` | Create isolated workspace session |

### Microsoft 365 MCP Tools — 22 tools (microsoft/tools.py)

| # | Tool | Description |
|---|---|---|
| 1 | `ms_auth_status` | Check Microsoft 365 authentication status |
| 2 | `ms_auth_start` | Start Microsoft device login flow |
| 3 | `ms_auth_poll` | Complete Microsoft device login |
| 4 | `resolve_share_link` | Resolve SharePoint/OneDrive sharing URL to file |
| 5 | `onedrive_list_files` | List files and folders in OneDrive |
| 6 | `onedrive_search` | Search OneDrive by name or content |
| 7 | `onedrive_download_file` | Download file from OneDrive to sandbox |
| 8 | `onedrive_upload_file` | Upload file to OneDrive (max 4MB) |
| 9 | `onedrive_create_folder` | Create folder in OneDrive |
| 10 | `onedrive_delete_item` | Delete file or folder from OneDrive |
| 11 | `onedrive_move_item` | Move file or folder in OneDrive |
| 12 | `onedrive_copy_item` | Copy file or folder in OneDrive |
| 13 | `onedrive_share_item` | Create sharing link for OneDrive item |
| 14 | `sharepoint_list_sites` | List or search accessible SharePoint sites |
| 15 | `sharepoint_get_site` | Get details of a specific SharePoint site |
| 16 | `sharepoint_list_drives` | List document libraries in a site |
| 17 | `sharepoint_list_files` | List files in a document library |
| 18 | `sharepoint_download_file` | Download file from SharePoint to sandbox |
| 19 | `sharepoint_upload_file` | Upload file to SharePoint (max 4MB) |
| 20 | `sharepoint_search` | Search files in a SharePoint site |
| 21 | `sharepoint_list_lists` | List SharePoint lists in a site |
| 22 | `sharepoint_list_items` | List items in a SharePoint list |

**Total: 34 MCP tools (12 core + 22 Microsoft)**

---

## Endpoints

| Endpoint | Method | Auth | Purpose |
|---|---|---|---|
| `/health` | GET | None | Health check |
| `/mcp/sse` | GET | None | MCP SSE transport (standard clients) |
| `/mcp/sse` | POST | None | MCP JSON-RPC direct (SimTheory) |
| `/dl/{file_id}` | GET | None | Public file downloads |
| `/charts/{session_id}/{filename}` | GET | None | Chart image serving |
| `/api/execute` | POST | API Key | Direct code execution |
| `/api/jobs/*` | Various | API Key | Job management |
| `/api/files/*` | Various | API Key | File management |
| `/api/data/*` | Various | API Key | Dataset management |
| `/api/sessions/*` | Various | API Key | Session management |
| `/docs` | GET | None | OpenAPI documentation |

---

## Configuration

### Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `API_KEY` | Yes | — | API key for protected endpoints |
| `DATABASE_URL` | Yes | — | PostgreSQL connection string |
| `PORT` | No | `8080` | Server port |
| `LOG_LEVEL` | No | `INFO` | Logging level |
| `SANDBOX_DIR` | No | `/app/sandbox_data` | Sandbox file directory |
| `SANDBOX_FILE_MAX_MB` | No | `50` | Max file size (MB) |
| `SANDBOX_FILE_TTL_HOURS` | No | `72` | File expiration (hours) |
| `MAX_EXECUTION_TIME` | No | `300` | Code execution timeout (seconds) |
| `MAX_MEMORY_MB` | No | `16384` | Max memory per execution (MB) |
| `MAX_CONCURRENT_JOBS` | No | `4` | Max parallel async jobs |
| `JOB_TIMEOUT` | No | `1800` | Async job timeout (seconds) |
| `RAILWAY_PUBLIC_DOMAIN` | Auto | — | Set by Railway for public URLs |

### Microsoft 365 Integration

| Variable | Required | Description |
|---|---|---|
| `AZURE_CLIENT_ID` | Yes* | Azure AD app client ID |
| `AZURE_TENANT_ID` | Yes* | Azure AD tenant ID |

*Required only if Microsoft integration is enabled.

---

## Deployment

### Railway (Production)

The app is deployed on Railway with automatic deploys from the `main` branch.

```
Railway Project: power-interpreter
Service: power-interpreter
Environment: production
URL: https://power-interpreter-production-6396.up.railway.app
```

### Docker

```bash
docker build -t power-interpreter .
docker run -p 8080:8080 \
  -e API_KEY=your-key \
  -e DATABASE_URL=postgresql://... \
  power-interpreter
```

---

## Version History

| Version | Date | Changes |
|---|---|---|
| **v2.9.1** | 2026-03-04 | Smart error handling for empty execute_code args, version alignment, tool count fix |
| **v2.9.0** | 2026-03-03 | Trimmed all 34 tool descriptions for token optimization (~57% reduction) |
| **v1.9.2** | 2026-02-28 | Token persistence rewrite (SQLAlchemy), ms_auth_poll tool |
| **v1.9.0** | 2026-02-27 | Microsoft OneDrive + SharePoint integration (22 new MCP tools) |
| **v1.8.1** | 2026-02-25 | Chart serving route + inline base64 image blocks |
| **v1.7.2** | 2026-02-23 | fetch_from_url route fix, stable release |
| **v1.0.0** | 2026-02-14 | Initial release — Power Interpreter MCP v1.0 |

---

## Project Structure

```
power-interpreter/
├── app/
│   ├── __init__.py              # Package init + version (2.9.1)
│   ├── main.py                  # FastAPI app, lifespan, MCP JSON-RPC handler
│   ├── mcp_server.py            # MCP server: 12 core tools + MS bootstrap
│   ├── config.py                # Settings and environment config
│   ├── auth.py                  # API key authentication
│   ├── database.py              # PostgreSQL connection management
│   ├── models.py                # SQLAlchemy models
│   ├── data_manager.py          # Large dataset operations
│   ├── fetch_from_url.py        # URL content fetching
│   ├── engine/
│   │   ├── executor.py          # Sandboxed Python execution engine
│   │   ├── kernel_manager.py    # Kernel lifecycle management
│   │   ├── job_manager.py       # Async job queue
│   │   ├── file_manager.py      # File storage and serving
│   │   ├── data_manager.py      # Dataset PostgreSQL operations
│   │   └── memory_guard.py      # Memory limit enforcement
│   ├── microsoft/
│   │   ├── auth_manager.py      # Microsoft OAuth + token persistence
│   │   ├── graph_client.py      # Microsoft Graph API client
│   │   ├── tools.py             # 22 SharePoint/OneDrive MCP tools
│   │   ├── mcp_tools.py         # Deprecated redirect → tools.py
│   │   └── bootstrap.py         # Microsoft integration bootstrap
│   └── routes/
│       ├── execute.py           # /api/execute endpoints
│       ├── jobs.py              # /api/jobs endpoints
│       ├── files.py             # /api/files + /dl/ endpoints
│       ├── data.py              # /api/data endpoints
│       ├── sessions.py          # /api/sessions endpoints
│       └── health.py            # /health endpoint
├── patches/                     # Bug fix patches (pending integration)
│   ├── fix_empty_args_validation.py
│   ├── fix_kernel_persistence.py
│   └── kernel_startup.py
├── Dockerfile
├── requirements.txt
├── railway.toml
├── start.py
├── .env.example
└── README.md
```

---

## Pending Fixes (Branch: `fix/kernel-persistence-and-preload`)

### 1. Empty Args Validation
Improved error response when `execute_code` is called with empty arguments. Returns MCP-compliant structured error with retry instructions.

### 2. Kernel State Persistence
Persistent kernel manager that maps `session_id` → kernel instance, keeping Python state (imports, variables) alive across consecutive `execute_code` calls.

### 3. Library Pre-load
Pre-imports common libraries (PDF, data, document handling) when new kernels are created, so frequently needed tools are immediately available.

---

## License

Proprietary — Bolthouse Fresh Foods / New Carrot Farms LLC. All rights reserved.
