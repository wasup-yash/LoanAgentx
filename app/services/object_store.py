import re
import uuid
from pathlib import Path

from app.core.config import get_settings

_SAFE_SUFFIX = re.compile(r"^\.(?:pdf|txt|jpg|jpeg|png|bin)$", re.IGNORECASE)


def _atomic_write_bytes(target: Path, payload: bytes) -> None:
    """Atomic write via temp file + rename to avoid partial files on crash."""
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_bytes(payload)
    tmp.replace(target)


def _atomic_write_text(target: Path, content: str) -> None:
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(target)


def persist_document(application_id: uuid.UUID, document_id: uuid.UUID, filename: str, payload: bytes) -> str:
    root = Path(get_settings().data_dir) / "s3" / str(application_id)
    root.mkdir(parents=True, exist_ok=True)

    suffix = Path(filename).suffix
    if not _SAFE_SUFFIX.fullmatch(suffix):
        suffix = ".bin"

    target = root / f"{document_id}{suffix}"
    _atomic_write_bytes(target, payload)
    return f"s3://loan-agent-documents/{application_id}/{target.name}"


def persist_export(application_id: uuid.UUID, xml_body: str) -> str:
    root = Path(get_settings().data_dir) / "outbox"
    root.mkdir(parents=True, exist_ok=True)

    target = root / f"{application_id}.xml"
    _atomic_write_text(target, xml_body)
    return str(target)


_S3_URI_PREFIX = "s3://loan-agent-documents/"


def local_document_path(application_id: uuid.UUID, s3_path: str | None) -> Path | None:
    if not s3_path or not s3_path.startswith(_S3_URI_PREFIX):
        return None
    relative = s3_path.removeprefix(_S3_URI_PREFIX)
    if not relative.startswith(f"{application_id}/"):
        return None

    root = (Path(get_settings().data_dir) / "s3").resolve()
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate
