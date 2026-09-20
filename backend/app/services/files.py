"""Server-side upload handling: chunked reads, size caps and content sniffing.

Filenames and client-declared MIME types are never trusted; the kind of a file is
decided from its bytes.
"""
from __future__ import annotations

import io
import re
import uuid
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass

import pymupdf as fitz  # PyMuPDF
from fastapi import UploadFile

from app.core.errors import PayloadTooLarge, Unprocessable, UnsupportedMedia
from app.providers.storage import StorageError, StorageProvider

CHUNK = 1024 * 256

MIME = {
    "pdf": "application/pdf",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "png": "image/png",
    "jpeg": "image/jpeg",
    "text": "text/plain",
}
EXTENSION_FOR = {"pdf": "pdf", "pptx": "pptx", "docx": "docx", "png": "png", "jpeg": "jpg", "text": "txt"}
ALLOWED_EXTENSIONS = {
    "pdf": {"pdf"},
    "pptx": {"pptx"},
    "docx": {"docx"},
    "png": {"png"},
    "jpeg": {"jpg", "jpeg"},
    "text": {"txt", "md", "markdown", "text", "csv"},
}
LEGACY_OFFICE = {".ppt": "Legacy .ppt files are not supported; save the deck as .pptx and upload again.",
                 ".doc": "Legacy .doc files are not supported; save the document as .docx and upload again."}


@dataclass
class StoredUpload:
    storage_key: str
    kind: str
    mime_type: str
    size_bytes: int
    sha256: str
    original_filename: str
    page_count: int | None = None


def safe_display_name(filename: str | None) -> str:
    """A sanitized name for display only; never used to build a storage path."""
    name = (filename or "upload").replace("\\", "/").rsplit("/", 1)[-1]
    name = re.sub(r"[^\w.\- ()\[\]]+", "_", name).strip(" .") or "upload"
    return name[:200]


def sniff_kind(head: bytes, full_reader=None) -> str | None:
    if head.startswith(b"%PDF-"):
        return "pdf"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if head.startswith(b"PK\x03\x04") and full_reader is not None:
        try:
            with zipfile.ZipFile(full_reader()) as z:
                names = z.namelist()
                if "[Content_Types].xml" in names:
                    if any(n.startswith("ppt/") for n in names):
                        return "pptx"
                    if any(n.startswith("word/") for n in names):
                        return "docx"
        except zipfile.BadZipFile:
            return None
        return None
    # plain text: must decode as UTF-8 and contain no NUL bytes
    if b"\x00" not in head:
        try:
            head.decode("utf-8")
            return "text"
        except UnicodeDecodeError:
            try:
                head[:-3].decode("utf-8")
                return "text"
            except UnicodeDecodeError:
                return None
    return None


def _chunks(upload: UploadFile) -> Iterator[bytes]:
    while chunk := upload.file.read(CHUNK):
        yield chunk


def store_upload(
    upload: UploadFile,
    storage: StorageProvider,
    *,
    prefix: str,
    allowed_kinds: set[str],
    max_bytes: int,
) -> StoredUpload:
    """Validate and persist an upload. Raises AppError subclasses with user-actionable messages."""
    original = safe_display_name(upload.filename)
    lower = original.lower()
    for ext, msg in LEGACY_OFFICE.items():
        if lower.endswith(ext):
            raise UnsupportedMedia(msg, code="legacy_office_format")

    key_stem = f"{prefix}/{uuid.uuid4().hex}"
    tmp_key = f"{key_stem}"  # extension is added once the kind is known
    try:
        size, sha = storage.save_stream(tmp_key, _chunks(upload), max_bytes=max_bytes)
    except StorageError as e:
        raise PayloadTooLarge(
            f"The file is larger than the {max_bytes // (1024 * 1024)} MB limit. Split or compress it and try again.",
            code="file_too_large",
        ) from e
    if size == 0:
        storage.delete(tmp_key)
        raise Unprocessable("The uploaded file is empty.", code="empty_file")

    try:
        head = storage.read_bytes(tmp_key)[:8192] if size > 0 else b""
        kind = sniff_kind(head, full_reader=lambda: io.BytesIO(storage.read_bytes(tmp_key)))
        if kind is None:
            raise UnsupportedMedia(
                "Unrecognized or unsupported file type. Supported: " + ", ".join(sorted(allowed_kinds)) + ".",
                code="unsupported_file_type",
            )
        if kind not in allowed_kinds:
            raise UnsupportedMedia(
                f"A {kind.upper()} file is not accepted here. Supported: {', '.join(sorted(allowed_kinds))}.",
                code="unsupported_file_type",
            )
        ext = lower.rsplit(".", 1)[-1] if "." in lower else ""
        if ext and ext not in ALLOWED_EXTENSIONS[kind]:
            raise UnsupportedMedia(
                f"The file extension .{ext} does not match its content ({kind.upper()}). Rename or re-export the file.",
                code="extension_mismatch",
            )
        page_count = None
        if kind == "pdf":
            page_count = validate_pdf(storage.read_bytes(tmp_key))
        final_key = f"{key_stem}.{EXTENSION_FOR[kind]}"
        storage.save_bytes(final_key, storage.read_bytes(tmp_key))
        storage.delete(tmp_key)
    except BaseException:
        storage.delete(tmp_key)
        raise
    return StoredUpload(final_key, kind, MIME[kind], size, sha, original, page_count)


def validate_pdf(data: bytes, *, max_pages: int | None = None) -> int:
    """Open the PDF to prove it is parseable; returns the page count."""
    try:
        doc = fitz.open(stream=data, filetype="pdf")
    except Exception as e:  # PyMuPDF raises several distinct types for damaged files
        raise Unprocessable(
            "This PDF is corrupted or unreadable. Re-export or re-scan it and upload again.", code="corrupt_pdf"
        ) from e
    try:
        if doc.needs_pass or doc.is_encrypted:
            raise Unprocessable("This PDF is password-protected. Remove the password and upload again.", code="encrypted_pdf")
        n = doc.page_count
        if n == 0:
            raise Unprocessable("This PDF has no pages.", code="empty_pdf")
        if max_pages is not None and n > max_pages:
            raise Unprocessable(f"This PDF has {n} pages; the limit is {max_pages}.", code="too_many_pages")
        # touching the first page catches truncated files that still parse a header
        doc.load_page(0).get_pixmap(matrix=fitz.Matrix(0.1, 0.1))
        return n
    except Unprocessable:
        raise
    except Exception as e:
        raise Unprocessable("This PDF could not be rendered; it appears damaged.", code="corrupt_pdf") from e
    finally:
        doc.close()
