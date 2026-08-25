import fitz

from app.core.config import get_settings
from app.core.exceptions import MalformedPDFError, TextDecodingError


def parse_pdf(payload: bytes) -> str:
    try:
        with fitz.open(stream=payload, filetype="pdf") as document:
            if document.is_encrypted:
                raise MalformedPDFError("PDF is password protected.")
            if document.page_count == 0:
                raise MalformedPDFError("PDF contains zero pages.")
            pages = [page.get_text("text") for page in document]
    except (fitz.FileDataError, RuntimeError, ValueError) as exc:
        raise MalformedPDFError(f"PDF structure is corrupt: {exc}") from exc

    text = "\n".join(pages)
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
