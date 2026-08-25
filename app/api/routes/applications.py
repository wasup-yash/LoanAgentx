import json
import uuid

from fastapi import APIRouter, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps.common import DbSession
from app.core.exceptions import ApplicationNotFoundError, InvalidApplicationStateError
from app.core.logging import get_logger
from app.models import Application, ApplicationStatus, AuditLog, ExtractedFact
from app.schemas.application import AnomalyItem, AuditTrailResponse, FactProvenance, LOSExportResponse
from app.services.audit_service import record_audit
from app.services.los_export import build_credit_memo_xml, deliver_to_core_banking
from app.services.object_store import persist_export

router = APIRouter(tags=["applications"])
logger = get_logger(__name__)


@router.get(
    "/applications/{application_id}/audit-trail",
    response_model=AuditTrailResponse,
    summary="Credit memo where every fact maps to its ExtractedFact and source snippet",
)
async def get_audit_trail(application_id: uuid.UUID, db: DbSession) -> AuditTrailResponse:
    application = await _get_application_or_raise(db, application_id)

    result = await db.execute(
        select(ExtractedFact)
        .where(ExtractedFact.application_id == application.id)
        .options(selectinload(ExtractedFact.document))
        .order_by(ExtractedFact.key, ExtractedFact.created_at)
    )
    facts = result.scalars().all()

    anomaly_result = await db.execute(
        select(AuditLog)
        .where(AuditLog.application_id == application.id, AuditLog.action == "reconcile.anomaly")
        .order_by(AuditLog.created_at)
    )
    anomalies = []
    for row in anomaly_result.scalars().all():
        try:
            payload = json.loads(row.llm_response) if row.llm_response else {}
        except json.JSONDecodeError:
            payload = {"raw": row.llm_response}
        anomalies.append(
            AnomalyItem(
                key=payload.get("key", ""),
                stated_value=payload.get("stated_value"),
                document_value=payload.get("document_value"),
                document_id=payload.get("document_id"),
                variance_pct=payload.get("variance_pct"),
                detected_at=row.created_at,
            )
        )

    return AuditTrailResponse(
        application_id=application.id,
        external_borrower_id=application.external_borrower_id,
        status=application.status.value,
        facts=[
            FactProvenance(
                key=fact.key,
                value=fact.value,
                confidence_score=float(fact.confidence_score) if fact.confidence_score is not None else None,
                source_snippet=fact.source_snippet,
                document_id=fact.document_id,
                document_s3_path=fact.document.s3_path if fact.document is not None else None,
                document_file_type=fact.document.file_type if fact.document is not None else None,
            )
            for fact in facts
        ],
        anomalies=anomalies,
    )


@router.post(
    "/applications/{application_id}/export-los",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=LOSExportResponse,
    summary="Convert the final assessment to legacy XML and deliver it to the core banking API",
)
async def export_to_los(application_id: uuid.UUID, db: DbSession) -> LOSExportResponse:
    application = await _get_application_or_raise(db, application_id)

    if application.status is not ApplicationStatus.ready_for_los:
        raise InvalidApplicationStateError(
            f"Application status is '{application.status.value}'; only 'ready_for_los' applications can be exported."
        )

    result = await db.execute(
        select(ExtractedFact)
        .where(ExtractedFact.application_id == application.id)
        .order_by(ExtractedFact.key, ExtractedFact.created_at)
    )
    facts = result.scalars().all()

    xml_body = build_credit_memo_xml(application, list(facts))
    archive_path = persist_export(application.id, xml_body)
    reference, delivery_mode = await deliver_to_core_banking(xml_body, application.id)

    await record_audit(
        db,
        application_id=application.id,
        action="los.export",
        llm_response=xml_body[:4000],
        error_code=None,
    )
    await db.commit()

    logger.info(
        "los.export.completed",
        extra={
            "application_id": str(application.id),
            "delivery_mode": delivery_mode,
            "reference": reference,
            "archive_path": archive_path,
        },
    )
    return LOSExportResponse(
        application_id=application.id,
        delivery_mode=delivery_mode,
        core_banking_reference=reference,
        xml=xml_body,
    )


async def _get_application_or_raise(db: AsyncSession, application_id: uuid.UUID) -> Application:
    result = await db.execute(select(Application).where(Application.id == application_id))
    application = result.scalar_one_or_none()
    if application is None:
        raise ApplicationNotFoundError(str(application_id))
    return application
