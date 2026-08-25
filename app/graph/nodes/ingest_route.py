from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import MalformedPDFError, OCRUnavailableError, TextDecodingError
from app.core.logging import get_logger
from app.models import Application, AuditLog, Communication, Direction, Document
from app.services.document_parser import parse_pdf, parse_plain_text
from app.services.object_store import local_document_path

logger = get_logger(__name__)


def make_ingest_route_node(db: AsyncSession, application: Application):
    async def ingest_route(state: dict) -> dict:
        result = await db.execute(select(Document).where(Document.application_id == application.id))
        documents = result.scalars().all()

        parsed: dict[str, str] = {}
        for document in documents:
            if document.raw_text:
                parsed[str(document.id)] = document.raw_text
                continue

            try:
                payload = _read_local_payload(application.id, document.s3_path)
                if payload is None:
                    raise MalformedPDFError(
                        "Document is remotely hosted or its storage path is untrusted; fetch not supported in MVP."
                    )
                if document.file_type == "application/pdf":
                    text = parse_pdf(payload)
                elif document.file_type == "text/plain":
                    text = parse_plain_text(payload)
                else:
                    raise MalformedPDFError(f"Unsupported file_type '{document.file_type}'.")
            except (MalformedPDFError, TextDecodingError, OCRUnavailableError) as exc:
                logger.warning("ingest.parse_failed", extra={"document_id": str(document.id), "code": exc.code})
                db.add(
                    AuditLog(
                        application_id=application.id,
                        action="ingest.parse_failed",
                        llm_response=str(exc),
                        error_code=exc.code,
                    )
                )
                continue

            document.raw_text = text
            parsed[str(document.id)] = text

        comm_result = await db.execute(
            select(Communication)
            .where(
                Communication.application_id == application.id,
                Communication.direction == Direction.inbound,
                Communication.content.is_not(None),
            )
            .order_by(Communication.timestamp)
        )
        stated: dict[str, str] = {}
        for communication in comm_result.scalars().all():
            stated[f"chat:{communication.id}"] = communication.content or ""

        await db.commit()
        logger.info(
            "graph.ingest_route.done",
            extra={
                "application_id": str(application.id),
                "documents_parsed": len(parsed),
                "chat_messages": len(stated),
            },
        )
        return {"parsed_documents": parsed, "stated_messages": stated}

    return ingest_route


def _read_local_payload(application_id, s3_path: str | None) -> bytes | None:
    path = local_document_path(application_id, s3_path)
    if path is None:
        return None
    try:
        return path.read_bytes()
    except FileNotFoundError as exc:
        raise MalformedPDFError(f"Stored file missing on disk: {path}") from exc
