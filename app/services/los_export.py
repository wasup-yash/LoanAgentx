import json
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any

import httpx

from app.core.config import get_settings
from app.core.exceptions import LOSExportError
from app.core.logging import get_logger

logger = get_logger(__name__)


def build_credit_memo_xml(application: Any, facts: list[Any]) -> str:
    # Cap facts to prevent XML bomb; 50 facts max aligns with ExtractionResult max_length
    capped_facts = facts[:50]
    root = ET.Element(
        "CreditMemo",
        {
            "applicationId": str(application.id),
            "status": application.status.value,
            "generatedAt": datetime.now(timezone.utc).isoformat(),
        },
    )
    ET.SubElement(root, "Borrower", {"externalId": application.external_borrower_id})

    facts_el = ET.SubElement(root, "ExtractedFacts")
    for fact in capped_facts:
        fact_el = ET.SubElement(facts_el, "Fact", {"key": fact.key})
        if fact.confidence_score is not None:
            fact_el.set("confidence", f"{fact.confidence_score:.4f}")

        value_el = ET.SubElement(fact_el, "Value")
        # Ensure value serialization is bounded; truncate overly long JSON
        raw_val = json.dumps(fact.value, ensure_ascii=False) if fact.value is not None else ""
        value_el.text = raw_val[:5000] if len(raw_val) > 5000 else raw_val

        source_el = ET.SubElement(fact_el, "Source")
        document_el = ET.SubElement(source_el, "Document")
        if fact.document_id is not None:
            document_el.set("id", str(fact.document_id))
        path_el = ET.SubElement(document_el, "S3Path")
        raw_path = fact.document.s3_path if fact.document is not None else ""
        path_el.text = raw_path[:1024] if raw_path and len(raw_path) > 1024 else raw_path
        snippet_el = ET.SubElement(source_el, "Snippet")
        # Truncate snippet to avoid huge XML (source_quote capped at 2000 in schema but still)
        raw_snip = fact.source_snippet or ""
        snippet_el.text = raw_snip[:2000] if len(raw_snip) > 2000 else raw_snip

    ET.indent(root, space="  ")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode")


async def deliver_to_core_banking(xml_body: str, application_id: Any) -> tuple[str, str]:
    settings = get_settings()

    if not settings.core_banking_url:
        reference = f"CORE-SIM-{uuid.uuid4().hex[:12].upper()}"
        logger.info(
            "los.delivery.simulated",
            extra={
                "application_id": str(application_id),
                "reference": reference,
                "bytes": len(xml_body),
            },
        )
        return reference, "simulated"

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                settings.core_banking_url,
                content=xml_body.encode("utf-8"),
                headers={"Content-Type": "application/xml"},
            )
            response.raise_for_status()
    except httpx.TimeoutException as exc:
        raise LOSExportError("Core banking API timed out.") from exc
    except httpx.HTTPStatusError as exc:
        raise LOSExportError(
            f"Core banking API rejected export with HTTP {exc.response.status_code}."
        ) from exc
    except httpx.TransportError as exc:
        raise LOSExportError("Core banking API is unreachable.") from exc

    reference = response.headers.get("X-Core-Reference") or f"CORE-{uuid.uuid4().hex[:12].upper()}"
    logger.info(
        "los.delivery.completed",
        extra={"application_id": str(application_id), "reference": reference},
    )
    return reference, "core_banking_api"
