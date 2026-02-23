# Power Interpreter

A production-grade Python execution sandbox exposed as an MCP (Model Context Protocol) server. Designed to exceed the capabilities of standard Code Interpreter environments — with multi-session support, direct CDN file loading, async job execution, SQL querying, and a full enterprise analytics library stack.

Deployed on [Railway](https://railway.app) and integrated with SimTheory AI agents via MCP/SSE.

---

## What Makes This Different from Code Interpreter

| Capability | Code Interpreter | Power Interpreter |
|---|---|---|
| pandas / numpy / matplotlib | ✅ | ✅ |
| scikit-learn / xgboost / lightgbm | ✅ | ✅ |
| statsmodels / pingouin | ✅ | ✅ |
| plotly / seaborn / kaleido | ✅ | ✅ |
| sympy (symbolic math) | ✅ | ✅ |
| DuckDB (in-process SQL) | ❌ | ✅ |
| Parquet / Arrow columnar data | ❌ | ✅ |
| PDF reading (pdfplumber) | ❌ | ✅ |
| Multi-session (concurrent) | ❌ One per conversation | ✅ Up to 6 named sessions |
| Execution timeout | ~120s | 120s default, up to 300s |
| File upload via CDN URL | ❌ | ✅ `fetch_from_url` |
| Async long-running jobs | ❌ | ✅ `submit_job` / `get_job_result` |
| Cross-session file isolation | ❌ | ✅ Per `session_id` sandbox |
| Internet access | ✅ | ❌ Sandboxed (by design) |

---

## Architecture

```
SimTheory Agent
      │
      │  MCP/SSE (HTTPS)
      ▼
┌─────────────────────────────────┐
│         main.py (FastAPI)       │
│   /mcp/sse  ←── MCP endpoint   │
│   /health   ←── Railway probe  │
└────────────┬────────────────────┘
             │
             ▼
┌─────────────────────────────────┐
│       mcp_server.py (FastMCP)   │
│  12 registered MCP tools        │
│  fetch_from_url ← NEW           │
│  execute_code                   │
│  submit_job / get_job_result    │
│  load_dataset / query_dataset   │
│  upload_file / fetch_file       │
│  list_files / list_datasets     │
│  create_session                 │
└────────────┬────────────────────┘
             │
             ▼
┌─────────────────────────────────┐
│     executor.py (Kernel Mgr)    │
│  Up to 6 concurrent kernels     │
│  Session persistence            │
│  Chart capture (PNG)            │
│  120s timeout (300s max)        │
└────────────┬────────────────────┘
             │
             ▼
┌─────────────────────────────────┐
│   /app/sandbox_data/{session}/  │
│   Isolated per session_id       │
│   Files persist within session  │
└─────────────────────────────────┘
```

---

## MCP Tools Reference (12 Tools)

### 🔴 Priority Tools — Start Here

#### `fetch_from_url` ⭐ NEW
Load any file directly from a CDN URL (Cloudinary, S3, HTTPS) into the sandbox. This is the **primary way to load files** — no base64 encoding required.

```json
{
  "tool": "fetch_from_url",
  "args": {
    "url": "https://cdn.simtheory.ai/raw/upload/v.../myfile.xlsx",
    "filename": "myfile.xlsx",
    "session_id": "default"
  }
}
```

Returns the exact sandbox path to use in `execute_code`. Supports: `xlsx, xls, csv, tsv, json, jsonl, parquet, pdf, txt, png, jpg, zip, db, sqlite`. Max file size: 500MB.

---

#### `execute_code`
Run Python in a sandboxed, persistent kernel session.

```json
{
  "tool": "execute_code",
  "args": {
    "code": "import pandas as pd\ndf = pd.read_excel('/app/sandbox_data/default/myfile.xlsx')\nprint(df.describe())",
    "session_id": "default",
    "timeout": 120
  }
}
```

- Variables persist across calls within the same `session_id`
- Charts (matplotlib, seaborn, plotly) are captured and returned as PNG image blocks
- Files written to `/app/sandbox_data/{session_id}/` are accessible to other tools
- Default timeout: 120s. Maximum: 300s.

---

#### `submit_job` + `get_job_status` + `get_job_result`
For long-running tasks (large dataset processing, ML training, complex cross-references). Submit asynchronously and poll for results.

```json
// Submit
{ "tool": "submit_job", "args": { "code": "...", "session_id": "analysis", "timeout": 240 } }
// → Returns: { "job_id": "a3f9c1b2" }

// Poll
{ "tool": "get_job_status", "args": { "job_id": "a3f9c1b2" } }
// → Returns: { "status": "running", "elapsed": "14.2s" }

// Retrieve
{ "tool": "get_job_result", "args": { "job_id": "a3f9c1b2" } }
// → Returns: full output + charts
```

---

### 📁 File Management

#### `upload_file`
Upload a file via base64-encoded content. For large files, prefer `fetch_from_url`.

```json
{
  "tool": "upload_file",
  "args": {
    "filename": "data.csv",
    "content_base64": "<base64 string>",
    "session_id": "default"
  }
}
```

#### `fetch_file`
Retrieve a file generated by `execute_code` from the sandbox (returned as base64).

```json
{ "tool": "fetch_file", "args": { "filename": "results.xlsx", "session_id": "default" } }
```

#### `list_files`
List all files in a sandbox session.

```json
{ "tool": "list_files", "args": { "session_id": "default" } }
```

---

### 📊 Dataset Tools

#### `load_dataset`
Load a sandbox file into a named pandas DataFrame. Supports xlsx, csv, parquet, json.

```json
{
  "tool": "load_dataset",
  "args": {
    "filename": "invoices.xlsx",
    "dataset_name": "invoices",
    "session_id": "default",
    "sheet_name": "Sheet1"
  }
}
```

#### `query_dataset`
Run SQL against any loaded DataFrame using DuckDB — no database setup required.

```json
{
  "tool": "query_dataset",
  "args": {
    "query": "SELECT vendor, SUM(amount) as total FROM invoices GROUP BY vendor ORDER BY total DESC",
    "session_id": "default"
  }
}
```

#### `list_datasets`
List all DataFrames currently loaded in a session (name, shape, columns).

---

### ⚙️ Session Management

#### `create_session`
Create a named session with its own isolated sandbox directory and kernel.

```json
{ "tool": "create_session", "args": { "session_id": "vestis_analysis" } }
```

Up to 6 concurrent sessions supported. Each session has:
- Its own kernel with persistent variable state
- Its own `/app/sandbox_data/{session_id}/` file directory
- Independent execution context

---

## Recommended Workflow

### Loading and Analyzing a File from CDN

```
1. fetch_from_url(url="https://cdn.simtheory.ai/.../data.xlsx", session_id="analysis")
   → ✅ File saved to /app/sandbox_data/analysis/data.xlsx (212,789 bytes)

2. execute_code(code="""
   import pandas as pd
   df = pd.read_excel('/app/sandbox_data/analysis/data.xlsx')
   print(df.shape)
   print(df.dtypes)
   print(df.describe())
   """, session_id="analysis")

3. query_dataset(query="SELECT vendor, COUNT(*) as n, SUM(amount) as total FROM df GROUP BY vendor", session_id="analysis")

4. execute_code(code="""
   import matplotlib.pyplot as plt
   df.groupby('vendor')['amount'].sum().plot(kind='bar')
   plt.title('Revenue by Vendor')
   plt.tight_layout()
   plt.savefig('/app/sandbox_data/analysis/chart.png')
   """, session_id="analysis")

5. fetch_file(filename="chart.png", session_id="analysis")
```

---

## Analytics Library Stack

### Core Data Science
- `pandas` — DataFrames, time series, data wrangling
- `numpy` — Numerical computing
- `scipy` — Scientific computing, statistical tests

### Machine Learning
- `scikit-learn` — Classification, regression, clustering, preprocessing
- `xgboost` — Gradient boosting
- `lightgbm` — Fast gradient boosting for large datasets

### Visualization
- `matplotlib` — Publication-quality charts
- `seaborn` — Statistical visualization
- `plotly` + `kaleido` — Interactive charts, static export

### Statistics & Econometrics
- `statsmodels` — OLS, time series (ARIMA), hypothesis testing
- `pingouin` — Statistical tests (t-test, ANOVA, correlation)

### Symbolic Math
- `sympy` — Symbolic algebra, calculus, equation solving

### Data Formats
- `openpyxl` / `xlsxwriter` / `xlrd` — Excel read/write
- `pyarrow` / `fastparquet` — Parquet / columnar data
- `duckdb` — In-process SQL on DataFrames
- `pdfplumber` / `PyPDF2` — PDF text extraction
- `python-docx` — Word document generation
- `beautifulsoup4` / `lxml` — HTML/XML parsing

### Image Processing
- `Pillow` — Image manipulation, format conversion

### Utilities
- `rich` — Beautiful terminal output
- `tabulate` — Table formatting
- `tqdm` — Progress bars
- `tenacity` — Retry logic

---

## Deployment

### Environment Variables

| Variable | Default | Description |
|---|---|---|
| `PORT` | `8000` | HTTP port (set by Railway) |
| `EXECUTOR_URL` | `http://127.0.0.1:8080` | Internal executor endpoint |
| `SANDBOX_DATA_DIR` | `/app/sandbox_data` | Root sandbox directory |
| `MAX_UPLOAD_MB` | `500` | Max file upload size |
| `MAX_FETCH_SIZE_MB` | `500` | Max fetch_from_url file size |
| `LOG_LEVEL` | `INFO` | Logging verbosity |

### Railway Deployment

Pushes to `main` trigger automatic Railway deployments. Health check is configured in `railway.toml` at `/health`.

```toml
# railway.toml
[deploy]
healthcheckPath = "/health"
healthcheckTimeout = 30
restartPolicyType = "on_failure"
```

---

## Project Structure

```
power-interpreter/
├── app/
│   ├── __init__.py
│   ├── main.py              # FastAPI app, /mcp/sse endpoint, /health
│   ├── mcp_server.py        # FastMCP — all 12 MCP tools registered here
│   ├── fetch_from_url.py    # ★ NEW — CDN/URL file fetcher
│   ├── models.py            # Pydantic request/response models
│   ├── storage.py           # Sandbox file management
│   └── engine/
│       ├── executor.py      # Python kernel execution, chart capture
│       └── kernel_manager.py # Session lifecycle, up to 6 kernels
├── Dockerfile
├── railway.toml
├── requirements.txt         # Full analytics library suite
├── start.py                 # Startup script (reads PORT env var)
└── README.md
```

---

## Recent Changes

### v2.0 — Priority 1 & 2 (Feb 2026)

**Priority 1 — Full Analytics Library Suite**
- Added `scikit-learn`, `xgboost`, `lightgbm` for ML
- Added `plotly` + `kaleido` for interactive/static charts
- Added `statsmodels`, `pingouin` for advanced statistics
- Added `sympy` for symbolic math
- Added `Pillow` for image processing
- Added `pdfplumber`, `PyPDF2`, `python-docx` for document handling
- Added `duckdb`, `sqlalchemy` for in-process SQL
- Added `pyarrow`, `fastparquet` for columnar data formats
- Added `beautifulsoup4`, `lxml` for HTML/XML parsing
- Added `rich`, `tabulate`, `tqdm`, `tenacity` for utilities

**Priority 2 — File Loading via CDN URL**
- Added `fetch_from_url` tool — streams files directly from any HTTPS URL into sandbox
- Eliminates base64 upload bottleneck for large files
- Supports 500MB max file size with 64KB streaming chunks
- Sanitizes filenames, validates extensions, guards against path traversal
- Execution timeout increased from 30s → 120s default (300s max)
- `mcp_server.py` fully rewritten with clean tool registration and docstrings
- `load_dataset` now supports xlsx, csv, parquet, json natively
- `query_dataset` uses DuckDB for SQL on any loaded DataFrame

---

## License

MIT
