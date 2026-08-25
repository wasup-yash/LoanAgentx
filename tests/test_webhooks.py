"""Tests for webhook endpoint."""

import base64
import pytest
from fastapi.testclient import TestClient


class TestWebhookEndpoint:
    """Tests for the webhook ingestion endpoint."""

    def test_webhook_requires_idempotency_key(self, client: TestClient):
        """Test that webhook works without explicit idempotency key (fallback)."""
        response = client.post(
            "/webhooks/incoming-message",
            json={"user_id": "test-user", "channel": "sms", "text": "Hello"},
        )
        assert response.status_code == 202
        data = response.json()
        assert "application_id" in data
        assert "communication_id" in data

    def test_webhook_with_explicit_idempotency_key(self, client: TestClient):
        """Test webhook with explicit idempotency key."""
        response = client.post(
            "/webhooks/incoming-message",
            json={"user_id": "test-user-2", "channel": "email", "text": "Hello"},
            headers={"Idempotency-Key": "explicit-key-123"},
        )
        assert response.status_code == 202

    def test_idempotency_duplicate_returns_cached_response(self, client: TestClient):
        """Test that duplicate idempotency key returns cached response."""
        key = "duplicate-test-key"
        
        # First request
        resp1 = client.post(
            "/webhooks/incoming-message",
            json={"user_id": "test-user-3", "channel": "sms", "text": "First message"},
            headers={"Idempotency-Key": key},
        )
        assert resp1.status_code == 202
        data1 = resp1.json()
        
        # Second request with same key
        resp2 = client.post(
            "/webhooks/incoming-message",
            json={"user_id": "test-user-3", "channel": "sms", "text": "Different message"},
            headers={"Idempotency-Key": key},
        )
        assert resp2.status_code == 202
        data2 = resp2.json()
        
        # Should return identical cached response
        assert data2["application_id"] == data1["application_id"]
        assert data2["communication_id"] == data1["communication_id"]

    def test_webhook_with_attachment(self, client: TestClient):
        """Test webhook with base64-encoded PDF attachment."""
        # Create a minimal PDF
        pdf_content = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF"
        b64_content = base64.b64encode(pdf_content).decode()
        
        response = client.post(
            "/webhooks/incoming-message",
            json={
                "user_id": "test-user-4",
                "channel": "email",
                "text": "Here's my document",
                "attachments": [
                    {
                        "file_type": "application/pdf",
                        "filename": "test.pdf",
                        "content_base64": b64_content,
                    }
                ],
            },
            headers={"Idempotency-Key": "attach-test-key"},
        )
        assert response.status_code == 202
        data = response.json()
        assert len(data["document_ids"]) == 1

    def test_webhook_invalid_base64_rejected(self, client: TestClient):
        """Test that invalid base64 is rejected with 422."""
        response = client.post(
            "/webhooks/incoming-message",
            json={
                "user_id": "test-user-5",
                "channel": "sms",
                "attachments": [{"file_type": "application/pdf", "content_base64": "!!!not-base64!!!"}],
            },
            headers={"Idempotency-Key": "invalid-b64-key"},
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "invalid_attachment"

    def test_webhook_unsupported_media_type(self, client: TestClient):
        """Test that unsupported file types are rejected."""
        b64 = base64.b64encode(b"test").decode()
        response = client.post(
            "/webhooks/incoming-message",
            json={
                "user_id": "test-user-6",
                "channel": "email",
                "attachments": [{"file_type": "application/x-unknown", "content_base64": b64}],
            },
            headers={"Idempotency-Key": "bad-type-key"},
        )
        assert response.status_code == 415
        assert response.json()["error"]["code"] == "unsupported_media_type"

    def test_webhook_empty_payload_rejected(self, client: TestClient):
        """Test that empty payload (no text, no attachments) is rejected."""
        response = client.post(
            "/webhooks/incoming-message",
            json={"user_id": "test-user-7", "channel": "sms"},
            headers={"Idempotency-Key": "empty-key"},
        )
        assert response.status_code == 422

    def test_webhook_payload_too_large(self, client: TestClient):
        """Test that oversized attachments are rejected."""
        # Create a payload larger than MAX_ATTACHMENT_BYTES (10MB)
        large_content = b"x" * (11 * 1024 * 1024)
        b64 = base64.b64encode(large_content).decode()
        
        response = client.post(
            "/webhooks/incoming-message",
            json={
                "user_id": "test-user-8",
                "channel": "email",
                "attachments": [{"file_type": "application/pdf", "content_base64": b64}],
            },
            headers={"Idempotency-Key": "large-key"},
        )
        assert response.status_code == 413
        assert response.json()["error"]["code"] == "payload_too_large"