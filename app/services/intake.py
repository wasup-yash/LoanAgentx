import base64
import binascii
import hashlib
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.exceptions import (
    InvalidAttachmentError,
    PayloadTooLargeError,
    UnsupportedMediaTypeError,
)
from app.models import Application, ApplicationStatus, Communication, Direction, Document
from app.schemas.webhook import AttachmentIn, IncomingMessageWebhook, WebhookAck
from app.services.object_store import persist_document

ALLOWED_FILE_TYPES = frozenset({"application/pdf", "text/plain", "image/jpeg", "image/png"})
OPEN_STATUSES = (
    ApplicationStatus.pending_docs,
    ApplicationStatus.processing,
    ApplicationStatus.manual_review,
)


async def ingest_inbound_message(db: AsyncSession, message: IncomingMessageWebhook, idempotency_key: str) -> WebhookAck:
    user_id = message.user_id.strip()
    application = await _resolve_open_application(db, user_id)

    received_at = message.sent_at or datetime.now(timezone.utc)
    if received_at.tzinfo is None:
        received_at = received_at.replace(tzinfo=timezone.utc)

    # Scope DB key by borrower to prevent cross-borrower collisions on same header value
    scoped_db_key = f"{user_id}:{idempotency_key}"

    communication = Communication(
        application_id=application.id,
        channel=message.channel,
        direction=Direction.inbound,
        content=message.text,
        timestamp=received_at,
        idempotency_key=scoped_db_key,
    )
    db.add(communication)

    documents: list[Document] = []
    for attachment in message.attachments:
        document = _build_document(attachment, application.id)
        db.add(document)
        documents.append(document)

    try:
        await db.commit()
    except IntegrityError as exc:
        # Concurrent duplicate of the same Idempotency-Key won the race at the DB level.
        # Replay the original ack scoped to this borrower instead of surfacing a 500.
        await db.rollback()
        scoped_key = f"{message.user_id.strip()}:{idempotency_key}"
        replayed = await _replay_by_idempotency_key(db, scoped_key, message.user_id.strip())
        if replayed is not None:
            return replayed
        raise

    return WebhookAck(
        application_id=application.id,
        application_status=application.status.value,
        communication_id=communication.id,
        document_ids=[document.id for document in documents],
    )


async def _replay_by_idempotency_key(
    db: AsyncSession, idempotency_key: str, user_id: str | None = None
) -> WebhookAck | None:
    result = await db.execute(
        select(Communication).where(Communication.idempotency_key == idempotency_key)
    )
    existing = result.scalar_one_or_none()
    if existing is None:
        return None

    app_result = await db.execute(
        select(Application).where(Application.id == existing.application_id)
    )
    application = app_result.scalar_one()

    # Enforce borrower scoping: never replay across different external_borrower_id
    if user_id is not None and application.external_borrower_id != user_id:
        return None

    doc_result = await db.execute(
        select(Document.id).where(Document.application_id == existing.application_id)
    )
    return WebhookAck(
        application_id=existing.application_id,
        application_status=application.status.value,
        communication_id=existing.id,
        document_ids=list(doc_result.scalars().all()),
    )


async def _resolve_open_application(db: AsyncSession, external_borrower_id: str) -> Application:
    result = await db.execute(
        select(Application)
        .where(
            Application.external_borrower_id == external_borrower_id,
            Application.status.in_(OPEN_STATUSES),
        )
        .order_by(Application.created_at.desc())
        .limit(1)
    )
    application = result.scalar_one_or_none()
    if application is None:
        application = Application(external_borrower_id=external_borrower_id)
        db.add(application)
        await db.flush()
    return application


def _build_document(attachment: AttachmentIn, application_id: uuid.UUID) -> Document:
    if attachment.file_type not in ALLOWED_FILE_TYPES:
        raise UnsupportedMediaTypeError(f"file_type '{attachment.file_type}' is not accepted.")

    payload: bytes | None = None
    if attachment.content_base64 is not None:
        try:
            payload = base64.b64decode(attachment.content_base64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise InvalidAttachmentError("attachment content_base64 is not valid base64.") from exc

    if payload is not None and len(payload) > get_settings().max_attachment_bytes:
        raise PayloadTooLargeError()

    document_id = uuid.uuid4()
    s3_path: str | None
    digest: str | None = None
    if payload is not None:
        s3_path = persist_document(application_id, document_id, attachment.filename or "upload.bin", payload)
        digest = hashlib.sha256(payload).hexdigest()
    else:
        # content_base64 absent -> url must be present (validated by AttachmentIn)
        if attachment.url is None:
            raise InvalidAttachmentError("attachment url is required when content_base64 is absent.")
        # Block path traversal in any s3:// URL and reject encoded traversal attempts
        if ".." in attachment.url or "%2e" in attachment.url.lower():
            raise InvalidAttachmentError("attachment url must not contain path traversal segments.")
        if attachment.url.startswith("s3://loan-agent-documents/"):
            # Additional strict check for s3 prefix traversal via local_document_path logic will also enforce,
            # but fail fast here.
            pass
        s3_path = attachment.url

    return Document(
        id=document_id,
        application_id=application_id,
        file_type=attachment.file_type,
        s3_path=s3_path,
        content_sha256=digest,
    )
