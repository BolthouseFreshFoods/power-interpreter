# Power Interpreter MCP

> General-purpose sandboxed Python execution engine for SimTheory.ai

## Overview

Power Interpreter is a robust code execution service that provides:

- **Sandboxed Python Execution** - Run code safely with resource limits
- **Async Job Queue** - Long-running operations never timeout
- **Large Dataset Support** - Load 1.5M+ rows into PostgreSQL
- **File Management** - Upload, download, and manage files
- **Pre-installed Libraries** - pandas, numpy, matplotlib, scikit-learn, and more
- **MCP Protocol** - Native integration with SimTheory.ai

## Architecture

```
SimTheory.ai (Kaffer) -> MCP Tools -> FastAPI -> Sandbox Executor
                                              -> PostgreSQL (datasets)
                                              -> Persistent Volume (files)
```

## Deployment

### Railway Setup

1. Create new project in Railway
2. Connect this GitHub repo
3. Add PostgreSQL plugin
4. Set environment variables:
   - `API_KEY` - Your secret API key
   - `DATABASE_URL` - Auto-provided by Railway
5. Deploy!

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `API_KEY` | API authentication key | (required) |
| `DATABASE_URL` | PostgreSQL connection | (auto from Railway) |
| `MAX_EXECUTION_TIME` | Max sync execution (seconds) | 300 |
| `MAX_MEMORY_MB` | Memory limit per execution | 4096 |
| `MAX_FILE_SIZE_MB` | Max upload file size | 500 |
| `MAX_CONCURRENT_JOBS` | Parallel job limit | 4 |
| `JOB_TIMEOUT` | Max async job time (seconds) | 600 |

## MCP Tools

| Tool | Description | Sync/Async |
|------|-------------|------------|
| `execute_code` | Run Python code (<60s) | Sync |
| `submit_job` | Submit long-running job | Async |
| `get_job_status` | Check job progress | Sync |
| `get_job_result` | Get completed output | Sync |
| `list_files` | List sandbox files | Sync |
| `load_dataset` | Load CSV into PostgreSQL | Async |
| `query_dataset` | SQL query datasets | Sync |
| `list_datasets` | List loaded datasets | Sync |
| `create_session` | Create workspace | Sync |

## Pre-installed Libraries

### Data
- pandas, numpy, openpyxl, xlsxwriter, pdfplumber, tabulate

### Visualization
- matplotlib, plotly, seaborn

### Statistics & ML
- scipy, scikit-learn, statsmodels

### Standard Library
- math, statistics, datetime, collections, re, json, csv, io, pathlib

## API Endpoints

### Quick Execution
```
POST /api/execute
{"code": "import pandas as pd; print(pd.__version__)", "timeout": 30}
```

### Async Jobs (No Timeout)
```
POST /api/jobs/submit
{"code": "...", "timeout": 600}
-> {"job_id": "abc-123", "status": "pending"}

GET /api/jobs/abc-123/status
-> {"status": "running", "elapsed_ms": 5000}

GET /api/jobs/abc-123/result
-> {"status": "completed", "stdout": "...", "result": {...}}
```

### Data Management
```
POST /api/data/load-csv
{"file_path": "data.csv", "dataset_name": "my_data"}

POST /api/data/query
{"sql": "SELECT * FROM data_xxx WHERE amount > 100", "limit": 1000}
```

## Security

- API key authentication (X-API-Key header)
- Sandboxed code execution (restricted imports, no network access)
- File I/O limited to sandbox directory
- SQL injection prevention (SELECT only for queries)
- Resource limits (time, memory)

## Author

Built by Kaffer AI for Timothy Escamilla

## License

Private - All rights reserved
