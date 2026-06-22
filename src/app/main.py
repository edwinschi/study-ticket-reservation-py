from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.error_handlers import register_exception_handlers
from app.core.exceptions import AppError
from app.core.logging import configure_logging
from app.core.middleware import RequestContextMiddleware
from app.db.redis import close_redis, get_redis
from app.db.session import close_database, get_db_session
from app.modules.events.router import router as events_router
from app.modules.reservations.router import router as reservations_router
from app.modules.sessions.router import router as sessions_router
from app.modules.stress_admin.router import router as stress_admin_router
from app.modules.users.router import router as auth_router

settings = get_settings()
configure_logging(settings.log_level)

DatabaseSession = Annotated[AsyncSession, Depends(get_db_session)]
RedisClient = Annotated[Redis, Depends(get_redis)]


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncGenerator[None]:
    yield
    await close_redis()
    await close_database()


app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.add_middleware(RequestContextMiddleware)
register_exception_handlers(app)
app.include_router(sessions_router)
app.include_router(auth_router)
app.include_router(events_router)
app.include_router(reservations_router)
app.include_router(stress_admin_router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/ready")
async def ready(db_session: DatabaseSession, redis_client: RedisClient) -> dict[str, str]:
    try:
        await db_session.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        raise AppError(
            "PostgreSQL is unavailable",
            code="POSTGRES_UNAVAILABLE",
            status_code=503,
        ) from exc

    try:
        await redis_client.ping()
    except RedisError as exc:
        raise AppError(
            "Redis is unavailable",
            code="REDIS_UNAVAILABLE",
            status_code=503,
        ) from exc

    return {"status": "ready", "postgres": "ok", "redis": "ok"}
