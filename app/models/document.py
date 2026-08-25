import uuid

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, new_uuid


class Document(Base, TimestampMixin):
    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    application_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("applications.id", ondelete="CASCADE"), nullable=False, index=True
    )
    file_type: Mapped[str] = mapped_column(String(100), nullable=False)
    raw_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    s3_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    content_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    application = relationship("Application", back_populates="documents")
    facts = relationship(
        "ExtractedFact",
        back_populates="document",
        passive_deletes=True,
    )
