"""Paragraph formatting operations: indent, line spacing, alignment, spacing.

Refactored from the legacy paragraph-formatting helpers so the new pipeline can
apply paragraph-level layout through a single Op.
"""

from __future__ import annotations

from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING
from docx.oxml.ns import qn
from docx.shared import Pt


# Mapping of friendly align names to ``WD_ALIGN_PARAGRAPH`` members.
_ALIGN_MAP = {
    "left": WD_ALIGN_PARAGRAPH.LEFT,
    "center": WD_ALIGN_PARAGRAPH.CENTER,
    "right": WD_ALIGN_PARAGRAPH.RIGHT,
    "justify": WD_ALIGN_PARAGRAPH.JUSTIFY,
    "both": WD_ALIGN_PARAGRAPH.JUSTIFY,
    "distribute": WD_ALIGN_PARAGRAPH.DISTRIBUTE,
}


class ParagraphOp:
    """Apply paragraph-level formatting (indent / spacing / alignment)."""

    def __init__(self, config) -> None:
        self.config = config

    def set_indent(self, para, indent_chars) -> None:
        """Set first-line indent in character units.

        Uses the OOXML ``w:firstLineChars`` attribute (1 char == 100 units)
        which renders consistently regardless of font size, mirroring the
        legacy implementation. Stale ``w:firstLine`` point values are cleared
        so they do not stack on top of the character-based indent.
        """
        pf = para.paragraph_format
        pf.first_line_indent = None
        ind = para._p.get_or_add_pPr().get_or_add_ind()
        # Clear stale point-based first-line indent / hanging indent.
        ind.attrib.pop(qn("w:firstLine"), None)
        ind.attrib.pop(qn("w:hanging"), None)
        ind.attrib.pop(qn("w:hangingChars"), None)
        try:
            chars_value = int(float(indent_chars) * 100)
        except (TypeError, ValueError):
            return
        if chars_value > 0:
            ind.set(qn("w:firstLineChars"), str(chars_value))
        else:
            ind.attrib.pop(qn("w:firstLineChars"), None)

    def set_line_spacing(self, para, spacing_pt) -> None:
        """Set exact line spacing in points.

        Args:
            spacing_pt: Spacing in points. When ``None`` the call is a no-op.
        """
        if spacing_pt is None:
            return
        pf = para.paragraph_format
        pf.line_spacing_rule = WD_LINE_SPACING.EXACTLY
        pf.line_spacing = Pt(spacing_pt)

    def set_align(self, para, align) -> None:
        """Set paragraph alignment.

        Args:
            align: One of ``'left'``, ``'center'``, ``'right'``, ``'justify'``
                (also accepts ``'both'`` / ``'distribute'``). Accepts a
                ``WD_ALIGN_PARAGRAPH`` member directly as well.
        """
        if align is None:
            return
        if align in _ALIGN_MAP:
            para.alignment = _ALIGN_MAP[align]
        else:
            # Assume it is already a WD_ALIGN_PARAGRAPH enum member.
            para.alignment = align

    def set_spacing(self, para, before=None, after=None) -> None:
        """Set space before / after a paragraph in points."""
        pf = para.paragraph_format
        if before is not None:
            pf.space_before = Pt(before)
        if after is not None:
            pf.space_after = Pt(after)

    def apply(
        self,
        para,
        align=None,
        indent=None,
        line_spacing=None,
        space_before=None,
        space_after=None,
    ) -> None:
        """Convenience wrapper applying all requested attributes at once."""
        if align is not None:
            self.set_align(para, align)
        if indent is not None:
            self.set_indent(para, indent)
        if line_spacing is not None:
            self.set_line_spacing(para, line_spacing)
        if space_before is not None or space_after is not None:
            self.set_spacing(para, before=space_before, after=space_after)
