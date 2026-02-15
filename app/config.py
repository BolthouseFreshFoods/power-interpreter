"""Power Interpreter - Configuration

All settings loaded from environment variables with sensible defaults.
Railway automatically provides DATABASE_URL when PostgreSQL is attached.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()


class Settings:
    """Application settings"""
    
    # --- API Security ---
    API_KEY: str = os.getenv("API_KEY", "")
    
    # --- Database ---
    DATABASE_URL: str = os.getenv("DATABASE_URL", "")
    
    @property
    def async_database_url(self) -> str:
        """Convert DATABASE_URL to async format for SQLAlchemy"""
        url = self.DATABASE_URL
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql+asyncpg://", 1)
        elif url.startswith("postgresql://"):
            url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
        return url
    
    @property
    def sync_database_url(self) -> str:
        """Sync database URL for Alembic migrations"""
        url = self.DATABASE_URL
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql://", 1)
        return url
    
    # --- Sandbox Limits ---
    MAX_EXECUTION_TIME: int = int(os.getenv("MAX_EXECUTION_TIME", "300"))  # 5 min default
    MAX_MEMORY_MB: int = int(os.getenv("MAX_MEMORY_MB", "4096"))  # 4 GB default
    MAX_FILE_SIZE_MB: int = int(os.getenv("MAX_FILE_SIZE_MB", "500"))  # 500 MB max upload
    MAX_OUTPUT_SIZE: int = int(os.getenv("MAX_OUTPUT_SIZE", "1048576"))  # 1 MB max output text
    
    # --- Directories ---
    BASE_DIR: Path = Path("/app")
    SANDBOX_DIR: Path = Path(os.getenv("SANDBOX_DIR", "/app/sandbox_data"))
    UPLOAD_DIR: Path = Path(os.getenv("UPLOAD_DIR", "/app/uploads"))
    TEMP_DIR: Path = Path(os.getenv("TEMP_DIR", "/app/temp"))
    LOG_DIR: Path = Path(os.getenv("LOG_DIR", "/app/logs"))
    
    # --- Job Queue ---
    MAX_CONCURRENT_JOBS: int = int(os.getenv("MAX_CONCURRENT_JOBS", "4"))
    JOB_TIMEOUT: int = int(os.getenv("JOB_TIMEOUT", "600"))  # 10 min max per job
    JOB_CLEANUP_HOURS: int = int(os.getenv("JOB_CLEANUP_HOURS", "24"))  # Clean old jobs after 24h
    
    # --- Logging ---
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    
    # --- Pre-approved Libraries for Sandbox ---
    ALLOWED_IMPORTS: set = {
        # Data
        'pandas', 'numpy', 'csv', 'json', 'openpyxl', 'xlsxwriter',
        'pdfplumber', 'tabulate',
        # Visualization
        'matplotlib', 'matplotlib.pyplot', 'plotly', 'plotly.express',
        'plotly.graph_objects', 'seaborn',
        # Statistics & ML
        'scipy', 'scipy.stats', 'sklearn', 'statsmodels',
        # Standard library
        'math', 'statistics', 'datetime', 'collections', 'itertools',
        'functools', 'operator', 're', 'string', 'textwrap',
        'decimal', 'fractions', 'random', 'hashlib', 'base64',
        'io', 'os.path', 'pathlib', 'glob', 'copy', 'typing',
        'dataclasses', 'enum', 'abc',
    }
    
    # --- Blocked Operations ---
    BLOCKED_BUILTINS: set = {
        'exec', 'eval', 'compile', '__import__',
        'globals', 'locals', 'vars', 'dir',
        'getattr', 'setattr', 'delattr',
        'open',  # We provide our own safe file I/O
    }
    
    def ensure_directories(self):
        """Create required directories if they don't exist"""
        for d in [self.SANDBOX_DIR, self.UPLOAD_DIR, self.TEMP_DIR, self.LOG_DIR]:
            d.mkdir(parents=True, exist_ok=True)


# Singleton
settings = Settings()
