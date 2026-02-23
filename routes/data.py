"""Power Interpreter - Data Management Routes

Load datasets (CSV, Excel, PDF, JSON, Parquet) into PostgreSQL and query them.
Handles 1.5M+ rows efficiently via chunked loading and SQL queries.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Optional, Dict, List

from app.engine.data_manager import data_manager

router = APIRouter()


class LoadDataRequest(BaseModel):
    """Request to load a data file into PostgreSQL"""
    file_path: str = Field(..., description="Path to data file in sandbox")
    dataset_name: str = Field(..., description="Logical name for the dataset")
    session_id: Optional[str] = Field(default=None, description="Session ID")
    delimiter: str = Field(default=",", description="CSV delimiter (CSV only)")
    encoding: str = Field(default="utf-8", description="File encoding (CSV only)")
    sheet_name: Optional[str] = Field(default=None, description="Excel sheet name (Excel only, defaults to first sheet)")
    pdf_pages: Optional[str] = Field(default=None, description="PDF pages to extract, e.g. '1,3,5' or 'all' (PDF only)")


# Keep old endpoint for backwards compatibility
class LoadCSVRequest(BaseModel):
    """Legacy request - use LoadDataRequest instead"""
    file_path: str = Field(..., description="Path to CSV file in sandbox")
    dataset_name: str = Field(..., description="Logical name for the dataset")
    session_id: Optional[str] = Field(default=None, description="Session ID")
    delimiter: str = Field(default=",", description="CSV delimiter")
    encoding: str = Field(default="utf-8", description="File encoding")


class QueryRequest(BaseModel):
    """Request to query a dataset"""
    sql: str = Field(..., description="SQL SELECT query")
    params: Optional[Dict] = Field(default=None, description="Query parameters")
    limit: int = Field(default=1000, description="Max rows to return")
    offset: int = Field(default=0, description="Row offset for pagination")


@router.post("/data/load")
async def load_data(request: LoadDataRequest):
    """Load any supported data file into PostgreSQL for fast querying.
    
    Supported formats:
      - CSV / TSV (.csv, .tsv, .txt)
      - Excel (.xlsx, .xls, .xlsm, .xlsb)
      - PDF with tables (.pdf) -- extracts tabular data via pdfplumber
      - JSON (.json) -- expects array of objects or records format
      - Parquet (.parquet, .pq)
    
    Handles files with 1.5M+ rows by loading in 50K-row chunks.
    Creates indexes automatically on date and ID columns.
    
    After loading, query with POST /api/data/query using SQL.
    
    Example:
        Load: {"file_path": "invoices.xlsx", "dataset_name": "vestis_invoices"}
        Query: {"sql": "SELECT * FROM data_xxx WHERE amount > 100 LIMIT 10"}
    """
    try:
        result = await data_manager.load_data(
            file_path=request.file_path,
            dataset_name=request.dataset_name,
            session_id=request.session_id,
            delimiter=request.delimiter,
            encoding=request.encoding,
            sheet_name=request.sheet_name,
            pdf_pages=request.pdf_pages,
        )
        return result
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"File not found: {request.file_path}")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load data: {str(e)}")


@router.post("/data/load-csv")
async def load_csv(request: LoadCSVRequest):
    """Legacy endpoint -- redirects to load_data with format auto-detection.
    
    Use /data/load instead for new integrations.
    This endpoint is kept for backwards compatibility and now supports
    all file formats (CSV, Excel, PDF, JSON, Parquet) via auto-detection.
    """
    try:
        result = await data_manager.load_data(
            file_path=request.file_path,
            dataset_name=request.dataset_name,
            session_id=request.session_id,
            delimiter=request.delimiter,
            encoding=request.encoding,
        )
        return result
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"File not found: {request.file_path}")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load data: {str(e)}")


@router.post("/data/query")
async def query_data(request: QueryRequest):
    """Execute a SQL query against loaded datasets
    
    Only SELECT queries are allowed for safety.
    Results are paginated (default 1000 rows).
    
    Use dataset info to find the table name:
        GET /api/data/datasets -> shows table_name for each dataset
    
    Then query:
        {"sql": "SELECT * FROM data_xxx WHERE column = 'value' ORDER BY date"}
    """
    try:
        result = await data_manager.query_dataset(
            sql=request.sql,
            params=request.params,
            limit=request.limit,
            offset=request.offset
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Query failed: {str(e)}")


@router.get("/data/datasets")
async def list_datasets(session_id: Optional[str] = None):
    """List all loaded datasets"""
    datasets = await data_manager.list_datasets(session_id)
    return {"datasets": datasets, "count": len(datasets)}


@router.get("/data/datasets/{name}")
async def get_dataset_info(name: str):
    """Get detailed info about a dataset
    
    Returns column names, types, row count, size, etc.
    """
    info = await data_manager.get_dataset_info(name)
    if not info:
        raise HTTPException(status_code=404, detail=f"Dataset '{name}' not found")
    return info


@router.delete("/data/datasets/{name}")
async def drop_dataset(name: str):
    """Drop a dataset (removes table and metadata)"""
    dropped = await data_manager.drop_dataset(name)
    if not dropped:
        raise HTTPException(status_code=404, detail=f"Dataset '{name}' not found")
    return {"dropped": True, "name": name}
