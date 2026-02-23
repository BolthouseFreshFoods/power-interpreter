# Power Interpreter MCP

**General-purpose sandboxed Python execution engine with MCP integration.**

Built for [SimTheory.ai](https://simtheory.ai) — execute Python code, load datasets, generate charts, and run long-running analysis jobs, all through the Model Context Protocol (MCP).

---

## Version

**v1.8.2** — Universal data loading + updated MCP tool descriptions

---

## Architecture

```
SimTheory.ai (MCP Client)
    │
    ▼  JSON-RPC over HTTP POST
┌─────────────────────────────────────────┐
│  Power Interpreter (Railway)            │
│                                         │
│  ┌─────────────┐  ┌──────────────────┐  │
│  │ MCP Server  │  │ FastAPI Routes   │  │
│  │ (12 tools)  │──│ /api/execute     │  │
│  │             │  │ /api/data/load   │  │
│  │             │  │ /api/files/*     │  │
│  │             │  │ /api/jobs/*      │  │
│  └─────────────┘  └──────────────────┘  │
│         │                  │            │
│         ▼                  ▼            │
│  ┌─────────────┐  ┌──────────────────┐  │
│  │ Python      │  │ PostgreSQL       │  │
│  │ Kernel      │  │ (datasets,       │  │
│  │ (persistent │  │  files, jobs,    │  │
│  │  sessions)  │  │  metadata)       │  │
│  └─────────────┘  └──────────────────┘  │
└─────────────────────────────────────────┘
```

---

## MCP Tools (12)

### Code Execution
| Tool | Description |
|------|-------------|
| `execute_code` | Run Python code in a persistent sandbox kernel (sync, <60s) |
| `submit_job` | Submit long-running code for async execution (up to 30 min) |
| `get_job_status` | Check async job progress |
| `get_job_result` | Retrieve completed job output |

### File Management
| Tool | Description |
|------|-------------|
| `fetch_from_url` | ★ Download file from any HTTPS URL into sandbox (CDN, S3, etc.) |
| `upload_file` | Upload a file via base64 encoding (<10MB) |
| `fetch_file` | Download file from URL (legacy, use `fetch_from_url`) |
| `list_files` | List files in the sandbox |

### Data & Datasets
| Tool | Description |
|------|-------------|
| `load_dataset` | Load data file into PostgreSQL — **auto-detects format** (see below) |
| `query_dataset` | Execute SQL SELECT queries against loaded datasets |
| `list_datasets` | List all datasets in PostgreSQL |

### Sessions
| Tool | Description |
|------|-------------|
| `create_session` | Create isolated workspace session |

---

## Supported Data Formats

The `load_dataset` tool (and the `/api/data/load` endpoint) **auto-detects file format** from the file extension:

| Format | Extensions | Reader | Notes |
|--------|-----------|--------|-------|
| **CSV** | `.csv`, `.tsv`, `.txt` | `pd.read_csv()` | Chunked loading for large files |
| **Excel** | `.xlsx`, `.xls`, `.xlsm`, `.xlsb` | `pd.read_excel()` | Full read, then chunked insert |
| **PDF** | `.pdf` | `pdfplumber` | Extracts tabular data from PDF pages |
| **JSON** | `.json` | `pd.read_json()` + `json_normalize` | Array of objects or nested JSON |
| **Parquet** | `.parquet`, `.pq` | `pd.read_parquet()` | Columnar format, very fast |

All formats are loaded into PostgreSQL in 50K-row chunks with automatic indexing on date and ID columns. Handles **1.5M+ rows** efficiently.

### Typical Workflow

```
1. fetch_from_url(url="https://cdn.example.com/invoices.xlsx", filename="invoices.xlsx")
2. load_dataset(file_path="invoices.xlsx", dataset_name="invoices")
3. query_dataset(sql="SELECT vendor, SUM(amount) FROM data_xxx GROUP BY vendor")
```

---

## API Endpoints

### Public (no auth)
| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Health check |
| `GET` | `/dl/{file_id}/{filename}` | Download generated files |
| `GET` | `/charts/{session_id}/{filename}` | Serve chart images |

### Protected (API key required)
| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/execute` | Execute Python code |
| `POST` | `/api/data/load` | Load data file (universal format) |
| `POST` | `/api/data/load-csv` | Legacy alias (now auto-detects all formats) |
| `POST` | `/api/data/query` | SQL query against datasets |
| `GET` | `/api/data/datasets` | List datasets |
| `GET` | `/api/data/datasets/{name}` | Dataset info |
| `DELETE` | `/api/data/datasets/{name}` | Drop dataset |
| `POST` | `/api/files/upload` | Upload file (base64) |
| `POST` | `/api/files/fetch` | Fetch file from URL |
| `GET` | `/api/files` | List sandbox files |
| `POST` | `/api/jobs/submit` | Submit async job |
| `GET` | `/api/jobs/{id}/status` | Job status |
| `GET` | `/api/jobs/{id}/result` | Job result |
| `POST` | `/api/sessions` | Create session |

### MCP Transport
| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/mcp/sse` | Direct JSON-RPC handler (SimTheory) |
| `GET` | `/mcp/sse` | SSE transport (standard MCP clients) |

---

## Deployment

Deployed on **Railway** with:
- Python 3.11+
- PostgreSQL (datasets, files, jobs, metadata)
- Uvicorn ASGI server on port 8080

### Environment Variables
| Variable | Description |
|----------|-------------|
| `DATABASE_URL` | PostgreSQL connection string |
| `API_KEY` | API key for protected endpoints |
| `RAILWAY_PUBLIC_DOMAIN` | Auto-set by Railway for public URLs |

### Configuration
| Setting | Default |
|---------|---------|
| Max execution time | 300s |
| Max memory | 4096 MB |
| Max concurrent jobs | 4 |
| Job timeout | 1800s (30 min) |
| Sandbox file max size | 50 MB |
| Sandbox file TTL | 72 hours |
| Dataset chunk size | 50,000 rows |

---

## Pre-installed Libraries

The sandbox kernel includes:
- **Data**: pandas, numpy, openpyxl, xlsxwriter
- **Visualization**: matplotlib, seaborn, plotly
- **Statistics**: scipy, statsmodels, scikit-learn
- **Math**: sympy
- **PDF**: pdfplumber
- **Web**: requests, beautifulsoup4, httpx
- **Images**: Pillow (PIL)
- **Standard**: json, csv, re, datetime, collections, math, statistics

---

## Project Structure

```
power-interpreter/
├── app/
│   ├── main.py              # FastAPI app, lifespan, MCP JSON-RPC handler
│   ├── mcp_server.py         # MCP tool definitions (12 tools)
│   ├── config.py              # Settings and environment config
│   ├── auth.py                # API key authentication
│   ├── database.py            # PostgreSQL connection management
│   ├── models.py              # SQLAlchemy models (Dataset, SandboxFile, etc.)
│   ├── engine/
│   │   ├── data_manager.py    # ★ Universal data loading (CSV/Excel/PDF/JSON/Parquet)
│   │   ├── executor.py        # Python code execution engine
│   │   ├── file_manager.py    # Sandbox file management
│   │   ├── job_manager.py     # Async job queue
│   │   └── kernel_manager.py  # Persistent Python kernel sessions
│   └── routes/
│       ├── data.py            # /api/data/* endpoints
│       ├── execute.py         # /api/execute endpoint
│       ├── files.py           # /api/files/* + /dl/* endpoints
│       ├── health.py          # /health endpoint
│       ├── jobs.py            # /api/jobs/* endpoints
│       └── sessions.py        # /api/sessions endpoint
├── Dockerfile
├── requirements.txt
└── README.md
```

---

## Version History

| Version | Date | Changes |
|---------|------|---------|
| **v1.8.2** | 2026-02-23 | `load_dataset` tool description updated for universal format support |
| **v1.8.1** | 2026-02-23 | Chart base64 enrichment fix (stdout regex fallback) |
| **v1.8.0** | 2026-02-22 | Base64 ImageContent blocks for charts |
| **v1.7.2** | 2026-02-22 | `fetch_from_url` route fix (404 → correct path) |
| **v1.7.1** | 2026-02-22 | FastMCP constructor fix (removed unsupported kwarg) |
| **v1.7.0** | 2026-02-21 | `fetch_from_url` tool added (CDN/S3/URL direct download) |
| **v1.6.0** | 2026-02-20 | Auto file handling — tool descriptions rewritten for reliable chaining |
| **v1.5.2** | 2026-02-19 | Stop stripping stdout — pass URLs through as-is |
| **v1.5.1** | 2026-02-19 | Plain text URL format (still broken due to stdout stripping) |
| **v1.5.0** | 2026-02-18 | Content blocks introduced (broke URL passing) |
| **v1.2.0** | 2026-02-15 | Initial working version — JSON response with URLs in stdout |

---

## Author

Built by **AI**, at New Carrot Farms LLC.

Part of the AI infrastructure stack for business analytics, M&A due diligence, and operational intelligence.
