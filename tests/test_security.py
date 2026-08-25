"""Tests for security features."""

import pytest
from fastapi.testclient import TestClient
import base64
import uuid


class TestSecurity:
    """Tests for security features."""

    def test_webhook_signature_verification_disabled_by_default(self, client: TestClient):
        """Test that signature verification is disabled by default."""
        response = client.post(
            "/webhooks/incoming-message",
            json={"user_id": "sec-test-1", "channel": "sms", "text": "Hello"},
            headers={"Idempotency-Key": "sig-test-1"},
        )
        # Should succeed without signature headers when verification disabled
        assert response.status_code == 202

    def test_rate_limiting_disabled_in_tests(self, client: TestClient):
        """Test that rate limiting is disabled in test environment."""
        # Make multiple requests rapidly - should not be rate limited
        for i in range(10):
            response = client.post(
                "/webhooks/incoming-message",
                json={"user_id": f"rate-test-{i}", "channel": "sms", "text": f"Message {i}"},
                headers={"Idempotency-Key": f"rate-{i}"},
            )
            # All should succeed (rate limiting disabled in tests)
            assert response.status_code == 202

    def test_secrets_not_logged(self, client: TestClient, caplog):
        """Test that secrets are redacted from logs."""
        import logging
        # This test would need more sophisticated log capture
        # For now, just verify the redaction module is importable
        from app.core.logging import _redact_secrets
        
        # Test API key redaction
        text = "api_key=sk-1234567890abcdefghijklmnopqrstuvwxyz"
        redacted = _redact_secrets(text)
        assert "sk-1234567890abcdefghijklmnopqrstuvwxyz" not in redacted
        assert "[REDACTED]" in redacted
        
        # Test Bearer token redaction
        text = "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
        redacted = _redact_secrets(text)
        assert "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9" not in redacted
        
        # Test database URL redaction
        text = "postgresql://user:secretpassword@localhost/db"
        redacted = _redact_secrets(text)
        assert "secretpassword" not in redacted

    def test_pii_redaction_in_audit_logs(self):
        """Test that PII is redacted in audit service."""
        from app.core.redaction import redact_pii
        
        text = "User SSN: 123-45-6789, Income: $5000/month, Account: 1234567890123456"
        result = redact_pii(text)
        assert "123-45-6789" not in result.text
        assert "SSN_REDACTED" in result.text
        assert "ACCOUNT_NUMBER_REDACTED" in result.text

    def test_missing_webhook_signature_header_when_required(self):
        """Test that missing signature is handled (feature flag off by default)."""
        # This is a placeholder - actual test would require REQUIRE_WEBHOOK_SIGNATURE=true
        pass