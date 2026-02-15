"""Power Interpreter - File Management Routes

Upload, download, list, and manage files in the sandbox.
Supports large file uploads via chunked transfer.
"""

import base64
from fastapi import APIRouter, HTTPException, UploadFile, File as FastAPIFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from typing import Optional, List

from app.engine.file_manager import file_manager
from app.models import FileType
from app.config import settings

router = APIRouter()


class FileUploadBase64Request(BaseModel):
    """Upload file via base64 (for MCP calls)"""
    filename: str = Field(..., description="Filename")
    content_base64: str = Field(..., description="File content as base64 string")
    session_id: Optional[str] = Field(default=None, description="Session ID")


@router.post("/files/upload")
async def upload_file(file: UploadFile = FastAPIFile(...), session_id: Optional[str] = None):
    """Upload a file via multipart form
    
    Supports CSV, Excel, PDF, JSON, text, and more.
    Files are stored in the sandbox and available for code execution.
    """
    content = await file.read()
    
    # Check file size
    max_bytes = settings.MAX_FILE_SIZE_MB * 1024 * 1024
    if len(content) > max_bytes:
        raise HTTPException(
            status_code=413, 
            detail=f"File too large. Max: {settings.MAX_FILE_SIZE_MB} MB"
        )
    
    result = await file_manager.upload_file(
        filename=file.filename,
        content=content,
        session_id=session_id
    )
    
    return result


@router.post("/files/upload-base64")
async def upload_file_base64(request: FileUploadBase64Request):
    """Upload a file via base64 encoding (for MCP tool calls)
    
    Use this when calling from SimTheory.ai MCP where multipart isn't available.
    """
    try:
        content = base64.b64decode(request.content_base64)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid base64 content")
    
    max_bytes = settings.MAX_FILE_SIZE_MB * 1024 * 1024
    if len(content) > max_bytes:
        raise HTTPException(
            status_code=413, 
            detail=f"File too large. Max: {settings.MAX_FILE_SIZE_MB} MB"
        )
    
    result = await file_manager.upload_file(
        filename=request.filename,
        content=content,
        session_id=request.session_id
    )
    
    return result


@router.get("/files/{file_id}")
async def get_file_info(file_id: str):
    """Get file metadata"""
    info = await file_manager.get_file(file_id)
    if not info:
        raise HTTPException(status_code=404, detail="File not found")
    return info


@router.get("/files/{file_id}/download")
async def download_file(file_id: str):
    """Download a file"""
    result = await file_manager.download_file(file_id)
    if not result:
        raise HTTPException(status_code=404, detail="File not found")
    
    content, filename, mime_type = result
    
    return Response(
        content=content,
        media_type=mime_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"'
        }
    )


@router.get("/files")
async def list_files(
    session_id: Optional[str] = None,
    file_type: Optional[str] = None,
    limit: int = 100
):
    """List all files"""
    files = await file_manager.list_files(
        session_id=session_id,
        file_type=file_type,
        limit=limit
    )
    return {"files": files, "count": len(files)}


@router.delete("/files/{file_id}")
async def delete_file(file_id: str):
    """Delete a file"""
    deleted = await file_manager.delete_file(file_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="File not found")
    return {"deleted": True, "file_id": file_id}


@router.get("/sandbox/files")
async def list_sandbox_files(session_id: str = "default"):
    """List files in a session's sandbox directory
    
    Shows files created by code execution.
    """
    files = await file_manager.list_sandbox_files(session_id)
    return {"files": files, "count": len(files), "session_id": session_id}
