import uuid
from datetime import datetime

from pydantic import BaseModel, Field, model_validator

from app.models.enums import Channel


class AttachmentIn(BaseModel):
    file_type: str = Field(min_length=3, max_length=100, examples=["application/pdf"])
    filename: str | None = Field(default=None, max_length=255)
    content_base64: str | None = Field(default=None, max_length=40_000_000)
    url: str | None = Field(default=None, max_length=1024)

    @model_validator(mode="after")
    def _require_payload_or_url(self) -> "AttachmentIn":
        if self.content_base64 is None and self.url is None:
            raise ValueError("attachment requires either content_base64 or url")
        return self


class IncomingMessageWebhook(BaseModel):
    user_id: str = Field(min_length=1, max_length=255)
    channel: Channel
    text: str | None = Field(default=None, max_length=20_000)
    sent_at: datetime | None = None
    attachments: list[AttachmentIn] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def _require_content(self) -> "IncomingMessageWebhook":
        if self.text is None and not self.attachments:
            raise ValueError("message must include text or at least one attachment")
        return self


class WebhookAck(BaseModel):
    application_id: uuid.UUID
    application_status: str
    communication_id: uuid.UUID
    document_ids: list[uuid.UUID]
