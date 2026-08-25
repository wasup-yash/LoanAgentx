import json
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models import AuditLog, ExtractedFact

logger = get_logger(__name__)

_REQUIRED_KEYS = frozenset(("government_id", "monthly_income", "address"))
_NUMERIC_KEYS = frozenset(("monthly_income", "annual_income"))
_TOLERANCE_PCT = 0.05


def make_reconcile_node(db: AsyncSession, application):
    async def reconcile(state: dict) -> dict:
        result = await db.execute(
            select(ExtractedFact).where(ExtractedFact.application_id == application.id).order_by(ExtractedFact.key)
        )
        facts = result.scalars().all()

        stated = {}
        documented = {}
        for fact in facts:
            key = fact.key
            value = _as_number(fact.value) if key in _NUMERIC_KEYS else fact.value
            if fact.document_id is None:
                stated.setdefault(key, []).append(value)
            else:
                documented.setdefault(key, []).append((value, fact.document_id))

        anomalies = []
        for key in stated.keys() & documented.keys():
            for s_val in stated[key]:
                for d_val, d_id in documented[key]:
                    anomaly = _compare(key, s_val, d_val, d_id)
                    if anomaly:
                        anomalies.append(anomaly)

        for anomaly in anomalies:
            db.add(
                AuditLog(
                    application_id=application.id,
                    action="reconcile.anomaly",
                    llm_response=json.dumps(anomaly, default=str),
                )
            )

        await db.commit()

        logger.info(
            "graph.reconcile.done",
            extra={"application_id": str(application.id), "anomalies": len(anomalies)},
        )
        return {"anomalies": anomalies}

    return reconcile


def _as_number(value: object) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        s = value.strip().replace(",", "").lstrip("$")
        try:
            return float(s)
        except ValueError:
            pass
    return None


def _compare(key: str, stated_val: object, doc_val: object, doc_id: str) -> dict | None:
    s_num = _as_number(stated_val)
    d_num = _as_number(doc_val)

    if s_num is not None and d_num is not None:
        variance = abs(s_num - d_num) / max(abs(d_num), 1e-9)
        if variance > _TOLERANCE_PCT:
            return {
                "key": key,
                "stated_value": stated_val,
                "document_value": doc_val,
                "document_id": doc_id,
                "variance_pct": round(variance * 100, 2),
            }
    elif str(stated_val).strip().lower() != str(doc_val).strip().lower():
        return {
            "key": key,
            "stated_value": stated_val,
            "document_value": doc_val,
            "document_id": doc_id,
            "variance_pct": None,
        }
    return None