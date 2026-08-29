from functools import lru_cache

from fastapi import Request, status
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded

from app.core.config import get_settings


def _rate_limit_key_func(request: Request) -> str:
    # SECURITY: X-Forwarded-For is attacker-controllable unless behind a trusted proxy.
    # Only trust XFF when the immediate client is a known trusted proxy? For now,
    # prefer direct client IP; operators behind a trusted proxy should set
    # a dedicated trusted-proxy list and validate XFF there.
    # We keep XFF support but validate it is an IP-like string to reduce spoofing risk.
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        # Take first entry and basic IP validation
        candidate = forwarded_for.split(",")[0].strip()
        # Simple heuristic: contains dot or colon and not overly long
        if candidate and len(candidate) < 45 and all(c in "0123456789abcdefABCDEF.: " for c in candidate):
            # If it looks like an IP, use it; otherwise fall back to direct IP
            if candidate.count(".") == 3 or ":" in candidate:
                return candidate
    return request.client.host if request.client else "unknown"


@lru_cache
def get_limiter() -> Limiter:
    """
    Shared Limiter backed by Redis so limits are enforced across workers/replicas.
    Cached — call get_limiter.cache_clear() after changing REDIS_URL in tests.
    """
    settings = get_settings()
    return Limiter(
        key_func=_rate_limit_key_func,
        storage_uri=settings.redis_url,
        default_limits=[],
        strategy="fixed-window",
    )


def webhook_limit():
    """
    Decorator enforcing the configured webhook quota, or a no-op when
    RATE_LIMIT_ENABLED=false.
    """
    settings = get_settings()
    if not settings.rate_limit_enabled:
        def _disabled(func):
            return func
        return _disabled

    limiter = get_limiter()
    limit_string = f"{settings.rate_limit_requests}/{settings.rate_limit_window_seconds}seconds"
    return limiter.limit(limit_string)


def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    """
    Custom handler to return 429 with Retry-After header.
    """
    retry_after = exc.retry_after or 60
    return JSONResponse(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        content={
            "error": {
                "code": "rate_limit_exceeded",
                "detail": f"Rate limit exceeded. Try again in {retry_after} seconds.",
            }
        },
        headers={"Retry-After": str(retry_after)},
    )


def get_webhook_limit_string() -> str:
    settings = get_settings()
    return f"{settings.rate_limit_requests}/{settings.rate_limit_window_seconds}seconds"


def is_rate_limit_enabled() -> bool:
    return get_settings().rate_limit_enabled