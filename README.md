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
| **Microsoft 365** | OneDrive + SharePoint integration (21 tools) |
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

## Tool Registry

### Core Tools (22)

| # | Tool | Description |
|---|---|---|
| 1 | `execute_code` | Execute Python code in sandboxed environment |
| 2 | `upload_file` | Upload file to sandbox storage |
| 3 | `download_file` | Download file from sandbox |
| 4 | `list_files` | List files in sandbox directory |
| 5 | `delete_file` | Delete a sandbox file |
| 6 | `submit_job` | Submit async job for long-running operations |
| 7 | `get_job_status` | Check async job status |
| 8 | `get_job_result` | Retrieve completed job results |
| 9 | `cancel_job` | Cancel a running job |
| 10 | `list_jobs` | List all jobs for a session |
| 11 | `load_dataset` | Load large dataset into PostgreSQL |
| 12 | `query_dataset` | Query dataset with SQL |
| 13 | `list_datasets` | List available datasets |
| 14 | `delete_dataset` | Delete a dataset |
| 15 | `get_dataset_info` | Get dataset schema and stats |
| 16 | `create_session` | Create new execution session |
| 17 | `get_session` | Get session details |
| 18 | `list_sessions` | List active sessions |
| 19 | `delete_session` | Delete a session |
| 20 | `fetch_from_url` | Fetch content from URL into sandbox |
| 21 | `get_sandbox_status` | Get sandbox environment status |
| 22 | `get_chart_url` | Get public URL for generated chart |

### Microsoft 365 Tools (21)

| # | Tool | Description |
|---|---|---|
| 1 | `ms_auth` | Initiate Microsoft OAuth device code flow |
| 2 | `ms_auth_poll` | Poll for OAuth completion |
| 3 | `ms_auth_status` | Check current auth status |
| 4 | `ms_logout` | Clear Microsoft tokens |
| 5 | `sharepoint_list_sites` | List accessible SharePoint sites |
| 6 | `sharepoint_get_site` | Get site details by name or URL |
| 7 | `sharepoint_list_drives` | List document libraries for a site |
| 8 | `sharepoint_list_files` | List files in a drive/folder |
| 9 | `sharepoint_search_files` | Search files across SharePoint |
| 10 | `sharepoint_download_file` | Download file to sandbox |
| 11 | `sharepoint_upload_file` | Upload file from sandbox to SharePoint |
| 12 | `sharepoint_create_folder` | Create folder in document library |
| 13 | `sharepoint_delete_item` | Delete file or folder |
| 14 | `sharepoint_move_item` | Move file or folder |
| 15 | `sharepoint_copy_item` | Copy file or folder |
| 16 | `sharepoint_get_file_info` | Get file metadata and properties |
| 17 | `sharepoint_get_sharing_link` | Create sharing link for file |
| 18 | `onedrive_list_files` | List files in user's OneDrive |
| 19 | `onedrive_download_file` | Download from OneDrive to sandbox |
| 20 | `onedrive_upload_file` | Upload from sandbox to OneDrive |
| 21 | `onedrive_search` | Search files in OneDrive |

**Total: 43 MCP tools**

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
| `MS_CLIENT_ID` | Yes* | Azure AD app client ID |
| `MS_TENANT_ID` | Yes* | Azure AD tenant ID |
| `MS_CLIENT_SECRET` | No | For service principal auth |

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
| **v2.9.1** | 2026-03-04 | Smart error handling for empty execute_code args (model-agnostic) |
| **v2.9.0** | 2026-03-03 | Trimmed all 34→43 tool descriptions for token optimization (~57% reduction) |
| **v1.9.2** | 2026-02-28 | Token persistence rewrite (SQLAlchemy), ms_auth_poll tool |
| **v1.9.0** | 2026-02-27 | Microsoft OneDrive + SharePoint integration (20 new MCP tools) |
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
│   ├── mcp_server.py            # MCP server setup + tool registration
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
│   │   ├── tools.py             # SharePoint/OneDrive tool definitions
│   │   ├── mcp_tools.py         # MCP tool wrappers
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
