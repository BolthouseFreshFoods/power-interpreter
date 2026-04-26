"""Power Interpreter - SQL Engine

PostgreSQL-backed SQL execution engine for the 3 CSV-SQL tools:
- execute_sql_query: Inline query with MAX_INLINE_ROWS cap
- execute_sql_query_paged: OFFSET/FETCH pagination
- export_sql_query_to_csv: CSV.GZ export with download URL

All queries run against the same PostgreSQL instance that
load_dataset populates, supporting millions of rows.

Version: 1.0.0
"""

import gzip
import io
import logging
import uuid
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
from sqlalchemy import text

from app.config import settings
from app.database import get_engine

logger = logging.getLogger(__name__)

MAX_INLINE_ROWS = 5000

DANGEROUS_KEYWORDS = [
    "DROP", "DELETE", "UPDATE", "INSERT",
    "ALTER", "TRUNCATE", "GRANT", "REVOKE",
    "CREATE", "EXECUTE", "MERGE",
]


def _validate_query(sql: str) -> str:
    """Validate that a SQL query is read-only (SELECT or WITH).

    Raises ValueError for dangerous or non-SELECT statements.
    Returns the cleaned SQL string.
    """
    cleaned = sql.strip()
    if not cleaned:
        raise ValueError("Empty SQL query")

    upper = cleaned.upper()

    if not (upper.startswith("SELECT") or upper.startswith("WITH")):
        raise ValueError(
            "Only SELECT/WITH queries are allowed. "
            "Use load_dataset() to modify data."
        )

    for keyword in DANGEROUS_KEYWORDS:
        # Check as whole word to avoid false positives (e.g., "UPDATED_AT")
        import re
        if re.search(rf'\b{keyword}\b', upper):
            raise ValueError(
                f"Operation '{keyword}' is not allowed in queries. "
                f"Only SELECT and WITH statements are permitted."
            )

    return cleaned


def _serialize_value(value: Any) -> Any:
    """Serialize a database value for JSON output."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, (list, dict)):
        return value
    return value


def _format_results(columns: list, rows: list) -> Dict:
    """Convert SQLAlchemy result columns and rows to dict format."""
    data = []
    for row in rows:
        row_dict = {}
        for col_name, value in zip(columns, row):
            row_dict[col_name] = _serialize_value(value)
        data.append(row_dict)

    return {
        "columns": list(columns),
        "data": data,
        "row_count": len(data),
    }


async def execute_sql_query(
    query: str,
    session_id: str = "default",
) -> Dict:
    """Execute a read-only SQL SELECT/WITH query against PostgreSQL.

    Returns up to MAX_INLINE_ROWS rows inline. If the result set
    exceeds this cap, rows are truncated and has_more is set to True.

    Args:
        query: SQL SELECT or WITH statement
        session_id: Session identifier (for logging/auditing)

    Returns:
        Dict with columns, data, row_count, has_more
    """
    sql = _validate_query(query)

    # Cap inline results by appending LIMIT if not already present
    upper_sql = sql.upper()
    if "LIMIT" not in upper_sql:
        capped_sql = f"{sql.rstrip(';')} LIMIT {MAX_INLINE_ROWS + 1}"
    else:
        capped_sql = sql

    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(text(capped_sql))
        columns = list(result.keys())
        rows = result.fetchall()

    has_more = len(rows) > MAX_INLINE_ROWS
    if has_more:
        rows = rows[:MAX_INLINE_ROWS]

    formatted = _format_results(columns, rows)
    formatted["has_more"] = has_more

    if has_more:
        logger.info(
            "execute_sql_query: result capped at %d rows (has_more=True), session=%s",
            MAX_INLINE_ROWS,
            session_id,
        )
    else:
        logger.info(
            "execute_sql_query: %d rows returned, session=%s",
            formatted["row_count"],
            session_id,
        )

    return formatted


async def execute_sql_query_paged(
    query: str,
    offset: int,
    limit: int,
    session_id: str = "default",
) -> Dict:
    """Execute a read-only SQL query with OFFSET/FETCH pagination.

    ORDER BY is required for deterministic paging. If the query
    does not contain ORDER BY, a ValueError is raised.

    Args:
        query: SQL SELECT or WITH statement (must include ORDER BY)
        offset: Row offset (0-based)
        limit: Maximum rows to return per page
        session_id: Session identifier

    Returns:
        Dict with columns, data, row_count, limit, offset, has_more
    """
    sql = _validate_query(query)

    upper_sql = sql.upper()
    if "ORDER BY" not in upper_sql:
        raise ValueError(
            "ORDER BY is required for deterministic paging. "
            "Please add an ORDER BY clause to your query."
        )

    # Append LIMIT/OFFSET if not already present
    if "LIMIT" not in upper_sql:
        # Fetch one extra row to detect has_more
        paged_sql = f"{sql.rstrip(';')} LIMIT {limit + 1} OFFSET {offset}"
    else:
        paged_sql = sql

    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(text(paged_sql))
        columns = list(result.keys())
        rows = result.fetchall()

    has_more = len(rows) > limit
    if has_more:
        rows = rows[:limit]

    formatted = _format_results(columns, rows)
    formatted["limit"] = limit
    formatted["offset"] = offset
    formatted["has_more"] = has_more

    logger.info(
        "execute_sql_query_paged: %d rows (offset=%d, limit=%d, has_more=%s), session=%s",
        formatted["row_count"],
        offset,
        limit,
        has_more,
        session_id,
    )

    return formatted


async def export_sql_query_to_csv(
    query: str,
    session_id: str = "default",
    gzip_enabled: bool = True,
) -> Dict:
    """Execute a SQL query and export results to CSV or CSV.GZ.

    The file is saved to the temp directory and optionally registered
    with the file tracking system for a public download URL.

    Args:
        query: SQL SELECT or WITH statement
        session_id: Session identifier
        gzip_enabled: If True, create .csv.gz (recommended for large data)

    Returns:
        Dict with file_path, filename, size_bytes, download_url, row_count
    """
    sql = _validate_query(query)

    # Ensure temp directory exists
    settings.ensure_directories()
    temp_dir = settings.TEMP_DIR
    temp_dir.mkdir(parents=True, exist_ok=True)

    file_id = str(uuid.uuid4())
    ext = ".csv.gz" if gzip_enabled else ".csv"
    filename = f"query_export_{file_id[:8]}{ext}"
    file_path = temp_dir / filename

    engine = get_engine()

    # Use pandas to read query result and write CSV
    sync_url = settings.sync_database_url
    from sqlalchemy import create_engine as sync_create_engine
    sync_engine = sync_create_engine(sync_url)

    try:
        df = pd.read_sql(sql, sync_engine)
        row_count = len(df)

        if gzip_enabled:
            # Write CSV.GZ
            with gzip.open(str(file_path), "wt", encoding="utf-8") as f:
                df.to_csv(f, index=False)
        else:
            df.to_csv(str(file_path), index=False)

        size_bytes = file_path.stat().st_size
    finally:
        sync_engine.dispose()

    # Try to register with the file tracking system for a download URL
    download_url = None
    try:
        from app.database import get_session_factory
        from app.models import SandboxFile

        factory = get_session_factory()
        async with factory() as db_session:
            from datetime import timedelta

            content_bytes = file_path.read_bytes()
            sandbox_file = SandboxFile(
                id=uuid.UUID(file_id),
                session_id=session_id,
                filename=filename,
                mime_type="application/gzip" if gzip_enabled else "text/csv",
                file_size=size_bytes,
                content=content_bytes,
                expires_at=datetime.utcnow() + timedelta(hours=settings.SANDBOX_FILE_TTL_HOURS),
            )
            db_session.add(sandbox_file)
            await db_session.commit()

        base_url = settings.public_base_url
        if base_url:
            from urllib.parse import quote
            download_url = f"{base_url}/dl/{file_id}/{quote(filename)}"

    except Exception as e:
        logger.warning(
            "export_sql_query_to_csv: file tracking failed: %s. "
            "File saved locally but no download URL.",
            e,
        )

    logger.info(
        "export_sql_query_to_csv: %d rows -> %s (%s bytes, gzip=%s), session=%s",
        row_count,
        filename,
        size_bytes,
        gzip_enabled,
        session_id,
    )

    return {
        "file_path": str(file_path),
        "filename": filename,
        "size_bytes": size_bytes,
        "download_url": download_url,
        "row_count": row_count,
        "gzip": gzip_enabled,
    }
