from typing import Union

from pydantic import BaseModel, Field

FACT_VALUE = Union[str, float, int, None]


class RawExtractedEntity(BaseModel):
    key: str = Field(min_length=1, max_length=100)
    value: FACT_VALUE = None
    confidence_score: float = Field(default=0.5, ge=0.0, le=1.0)
    source_quote: str | None = Field(default=None, max_length=2000)
    document_id: str | None = Field(default=None, max_length=64)


class ExtractionResult(BaseModel):
    facts: list[RawExtractedEntity] = Field(default_factory=list, max_length=50)


KNOWN_FACT_KEYS = frozenset(
    {
        "monthly_income",
        "annual_income",
        "employer_name",
        "address",
        "government_id",
        "employment_status",
    }
)

REQUIRED_FACT_KEYS = ("government_id", "monthly_income", "address")
