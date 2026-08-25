# Traceable Omni-Channel Loan Agent

Agentic loan origination system: inbound SMS/email webhooks, LLM extraction of financial facts with strict source traceability (`source_quote` + `document_id`, null when unsourced), reconciliation of stated vs. documented values, automated borrower "chase" for missing documents, and legacy-XML export to a core banking LOS.

## Stack

Python 3.11+ / FastAPI / SQLAlchemy 2 (async) / PostgreSQL / LangGraph / litellm / PyMuPDF

## Quickstart

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
Copy-Item .env.example .env
docker compose up -d db
.\.venv\Scripts\alembic upgrade head
uvicorn app.main:app --reload
```

Schema is managed exclusively by Alembic (`create_all` is not used). After changing models:

```powershell
.\.venv\Scripts\alembic revision --autogenerate -m "describe change"
.\.venv\Scripts\alembic upgrade head
```

Health check: `GET http://localhost:8000/healthz` · OpenAPI docs at `/docs`

## Rate limiting

The webhook endpoint (`POST /webhooks/incoming-message`) is protected by a **per-IP fixed-window rate limiter** backed by Redis. Config: `RATE_LIMIT_ENABLED`, `RATE_LIMIT_REQUESTS` (default 100), `RATE_LIMIT_WINDOW_SECONDS` (default 60). Exceeding the quota returns `429 Too Many Requests` with a `Retry-After` header.

## Security & Secret Management

| Setting | Purpose |
|---------|---------|
| `OPENAI_API_KEY` | Required in non-dev; LLM calls fail without it |
| `CORE_BANKING_URL` | Required in non-dev; LOS export fails without it |
| `WEBHOOK_SIGNING_SECRET` | HMAC key for Twilio/SendGrid signature verification |
| `REQUIRE_WEBHOOK_SIGNATURE` | When `true`, rejects requests without valid `X-Twilio-Signature` or `X-SendGrid-Signature` |
| `LOG_SECRETS_REDACTION` | When `true` (default), strips API keys, tokens, passwords from logs |
| `WEBHOOK_SIGNING_SECRET` | Must be set if `REQUIRE_WEBHOOK_SIGNATURE=true` |

**Startup validation** — app fails fast in non-dev if required secrets are missing.

**Audit log PII redaction** — `llm_prompt` and `llm_response` are passed through the redaction pipeline before storage.

**Webhook signature verification** — optional HMAC-SHA256 verification for Twilio (`X-Twilio-Signature`) and SendGrid (`X-SendGrid-Signature` + `X-Request-Timestamp`). Enabled via `REQUIRE_WEBHOOK_SIGNATURE=true` + `WEBHOOK_SIGNING_SECRET`.

### TLS Enforcement (Infrastructure)

Terminate TLS at the reverse proxy; app runs plain HTTP internally.

**Nginx**
```nginx
server {
    listen 443 ssl http2;
    server_name loan-agent.example.com;

    ssl_certificate /etc/letsencrypt/live/loan-agent.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/loan-agent.example.com/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;

    # Security headers
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
    add_header X-Content-Type-Options nosniff;
    add_header X-Frame-Options DENY;

    location / {
        proxy_pass http://app:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

**Traefik (Docker labels)**
```yaml
services:
  app:
    image: loan-agent
    labels:
      - "traefik.enable=true"
      - "traefik.http.routers.app.rule=Host(`loan-agent.example.com`)"
      - "traefik.http.routers.app.tls=true"
      - "traefik.http.routers.app.tls.certresolver=letsencrypt"
      - "traefik.http.middlewares.app-secure.headers.stsseconds=31536000"
      - "traefik.http.middlewares.app-secure.headers.stsinclude=true"
      - "traefik.http.routers.app.middlewares=app-secure"
```

## OCR fallback for scanned PDFs

PDFs without a text layer are rasterized with PyMuPDF and recognized by **Tesseract** (`pytesseract`). Config: `OCR_ENABLED`, `OCR_DPI`, `OCR_LANGUAGE`, `TESSERACT_CMD` (optional explicit binary path; otherwise resolved via PATH or known install locations). If OCR is unavailable or fails, the failure is audit-logged per document (`ingest.parse_failed` / `ocr_unavailable`) and the pipeline continues — one bad attachment never blocks an application.

System dependency:

```dockerfile
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr && rm -rf /var/lib/apt/lists/*
```

## API surface

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/webhooks/incoming-message` | Ingest Twilio/SendGrid-style message + attachments; persists Communication/Document rows and queues the agent pipeline |
| GET | `/applications/{id}/audit-trail` | Credit memo where every fact maps to its `ExtractedFact`, confidence, source snippet and originating document |
| POST | `/applications/{id}/export-los` | Builds legacy XML credit memo, archives it, delivers to core banking API (mocked) |

### Webhook example

```bash
curl -X POST http://localhost:8000/webhooks/incoming-message \
  -H "Content-Type: application/json" \
  -d '{"user_id":"borrower-001","channel":"sms","text":"I make $5,200 a month"}'
```

## Design conventions

- `ExtractedFact.document_id IS NULL` → fact was stated in chat (SMS/email body); non-null → derived from an official document. DB check constraint `ck_extracted_facts_source_integrity` forbids a value without a `source_snippet`.
- Every LLM call must be written to `AuditLog` (prompt, raw response, latency, cost, model). Failures log a specific `error_code` and flip the application to `manual_review`.
- Exceptions are typed per failure mode (rate limit, timeout, unparseable output, malformed PDF, oversized attachment...); no generic catch-alls.
- Local filesystem under `DATA_DIR` mocks S3 (`data/s3/<application_id>/`) and the LOS outbox (`data/outbox/`).