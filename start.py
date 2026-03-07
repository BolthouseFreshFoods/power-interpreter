"""Power Interpreter - Start Script

Railway uses: python start.py
This is the entrypoint that Railway calls.

v2.9.2: Added sys.stderr redirect to fix Railway severity misclassification (Change #1)
"""
import sys
import uvicorn
import os

# ── Fix Railway severity misclassification (Change #1) ──────────────────
# Railway tags ALL stderr output as severity:"error", but Python's
# logging module, Rich Console (used by FastMCP), and httpx all
# default to stderr. This process-level redirect ensures INFO logs
# are correctly tagged as severity:"info" in Railway's log viewer.
# Must be set BEFORE uvicorn imports main.py (which triggers FastMCP/Rich).
sys.stderr = sys.stdout

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=port,
        log_level="info",
    )
