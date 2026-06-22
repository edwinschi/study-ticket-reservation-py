from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings

settings = get_settings()

engine = create_async_engine(
    settings.database_url,
    pool_pre_ping=True,
)

# The factory creates lightweight AsyncSession objects bound to the shared async engine.
# The engine owns the connection pool; individual sessions should stay request-scoped.
async_session_factory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db_session() -> AsyncIterator[AsyncSession]:
    """
    Provide one SQLAlchemy AsyncSession per FastAPI request.

    AsyncSession is not safe to share across concurrent requests, background tasks, or workers.
    Keeping it scoped to this dependency avoids cross-request transaction state leaks.
    """
    async with async_session_factory() as session:
        yield session


async def close_database() -> None:
    """Dispose the async engine connection pool during application or worker shutdown."""
    await engine.dispose()
