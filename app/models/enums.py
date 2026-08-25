import enum


class ApplicationStatus(str, enum.Enum):
    pending_docs = "pending_docs"
    processing = "processing"
    manual_review = "manual_review"
    ready_for_los = "ready_for_los"


class Channel(str, enum.Enum):
    sms = "sms"
    email = "email"


class Direction(str, enum.Enum):
    inbound = "inbound"
    outbound = "outbound"
