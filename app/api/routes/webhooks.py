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
    """Deterministic fallback key derived from payload for clients that don't send the header."""
    payload = message.model_dump(mode="json", exclude_none=True)
    import json
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
    # Use provided key or derive a deterministic fallback (logs warning for observability)
    if idempotency_key is None:
        idempotency_key = _fallback_idempotency_key(message)
        logger.warning("webhook.missing_idempotency_key", extra={"fallback_key": idempotency_key})

    # Check idempotency FIRST - before any DB writes
    is_duplicate, cached = await check_idempotency(idempotency_key)
    if is_duplicate:
        return JSONResponse(status_code=status.HTTP_202_ACCEPTED, content=cached)

    ack = await ingest_inbound_message(db, message, idempotency_key)

    # Store the REAL ack response for future duplicates
    await store_idempotency_response(idempotency_key, ack.model_dump())

    background_tasks.add_task(run_pipeline, str(ack.application_id))
    return ack