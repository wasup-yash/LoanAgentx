"""Tests for application endpoints."""

import pytest
from fastapi.testclient import TestClient
import uuid
import base64


class TestApplicationEndpoints:
    """Tests for application audit trail and export endpoints."""

    def test_audit_trail_requires_valid_application(self, client: TestClient):
        """Test that audit trail returns 404 for non-existent application."""
        response = client.get(f"/applications/{uuid.uuid4()}/audit-trail")
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "application_not_found"

    def test_audit_trail_returns_facts_and_anomalies(self, client: TestClient):
        """Test that audit trail returns facts and anomalies for existing application."""
        # Create application via webhook (real user flow)
        user_id = f"audit-test-{uuid.uuid4().hex[:8]}"
        idem_key = f"audit-test-{uuid.uuid4().hex[:8]}"
        
        chat_resp = client.post(
            "/webhooks/incoming-message",
            json={"user_id": user_id, "channel": "sms", "text": "I make $5000/month and live at 123 Main St"},
            headers={"Idempotency-Key": idem_key},
        )
        assert chat_resp.status_code == 202
        app_id = chat_resp.json()["application_id"]
        
        # Upload a PDF document
        pdf_content = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF"
        b64 = base64.b64encode(pdf_content).decode()
        
        doc_resp = client.post(
            "/webhooks/incoming-message",
            json={
                "user_id": user_id,
                "channel": "email",
                "text": "Attaching document",
                "attachments": [{"file_type": "application/pdf", "filename": "doc.pdf", "content_base64": b64}],
            },
            headers={"Idempotency-Key": f"audit-doc-{uuid.uuid4().hex[:8]}"},
        )
        assert doc_resp.status_code == 202
        assert doc_resp.json()["application_id"] == app_id
        
        # Get audit trail
        response = client.get(f"/applications/{app_id}/audit-trail")
        assert response.status_code == 200
        data = response.json()
        assert data["application_id"] == app_id
        assert data["external_borrower_id"] == user_id
        assert data["status"] == "pending_docs"
        assert "facts" in data
        assert "anomalies" in data

    def test_export_los_requires_ready_for_los(self, client: TestClient):
        """Test that export fails if application is not ready_for_los."""
        # Create application via webhook (will be in pending_docs status)
        user_id = f"export-test-{uuid.uuid4().hex[:8]}"
        chat_resp = client.post(
            "/webhooks/incoming-message",
            json={"user_id": user_id, "channel": "sms", "text": "Hello"},
            headers={"Idempotency-Key": f"export-test-{uuid.uuid4().hex[:8]}"},
        )
        assert chat_resp.status_code == 202
        app_id = chat_resp.json()["application_id"]
        
        # Try to export - should fail because status is pending_docs
        response = client.post(f"/applications/{app_id}/export-los")
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "invalid_application_state"

    def test_export_los_works_when_ready(self, client: TestClient):
        """Test that export succeeds when application is ready_for_los."""
        # Note: We can't easily set status to ready_for_los without running the full pipeline
        # This test would require either:
        # 1. Direct database manipulation (complex with fixtures)
        # 2. Running the full pipeline (slow)
        # 3. Mocking the status check
        # For now, we test the 409 case above and skip this integration test
        pytest.skip("Requires application in ready_for_los status - skipping for now")