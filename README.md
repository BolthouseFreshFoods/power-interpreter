# Power Interpreter MCP

A Model Context Protocol (MCP) server that provides AI assistants with Python code execution, file management, data analysis, and Microsoft 365 integration (OneDrive + SharePoint).

Built for [SimTheory.ai](https://simtheory.ai) — deployed on [Railway](https://railway.app).

## Version

**v1.9.4** — March 2026

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

### Microsoft 365 Tools (22) — v1.9.4
| Tool | Description |
|------|-------------|
| `ms_auth_status` | Check Microsoft 365 auth status |
| `ms_auth_start` | Start device code login flow |
| `ms_auth_poll` | Complete device code login |
| **`resolve_share_link`** | **NEW** — Resolve SharePoint/OneDrive sharing URL → metadata + sandbox download |
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

### v1.9.4 Changes
- **`user_id` is now optional** on all 22 Microsoft tools — auto-resolves from the last authenticated user
- **New `resolve_share_link` tool** — paste a SharePoint/OneDrive sharing URL and get the file downloaded to sandbox automatically
- **Consolidated tool files** — `mcp_tools.py` deprecated in favor of `tools.py` (single source of truth)
- **Improved auth tracking** — `_last_authenticated_user` persisted across token refreshes and Postgres loads

### Architecture
- **Sandbox**: Persistent Python kernel with session isolation
- **Database**: PostgreSQL for datasets, tokens, and job state
- **Auth**: Microsoft device code OAuth 2.0 with token persistence
- **Charts**: Inline base64 image blocks (matplotlib, seaborn, plotly)
- **Files**: Direct sandbox write from OneDrive/SharePoint downloads

## File Structure

```
app/
├── main.py                    # FastAPI app + lifespan
├── mcp_server.py              # MCP tool definitions (12 core tools)
├── config.py                  # Settings
├── database.py                # SQLAlchemy async engine
├── microsoft/
│   ├── __init__.py
│   ├── auth_manager.py        # OAuth token management (v1.9.4)
│   ├── bootstrap.py           # Init + registration (v1.9.4)
│   ├── graph_client.py        # Microsoft Graph API client (v1.9.4)
│   ├── tools.py               # 22 MCP tool registrations (v1.9.4) ← CANONICAL
│   └── mcp_tools.py           # DEPRECATED redirect → tools.py
├── engine/
│   ├── executor.py            # Code execution engine
│   └── memory_guard.py        # Memory isolation
├── routes/
│   ├── execute.py             # /api/execute
│   ├── files.py               # /api/files/*
│   ├── jobs.py                # /api/jobs/*
│   └── data.py                # /api/data/*
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

| Version | Date | Changes |
|---------|------|---------|
| v1.9.4 | Mar 2026 | Optional user_id, resolve_share_link, file consolidation |
| v1.9.3 | Mar 2026 | Sandbox file bridge for OneDrive/SharePoint downloads |
| v1.9.2 | Mar 2026 | Token persistence rewrite (SQLAlchemy), ms_auth_poll |
| v1.9.0 | Feb 2026 | Microsoft OneDrive + SharePoint integration (20 tools) |
| v1.8.2 | Feb 2026 | Universal data loading (CSV, Excel, PDF, JSON, Parquet) |
| v1.8.1 | Feb 2026 | Inline chart images via base64 ImageContent blocks |
| v1.7.0 | Jan 2026 | fetch_from_url tool for direct CDN/URL downloads |
| v1.6.0 | Jan 2026 | Auto file handling workflow improvements |

---

*Built by Timothy Escamilla for Bolthouse Fresh Foods / New Carrot Farms LLC*
