"""Heading detection and outline-level operations.

Provides a paragraph-type classifier (``detect_type``) based on the heading
regex patterns used by the legacy formatting engine, plus a helper to set a
paragraph's OOXML outline level (``w:outlineLvl``).
"""

from __future__ import annotations

import re

from docx.oxml import OxmlElement
from docx.oxml.ns import qn


# ---------------------------------------------------------------------------
# Regex patterns copied from wfp_core.py
# ---------------------------------------------------------------------------

CHINESE_NUM_PATTERN = r"[一二三四五六七八九十百千万零]+"

# Title-region heading markers (一、 / （一）) — contextually a title's outline.
RE_TITLE_H1 = re.compile(r"^" + CHINESE_NUM_PATTERN + r"\s*、")
RE_TITLE_H2 = re.compile(r"^[（\(]" + CHINESE_NUM_PATTERN + r"[）\)]")

# Body heading markers.
RE_HEADING_H1 = re.compile(r"^[一二三四五六七八九十百千万零]+\s*、")
RE_HEADING_H2 = re.compile(r"^[（\(][一二三四五六七八九十百千万零]+[）\)]")
RE_HEADING_H3 = re.compile(r"^\d+\s*[\.．]")
RE_HEADING_H4 = re.compile(r"^[（\(]\d+[）\)]")

# Attachment marker.
RE_ATTACHMENT = re.compile(r"^附件\s*(\d+|[一二三四五六七八九十百千万零]+)?\s*[:：]?$")

# Explicit official-document title patterns (copied from formatter.py logic).
RE_TITLE_EXPLICIT = [
    re.compile(r"^关于.+的(通知|报告|请示|函|意见|决定|公告|通报|批复|说明|方案|总结|汇报|复函|答复|建议)$"),
    re.compile(r"^.{2,30}(通知|报告|请示|函|意见|决定|公告|通报|批复|工作方案|工作总结|实施方案|管理办法|暂行规定)$"),
    re.compile(r"^[\u4e00-\u9fff]{2,20}(委员会|办公室|局|厅|院|部|委|中心|公司|集团|学校|大学)$"),
]

# Indicators that a paragraph is body text, not a title.
RE_BODY_END_PUNCT = re.compile(r"[。！？，、；：.!?,;:]\s*$")


class HeadingOp:
    """Classify paragraphs and apply outline levels."""

    def __init__(self, config) -> None:
        self.config = config

    def detect_type(self, text, para_index=0, size=0, style=""):
        """Classify a paragraph's type from its text and weak context signals.

        Args:
            text: Paragraph text.
            para_index: Index of the paragraph within the document (used to
                bias title / subtitle detection toward the document head).
            size: Font size hint (points). Larger sizes favour title/subtitle.
            style: Paragraph style / alignment hint (e.g. ``'center'``).

        Returns:
            One of ``'title'``, ``'subtitle'``, ``'h1'``, ``'h2'``,
            ``'h3'``, ``'h4'``, ``'body'``, ``'attachment'`` or ``None``
            (``None`` for empty / whitespace-only text).
        """
        if text is None:
            return None
        stripped = text.strip()
        if not stripped:
            return None

        # Attachment marker takes priority.
        if RE_ATTACHMENT.match(stripped):
            return "attachment"

        # Heading levels (with length guards borrowed from formatter.py for
        # the numeric heading variants to avoid grabbing dates / versions).
        if RE_HEADING_H1.match(stripped):
            return "h1"
        if RE_HEADING_H2.match(stripped):
            return "h2"
        if RE_HEADING_H3.match(stripped) and len(stripped) < 60:
            return "h3"
        if RE_HEADING_H4.match(stripped) and len(stripped) < 60:
            return "h4"

        style_lower = str(style or "").lower()
        is_centered = "center" in style_lower
        near_head = para_index < 5

        # Explicit official-document title patterns.
        for pattern in RE_TITLE_EXPLICIT:
            if pattern.match(stripped):
                return "title"

        # Title heuristics: short, near the document head, centred or large.
        if near_head and not RE_BODY_END_PUNCT.search(stripped):
            if is_centered and len(stripped) < 60:
                return "title"
            if size and size >= 18 and len(stripped) < 60:
                return "title"
            # Long title-like text without trailing punctuation.
            if 15 < len(stripped) < 80 and not re.match(r"^[一二三四五六七八九十\d（(]", stripped):
                return "title"

        # Subtitle: centred, near the head, shorter than a typical title and
        # not matching body-end punctuation.
        if near_head and is_centered and len(stripped) < 40 and not RE_BODY_END_PUNCT.search(stripped):
            return "subtitle"

        return "body"

    def set_outline_level(self, para, level) -> None:
        """Set the paragraph's OOXML outline level.

        Args:
            level: Outline level in the 1–9 range (Word stores ``level - 1``
                internally). Out-of-range values are ignored.
        """
        try:
            level_int = int(level)
        except (TypeError, ValueError):
            return
        if level_int < 1 or level_int > 9:
            return

        pPr = para._p.get_or_add_pPr()
        outlineLvl = pPr.find(qn("w:outlineLvl"))
        if outlineLvl is None:
            outlineLvl = OxmlElement("w:outlineLvl")
            pPr.append(outlineLvl)
        outlineLvl.set(qn("w:val"), str(level_int - 1))
