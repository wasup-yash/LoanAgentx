import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, Index, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, new_uuid


class ExtractedFact(Base, TimestampMixin):
    __tablename__ = "extracted_facts"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    application_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("applications.id", ondelete="CASCADE"), nullable=False, index=True
    )
    key: Mapped[str] = mapped_column(String(100), nullable=False)
    value: Mapped[Any] = mapped_column(JSONB, nullable=True)
    confidence_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 4), nullable=True)
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("documents.id", ondelete="SET NULL"), nullable=True, index=True
    )
    source_snippet: Mapped[str | None] = mapped_column(Text, nullable=True)

    application = relationship("Application", back_populates="facts")
    document = relationship("Document", back_populates="facts")

    __table_args__ = (
        CheckConstraint(
            "value IS NULL OR source_snippet IS NOT NULL",
            name="ck_extracted_facts_source_integrity",
        ),
        Index("ix_extracted_facts_application_key", "application_id", "key"),
    )
