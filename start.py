"""Power Interpreter - Start Script

Reads the PORT environment variable (set by Railway) and starts uvicorn.
Starts with 1 worker for fast startup, scales if needed.
"""

import os
import sys
import uvicorn

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    workers = int(os.environ.get("WORKERS", 1))
    
    print("=" * 50)
    print("Power Interpreter MCP v1.0.0")
    print("=" * 50)
    print(f"  Port: {port}")
    print(f"  Workers: {workers}")
    print(f"  DATABASE_URL: {'configured' if os.environ.get('DATABASE_URL') else 'NOT SET'}")
    print(f"  API_KEY: {'configured' if os.environ.get('API_KEY') else 'NOT SET (dev mode)'}")
    print("=" * 50)
    sys.stdout.flush()
    
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=port,
        workers=workers,
        log_level="info",
        access_log=True,
        timeout_keep_alive=65,
    )
