"""Power Interpreter - Database Setup

PostgreSQL connection management with async SQLAlchemy.
Railway provides DATABASE_URL automatically when Postgres is attached.
"""

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import text
from app.config import settings
import logging

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    """SQLAlchemy declarative base"""
    pass


# Async engine - created lazily
_engine = None
_session_factory = None


def get_engine():
    """Get or create async engine"""
    global _engine
    if _engine is None:
        _engine = create_async_engine(
            settings.async_database_url,
            echo=False,
            pool_size=10,
            max_overflow=20,
            pool_timeout=30,
            pool_recycle=1800,
            pool_pre_ping=True,
        )
    return _engine


def get_session_factory():
    """Get or create session factory"""
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            bind=get_engine(),
            class_=AsyncSession,
            expire_on_commit=False
        )
    return _session_factory


async def get_session() -> AsyncSession:
    """Dependency: Get database session"""
    factory = get_session_factory()
    async with factory() as session:
        yield session


async def init_database():
    """Initialize database - create all tables"""
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database tables created successfully")


async def check_database():
    """Health check - verify database connection"""
    engine = get_engine()
    try:
        async with engine.connect() as conn:
            result = await conn.execute(text("SELECT 1"))
            return result.scalar() == 1
    except Exception as e:
        logger.error(f"Database health check failed: {e}")
        return False


async def shutdown_database():
    """Cleanup on shutdown"""
    global _engine, _session_factory
    if _engine:
        await _engine.dispose()
        _engine = None
        _session_factory = None
    logger.info("Database connections closed")
