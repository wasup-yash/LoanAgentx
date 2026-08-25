import redis.asyncio as redis

from app.core.config import get_settings

_settings = get_settings()
_pool: redis.ConnectionPool | None = None
_client: redis.Redis | None = None


def get_redis_pool() -> redis.ConnectionPool:
    global _pool
    if _pool is None:
        _pool = redis.ConnectionPool.from_url(
            _settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
            max_connections=20,
        )
    return _pool


def get_redis_client() -> redis.Redis:
    global _client
    if _client is None:
        _client = redis.Redis(connection_pool=get_redis_pool())
    return _client


async def close_redis() -> None:
    global _pool, _client
    if _client is not None:
        await _client.close()
        _client = None
    if _pool is not None:
        await _pool.disconnect()
        _pool = None