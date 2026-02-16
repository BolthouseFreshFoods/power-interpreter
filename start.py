"""Power Interpreter - Start Script

Reads the PORT environment variable (set by Railway) and starts uvicorn.
Uses single worker to avoid multiprocessing issues on Railway.
"""

import os
import uvicorn

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    
    print(f"Starting Power Interpreter on port {port}")
    print(f"  DATABASE_URL: {'***configured***' if os.environ.get('DATABASE_URL') else 'NOT SET (will start without DB)'}")
    print(f"  API_KEY: {'***configured***' if os.environ.get('API_KEY') else 'NOT SET (dev mode)'}")
    
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=port,
        workers=1,  # Single worker - avoids multiprocessing issues on Railway
        log_level="info",
        access_log=True,
    )
