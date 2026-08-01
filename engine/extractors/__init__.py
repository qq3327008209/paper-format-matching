"""Extractor entry-point: auto-select based on file extension."""

from .docx import DocxExtractor
from .doc import DocExtractor
from .pdf import PdfExtractor


def get_extractor(file_path: str):
    """Return an extractor instance for the given file path.

    Args:
        file_path: Document file path. Extension determines extractor.

    Returns:
        An instance of DocxExtractor, DocExtractor, or PdfExtractor.

    Raises:
        ValueError: When the file extension is not supported.
    """
    lower = file_path.lower()
    if lower.endswith(".docx"):
        return DocxExtractor()
    if lower.endswith(".doc"):
        return DocExtractor()
    if lower.endswith(".pdf"):
        return PdfExtractor()
    raise ValueError(f"Unsupported file format: {file_path}")
