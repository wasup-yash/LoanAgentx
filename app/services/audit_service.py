import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.redaction import redact_pii
from app.models import AuditLog


async def record_audit(
    db: AsyncSession,
    *,
    application_id: uuid.UUID,
    action: str,
    llm_prompt: str | None = None,
    llm_response: str | None = None,
    latency_ms: int | None = None,
    cost_usd: float | None = None,
    error_code: str | None = None,
    model_name: str | None = None,
) -> None:
    settings = get_settings()
    if settings.log_secrets_redaction:
        if llm_prompt is not None:
            llm_prompt = redact_pii(llm_prompt).text
        if llm_response is not None:
            llm_response = redact_pii(llm_response).text

    db.add(
        AuditLog(
            application_id=application_id,
            action=action,
            llm_prompt=llm_prompt,
            llm_response=llm_response,
            latency_ms=latency_ms,
            cost_usd=None if cost_usd is None else round(cost_usd, 7),
            error_code=error_code,
            model_name=model_name,
        )
    )
