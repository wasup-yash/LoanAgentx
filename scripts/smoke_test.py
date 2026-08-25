import base64
import json
import os
import uuid
from datetime import datetime, timezone

os.environ["LLM_MOCK"] = "true"

from fastapi.testclient import TestClient

from app.main import app


def _make_payslip_pdf() -> bytes:
    import fitz
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "INCOME STATEMENT")
    page.insert_text((72, 108), "Employer: Acme Corp")
    page.insert_text((72, 144), "Monthly gross income: $5200.00")
    page.insert_text((72, 180), "Employee: John Doe")
    return doc.tobytes()


def _make_scanned_payslip_pdf() -> bytes:
    import fitz
    source = fitz.open(stream=_make_payslip_pdf(), filetype="pdf")
    pixmap = source[0].get_pixmap(dpi=150)
    scanned = fitz.open()
    page = scanned.new_page(width=pixmap.width, height=pixmap.height)
    page.insert_image(page.rect, pixmap=pixmap)
    payload = scanned.tobytes()
    assert not scanned[0].get_text().strip(), "rasterized PDF unexpectedly has a text layer"
    return payload


def _assert_json(response, expected_status, msg=""):
    assert response.status_code == expected_status, f"{msg}: {response.status_code} {response.text}"
    return response.json()


def _post_webhook(client: TestClient, payload: dict, idem_key: str) -> tuple[int, dict]:
    response = client.post(
        "/webhooks/incoming-message",
        json=payload,
        headers={"Idempotency-Key": idem_key},
    )
    return response.status_code, response.json()


with TestClient(app) as client:
    health = client.get("/healthz")
    print("HEALTH:", health.status_code, health.json())
    assert health.status_code == 200

    # --- Happy path: chat + PDF payslip with income mismatch ---
    user_id = f"borrower-happy-{uuid.uuid4().hex[:8]}"
    idem_1 = f"idem-happy-{uuid.uuid4().hex[:8]}"
    status, body = _post_webhook(client, {
        "user_id": user_id,
        "channel": "sms",
        "text": "Hi, I make $6,000 a month and I live at 12 Oak Street, Austin TX 78701.",
    }, idem_1)
    print("CHAT:", status, body)
    assert status == 202
    app_id = body["application_id"]

    pdf_bytes = _make_payslip_pdf()
    idem_2 = f"idem-happy-doc-{uuid.uuid4().hex[:8]}"
    status, body = _post_webhook(client, {
        "user_id": user_id,
        "channel": "email",
        "text": "Attaching my payslip.",
        "attachments": [
            {
                "file_type": "application/pdf",
                "filename": "payslip.pdf",
                "content_base64": base64.b64encode(pdf_bytes).decode(),
            }
        ],
    }, idem_2)
    print("DOC_UPLOAD:", status, body)
    assert status == 202
    assert body["application_id"] == app_id

    # Audit trail after both messages
    audit = client.get(f"/applications/{app_id}/audit-trail")
    print("AUDIT:", audit.status_code)
    audit_data = audit.json()
    assert audit.status_code == 200
    print("AUDIT_FACTS:", json.dumps(audit_data["facts"], indent=2, default=str))
    print("AUDIT_ANOMALIES:", json.dumps(audit_data["anomalies"], indent=2, default=str))

    fact_keys = {f["key"] for f in audit_data["facts"]}
    assert "monthly_income" in fact_keys, "monthly_income fact missing"
    assert "address" in fact_keys, "address fact missing"
    assert "government_id" not in fact_keys, "government_id should be missing (not yet provided)"

    income_anomalies = [a for a in audit_data["anomalies"] if a["key"] == "monthly_income"]
    assert len(income_anomalies) == 1, f"expected 1 income anomaly, got {income_anomalies}"
    assert income_anomalies[0]["variance_pct"] is not None and income_anomalies[0]["variance_pct"] > 5.0

    assert audit_data["status"] == "pending_docs", f"expected pending_docs, got {audit_data['status']}"

    # --- Scanned (image-only) PDF goes through the OCR fallback ---
    scanned_user = f"borrower-scanned-{uuid.uuid4().hex[:8]}"
    idem_scan = f"idem-scan-{uuid.uuid4().hex[:8]}"
    status, body = _post_webhook(client, {
        "user_id": scanned_user,
        "channel": "email",
        "text": "Here is a scan of my payslip.",
        "attachments": [
            {
                "file_type": "application/pdf",
                "filename": "payslip-scan.pdf",
                "content_base64": base64.b64encode(_make_scanned_payslip_pdf()).decode(),
            }
        ],
    }, idem_scan)
    print("SCANNED_WEBHOOK:", status, body)
    assert status == 202
    scanned_app_id = body["application_id"]

    scanned_audit = client.get(f"/applications/{scanned_app_id}/audit-trail")
    scanned_data = scanned_audit.json()
    print("SCANNED_AUDIT:", json.dumps(scanned_data["facts"], indent=2, default=str))
    scanned_fact_keys = {f["key"] for f in scanned_data["facts"]}
    if "monthly_income" in scanned_fact_keys:
        ocr_fact = next(f for f in scanned_data["facts"] if f["key"] == "monthly_income")
        assert abs(float(ocr_fact["value"]) - 5200.0) < 1.0, f"OCR misread income: {ocr_fact['value']}"
        assert ocr_fact["source_snippet"], "OCR fact must still carry a source snippet"
        print("SCANNED_OCR: extracted via OCR OK")
    else:
        raise AssertionError(f"scanned PDF produced no monthly_income fact; facts={scanned_data['facts']}")

    # --- LLM timeout degradation test ---
    timeout_user = f"borrower-timeout-{uuid.uuid4().hex[:8]}"
    idem_timeout = f"idem-timeout-{uuid.uuid4().hex[:8]}"
    status, body = _post_webhook(client, {
        "user_id": timeout_user,
        "channel": "sms",
        "text": "SIMULATE_LLM_TIMEOUT please process my application.",
    }, idem_timeout)
    print("TIMEOUT_WEBHOOK:", status, body)
    assert status == 202
    timeout_app_id = body["application_id"]

    timeout_audit = client.get(f"/applications/{timeout_app_id}/audit-trail")
    print("TIMEOUT_AUDIT:", timeout_audit.status_code, timeout_audit.json())
    assert timeout_audit.status_code == 200
    assert timeout_audit.json()["status"] == "manual_review", f"expected manual_review, got {timeout_audit.json()['status']}"

    # --- Idempotency: duplicate key returns cached response ---
    status_dup, body_dup = _post_webhook(client, {
        "user_id": "some-other-borrower",
        "channel": "sms",
        "text": "This should be ignored due to duplicate idempotency key.",
    }, idem_1)  # re-use idem_1 from the first chat message
    print("IDEMP_DUP:", status_dup, body_dup)
    assert status_dup == 202
    # The response body should be identical to the first call's ack
    first_chat_ack = _post_webhook(client, {
        "user_id": user_id,
        "channel": "sms",
        "text": "Hi, I make $6,000 a month and I live at 12 Oak Street, Austin TX 78701.",
    }, idem_1)
    assert body_dup["application_id"] == first_chat_ack[1]["application_id"]
    assert body_dup["communication_id"] == first_chat_ack[1]["communication_id"]
    print("IDEMPOTENCY: duplicate key returned cached response OK")

    # --- Missing app 404 ---
    missing = client.get(f"/applications/{uuid.uuid4()}/audit-trail")
    print("MISSING:", missing.status_code)
    assert missing.status_code == 404

    # --- Early export 409 ---
    export = client.post(f"/applications/{app_id}/export-los")
    print("EXPORT_EARLY:", export.status_code)
    assert export.status_code == 409

    # --- Bad base64 422 ---
    status, _ = _post_webhook(client, {"user_id": f"bad-{uuid.uuid4().hex[:8]}", "channel": "sms", "attachments": [{"file_type": "application/pdf", "content_base64": "!!!"}]}, f"idem-bad-{uuid.uuid4().hex[:8]}")
    print("BAD_B64:", status)
    assert status == 422

    # --- Empty payload 422 ---
    status, _ = _post_webhook(client, {"user_id": f"empty-{uuid.uuid4().hex[:8]}", "channel": "sms"}, f"idem-empty-{uuid.uuid4().hex[:8]}")
    print("EMPTY:", status)
    assert status == 422

    # --- Idempotency-Key optional: fallback auto-key works ---
    no_idem_user = f"no-idem-{uuid.uuid4().hex[:8]}"
    resp = client.post("/webhooks/incoming-message", json={"user_id": no_idem_user, "channel": "sms", "text": "hello no header"})
    print("NO_IDEM_HEADER:", resp.status_code, resp.json())
    assert resp.status_code == 202, "should succeed with auto-generated idempotency key"
    # Duplicate with same payload should hit the auto-key cache
    resp2 = client.post("/webhooks/incoming-message", json={"user_id": no_idem_user, "channel": "sms", "text": "hello no header"})
    print("NO_IDEM_DUP:", resp2.status_code)
    assert resp2.status_code == 202
    assert resp2.json()["communication_id"] == resp.json()["communication_id"], "duplicate payload should return cached response"

print("SMOKE_OK")