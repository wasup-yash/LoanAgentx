import re
from dataclasses import dataclass

from app.core.exceptions import PIIRedactionError

_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[SSN_REDACTED]"),
    (re.compile(r"\b\d{3}\s\d{2}\s\d{4}\b"), "[SSN_REDACTED]"),
    (re.compile(r"\b(?:\d[ -]?){13,19}\b"), "[ACCOUNT_NUMBER_REDACTED]"),
    (re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b"), "[IBAN_REDACTED]"),
)


@dataclass(frozen=True)
class RedactionResult:
    text: str
    redactions: int


def redact_pii(text: str) -> RedactionResult:
    if not isinstance(text, str):
        raise PIIRedactionError("redact_pii expects a string payload.")
    total = 0
    result = text
    for pattern, replacement in _PATTERNS:
        result, count = pattern.subn(replacement, result)
        total += count
    return RedactionResult(text=result, redactions=total)
