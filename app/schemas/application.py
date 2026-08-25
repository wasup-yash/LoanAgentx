import uuid
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


class FactProvenance(BaseModel):
    key: str
    value: Any | None
    confidence_score: float | None
    source_snippet: str | None
    document_id: uuid.UUID | None
    document_s3_path: str | None
    document_file_type: str | None


class AnomalyItem(BaseModel):
    key: str
    stated_value: Any | None
    document_value: Any | None
    document_id: uuid.UUID | None
    variance_pct: float | None
    detected_at: datetime | None = None


class AuditTrailResponse(BaseModel):
    application_id: uuid.UUID
    external_borrower_id: str
    status: str
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    facts: list[FactProvenance]
    anomalies: list[AnomalyItem] = Field(default_factory=list)


class LOSExportResponse(BaseModel):
    application_id: uuid.UUID
    delivery_mode: str
    core_banking_reference: str
    xml: str
