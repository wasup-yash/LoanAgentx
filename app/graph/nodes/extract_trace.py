import re
from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    LLMConfigurationError,
    LLMProviderError,
    LLMResponseParseError,
    LLMRateLimitError,
    LLMTimeoutError,
)
from app.core.logging import get_logger
from app.models import Application, ApplicationStatus, AuditLog, ExtractedFact
from app.services.llm_client import SourceText, extract_facts
from app.schemas.extraction import KNOWN_FACT_KEYS, RawExtractedEntity

logger = get_logger(__name__)


@dataclass(frozen=True)
class ValidatedEntity:
    entity: RawExtractedEntity
    document_id: str | None


def make_extract_trace_node(db: AsyncSession, application: Application):
    async def extract_trace(state: dict) -> dict:
        parsed = state.get("parsed_documents", {})
        stated = state.get("stated_messages", {})

        sources = [
            SourceText(label=doc_id, document_id=doc_id, text=text)
            for doc_id, text in parsed.items()
        ]
        sources.extend(
            SourceText(label=label, document_id=None, text=text)
            for label, text in stated.items()
        )

        if not sources:
            logger.info("graph.extract_trace.skip", extra={"application_id": str(application.id), "reason": "no_sources"})
            return {"extracted_fact_ids": [], "degraded": False}

        try:
            call = await extract_facts(sources)
        except (
            LLMRateLimitError,
            LLMTimeoutError,
            LLMResponseParseError,
            LLMProviderError,
            LLMConfigurationError,
        ) as exc:
            application.status = ApplicationStatus.manual_review
            await _record_degraded_audit(db, application, exc)
            await db.commit()
            logger.error(
                "graph.extract_trace.degraded",
                extra={"application_id": str(application.id), "code": exc.code, "detail": exc.detail},
            )
            return {"extracted_fact_ids": [], "degraded": True}

        validated = _validate_entities(call.result.facts, sources)
        persisted_facts = await _persist_facts(db, application, validated)

        await _record_extraction_audit(db, application, call)
        await db.commit()

        logger.info(
            "graph.extract_trace.done",
            extra={"application_id": str(application.id), "facts_persisted": len(persisted_facts)},
        )
        return {"extracted_fact_ids": [str(f.id) for f in persisted_facts], "degraded": False}

    return extract_trace


def _validate_entities(entities: list[RawExtractedEntity], sources: list[SourceText]) -> list[ValidatedEntity]:
    doc_texts = {s.document_id: s.text for s in sources if s.document_id}
    chat_texts = [s.text for s in sources if not s.document_id]

    out = []
    for entity in entities:
        if entity.key not in KNOWN_FACT_KEYS:
            continue

        if entity.document_id:
            text = doc_texts.get(entity.document_id)
            if not text or not entity.source_quote or not _contains(text, entity.source_quote):
                continue
            doc_id = entity.document_id
        else:
            if not entity.source_quote:
                continue
            if not any(_contains(t, entity.source_quote) for t in chat_texts):
                continue
            doc_id = None

        out.append(ValidatedEntity(entity=entity, document_id=doc_id))

    return out


def _contains(haystack: str, needle: str) -> bool:
    if not needle:
        return False
    norm_h = re.sub(r"[\s\W]+", " ", haystack.lower())
    norm_n = re.sub(r"[\s\W]+", " ", needle.lower())
    return norm_n in norm_h


async def _persist_facts(db: AsyncSession, application: Application, validated: list[ValidatedEntity]) -> list[ExtractedFact]:
    await db.execute(delete(ExtractedFact).where(ExtractedFact.application_id == application.id))
    await db.flush()

    facts = []
    for v in validated:
        fact = ExtractedFact(
            application_id=application.id,
            key=v.entity.key,
            value=_coerce_value(v.entity.key, v.entity.value),
            confidence_score=v.entity.confidence_score,
            document_id=v.document_id,
            source_snippet=v.entity.source_quote,
        )
        db.add(fact)
        facts.append(fact)
    await db.flush()
    return facts


def _coerce_value(key: str, value: object) -> object:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        s = value.strip().replace(",", "").lstrip("$")
        if key.endswith(("_income", "_amount", "_balance")):
            try:
                return float(s)
            except ValueError:
                pass
    return value


async def _record_degraded_audit(db: AsyncSession, application: Application, exc: Exception) -> None:
    from app.core.redaction import redact_pii

    raw = f"{exc.__class__.__name__}: {exc}"
    try:
        redacted = redact_pii(raw).text[:8000]
    except Exception:
        redacted = raw[:8000]
    db.add(
        AuditLog(
            application_id=application.id,
            action="extract.degraded",
            llm_response=redacted,
            error_code=getattr(exc, "code", "unknown"),
        )
    )


async def _record_extraction_audit(db: AsyncSession, application: Application, call) -> None:
    from app.core.redaction import redact_pii

    prompt_excerpt = call.prompt_excerpt[:8000] if call.prompt_excerpt else None
    response_excerpt = call.response_raw[:8000] if call.response_raw else None
    # Apply PII redaction before persisting (audit_service is not used in this node)
    try:
        if prompt_excerpt is not None:
            prompt_excerpt = redact_pii(prompt_excerpt).text
        if response_excerpt is not None:
            response_excerpt = redact_pii(response_excerpt).text
    except Exception:
        pass
    db.add(
        AuditLog(
            application_id=application.id,
            action="extract.trace",
            llm_prompt=prompt_excerpt,
            llm_response=response_excerpt,
            latency_ms=call.latency_ms,
            cost_usd=call.cost_usd,
            model_name=call.model_name,
        )
    )
