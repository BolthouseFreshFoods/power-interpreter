"""
Kernel Startup Script — Pre-loaded Common Libraries
=====================================================

Runs automatically when a new sandbox kernel is created.
Pre-imports commonly needed libraries so they're immediately
available for execute_code calls.

All imports wrapped in try/except — missing packages don't
prevent kernel startup.
"""

KERNEL_STARTUP_CODE = '''
# =============================================================
# MCP Sandbox Kernel — Automatic Library Pre-load
# =============================================================

import os
import sys
import json
import csv
import base64
import io
import re
import pathlib
import traceback
import hashlib
from datetime import datetime, timedelta
from collections import defaultdict, Counter

# ----- PDF Libraries -----
try:
    import fitz  # PyMuPDF
    _fitz_available = True
except ImportError:
    fitz = None
    _fitz_available = False

try:
    import PyPDF2
    _pypdf2_available = True
except ImportError:
    PyPDF2 = None
    _pypdf2_available = False

try:
    import pdfplumber
    _pdfplumber_available = True
except ImportError:
    pdfplumber = None
    _pdfplumber_available = False

# ----- Data Libraries -----
try:
    import pandas as pd
    _pandas_available = True
except ImportError:
    pd = None
    _pandas_available = False

try:
    import numpy as np
    _numpy_available = True
except ImportError:
    np = None
    _numpy_available = False

try:
    import openpyxl
    _openpyxl_available = True
except ImportError:
    openpyxl = None
    _openpyxl_available = False

# ----- Document Libraries -----
try:
    from docx import Document as DocxDocument
    _docx_available = True
except ImportError:
    DocxDocument = None
    _docx_available = False

# ----- Image Libraries -----
try:
    from PIL import Image
    _pillow_available = True
except ImportError:
    Image = None
    _pillow_available = False

# ----- HTTP Libraries -----
try:
    import requests
    _requests_available = True
except ImportError:
    requests = None
    _requests_available = False

# =============================================================
# Pre-load Summary
# =============================================================

__sandbox_preloaded__ = {
    "fitz (PyMuPDF)": _fitz_available,
    "PyPDF2": _pypdf2_available,
    "pdfplumber": _pdfplumber_available,
    "pandas": _pandas_available,
    "numpy": _numpy_available,
    "openpyxl": _openpyxl_available,
    "python-docx": _docx_available,
    "Pillow": _pillow_available,
    "requests": _requests_available,
}

# =============================================================
# Sandbox Data Directory
# =============================================================

SANDBOX_DATA_DIR = pathlib.Path("/app/sandbox_data")
if not SANDBOX_DATA_DIR.exists():
    SANDBOX_DATA_DIR.mkdir(parents=True, exist_ok=True)

# =============================================================
# Helper Functions
# =============================================================

def _human_size(size_bytes):
    for unit in ["B", "KB", "MB", "GB"]:
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"


def list_sandbox_files(extension=None):
    """List files in sandbox, optionally filtered by extension."""
    files = []
    for f in SANDBOX_DATA_DIR.iterdir():
        if extension and not f.suffix.lower() == extension.lower():
            continue
        files.append({
            "name": f.name,
            "size_bytes": f.stat().st_size,
            "size_human": _human_size(f.stat().st_size),
            "extension": f.suffix,
            "modified": datetime.fromtimestamp(f.stat().st_mtime).isoformat(),
        })
    return sorted(files, key=lambda x: x["name"])


def list_sandbox_pdfs():
    """List all PDF files in the sandbox."""
    return list_sandbox_files(extension=".pdf")


def sandbox_status():
    """Print sandbox environment summary."""
    print("=" * 60)
    print("SANDBOX ENVIRONMENT STATUS")
    print("=" * 60)
    print()
    print("Pre-loaded Libraries:")
    for lib, available in __sandbox_preloaded__.items():
        status = "available" if available else "NOT installed"
        print(f"  {lib}: {status}")
    print()
    print(f"Sandbox Data Directory: {SANDBOX_DATA_DIR}")
    files = list_sandbox_files()
    print(f"Files in sandbox: {len(files)}")
    for f in files:
        print(f"  {f['name']} ({f['size_human']})")
    print()
    print(f"Python version: {sys.version}")
    print("=" * 60)


def read_pdf_text(filepath, method="auto"):
    """
    Extract text from a PDF file using the best available library.

    Args:
        filepath: Path to the PDF file
        method: "auto", "fitz", "pypdf2", or "pdfplumber"

    Returns:
        String containing all extracted text
    """
    filepath = str(filepath)

    if method == "auto":
        if _fitz_available:
            method = "fitz"
        elif _pdfplumber_available:
            method = "pdfplumber"
        elif _pypdf2_available:
            method = "pypdf2"
        else:
            raise ImportError("No PDF library available.")

    if method == "fitz":
        doc = fitz.open(filepath)
        text = ""
        for page in doc:
            text += page.get_text()
        doc.close()
        return text

    elif method == "pdfplumber":
        with pdfplumber.open(filepath) as pdf:
            text = ""
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text += page_text + "\\n"
        return text

    elif method == "pypdf2":
        reader = PyPDF2.PdfReader(filepath)
        text = ""
        for page in reader.pages:
            page_text = page.extract_text()
            if page_text:
                text += page_text + "\\n"
        return text

    else:
        raise ValueError(f"Unknown method: {method}")


# Startup confirmation
_available_count = sum(1 for v in __sandbox_preloaded__.values() if v)
_total_count = len(__sandbox_preloaded__)
print(f"[Kernel Ready] {_available_count}/{_total_count} libraries pre-loaded | sandbox: {SANDBOX_DATA_DIR}")
'''


def get_startup_code() -> str:
    """Return the kernel startup code string."""
    return KERNEL_STARTUP_CODE


def get_diagnostic_code() -> str:
    """Return diagnostic code for troubleshooting."""
    return '''
sandbox_status()
print()
print("PDF files available:")
for pdf in list_sandbox_pdfs():
    print(f"  {pdf['name']} ({pdf['size_human']})")
'''
