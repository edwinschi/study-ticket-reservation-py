from collections.abc import AsyncIterator

from redis.asyncio import Redis

from app.core.config import get_settings

settings = get_settings()

redis_client = Redis.from_url(
    settings.redis_url,
    decode_responses=True,
)


async def get_redis() -> AsyncIterator[Redis]:
    yield redis_client


async def close_redis() -> None:
    await redis_client.aclose()
