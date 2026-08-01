"""Font operations: set western/east-asian fonts, size, bold and color on runs.

Refactored from the legacy ``_set_run_font`` logic so the new pipeline can
apply font formatting through a single, well-typed Op.
"""

from __future__ import annotations

from docx.oxml.ns import qn
from docx.shared import Pt, RGBColor


class FontOp:
    """Apply font attributes to ``run`` objects.

    Each instance is constructed with a ``Config`` object (a
    ``format.config.Config`` instance) so font behavior can be tweaked through
    configuration without changing call sites.
    """

    def __init__(self, config) -> None:
        self.config = config

    def set_font(
        self,
        run,
        font_cn: str,
        font_en: str,
        size,
        bold=None,
        set_color: bool = False,
        color=None,
    ) -> None:
        """Set font name, size, bold and color on a single run.

        Args:
            run: A ``docx.text.run.Run`` instance.
            font_cn: East-asian (Chinese) font name.
            font_en: Western (ASCII / latin) font name.
            size: Font size in points.
            bold: When not ``None``, sets ``run.font.bold`` to this value.
            set_color: When ``True``, apply ``color`` (defaults to black).
            color: Optional ``RGBColor``; falls back to black ``RGBColor(0,0,0)``.
        """
        run.font.size = Pt(size)

        if set_color:
            run.font.color.rgb = color if color is not None else RGBColor(0, 0, 0)

        # rPr / rFonts element configuration (mirrors legacy logic).
        rPr = run._r.get_or_add_rPr()
        rFonts = rPr.get_or_add_rFonts()
        # Drop any theme-based font references so they cannot override the
        # explicit font names set below.
        for theme_attr in (
            "w:eastAsiaTheme", "w:asciiTheme", "w:hAnsiTheme", "w:cstheme", "w:csTheme",
        ):
            rFonts.attrib.pop(qn(theme_attr), None)
        rFonts.set(qn("w:eastAsia"), font_cn)
        rFonts.set(qn("w:ascii"), font_en)
        rFonts.set(qn("w:hAnsi"), font_en)
        # ``run.font.name`` also writes the western (ascii/hAnsi) font.
        run.font.name = font_en

        if bold is not None:
            run.font.bold = bold

    def apply_to_paragraph(
        self,
        para,
        font_cn: str,
        font_en: str,
        size,
        bold=None,
        set_color: bool = False,
    ) -> None:
        """Apply :meth:`set_font` to every run in ``para``."""
        for run in para.runs:
            self.set_font(run, font_cn, font_en, size, bold=bold, set_color=set_color)
