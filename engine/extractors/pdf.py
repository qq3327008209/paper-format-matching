"""PDF document extractor (read-only).

PDF extraction is best-effort: font and size information is unreliable in
PDF, so the extractor only returns text content and page numbers. The rule
engine should skip font/size rules for PDF sources.
"""

import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)


class PdfExtractor:
    """Extract text and page-level information from a PDF file."""

    def extract(self, file_path: str) -> dict:
        """Extract structured content from a PDF.

        Args:
            file_path: Path to the .pdf file.

        Returns:
            Dict with ``paragraphs`` (list of dicts with text + page),
            ``source_format: "pdf"``.

        Raises:
            RuntimeError: If pypdf is not installed.
        """
        try:
            from pypdf import PdfReader
        except ImportError:
            raise RuntimeError("pypdf is not installed. Run: pip install pypdf")

        reader = PdfReader(file_path)
        paragraphs: list[dict] = []

        for page_num, page_text in enumerate(reader.pages, start=1):
            text = page_text.extract_text() or ""
            # Split into non-empty lines as rough "paragraphs"
            for line in text.splitlines():
                stripped = line.strip()
                if stripped:
                    paragraphs.append({
                        "paragraph_index": len(paragraphs),
                        "text": stripped,
                        "page": page_num,
                        "style": None,
                        "font": None,
                        "size": None,
                        "bold": None,
                        "italic": None,
                        "alignment": None,
                        "first_line_indent": None,
                        "line_spacing": None,
                    })

        return {
            "paragraphs": paragraphs,
            "source_format": "pdf",
        }
