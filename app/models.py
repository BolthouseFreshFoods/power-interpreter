"""Power Interpreter - Database Models

All persistent data models for:
- Sessions (workspace isolation)
- Jobs (async execution tracking)
- Files (uploaded and generated)
- Data tables (dynamic CSV/dataset storage)
- Execution logs (audit trail)
"""

import uuid
from datetime import datetime
from sqlalchemy import (
    Column, String, Text, Integer, BigInteger, Float,
    Boolean, DateTime, JSON, ForeignKey, Index, Enum as SQLEnum
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from app.database import Base
import enum


# ============================================================
# Enums
# ============================================================

class JobStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"


class FileType(str, enum.Enum):
    UPLOAD = "upload"       # User uploaded
    GENERATED = "generated" # Created by code execution
    DATASET = "dataset"     # Loaded into PostgreSQL
    CHART = "chart"         # Matplotlib/plotly output
    REPORT = "report"       # Excel/PDF report


# ============================================================
# Sessions - Workspace Isolation
# ============================================================

class Session(Base):
    __tablename__ = "sessions"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), nullable=False)
    description = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    is_active = Column(Boolean, default=True)
    metadata_ = Column("metadata", JSONB, default=dict)
    
    # Relationships
    jobs = relationship("Job", back_populates="session", cascade="all, delete-orphan")
    files = relationship("File", back_populates="session", cascade="all, delete-orphan")
    datasets = relationship("Dataset", back_populates="session", cascade="all, delete-orphan")


# ============================================================
# Jobs - Async Execution Tracking
# ============================================================

class Job(Base):
    __tablename__ = "jobs"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id = Column(UUID(as_uuid=True), ForeignKey("sessions.id"), nullable=True)
    
    # Job details
    code = Column(Text, nullable=False)  # Python code to execute
    status = Column(SQLEnum(JobStatus), default=JobStatus.PENDING, nullable=False)
    priority = Column(Integer, default=0)  # Higher = more important
    
    # Execution tracking
    submitted_at = Column(DateTime, default=datetime.utcnow)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    execution_time_ms = Column(BigInteger, nullable=True)
    
    # Results
    stdout = Column(Text, default="")  # Print output
    stderr = Column(Text, default="")  # Error output
    result = Column(JSONB, nullable=True)  # Structured result
    error_message = Column(Text, nullable=True)
    error_traceback = Column(Text, nullable=True)
    
    # Resource tracking
    memory_used_mb = Column(Float, nullable=True)
    files_created = Column(JSONB, default=list)  # List of file paths created
    
    # Metadata
    metadata_ = Column("metadata", JSONB, default=dict)
    
    # Relationships
    session = relationship("Session", back_populates="jobs")
    
    # Indexes
    __table_args__ = (
        Index('idx_jobs_status', 'status'),
        Index('idx_jobs_session', 'session_id'),
        Index('idx_jobs_submitted', 'submitted_at'),
    )


# ============================================================
# Files - Upload and Generated File Tracking
# ============================================================

class File(Base):
    __tablename__ = "files"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id = Column(UUID(as_uuid=True), ForeignKey("sessions.id"), nullable=True)
    
    # File info
    filename = Column(String(500), nullable=False)
    original_filename = Column(String(500), nullable=True)  # Original upload name
    file_type = Column(SQLEnum(FileType), default=FileType.UPLOAD)
    mime_type = Column(String(100), default="application/octet-stream")
    file_size = Column(BigInteger, default=0)  # Bytes
    
    # Storage
    storage_path = Column(String(1000), nullable=False)  # Path on persistent volume
    checksum = Column(String(64), nullable=True)  # SHA-256
    
    # Metadata
    row_count = Column(BigInteger, nullable=True)  # For CSV/datasets
    column_count = Column(Integer, nullable=True)
    columns = Column(JSONB, nullable=True)  # Column names and types
    preview = Column(JSONB, nullable=True)  # First few rows
    
    created_at = Column(DateTime, default=datetime.utcnow)
    metadata_ = Column("metadata", JSONB, default=dict)
    
    # Relationships
    session = relationship("Session", back_populates="files")
    
    __table_args__ = (
        Index('idx_files_session', 'session_id'),
        Index('idx_files_type', 'file_type'),
    )


# ============================================================
# Datasets - Large Data Storage in PostgreSQL
# ============================================================

class Dataset(Base):
    __tablename__ = "datasets"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id = Column(UUID(as_uuid=True), ForeignKey("sessions.id"), nullable=True)
    
    # Dataset info
    name = Column(String(255), nullable=False)  # Logical name (e.g., "vestis_invoices")
    table_name = Column(String(255), nullable=False, unique=True)  # Actual PG table name
    description = Column(Text, default="")
    
    # Schema
    row_count = Column(BigInteger, default=0)
    column_count = Column(Integer, default=0)
    columns = Column(JSONB, nullable=True)  # [{name, type, nullable}]
    
    # Source
    source_file_id = Column(UUID(as_uuid=True), ForeignKey("files.id"), nullable=True)
    source_filename = Column(String(500), nullable=True)
    
    # Size tracking
    size_bytes = Column(BigInteger, default=0)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    metadata_ = Column("metadata", JSONB, default=dict)
    
    # Relationships
    session = relationship("Session", back_populates="datasets")
    
    __table_args__ = (
        Index('idx_datasets_session', 'session_id'),
        Index('idx_datasets_name', 'name'),
    )


# ============================================================
# Execution Logs - Audit Trail
# ============================================================

class ExecutionLog(Base):
    __tablename__ = "execution_logs"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id = Column(UUID(as_uuid=True), ForeignKey("jobs.id"), nullable=True)
    
    # Log entry
    level = Column(String(20), default="INFO")  # INFO, WARNING, ERROR
    message = Column(Text, nullable=False)
    timestamp = Column(DateTime, default=datetime.utcnow)
    
    # Context
    code_snippet = Column(Text, nullable=True)  # Relevant code
    metadata_ = Column("metadata", JSONB, default=dict)
    
    __table_args__ = (
        Index('idx_logs_job', 'job_id'),
        Index('idx_logs_timestamp', 'timestamp'),
        Index('idx_logs_level', 'level'),
    )
