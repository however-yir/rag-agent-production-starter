"""Text extraction helpers for supported file types."""

from __future__ import annotations

import io


def extract_text_from_pdf(file_bytes: bytes) -> str:
    """Extract text from a PDF file."""
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(file_bytes))
    texts = []
    for page in reader.pages:
        texts.append(page.extract_text() or "")
    return "\n".join(texts).strip()


def extract_text_from_upload(filename: str, file_bytes: bytes) -> tuple[str, str]:
    """Infer source type and extract text."""
    lower_name = filename.lower()
    if lower_name.endswith(".pdf"):
        return "pdf", extract_text_from_pdf(file_bytes)
    return "text", file_bytes.decode("utf-8", errors="ignore")

