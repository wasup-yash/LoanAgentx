from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models import Application, ApplicationStatus, AuditLog, Communication, Direction, ExtractedFact
from app.schemas.extraction import REQUIRED_FACT_KEYS

logger = get_logger(__name__)

_MISSING_TEMPLATES = {
    "government_id": "a government-issued photo ID (passport or driver's license)",
    "monthly_income": "proof of income (recent payslip, bank statement, or tax return)",
    "address": "proof of address (utility bill, lease agreement, or bank statement dated within 90 days)",
}

_CHASE_PREFIX = {
    "sms": "Hi! We still need: ",
    "email": "Hello,\n\nThank you for starting your application. We're missing the following documents:\n",
}


def make_chase_node(db: AsyncSession, application: Application):
    async def chase(state: dict) -> dict:
        result = await db.execute(
            select(ExtractedFact).where(ExtractedFact.application_id == application.id)
        )
        facts = result.scalars().all()

        present = {f.key for f in facts if f.value is not None}
        missing = [k for k in REQUIRED_FACT_KEYS if k not in present]

        channel = await _pick_channel(db, application)
        content = _build_chase_content(missing, channel)

        already_chased = await _chase_already_sent(db, application.id, content)
        if already_chased:
            new_status = ApplicationStatus.ready_for_los if not missing else ApplicationStatus.pending_docs
            application.status = new_status
            await db.commit()
            logger.info(
                "graph.chase.skipped_duplicate",
                extra={"application_id": str(application.id), "missing": missing, "new_status": new_status.value},
            )
            return {"missing_requirements": missing, "outcome": "ready_for_los" if not missing else "chasing"}

        db.add(
            Communication(
                application_id=application.id,
                channel=channel,
                direction=Direction.outbound,
                content=content,
            )
        )

        new_status = ApplicationStatus.ready_for_los if not missing else ApplicationStatus.pending_docs
        application.status = new_status

        db.add(
            AuditLog(
                application_id=application.id,
                action="chase.sent",
                llm_response=content,
            )
        )

        await db.commit()

        logger.info(
            "graph.chase.done",
            extra={
                "application_id": str(application.id),
                "missing": missing,
                "new_status": new_status.value,
                "channel": channel.value,
            },
        )
        return {
            "missing_requirements": missing,
            "outcome": "ready_for_los" if not missing else "chasing",
        }

    return chase


async def _pick_channel(db: AsyncSession, application: Application) -> Direction:
    result = await db.execute(
        select(Communication.channel)
        .where(
            Communication.application_id == application.id,
            Communication.direction == Direction.inbound,
        )
        .order_by(Communication.timestamp.desc())
        .limit(1)
    )
    channel = result.scalar_one_or_none()
    return channel if channel else Direction.sms


async def _chase_already_sent(db: AsyncSession, application_id, content: str) -> bool:
    result = await db.execute(
        select(Communication.id)
        .where(
            Communication.application_id == application_id,
            Communication.direction == Direction.outbound,
            Communication.content == content,
        )
        .limit(1)
    )
    return result.scalar_one_or_none() is not None


def _build_chase_content(missing: list[str], channel: str) -> str:
    if not missing:
        return "All required documents have been received. Your application is ready for review."

    items = ", ".join(_MISSING_TEMPLATES.get(k, k) for k in missing)
    if channel == "sms":
        return f"{_CHASE_PREFIX['sms']}{items}. Please reply with the requested information."
    else:
        lines = "\n".join(f"• {_MISSING_TEMPLATES.get(k, k)}" for k in missing)
        return f"{_CHASE_PREFIX['email']}{lines}\n\nPlease reply with the requested documents at your earliest convenience."