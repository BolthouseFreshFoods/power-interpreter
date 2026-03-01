# Power Interpreter MCP

**General-purpose sandboxed Python execution engine with Microsoft 365 integration.**

Built for [SimTheory.ai](https://simtheory.ai) MCP integration. Deployed on [Railway](https://railway.app).

> **Current Version: 1.9.3a**

---

## Features

- **Sandboxed Python Execution** — Execute arbitrary Python code in a secure, isolated environment
- **Async Job Queue** — Long-running operations with no timeouts (up to 1800s)
- **Large Dataset Support** — 1.5M+ rows via PostgreSQL dataset engine
- **File Management** — Upload, download, and persist files with public download URLs
- **Pre-installed Data Science Libraries** — pandas, numpy, matplotlib, openpyxl, scikit-learn, etc.
- **Persistent Session State** — Kernel architecture maintains variables across executions
- **Auto File Storage** — Generated files stored in Postgres with public `/dl/{file_id}` URLs
- **Chart Serving** — Charts served at `/charts/{session_id}/{filename}`
- **Microsoft OneDrive Integration** — Browse, search, download, upload files from OneDrive
- **Microsoft SharePoint Integration** — Full CRUD on SharePoint sites, libraries, and lists
- **Sandbox File Bridge (v1.9.3)** — Downloaded OneDrive/SharePoint files are written directly to the sandbox filesystem, making them immediately accessible to `execute_code`

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    SimTheory.ai                         │
│              (MCP Client / AI Agent)                    │
└──────────────────────┬──────────────────────────────────┘
                       │ JSON-RPC over POST /mcp/sse
                       ▼
┌─────────────────────────────────────────────────────────┐
│              Power Interpreter (FastAPI)                 │
│                                                         │
│  ┌─────────────┐  ┌──────────────┐  ┌───────────────┐  │
│  │  MCP Server  │  │  Sandbox     │  │  Microsoft    │  │
│  │  (JSON-RPC)  │  │  Engine      │  │  Graph Client │  │
│  │             │  │  (execute)   │  │  (OneDrive +  │  │
│  │  33 tools   │  │              │  │  SharePoint)  │  │
│  └──────┬──────┘  └──────┬───────┘  └──────┬────────┘  │
│         │                │                  │           │
│         ▼                ▼                  ▼           │
│  ┌─────────────────────────────────────────────────┐    │
│  │           /app/sandbox_data (filesystem)         │    │
│  │  - execute_code reads/writes files here          │    │
│  │  - OneDrive downloads land here (v1.9.3)         │    │
│  │  - Charts and outputs saved here                 │    │
│  └─────────────────────────────────────────────────┘    │
│         │                                               │
│         ▼                                               │
│  ┌─────────────────────────────────────────────────┐    │
│  │              PostgreSQL (Railway)                │    │
│  │  - Sessions, jobs, datasets                      │    │
│  │  - Sandbox files (binary, with TTL)              │    │
│  │  - ms_tokens (OAuth tokens, encrypted)           │    │
│  └─────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────┘
```

---

## MCP Tools (33 total)

### Core Tools (12)
| Tool | Description |
|------|-------------|
| `execute_code` | Execute Python code in sandboxed environment |
| `create_session` | Create a new persistent execution session |
| `upload_file` | Upload a file to the sandbox |
| `fetch_from_url` | Download a file from a URL into the sandbox |
| `list_files` | List files in the sandbox |
| `submit_job` | Submit a long-running job (async) |
| `get_job_status` | Check job status |
| `get_job_result` | Retrieve job results |
| `upload_dataset` | Upload large dataset to PostgreSQL |
| `list_datasets` | List available datasets |
| `query_dataset` | SQL query against uploaded datasets |
| `delete_dataset` | Remove a dataset |

### Microsoft OneDrive Tools (11)
| Tool | Description |
|------|-------------|
| `ms_auth_start` | Start Microsoft device code login flow |
| `ms_auth_poll` | Complete device code login after user enters code |
| `ms_auth_status` | Check authentication status |
| `onedrive_list_files` | List files/folders in OneDrive |
| `onedrive_search` | Search OneDrive by name or content |
| `onedrive_download_file` | Download file → saves directly to sandbox (v1.9.3) |
| `onedrive_upload_file` | Upload file to OneDrive (< 4MB) |
| `onedrive_create_folder` | Create a new folder |
| `onedrive_delete_item` | Delete a file or folder |
| `onedrive_move_item` | Move item to different folder |
| `onedrive_copy_item` | Copy item to different folder |
| `onedrive_share_item` | Create sharing link |

### Microsoft SharePoint Tools (10)
| Tool | Description |
|------|-------------|
| `sharepoint_list_sites` | List/search accessible SharePoint sites |
| `sharepoint_get_site` | Get site details |
| `sharepoint_list_drives` | List document libraries in a site |
| `sharepoint_list_files` | List files in a document library |
| `sharepoint_download_file` | Download file → saves directly to sandbox (v1.9.3) |
| `sharepoint_upload_file` | Upload file to SharePoint (< 4MB) |
| `sharepoint_search` | Search files within a site |
| `sharepoint_list_lists` | List SharePoint lists |
| `sharepoint_list_items` | List items in a SharePoint list |

---

## Microsoft 365 Authentication

Power Interpreter uses **OAuth 2.0 Device Code Flow** for Microsoft authentication, designed for headless/server environments like Railway.

### Flow:
1. Call `ms_auth_start(user_id="user@company.com")`
2. User visits `https://microsoft.com/devicelogin` and enters the provided code
3. Call `ms_auth_poll(user_id="user@company.com")` to complete authentication
4. Tokens are cached in-memory and persisted to PostgreSQL
5. Access tokens auto-refresh using stored refresh tokens (no re-auth needed)

### Required Environment Variables:
```
AZURE_TENANT_ID=your-tenant-id
AZURE_CLIENT_ID=your-client-id
AZURE_CLIENT_SECRET=your-client-secret  # optional but recommended
```

### Scopes:
- `Files.ReadWrite.All` — OneDrive access
- `Sites.ReadWrite.All` — SharePoint access
- `offline_access` — Refresh tokens for persistent sessions

---

## OneDrive → Sandbox Bridge (v1.9.3)

**The Problem (pre-v1.9.3):**
Downloaded files were base64-encoded and returned in the MCP JSON response, but never written to the sandbox filesystem. This meant `execute_code` couldn't access downloaded files.

**The Fix (v1.9.3):**
`onedrive_download_file` and `sharepoint_download_file` now write files directly to `/app/sandbox_data/` via `GraphClient._write_to_sandbox()`. The response includes `sandbox_path` and a helpful message:

```json
{
  "name": "data.xlsx",
  "size": 1144579,
  "mimeType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  "sandbox_path": "/app/sandbox_data/data.xlsx",
  "saved_to_sandbox": true,
  "message": "File 'data.xlsx' saved to sandbox. Use it in execute_code with: pd.read_excel('data.xlsx')"
}
```

If the sandbox write fails for any reason, it falls back to returning `content_base64` in the response (no data loss).

---

## API Endpoints

| Endpoint | Auth | Description |
|----------|------|-------------|
| `GET /health` | None | Health check |
| `GET /dl/{file_id}` | None | Public file download |
| `GET /charts/{session_id}/{filename}` | None | Public chart serving |
| `POST /mcp/sse` | None | MCP JSON-RPC (SimTheory direct) |
| `GET /mcp/sse` | None | MCP SSE transport (standard clients) |
| `POST /api/execute` | API Key | Execute code |
| `POST /api/jobs/*` | API Key | Job management |
| `POST /api/files/*` | API Key | File management |
| `POST /api/data/*` | API Key | Dataset management |
| `POST /api/sessions/*` | API Key | Session management |

---

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `DATABASE_URL` | Yes | PostgreSQL connection string |
| `API_KEY` | Yes | API key for protected endpoints |
| `SANDBOX_DIR` | No | Sandbox directory (default: `/app/sandbox_data`) |
| `LOG_LEVEL` | No | Logging level (default: `INFO`) |
| `MAX_EXECUTION_TIME` | No | Max code execution time in seconds (default: `300`) |
| `MAX_MEMORY_MB` | No | Max memory per execution (default: `16384`) |
| `MAX_CONCURRENT_JOBS` | No | Max parallel jobs (default: `4`) |
| `JOB_TIMEOUT` | No | Job timeout in seconds (default: `1800`) |
| `SANDBOX_FILE_MAX_MB` | No | Max file size in sandbox (default: `50`) |
| `SANDBOX_FILE_TTL_HOURS` | No | File expiry in hours (default: `72`) |
| `AZURE_TENANT_ID` | For MS 365 | Azure AD tenant ID |
| `AZURE_CLIENT_ID` | For MS 365 | Azure AD app client ID |
| `AZURE_CLIENT_SECRET` | For MS 365 | Azure AD app client secret |

---

## Deployment (Railway)

The app is deployed on Railway with automatic deploys from the `main` branch.

```
Public URL: https://power-interpreter-production-6396.up.railway.app
```

### Runtime Configuration:
- **Sandbox dir:** `/app/sandbox_data`
- **Max execution time:** 300s
- **Max memory:** 16,384 MB
- **Max concurrent jobs:** 4
- **Job timeout:** 1,800s
- **Sandbox file max:** 50 MB
- **Sandbox file TTL:** 72 hours

---

## Version History

| Version | Date | Changes |
|---------|------|---------|
| **1.9.3a** | 2026-03-01 | Fixed sandbox path resolution for Railway (`/app/sandbox_data`). Added helpful `pd.read_excel()` hint in download response. |
| **1.9.3** | 2026-03-01 | **Critical fix:** OneDrive/SharePoint downloads now write files directly to sandbox via `_write_to_sandbox()`. Added token refresh logging. |
| **1.9.2** | 2026-02-xx | SQLAlchemy token persistence. `ms_auth_poll` tool. Memory guard module. |
| **1.9.1** | 2026-02-xx | Microsoft bootstrap ordering fix — MS failure can't take down core tools. |
| **1.9.0** | 2026-02-xx | Microsoft OneDrive + SharePoint integration (20 new MCP tools). |
| **1.8.1** | 2026-01-xx | Chart serving route (`/charts/{session_id}/{filename}`). |
| **1.8.0** | 2026-01-xx | Base64 ImageContent blocks in MCP server. |
| **1.7.2** | 2025-12-xx | `fetch_from_url` route fix, stable release. |

---

## Key Files

```
app/
├── main.py                    # FastAPI app, lifespan, MCP JSON-RPC handler
├── mcp_server.py              # MCP tool definitions (core 12 tools)
├── config.py                  # Settings and environment config
├── database.py                # SQLAlchemy async engine + session factory
├── models.py                  # SQLAlchemy models (SandboxFile, etc.)
├── auth.py                    # API key verification
├── engine/
│   ├── sandbox.py             # Sandboxed code execution engine
│   ├── job_manager.py         # Async job queue
│   └── dataset_manager.py     # Large dataset PostgreSQL engine
├── microsoft/
│   ├── __init__.py
│   ├── auth_manager.py        # OAuth 2.0 device code flow + token management
│   ├── graph_client.py        # Microsoft Graph API client (OneDrive + SharePoint)
│   ├── tools.py               # MCP tool registrations for Microsoft
│   └── bootstrap.py           # Microsoft integration startup
└── routes/
    ├── execute.py             # /api/execute
    ├── jobs.py                # /api/jobs/*
    ├── files.py               # /api/files/* + /dl/{file_id}
    ├── data.py                # /api/data/*
    ├── sessions.py            # /api/sessions/*
    └── health.py              # /health
```

---

## Author

Built by **Kaffer AI** for **Timothy Escamilla** — CEO, New Carrot Farms LLC / Bolthouse Fresh Foods.

*"Fresh produce production is important to me."*
