"""Integration tests for idempotency edge cases and graph behavior."""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Communication, Direction


@pytest.mark.asyncio
async def test_duplicate_key_with_redis_flushed_replays_from_db(
    client: TestClient, fresh_redis
):
    """
    If Redis loses the key (eviction/restart) but the DB still has the
    Communication row, a retried webhook must replay the original ack
    instead of crashing with IntegrityError.
    """
    key = f"replay-{uuid.uuid4().hex[:12]}"
    user_id = f"replay-user-{uuid.uuid4().hex[:8]}"
    payload = {"user_id": user_id, "channel": "sms", "text": "hello"}

    resp1 = client.post("/webhooks/incoming-message", json=payload, headers={"Idempotency-Key": key})
    assert resp1.status_code == 202, resp1.text
    first_ack = resp1.json()

    await fresh_redis.delete(f"idem:{key}")

    resp2 = client.post(
        "/webhooks/incoming-message",
        json={"user_id": user_id, "channel": "sms", "text": "different text"},
        headers={"Idempotency-Key": key},
    )
    assert resp2.status_code == 202, resp2.text
    second_ack = resp2.json()
    assert second_ack["communication_id"] == first_ack["communication_id"]
    assert second_ack["application_id"] == first_ack["application_id"]


@pytest.mark.asyncio
async def test_no_duplicate_outbound_chase_for_same_missing_set(
    client: TestClient, db_session: AsyncSession
):
    """The chase node must not spam borrowers with identical messages across re-runs."""
    user_id = f"chase-dedup-{uuid.uuid4().hex[:8]}"
    app_id: uuid.UUID | None = None

    for i in range(3):
        resp = client.post(
            "/webhooks/incoming-message",
            json={
                "user_id": user_id,
                "channel": "sms",
                "text": f"Update {i}: I make $5,000 a month and live at 12 Oak Street, Austin TX 78701.",
            },
            headers={"Idempotency-Key": f"chase-dedup-{uuid.uuid4().hex[:12]}"},
        )
        assert resp.status_code == 202, resp.text
        app_id = uuid.UUID(resp.json()["application_id"])

    result = await db_session.execute(
        select(Communication).where(
            Communication.application_id == app_id,
            Communication.direction == Direction.outbound,
        )
    )
    outbound = result.scalars().all()
    unique_contents = {c.content for c in outbound}
    assert len(unique_contents) <= 2, (
        f"expected at most 2 distinct chase contents (SMS/email variants), "
        f"got {len(unique_contents)}: {[c.content for c in outbound]}"
    )


@pytest.mark.asyncio
async def test_llm_timeout_degrades_to_manual_review(client: TestClient, db_session: AsyncSession):
    """Graceful degradation: LLM timeout flips status to manual_review."""
    user_id = f"degrade-{uuid.uuid4().hex[:8]}"

    resp = client.post(
        "/webhooks/incoming-message",
        json={"user_id": user_id, "channel": "sms", "text": "SIMULATE_LLM_TIMEOUT process me"},
        headers={"Idempotency-Key": f"degrade-{uuid.uuid4().hex[:12]}"},
    )
    assert resp.status_code == 202, resp.text
    app_id = resp.json()["application_id"]

    audit = client.get(f"/applications/{app_id}/audit-trail")
    assert audit.status_code == 200
    assert audit.json()["status"] == "manual_review"
