import hmac
import hashlib
from typing import Optional

from fastapi import Header, HTTPException, Request, status

from app.core.config import get_settings


async def verify_webhook_signature(
    request: Request,
    x_twilio_signature: Optional[str] = Header(default=None, alias="X-Twilio-Signature"),
    x_sendgrid_signature: Optional[str] = Header(default=None, alias="X-SendGrid-Signature"),
) -> None:
    """
    Verify webhook signature for Twilio or SendGrid.
    - Twilio: X-Twilio-Signature header with HMAC-SHA256 of URL + sorted params
    - SendGrid: X-SendGrid-Signature header with HMAC-SHA256 of payload + timestamp
    """
    settings = get_settings()

    if not settings.require_webhook_signature:
        return

    if not settings.webhook_signing_secret:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Webhook signature verification enabled but secret not configured.",
        )

    body = await request.body()

    # Try Twilio first
    if x_twilio_signature:
        _verify_twilio_signature(settings.webhook_signing_secret, str(request.url), body, x_twilio_signature)
        return

    # Try SendGrid
    if x_sendgrid_signature:
        x_timestamp = request.headers.get("X-Request-Timestamp")
        if not x_timestamp:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Missing X-Request-Timestamp header for SendGrid signature verification.",
            )
        _verify_sendgrid_signature(settings.webhook_signing_secret, body, x_timestamp, x_sendgrid_signature)
        return

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Missing required signature header (X-Twilio-Signature or X-SendGrid-Signature).",
    )


def _verify_twilio_signature(secret: str, url: str, body: bytes, signature: str) -> None:
    """Verify Twilio HMAC-SHA256 signature."""
    # Twilio sorts POST params and appends to URL
    # For JSON body, we treat the raw body as the "params" string
    message = url.encode() + body
    expected = hmac.new(secret.encode(), message, hashlib.sha256).digest()
    expected_b64 = expected.hex()
    if not hmac.compare_digest(expected_b64, signature):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Twilio signature.",
        )


def _verify_sendgrid_signature(secret: str, body: bytes, timestamp: str, signature: str) -> None:
    """Verify SendGrid HMAC-SHA256 signature."""
    # SendGrid: HMAC-SHA256(payload + timestamp)
    message = body + timestamp.encode()
    expected = hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid SendGrid signature.",
        )