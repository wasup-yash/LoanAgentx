import shutil
from pathlib import Path

import fitz
import pytesseract
from PIL import Image
from pytesseract import TesseractError, TesseractNotFoundError

from app.core.config import get_settings
from app.core.exceptions import OCRUnavailableError
from app.core.logging import get_logger

logger = get_logger(__name__)

_KNOWN_TESSERACT_PATHS = (
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    "/usr/bin/tesseract",
    "/usr/local/bin/tesseract",
)


def _resolve_tesseract_binary() -> str:
    explicit = get_settings().tesseract_cmd
    if explicit:
        return explicit

    on_path = shutil.which("tesseract")
    if on_path:
        return on_path

    for candidate in _KNOWN_TESSERACT_PATHS:
        if Path(candidate).is_file():
            return candidate

    raise OCRUnavailableError(
        "Tesseract binary not found. Install tesseract-ocr or set TESSERACT_CMD."
    )


def ocr_available() -> bool:
    try:
        _resolve_tesseract_binary()
        return True
    except OCRUnavailableError:
        return False


def ocr_pdf(payload: bytes) -> str:
    binary = _resolve_tesseract_binary()
    pytesseract.pytesseract.tesseract_cmd = binary
    settings = get_settings()

    page_texts: list[str] = []
    try:
        with fitz.open(stream=payload, filetype="pdf") as document:
            for page in document:
                pixmap = page.get_pixmap(dpi=settings.ocr_dpi)
                image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
                page_texts.append(pytesseract.image_to_string(image, lang=settings.ocr_language))
    except TesseractNotFoundError as exc:
        raise OCRUnavailableError("Tesseract binary could not be executed.") from exc
    except TesseractError as exc:
        raise OCRUnavailableError(f"Tesseract failed while recognizing a page: {exc}") from exc

    text = "\n".join(page_texts).strip()
    logger.info("ocr.completed", extra={"pages": len(page_texts), "chars": len(text)})
    if not text.strip():
        raise OCRUnavailableError("OCR produced no text; the document may be blank or unreadable.")
    return text
