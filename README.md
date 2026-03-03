# Power Interpreter MCP

A Model Context Protocol (MCP) server that provides AI assistants with Python code execution, file management, data analysis, and Microsoft 365 integration (OneDrive + SharePoint).

Built for [SimTheory.ai](https://simtheory.ai) — deployed on [Railway](https://railway.app).

## Version

**v2.8.6** — March 2026

> Version unified across all files as of v2.8.6. Previously the MCP server layer
> (main.py, mcp_server.py) and the sandbox engine (executor.py) had independent
> version numbers. Now there is ONE version for the entire service.

## MCP Tools (34 total)

### Core Tools (12)
| Tool | Description |
|------|-------------|
| `execute_code` | Run Python in a persistent sandbox kernel (variables persist across calls) |
| `submit_job` | Submit long-running jobs (async, up to 30 min) |
| `get_job_status` | Check async job progress |
| `get_job_result` | Retrieve completed job output |
| `upload_file` | Upload file to sandbox (base64) |
| `fetch_file` | Download file from URL to sandbox |
| `fetch_from_url` | Stream file from any HTTPS URL into sandbox (CDN, S3, Google Sheets) |
| `list_files` | List sandbox files |
| `load_dataset` | Load data into PostgreSQL (CSV, Excel, PDF, JSON, Parquet) |
| `query_dataset` | SQL query against loaded datasets |
| `list_datasets` | List loaded datasets |
| `create_session` | Create isolated workspace session |

### Microsoft 365 Tools (22)
| Tool | Description |
|------|-------------|
| `ms_auth_status` | Check Microsoft 365 auth status |
| `ms_auth_start` | Start device code login flow |
| `ms_auth_poll` | Complete device code login |
| `resolve_share_link` | Resolve SharePoint/OneDrive sharing URL → metadata + sandbox download |
| `onedrive_list_files` | List files/folders in OneDrive |
| `onedrive_search` | Search OneDrive by name or content |
| `onedrive_download_file` | Download from OneDrive → sandbox |
| `onedrive_upload_file` | Upload to OneDrive |
| `onedrive_create_folder` | Create folder in OneDrive |
| `onedrive_delete_item` | Delete file/folder |
| `onedrive_move_item` | Move item |
| `onedrive_copy_item` | Copy item |
| `onedrive_share_item` | Create sharing link |
| `sharepoint_list_sites` | List/search SharePoint sites |
| `sharepoint_get_site` | Get site details |
| `sharepoint_list_drives` | List document libraries |
| `sharepoint_list_files` | List files in library |
| `sharepoint_download_file` | Download from SharePoint → sandbox |
| `sharepoint_upload_file` | Upload to SharePoint |
| `sharepoint_search` | Search within SharePoint site |
| `sharepoint_list_lists` | List SharePoint lists |
| `sharepoint_list_items` | List items in a list |

## Key Features

### Sandbox Engine
- **Persistent kernels** — variables, imports, and loaded files survive across calls
- **Import allowlist** — pandas, numpy, matplotlib, seaborn, plotly, scipy, sklearn, statsmodels, openpyxl, reportlab, python-docx, and more
- **Path normalization** — auto-fixes /tmp/ paths, doubled session prefixes, Windows paths
- **Read-only upload access** — sandbox can read uploaded files from SimTheory
- **Chart auto-capture** — matplotlib/plotly figures captured via plt.show() interception
- **Timeout floor** — minimum 100s execution time, AI cannot override below this
- **File auto-storage** — generated files stored in Postgres with public download URLs

### Microsoft 365 Integration
- **Device code OAuth 2.0** with token persistence to Postgres
- **OneDrive** — full CRUD (list, search, upload, download, move, copy, share)
- **SharePoint** — sites, document libraries, lists, search, upload/download
- **Share link resolution** — paste a sharing URL, get the file in sandbox automatically
- **Optional user_id** — auto-resolves from last authenticated user

### Architecture
- **Runtime**: FastAPI + Uvicorn on Railway
- **Database**: PostgreSQL (datasets, tokens, job state, file storage)
- **MCP Transport**: SSE (standard clients) + direct JSON-RPC (SimTheory)
- **Charts**: Inline base64 ImageContent blocks
- **Files**: Direct sandbox write from OneDrive/SharePoint downloads

## File Structure

```
app/
├── main.py                    # FastAPI app + lifespan + JSON-RPC handler
├── mcp_server.py              # MCP tool definitions (12 core tools)
├── config.py                  # Settings
├── database.py                # SQLAlchemy async engine
├── microsoft/
│   ├── __init__.py
│   ├── auth_manager.py        # OAuth token management
│   ├── bootstrap.py           # Init + registration
│   ├── graph_client.py        # Microsoft Graph API client
│   ├── tools.py               # 22 MCP tool registrations ← CANONICAL
│   └── mcp_tools.py           # DEPRECATED redirect → tools.py
├── engine/
│   ├── executor.py            # Sandbox code execution engine
│   ├── kernel_manager.py      # Persistent kernel sessions
│   ├── data_manager.py        # Dataset loading
│   ├── file_manager.py        # File I/O management
│   ├── job_manager.py         # Async job queue
│   └── memory_guard.py        # Memory isolation
├── routes/
│   ├── execute.py             # /api/execute
│   ├── files.py               # /api/files/*
│   ├── jobs.py                # /api/jobs/*
│   ├── data.py                # /api/data/*
│   ├── sessions.py            # /api/sessions
│   └── health.py              # /health
└── data_manager.py            # Dataset loading (CSV, Excel, PDF, JSON, Parquet)
```

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `API_KEY` | Yes | API key for MCP authentication |
| `DATABASE_URL` | Yes | PostgreSQL connection string |
| `AZURE_TENANT_ID` | For Microsoft | Azure AD tenant ID |
| `AZURE_CLIENT_ID` | For Microsoft | Azure AD app client ID |
| `AZURE_CLIENT_SECRET` | For Microsoft | Azure AD app client secret |
| `SANDBOX_DIR` | No | Override sandbox directory (default: `/app/sandbox_data`) |
| `PUBLIC_URL` | No | Public URL for download links |

## Deployment

Deployed on Railway with automatic deploys from `main` branch.

```bash
# Health check
curl https://power-interpreter-production-6396.up.railway.app/health

# MCP SSE endpoint (for SimTheory)
POST https://power-interpreter-production-6396.up.railway.app/mcp/sse
```

## Version History

> As of v2.8.6, the MCP server and sandbox engine share a single version number.
> Earlier versions used separate numbering (v1.x for MCP server, v2.x for engine).

| Version | Date | Changes |
|---------|------|---------|
| **v2.8.6** | **Mar 2026** | **Timeout floor (100s minimum), version unification across all files** |
| v2.8.5 | Mar 2026 | python-docx + transitive dependency support (zipfile, lxml, xml, etc.) |
| v2.8.4 | Mar 2026 | datetime module injection fix (preserved as MODULE, not class) |
| v2.8.3 | Mar 2026 | /app/sandbox_data recognized in allowed read paths |
| v2.8.2 | Mar 2026 | Read-only upload access (sandbox reads SimTheory uploads) |
| v2.8.1 | Mar 2026 | /tmp/ path interception (redirect absolute paths to sandbox) |
| v2.8.0 | Mar 2026 | Defensive path normalization (session prefix doubling fix) |
| v2.7.0 | Mar 2026 | reportlab + matplotlib PDF backend support |
| v2.6.0 | Mar 2026 | Inline chart rendering (matplotlib/plotly auto-capture) |
| v2.1.0 | Feb 2026 | Auto file storage in Postgres with download URLs |
| v2.0.0 | Feb 2026 | Persistent session state (kernel architecture) |
| *v1.9.4* | *Mar 2026* | *Optional user_id on MS tools, resolve_share_link, file consolidation* |
| *v1.9.3* | *Mar 2026* | *Sandbox file bridge for OneDrive/SharePoint downloads* |
| *v1.9.2* | *Mar 2026* | *Token persistence rewrite (SQLAlchemy), ms_auth_poll* |
| *v1.9.0* | *Feb 2026* | *Microsoft OneDrive + SharePoint integration (20 tools)* |
| *v1.8.2* | *Feb 2026* | *Universal data loading (CSV, Excel, PDF, JSON, Parquet)* |
| *v1.8.1* | *Feb 2026* | *Inline chart images via base64 ImageContent blocks* |
| *v1.7.0* | *Jan 2026* | *fetch_from_url tool for direct CDN/URL downloads* |
| *v1.6.0* | *Jan 2026* | *Auto file handling workflow improvements* |

*Italicized entries are from the legacy v1.x MCP server numbering (pre-unification).*

---

*Built by Timothy Escamilla for Bolthouse Fresh Foods / New Carrot Farms LLC*
