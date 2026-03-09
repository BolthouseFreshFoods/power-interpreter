"""Root-level module re-export for Railway.

Railway's start command references 'main:app' directly.
This shim imports and re-exports the FastAPI app from app.main.
"""
from app.main import app  # noqa: F401
