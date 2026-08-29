from fastapi import APIRouter, BackgroundTasks, Depends, Header, Request, status
from fastapi.responses import JSONResponse
import hashlib

from app.api.deps.common import DbSession
from app.api.deps.webhook_auth import verify_webhook_signature
from app.core.logging import get_logger
from app.core.rate_limiter import webhook_limit
from app.schemas.webhook import IncomingMessageWebhook, WebhookAck
from app.services.idempotency import check_idempotency, store_idempotency_response
from app.services.intake import ingest_inbound_message
from app.services.pipeline import run_pipeline

logger = get_logger(__name__)

router = APIRouter(tags=["webhooks"])


def _fallback_idempotency_key(message: IncomingMessageWebhook) -> str:
    """Deterministic fallback key derived from payload for clients that don't send the header.

    Hashes digests of attachments instead of raw base64 to avoid OOM on large payloads.
    """
    import json

    payload = message.model_dump(mode="json", exclude_none=True)
    # Replace raw base64 with its sha256 digest to bound memory / hash cost
    for att in payload.get("attachments", []):
        if "content_base64" in att and att["content_base64"] is not None:
            raw_b64 = att["content_base64"]
            att["content_base64_sha256"] = hashlib.sha256(raw_b64.encode()).hexdigest()
            del att["content_base64"]
    return "auto-" + hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:32]


@router.post(
    "/webhooks/incoming-message",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=WebhookAck,
    summary="Ingest an inbound borrower message (Twilio/SendGrid-style)",
    dependencies=[Depends(verify_webhook_signature)],
)
@webhook_limit()
async def incoming_message(
    request: Request,
    message: IncomingMessageWebhook,
    background_tasks: BackgroundTasks,
    db: DbSession,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> WebhookAck | JSONResponse:
    # Validate explicit key length to bound Redis memory (keys are user-controlled)
    if idempotency_key is not None and len(idempotency_key) > 255:
        from fastapi import HTTPException

        raise HTTPException(status_code=422, detail="Idempotency-Key too long (max 255).")
    # Use provided key or derive a deterministic fallback (logs warning for observability)
    if idempotency_key is None:
        idempotency_key = _fallback_idempotency_key(message)
        logger.warning("webhook.missing_idempotency_key", extra={"fallback_key": idempotency_key})

    # Scope Redis key by borrower to prevent cross-borrower replay
    scoped_redis_key = f"{message.user_id.strip()}:{idempotency_key}"

    # Check idempotency FIRST - before any DB writes
    is_duplicate, cached = await check_idempotency(scoped_redis_key)
    if is_duplicate:
        return JSONResponse(status_code=status.HTTP_202_ACCEPTED, content=cached)

    ack = await ingest_inbound_message(db, message, idempotency_key)

    # Store the REAL ack response for future duplicates (scoped)
    await store_idempotency_response(scoped_redis_key, ack.model_dump())

    background_tasks.add_task(run_pipeline, str(ack.application_id))
    return ack