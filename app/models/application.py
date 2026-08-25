import uuid

from sqlalchemy import Enum, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, new_uuid
from app.models.enums import ApplicationStatus

_STATUS_ENUM = Enum(
    ApplicationStatus,
    name="application_status",
    values_callable=lambda enum_cls: [member.value for member in enum_cls],
)


class Application(Base, TimestampMixin):
    __tablename__ = "applications"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    external_borrower_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    status: Mapped[ApplicationStatus] = mapped_column(
        _STATUS_ENUM,
        default=ApplicationStatus.pending_docs,
        nullable=False,
        index=True,
    )

    communications = relationship(
        "Communication",
        back_populates="application",
        cascade="all, delete-orphan",
        order_by="Communication.timestamp",
    )
    documents = relationship(
        "Document",
        back_populates="application",
        cascade="all, delete-orphan",
    )
    facts = relationship(
        "ExtractedFact",
        back_populates="application",
        cascade="all, delete-orphan",
    )
    audit_logs = relationship(
        "AuditLog",
        back_populates="application",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("ix_applications_borrower_status_created", "external_borrower_id", "status", "created_at"),
    )
