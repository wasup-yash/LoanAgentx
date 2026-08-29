import asyncio
import json
import re
import time
from dataclasses import dataclass, field

import litellm
from pydantic import ValidationError

from app.core.config import get_settings
from app.core.exceptions import (
    LLMConfigurationError,
    LLMProviderError,
    LLMRateLimitError,
    LLMResponseParseError,
    LLMTimeoutError,
)
from app.schemas.extraction import ExtractionResult, RawExtractedEntity

SYSTEM_PROMPT = """You are a meticulous financial fact extraction engine for a loan origination system.

STRICT RULES:
1. Extract ONLY facts that are explicitly stated in the provided sources. Never infer, calculate, or guess.
2. Every fact MUST include `source_quote`: the exact verbatim text (copied character-for-character) from which the value was derived.
3. Every fact derived from a document MUST include `document_id` matching that document's ID exactly as given. Facts from chat transcripts MUST have `document_id` = null.
4. If you cannot find an exact supporting quote, omit the fact entirely.
5. Use these canonical keys: monthly_income, annual_income, employer_name, address, government_id, employment_status.
6. Numeric values must be plain numbers without currency symbols or separators.
7. Confidence score: 0.9+ only for clean document statements; 0.5-0.75 for informal chat statements."""

_MOCK_FAIL_TRIGGERS = {
    "SIMULATE_LLM_TIMEOUT": LLMTimeoutError,
    "SIMULATE_RATE_LIMIT": LLMRateLimitError,
    "SIMULATE_PARSE_ERROR": LLMResponseParseError,
}

_MOCK_INCOME_PATTERNS = (
    re.compile(r"monthly[^\$\n]{0,40}?\$([\d,]+(?:\.\d{1,2})?)", re.IGNORECASE),
    re.compile(r"\$([\d,]+(?:\.\d{1,2})?)\s*(?:per month|a month|/month|monthly)", re.IGNORECASE),
)
_MOCK_ADDRESS_PATTERN = re.compile(
    r"(?:i\s+)?(?:live|reside)\s+at\s+(.+?)(?:\.|$)", re.IGNORECASE | re.DOTALL
)
_MOCK_ID_PATTERN = re.compile(r"\b(?:id|ID)(?:\s+(?:#|number|no\.?|is|:))?\s*[:#]?\s*([A-Za-z0-9-]{5,20})")


@dataclass(frozen=True)
class SourceText:
    label: str
    document_id: str | None
    text: str


@dataclass(frozen=True)
class ExtractionCall:
    result: ExtractionResult
    latency_ms: int
    cost_usd: float | None
    model_name: str
    prompt_excerpt: str
    response_raw: str


async def extract_facts(sources: list[SourceText]) -> ExtractionCall:
    settings = get_settings()

    if settings.llm_mock:
        return _mock_extract_facts(sources)

    user_prompt = _build_user_prompt(sources)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]

    started = time.perf_counter()
    response = None
    for attempt in range(settings.llm_max_retries + 1):
        try:
            response = await litellm.acompletion(
                model=settings.llm_model,
                messages=messages,
                temperature=0,
                timeout=settings.llm_timeout_seconds,
                num_retries=0,
                response_format=ExtractionResult,
            )
            break
        except litellm.RateLimitError as exc:
            if attempt >= settings.llm_max_retries:
                raise LLMRateLimitError(f"LLM rate limit persisted after {attempt + 1} attempts.") from exc
            await asyncio.sleep(min(2**attempt, 8))
        except litellm.Timeout as exc:
            raise LLMTimeoutError(f"LLM call exceeded {settings.llm_timeout_seconds}s.") from exc
        except litellm.AuthenticationError as exc:
            raise LLMConfigurationError("LLM provider rejected the configured credentials.") from exc
        except litellm.BadRequestError as exc:
            raise LLMConfigurationError(f"LLM rejected the request as malformed: {exc}") from exc
        except litellm.APIConnectionError as exc:
            raise LLMProviderError("Could not reach the LLM provider.") from exc
        except Exception as exc:  # Catch-all for litellm APIError / unexpected provider errors
            raise LLMProviderError(f"LLM provider error: {exc}") from exc

    assert response is not None
    latency_ms = int((time.perf_counter() - started) * 1000)

    raw_content = response.choices[0].message.content or ""
    hidden = getattr(response, "_hidden_params", None) or {}
    cost_usd = hidden.get("response_cost")

    try:
        result = ExtractionResult.model_validate_json(raw_content)
    except ValidationError as exc:
        raise LLMResponseParseError(f"LLM output failed schema validation: {exc.error_count()} error(s).") from exc

    return ExtractionCall(
        result=result,
        latency_ms=latency_ms,
        cost_usd=float(cost_usd) if cost_usd is not None else None,
        model_name=str(response.model),
        prompt_excerpt=user_prompt,
        response_raw=raw_content,
    )


def _build_user_prompt(sources: list[SourceText]) -> str:
    blocks = []
    for source in sources:
        origin = f"DOCUMENT_ID={source.document_id}" if source.document_id else "ORIGIN=chat_transcript"
        safe_text = source.text[:20_000] if len(source.text) > 20_000 else source.text
        # Escape CDATA terminator to prevent prompt-injection via source text
        safe_text = safe_text.replace("]]>", "]]]]><![CDATA[>")
        blocks.append(f'<SOURCE {origin} label="{source.label}">\n<![CDATA[\n{safe_text}\n]]>\n</SOURCE>')
    full = (
        "Extract all supported financial facts from the following sources.\n\n"
        + "\n\n".join(blocks)
        + "\n\nReturn JSON matching the ExtractionResult schema."
    )
    return full[:80_000]


def _mock_extract_facts(sources: list[SourceText]) -> ExtractionCall:
    joined = "\n".join(source.text for source in sources)
    for trigger, error_cls in _MOCK_FAIL_TRIGGERS.items():
        if trigger in joined:
            raise error_cls(f"Mock extractor triggered by marker '{trigger}'.")

    entities: list[RawExtractedEntity] = []
    for source in sources:
        confidence = 0.55 if source.document_id is None else 0.93

        for pattern in _MOCK_INCOME_PATTERNS:
            match = pattern.search(source.text)
            if match:
                entities.append(
                    RawExtractedEntity(
                        key="monthly_income",
                        value=float(match.group(1).replace(",", "")),
                        confidence_score=confidence,
                        source_quote=match.group(0).strip(),
                        document_id=source.document_id,
                    )
                )
                break

        address_match = _MOCK_ADDRESS_PATTERN.search(source.text)
        if address_match:
            entities.append(
                RawExtractedEntity(
                    key="address",
                    value=address_match.group(1).strip().rstrip("."),
                    confidence_score=confidence,
                    source_quote=address_match.group(0).strip(),
                    document_id=source.document_id,
                )
            )

        id_match = _MOCK_ID_PATTERN.search(source.text)
        if id_match:
            entities.append(
                RawExtractedEntity(
                    key="government_id",
                    value=id_match.group(1).strip(),
                    confidence_score=confidence,
                    source_quote=id_match.group(0).strip(),
                    document_id=source.document_id,
                )
            )

    raw_response = json.dumps({"facts": [e.model_dump() for e in entities]})
    return ExtractionCall(
        result=ExtractionResult(facts=entities),
        latency_ms=1,
        cost_usd=0.0,
        model_name="mock-extractor",
        prompt_excerpt=_build_user_prompt(sources),
        response_raw=raw_response,
    )
