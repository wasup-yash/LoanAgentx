from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from app.api.routes.applications import router as applications_router
from app.api.routes.webhooks import router as webhooks_router
from app.core.config import get_settings
from app.core.exceptions import LoanAgentError, LLMConfigurationError
from app.core.logging import configure_logging, get_logger
from app.core.rate_limiter import get_limiter, is_rate_limit_enabled, rate_limit_exceeded_handler
from app.core.redis import close_redis
from app.db.session import engine
from slowapi.errors import RateLimitExceeded

settings = get_settings()
configure_logging(settings.log_level, redact_secrets=settings.log_secrets_redaction)
logger = get_logger("app.main")

# Initialize rate limiter and attach to app.state for slowapi
limiter = get_limiter()


def _validate_secrets() -> None:
    """Fail fast if required secrets are missing for enabled features."""
    if settings.environment not in ("dev", "test"):
        if not settings.openai_api_key:
            raise LLMConfigurationError("OPENAI_API_KEY is required in non-dev environments.")
        if not settings.core_banking_url:
            raise RuntimeError("CORE_BANKING_URL is required in non-dev environments.")
    if settings.require_webhook_signature and not settings.webhook_signing_secret:
        raise RuntimeError("WEBHOOK_SIGNING_SECRET is required when REQUIRE_WEBHOOK_SIGNATURE=true.")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    _validate_secrets()
    logger.info("app.startup", extra={"environment": settings.environment, "schema_management": "alembic"})
    # Attach limiter to app.state for slowapi
    if is_rate_limit_enabled():
        app.state.limiter = get_limiter()
    yield
    await engine.dispose()
    await close_redis()
    logger.info("db.engine_disposed")


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description="Agentic loan origination with per-fact source traceability and full LLM audit logging.",
    lifespan=lifespan,
)
app.include_router(webhooks_router)
app.include_router(applications_router)

app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)


@app.exception_handler(LoanAgentError)
async def loan_agent_error_handler(_: Request, exc: LoanAgentError) -> JSONResponse:
    log = logger.warning if exc.status_code < 500 else logger.error
    log(
        "request.rejected",
        extra={"code": exc.code, "status_code": exc.status_code, "detail": exc.detail},
    )
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": exc.code, "detail": exc.detail}},
    )


@app.get("/healthz", tags=["ops"], summary="Liveness + DB reachability probe")
async def healthz() -> dict[str, str]:
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except OperationalError:
        return {"status": "degraded", "database": "unreachable"}
    return {"status": "ok", "database": "reachable"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=settings.environment == "dev")
