import json
import uuid
from typing import Any

from app.core.config import get_settings
from app.core.redis import get_redis_client

_IDEMP_PREFIX = "idem:"


def _json_default(obj: Any) -> str:
    if isinstance(obj, uuid.UUID):
        return str(obj)
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


async def get_idempotency_response(key: str) -> dict[str, Any] | None:
    """Get cached response if key exists, else None."""
    redis_client = get_redis_client()
    cached = await redis_client.get(f"{_IDEMP_PREFIX}{key}")
    if cached:
        try:
            return json.loads(cached)
        except json.JSONDecodeError:
            return None
    return None


async def store_idempotency_response(key: str, response_payload: dict[str, Any]) -> None:
    """Store response for an idempotency key (called after successful processing)."""
    redis_client = get_redis_client()
    settings = get_settings()
    await redis_client.setex(f"{_IDEMP_PREFIX}{key}", settings.idempotency_ttl_seconds, json.dumps(response_payload, default=_json_default))


async def check_idempotency(key: str) -> tuple[bool, dict[str, Any] | None]:
    """
    Check if key exists in Redis.
    Returns (True, cached_response) if duplicate, (False, None) if new.
    """
    cached = await get_idempotency_response(key)
    if cached is not None:
        return True, cached
    return False, None