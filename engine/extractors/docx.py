"""Word document (.docx) content extractor.

Extracts paragraphs, tables, headers, footers, and section metadata.
All numeric values are converted to conventional units:
  - page size / margins / indent: cm
  - font size, spacing: pt
  - line spacing: float (multiplier) or pt for fixed values
"""

import logging
from typing import Optional

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn

logger = logging.getLogger(__name__)


class DocxExtractor:
    """Extract structured content from a .docx document via python-docx."""

    ALIGNMENT_MAP = {
        WD_ALIGN_PARAGRAPH.LEFT: "left",
        WD_ALIGN_PARAGRAPH.CENTER: "center",
        WD_ALIGN_PARAGRAPH.RIGHT: "right",
        WD_ALIGN_PARAGRAPH.JUSTIFY: "justify",
    }

    def extract(self, file_path: str) -> dict:
        """Extract full document structure.

        Args:
            file_path: Path to the .docx file.

        Returns:
            Dict containing paragraphs, tables, headers, footers, sections,
            and source_format fields.
        """
        document = Document(file_path)
        return {
            "paragraphs": self._extract_paragraphs(document),
            "tables": self._extract_tables(document),
            "headers": self._extract_headers(document),
            "footers": self._extract_footers(document),
            "sections": self._extract_sections(document),
            "source_format": "docx",
        }

    def _extract_paragraphs(self, document) -> list:
        """Extract all paragraphs with text and style attributes."""
        paragraphs: list[dict] = []
        for index, para in enumerate(document.paragraphs):
            item = {
                "index": index,
                "paragraph_index": index,
                "text": para.text,
                "style": para.style.name if para.style else None,
            }
            item.update(self._extract_run_style(para))
            item.update(self._extract_paragraph_format(para))
            paragraphs.append(item)
        return paragraphs

    # ---- run-level style ----

    def _extract_run_style(self, paragraph) -> dict:
        """Extract font-related attributes from the first meaningful run."""
        info: dict = {"font": None, "size": None, "bold": None, "italic": None}
        target_run = None
        for run in paragraph.runs:
            if run.text and run.text.strip():
                target_run = run
                break
        if target_run is None and paragraph.runs:
            target_run = paragraph.runs[0]
        if target_run is not None:
            font = target_run.font
            info["font"] = self._get_font_name(target_run)
            info["size"] = font.size.pt if font.size else None
            info["bold"] = font.bold
            info["italic"] = font.italic
        self._inherit_from_style_chain(paragraph, info)
        return info

    @staticmethod
    def _inherit_from_style_chain(paragraph, info: dict) -> None:
        """Fill missing fields by walking the style inheritance chain."""
        style = paragraph.style if paragraph is not None else None
        depth = 0
        while style is not None and depth < 10:
            try:
                font = style.font
                if info["size"] is None and font.size is not None:
                    info["size"] = font.size.pt
                if info["bold"] is None and font.bold is not None:
                    info["bold"] = font.bold
                if info["italic"] is None and font.italic is not None:
                    info["italic"] = font.italic
                if info["font"] is None:
                    inherited = DocxExtractor._get_style_font_name(style)
                    if inherited:
                        info["font"] = inherited
                if all(v is not None for v in (info["font"], info["size"],
                                               info["bold"], info["italic"])):
                    break
                style = style.base_style
            except Exception:
                break
            depth += 1

    @staticmethod
    def _get_style_font_name(style) -> Optional[str]:
        """Read font name from style XML (eastAsia / ascii)."""
        try:
            rPr = style.element.rPr if hasattr(style, "element") else None
            if rPr is not None and rPr.rFonts is not None:
                rFonts = rPr.rFonts
                ea = rFonts.get(qn("w:eastAsia"))
                if ea:
                    return ea
                ascii_f = rFonts.get(qn("w:ascii"))
                if ascii_f:
                    return ascii_f
        except Exception:
            pass
        try:
            return style.font.name
        except Exception:
            return None

    @staticmethod
    def _get_font_name(run) -> Optional[str]:
        """Get the full font name including CJK eastAsia font."""
        try:
            rPr = run._element.rPr
            if rPr is not None and rPr.rFonts is not None:
                rFonts = rPr.rFonts
                ea = rFonts.get(qn("w:eastAsia"))
                if ea:
                    return ea
                ascii_f = rFonts.get(qn("w:ascii"))
                if ascii_f:
                    return ascii_f
        except Exception:
            pass
        return run.font.name if run.font.name else None

    # ---- paragraph-level format ----

    @staticmethod
    def _extract_paragraph_format(paragraph) -> dict:
        """Extract paragraph formatting: alignment, indent, spacing.

        The canonical field name exposed to the rule engine is ``align``
        (matching the schema documented in ``config/schema.md``); ``alignment``
        is retained as a backwards-compatible alias.
        """
        fmt = paragraph.paragraph_format
        alignment = paragraph.alignment or fmt.alignment
        align = DocxExtractor.ALIGNMENT_MAP.get(alignment)
        return {
            "alignment": align,
            "align": align,
            "first_line_indent": DocxExtractor._length_to_cm(fmt.first_line_indent),
            "left_indent": DocxExtractor._length_to_cm(fmt.left_indent),
            "right_indent": DocxExtractor._length_to_cm(fmt.right_indent),
            "line_spacing": DocxExtractor._line_spacing_value(fmt.line_spacing),
            "space_before": DocxExtractor._length_to_pt(fmt.space_before),
            "space_after": DocxExtractor._length_to_pt(fmt.space_after),
        }

    @staticmethod
    def _length_to_cm(length):
        """Convert EMU length to cm, or None."""
        if length is None:
            return None
        try:
            return round(length.cm, 3)
        except Exception:
            return None

    @staticmethod
    def _length_to_pt(length):
        """Convert EMU length to pt, or None."""
        if length is None:
            return None
        try:
            return round(length.pt, 3)
        except Exception:
            return None

    @staticmethod
    def _line_spacing_value(line_spacing):
        """Parse line-spacing: multiplier (float) or fixed value in pt."""
        if line_spacing is None:
            return None
        try:
            if isinstance(line_spacing, float):
                return round(line_spacing, 3)
            return round(line_spacing.pt, 3)
        except Exception:
            return None

    # ---- tables ----

    def _extract_tables(self, document) -> list:
        """Extract table data as list-of-lists of strings."""
        tables = []
        for table in document.tables:
            rows = []
            for row in table.rows:
                cells = [cell.text for cell in row.cells]
                rows.append(cells)
            tables.append(rows)
        return tables

    # ---- headers / footers ----

    def _extract_headers(self, document) -> list:
        """Extract non-empty header paragraphs from all sections."""
        headers = []
        for section in document.sections:
            for para in section.header.paragraphs:
                if para.text:
                    headers.append(para.text)
        return headers

    def _extract_footers(self, document) -> list:
        """Extract non-empty footer paragraphs from all sections."""
        footers = []
        for section in document.sections:
            for para in section.footer.paragraphs:
                if para.text:
                    footers.append(para.text)
        return footers

    # ---- sections ----

    def _extract_sections(self, document) -> list:
        """Extract section margins and page dimensions in cm."""
        sections = []
        for section in document.sections:
            sections.append({
                "page_width": self._length_to_cm(section.page_width),
                "page_height": self._length_to_cm(section.page_height),
                "margins": {
                    "left": self._length_to_cm(section.left_margin),
                    "right": self._length_to_cm(section.right_margin),
                    "top": self._length_to_cm(section.top_margin),
                    "bottom": self._length_to_cm(section.bottom_margin),
                    "header": self._length_to_cm(section.header_distance),
                    "footer": self._length_to_cm(section.footer_distance),
                },
            })
        return sections
