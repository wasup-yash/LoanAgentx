import fitz

from app.core.config import get_settings
from app.core.exceptions import MalformedPDFError, TextDecodingError


def parse_pdf(payload: bytes) -> str:
    # Cap worst-case decompression/memory: payload already limited to 10MB, but extracted text
    # can still blow up. We stream pages and cap total chars.
    MAX_CHARS = 500_000
    try:
        with fitz.open(stream=payload, filetype="pdf") as document:
            if document.is_encrypted:
                raise MalformedPDFError("PDF is password protected.")
            if document.page_count == 0:
                raise MalformedPDFError("PDF contains zero pages.")
            if document.page_count > 200:
                raise MalformedPDFError(f"PDF has {document.page_count} pages; exceeds limit of 200.")
            parts: list[str] = []
            total = 0
            for page in document:
                t = page.get_text("text") or ""
                total += len(t)
                if total > MAX_CHARS:
                    raise MalformedPDFError("PDF text extraction exceeds 500k char limit.")
                parts.append(t)
    except MalformedPDFError:
        raise
    except (fitz.FileDataError, RuntimeError, ValueError) as exc:
        raise MalformedPDFError(f"PDF structure is corrupt: {exc}") from exc

    text = "\n".join(parts)
    if not text.strip():
        return _ocr_fallback(payload)
    return text


def _ocr_fallback(payload: bytes) -> str:
    settings = get_settings()
    if not settings.ocr_enabled:
        raise MalformedPDFError("No extractable text layer and OCR is disabled.")
    from app.services.ocr import ocr_pdf

    return ocr_pdf(payload)


def parse_plain_text(payload: bytes) -> str:
    try:
        return payload.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise TextDecodingError(f"Attachment is not valid UTF-8: {exc}") from exc
