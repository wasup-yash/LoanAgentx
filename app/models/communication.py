import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, new_uuid
from app.models.enums import Channel, Direction

_CHANNEL_ENUM = Enum(Channel, name="communication_channel", values_callable=lambda e: [m.value for m in e])
_DIRECTION_ENUM = Enum(Direction, name="communication_direction", values_callable=lambda e: [m.value for m in e])


class Communication(Base, TimestampMixin):
    __tablename__ = "communications"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    application_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("applications.id", ondelete="CASCADE"), nullable=False, index=True
    )
    channel: Mapped[Channel] = mapped_column(_CHANNEL_ENUM, nullable=False)
    direction: Mapped[Direction] = mapped_column(_DIRECTION_ENUM, nullable=False)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default="now()", nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True, unique=True, index=True)

    application = relationship("Application", back_populates="communications")

    __table_args__ = (Index("ix_communications_application_timestamp", "application_id", "timestamp"),)
