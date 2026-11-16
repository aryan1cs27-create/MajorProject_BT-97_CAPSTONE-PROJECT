"""Resume document parsing: format validation and text extraction.

* PDF  → PyMuPDF (``fitz``)
* DOCX → python-docx

The real document format is verified from the file content (not just the
extension) so a renamed `.exe` or a corrupted document is rejected before any
database record is created.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

from app.core.errors import APIError

PDF_MAGIC = b"%PDF"
DOCX_REQUIRED_ENTRIES = ("[Content_Types].xml", "word/document.xml")

MAX_PREVIEW_CHARS = 240


def detect_file_type(data: bytes) -> str | None:
    """Return "pdf" | "docx" | None based on the actual content."""
    if data.startswith(PDF_MAGIC):
        return "pdf"
    # DOCX is an OPC/ZIP container with a Word main document part.
    if data[:2] == b"PK":
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                names = set(archive.namelist())
                if all(entry in names for entry in DOCX_REQUIRED_ENTRIES):
                    return "docx"
        except zipfile.BadZipFile:
            return None
    return None


def validate_extension(filename: str) -> str:
    """Check the client-supplied extension; returns it normalised (with dot)."""
    extension = Path(filename or "").suffix.lower()
    if extension not in {".pdf", ".docx"}:
        raise APIError(
            415,
            "unsupported_file_type",
            "Unsupported file type. Please upload a PDF or DOCX document.",
        )
    return extension


def validate_content(data: bytes, extension: str) -> str:
    """Confirm the bytes really match the declared format. Returns pdf|docx."""
    detected = detect_file_type(data)
    if detected is None:
        raise APIError(
            415,
            "invalid_document",
            "The file content is not a readable PDF or DOCX document. "
            "It may be corrupted or mislabelled.",
        )
    expected = extension.lstrip(".")
    if detected != expected:
        raise APIError(
            415,
            "format_mismatch",
            f"File extension says '{expected}' but the content looks like '{detected}'.",
        )
    return detected


def extract_text(path: Path, file_type: str) -> str:
    """Extract plain text from a validated document.

    The file is read into memory first: libraries that memory-map the path
    (PyMuPDF on Windows, in particular) would otherwise keep the file locked,
    which blocks the cleanup of a corrupted upload.

    Raises APIError(415) when the document cannot be read (corrupted
    internals); returns an empty string for documents with no extractable
    text, which the caller records as ``no_text``.
    """
    try:
        content = path.read_bytes()
    except OSError:
        raise APIError(415, "unreadable_document", "The uploaded file could not be read.") from None

    if file_type == "pdf":
        return _extract_pdf(content)
    if file_type == "docx":
        return _extract_docx(content)
    raise APIError(415, "unsupported_file_type", "Unsupported file type.")


def _extract_pdf(content: bytes) -> str:
    try:
        import pymupdf  # PyMuPDF (import name; `fitz` is the legacy alias)
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise RuntimeError("PyMuPDF is not installed (pip install PyMuPDF)") from exc

    try:
        with pymupdf.open(stream=content, filetype="pdf") as document:
            if document.needs_pass:
                raise APIError(
                    422,
                    "password_protected",
                    "This PDF is password protected and cannot be read.",
                )
            return "\n".join(page.get_text("text") for page in document).strip()
    except APIError:
        raise
    except Exception:  # pymupdf raises several unrelated exception types
        raise APIError(
            415,
            "unreadable_document",
            "The PDF could not be read. The file may be corrupted.",
        ) from None


def _extract_docx(content: bytes) -> str:
    try:
        import docx  # python-docx
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise RuntimeError("python-docx is not installed (pip install python-docx)") from exc

    try:
        document = docx.Document(io.BytesIO(content))
    except Exception:
        raise APIError(
            415,
            "unreadable_document",
            "The DOCX file could not be read. The file may be corrupted.",
        ) from None

    paragraphs = [paragraph.text.strip() for paragraph in document.paragraphs if paragraph.text.strip()]
    # Tables are common in CVs — include their cell text as well.
    for table in document.tables:
        for row in table.rows:
            cells = [cell.text.strip() for cell in row.cells if cell.text.strip()]
            if cells:
                paragraphs.append(" | ".join(cells))
    return "\n".join(paragraphs).strip()


def make_preview(text: str, limit: int = MAX_PREVIEW_CHARS) -> str:
    """Short, single-line preview used in list/upload responses."""
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1].rstrip() + "…"
