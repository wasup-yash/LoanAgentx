import os
import uuid
from collections.abc import AsyncGenerator, Generator
from typing import Any

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Set test environment variables BEFORE importing anything from app
os.environ["LLM_MOCK"] = "true"
os.environ["ENVIRONMENT"] = "test"
os.environ["DATABASE_URL"] = "postgresql+asyncpg://loan_agent:loan_agent@localhost:5432/loan_agent"
os.environ["RATE_LIMIT_ENABLED"] = "false"
os.environ["REQUIRE_WEBHOOK_SIGNATURE"] = "false"
os.environ["LOG_SECRETS_REDACTION"] = "false"

from app.main import app  # noqa: E402
from app.models import Application, ApplicationStatus  # noqa: E402

_TRUNCATE_SQL = (
    "TRUNCATE TABLE extracted_facts, audit_logs, communications, "
    "documents, applications RESTART IDENTITY CASCADE"
)


@pytest_asyncio.fixture(autouse=True)
async def _isolate_app_loop_resources():
    """
    Reset app-level async singletons before each test.

    asyncpg/Redis transports are event-loop-bound. Without this reset, a
    singleton created on one test's loop (e.g. TestClient's portal) breaks
    when later tests touch it from a different loop.
    """
    import app.core.redis as app_redis

    await app_redis.close_redis()
    yield
    await app_redis.close_redis()


@pytest_asyncio.fixture(scope="function")
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """
    Test-owned database session on the pytest event loop, for direct
    service-layer access and assertions.

    IMPORTANT: never share live sessions/engines across event loops. The app
    runs on the TestClient's internal portal loop; this fixture owns a
    short-lived engine bound to the current test loop. Tables are truncated
    around each test so HTTP-driven tests stay isolated without overrides.
    """
    engine = create_async_engine(os.environ["DATABASE_URL"], pool_pre_ping=False)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)

    async with factory() as session:
        try:
            async with engine.begin() as conn:
                await conn.execute(text(_TRUNCATE_SQL))
            yield session
        finally:
            await session.close()
            async with engine.begin() as conn:
                await conn.execute(text(_TRUNCATE_SQL))
            await engine.dispose()


@pytest.fixture(scope="function")
def client() -> Generator[TestClient, None, None]:
    """
    FastAPI TestClient running the real app stack on its own internal loop.
    Isolation is provided by db_session's table truncation, not by overrides.
    """
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def sample_user_id() -> str:
    return "test-user-123"


@pytest.fixture
def sample_application(db_session: AsyncSession) -> Application:
    application = Application(
        external_borrower_id="test-borrower-001", status=ApplicationStatus.pending_docs
    )
    db_session.add(application)
    return application


@pytest.fixture
def webhook_payload() -> dict[str, Any]:
    return {
        "user_id": "test-borrower-001",
        "channel": "sms",
        "text": "Hi, I make $5,000 a month and live at 123 Main St.",
    }


@pytest.fixture
def idempotency_key() -> str:
    return f"test-idem-{uuid.uuid4().hex[:12]}"


@pytest_asyncio.fixture(scope="function")
async def fresh_redis() -> AsyncGenerator[Any, None]:
    """Test-owned Redis client on the pytest loop (never reuse the app's)."""
    import redis.asyncio as aioredis

    client = aioredis.from_url("redis://localhost:6379/0", decode_responses=True)
    try:
        await client.flushdb()
        yield client
    finally:
        await client.flushdb()
        await client.aclose()
