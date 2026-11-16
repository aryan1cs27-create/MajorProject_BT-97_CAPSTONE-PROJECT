"""Local file storage for uploaded resumes.

Rules enforced here:

* the client-supplied filename is **never** used as a path — only as display
  metadata; stored names are random UUIDs plus a validated extension;
* the upload size limit is enforced while streaming, so an oversized body is
  rejected before it is written to disk;
* files live in ``settings.upload_dir`` (``backend/uploads/``), which is
  outside the frontend directory and is never served as a static route.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from pathlib import Path

from fastapi import UploadFile

from app.core.config import get_settings
from app.core.errors import APIError

logger = logging.getLogger("matchwise.storage")


@dataclass(frozen=True)
class StoredFile:
    stored_filename: str
    path: Path
    size: int


def upload_dir() -> Path:
    directory = get_settings().upload_dir
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def build_stored_filename(extension: str) -> str:
    """Random, collision-free, traversal-free filename."""
    ext = extension.lower().lstrip(".")
    if ext not in {"pdf", "docx"}:
        raise APIError(400, "unsupported_file_type", "Only PDF and DOCX files are accepted.")
    return f"{uuid.uuid4().hex}.{ext}"


def read_upload(upload: UploadFile) -> bytes:
    """Read the request body into memory while enforcing the size limit.

    Uploads are capped (default 5 MB), so buffering is bounded and lets us
    validate the real document format *before* anything touches the disk.
    """
    limit = get_settings().max_upload_bytes
    data = upload.file.read(limit + 1)
    if len(data) > limit:
        raise APIError(
            413,
            "file_too_large",
            f"File exceeds the {limit // (1024 * 1024)} MB upload limit.",
        )
    if not data:
        raise APIError(400, "empty_upload", "The uploaded file is empty.")
    return data


def save_bytes(data: bytes, stored_filename: str) -> StoredFile:
    """Write already-validated bytes to the uploads directory."""
    destination = upload_dir() / stored_filename
    try:
        destination.write_bytes(data)
    except OSError as exc:
        destination.unlink(missing_ok=True)
        logger.error("Could not store upload %s: %s", stored_filename, exc.__class__.__name__)
        raise APIError(500, "storage_error", "The uploaded file could not be stored.") from None
    return StoredFile(stored_filename=stored_filename, path=destination, size=len(data))


def delete_file(stored_filename: str | None) -> bool:
    """Remove a stored file. Returns True when a file was actually deleted.

    Never raises: a locked or already-missing file is logged and reported as
    not deleted, so a cleanup hiccup cannot mask the original API error.
    """
    if not stored_filename:
        return False
    path = upload_dir() / Path(stored_filename).name  # re-apply basename safety
    if not path.is_file():
        return False
    try:
        path.unlink()
    except OSError as exc:
        logger.error("Could not delete %s: %s", path.name, exc)
        return False
    logger.info("Deleted stored resume file %s", path.name)
    return True
