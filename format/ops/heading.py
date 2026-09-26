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

# Numeric hierarchy: "1." -> Lv1, "1.1" -> Lv2, "1.1.1" -> Lv3.
#
# RE_HEADING_H3 (^\d+\s*[\.．]) matched ALL of these and mapped them all to
# Lv3, so "1.1" and "1.1.1" came out at the same level as "1.". Depth is
# derived from the dot-separated segment count instead. First segment is
# capped at 2 digits so dates/versions like "2024.5" are not headings.
RE_HEADING_NUMERIC = re.compile(r"^(\d{1,2}(?:[\.．]\d{1,2})+)(?:\s*[^\d\.．]|$)")
RE_HEADING_NUM_SINGLE = re.compile(r"^(\d{1,2})[\.．]\s*\S")

# Structural section markers. Matched against whitespace-stripped text because
# documents pad these with ideographic spaces for visual spacing ("摘  要",
# "参  考  文  献") which defeated the heading regexes and left them formatted
# as indented body text.
RE_STRUCTURAL_HEADING = re.compile(
    r"^(摘要|abstract|参考文献|references|bibliography|"
    r"致谢|acknowledgements?|附录|appendix|目录|contents|结论)\s*[：:.．]?\s*(.*)$"
)

# 关键词行：标签后必须带内容，所以单独匹配（不能复用 RE_STRUCTURAL_HEADING 的
# "无后续内容才算标题"约束）。
RE_KEYWORDS_LINE = re.compile(r"^(关键词|keywords)\s*[：:]?\s*(.*)$")

# Cap for heading-length guards: longer text is prose that merely starts with
# a number, not a heading.
MAX_HEADING_LEN = 60


def strip_all_whitespace(text):
    """Remove every whitespace char including full-width ideographic space."""
    return re.sub(r"[\s　 ]+", "", text or "")


def numeric_heading_level(text):
    """Return outline depth 1-4 for a numeric heading, else ``None``.

    ``"1.1.1 背景"`` -> 3, ``"1.1 背景"`` -> 2, ``"1. 数据来源"`` -> 1.
    Depth is the number of dot-separated segments, capped at 4.
    """
    if not text or len(text.strip()) > MAX_HEADING_LEN:
        return None
    probe = text.lstrip()
    m = RE_HEADING_NUMERIC.match(probe)
    if m:
        segments = m.group(1).count(".") + m.group(1).count("．") + 1
        return min(max(segments, 1), 4)
    if RE_HEADING_NUM_SINGLE.match(probe):
        return 1
    return None


def structural_heading_kind(text):
    """Return ``'h1'`` / ``'keywords'`` if ``text`` is a structural marker.

    Whitespace-insensitive.  ``'参考文献[1]张三'`` is a reference entry and
    returns ``None``: a marker followed by content is not the section heading.
    """
    if not text:
        return None
    norm = strip_all_whitespace(text).lower()
    if not norm or len(norm) > 30:
        return None
    if RE_KEYWORDS_LINE.match(norm):
        return "keywords"
    m = RE_STRUCTURAL_HEADING.match(norm)
    if not m or m.group(2).strip():
        return None
    return "h1"

# Attachment marker.
RE_ATTACHMENT = re.compile(r"^附件\s*(\d+|[一二三四五六七八九十百千万零]+)?\s*[:：]?$")

# Secondary-heading inline title: e.g. "（一）标题内容" captures the title.
RE_H2_INLINE_TITLE = re.compile(r"^[（\(](.+?)[）\)](.*)", re.DOTALL)

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
            ``'h3'``, ``'h4'``, ``'keywords'``, ``'body'``,
            ``'attachment'`` or ``None`` (``None`` for empty /
            whitespace-only text).
        """
        if text is None:
            return None
        stripped = text.strip()
        if not stripped:
            return None

        # Attachment marker takes priority.
        if RE_ATTACHMENT.match(stripped):
            return "attachment"

        if RE_HEADING_H1.match(stripped):
            return "h1"
        if RE_HEADING_H2.match(stripped):
            return "h2"

        struct = structural_heading_kind(stripped)
        if struct == "h1":
            return "h1"
        if struct == "keywords":
            return "keywords"

        # Numeric hierarchy: depth comes from the dot-segment count, so
        # "1." / "1.1" / "1.1.1" land on h1 / h2 / h3 instead of all h3.
        depth = numeric_heading_level(stripped)
        if depth is not None:
            return f"h{depth}"

        if RE_HEADING_H4.match(stripped) and len(stripped) < MAX_HEADING_LEN:
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
