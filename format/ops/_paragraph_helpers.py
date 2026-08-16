"""Shared paragraph-formatting helpers.

Extracted from ``ParagraphFormatter`` so ``TitleFinder`` and
``ParagraphClassifier`` can reuse the same logic without duplicating it.
Every function is a plain module-level callable -- no class state is hidden
inside the helpers, which keeps the three consumers easy to test in
isolation.
"""

from __future__ import annotations

from typing import Optional

from docx.document import Document as _Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.oxml.table import CT_Tbl
from docx.oxml.text.paragraph import CT_P
from docx.shared import Cm, Pt
from docx.table import Table
from docx.text.paragraph import Paragraph


def iter_block_items(parent):
    """Yield ``Paragraph`` and ``Table`` objects in document order."""
    parent_elm = parent.element.body if isinstance(parent, _Document) else parent._tc
    for child in parent_elm.iterchildren():
        if isinstance(child, CT_P):
            yield Paragraph(child, parent)
        elif isinstance(child, CT_Tbl):
            yield Table(child, parent)


def get_paragraph_alignment(para):
    """Return the ``WD_ALIGN_PARAGRAPH`` value for ``para``.

    Falls back to the raw ``w:jc`` attribute mapping when ``para.alignment``
    raises (e.g. when the paragraph has no explicit alignment set).
    """
    try:
        return para.alignment
    except ValueError:
        p_pr = para._p.pPr
        jc = p_pr.jc if p_pr is not None else None
        raw_value = jc.get(qn("w:val")) if jc is not None else None
        return {
            "start": WD_ALIGN_PARAGRAPH.LEFT,
            "left": WD_ALIGN_PARAGRAPH.LEFT,
            "end": WD_ALIGN_PARAGRAPH.RIGHT,
            "right": WD_ALIGN_PARAGRAPH.RIGHT,
            "center": WD_ALIGN_PARAGRAPH.CENTER,
            "both": WD_ALIGN_PARAGRAPH.JUSTIFY,
            "distribute": WD_ALIGN_PARAGRAPH.JUSTIFY,
            "mediumKashida": WD_ALIGN_PARAGRAPH.JUSTIFY,
            "highKashida": WD_ALIGN_PARAGRAPH.JUSTIFY,
            "lowKashida": WD_ALIGN_PARAGRAPH.JUSTIFY,
            "thaiDistribute": WD_ALIGN_PARAGRAPH.JUSTIFY,
        }.get(raw_value)


def get_paragraph_font_info(para):
    """Return ``(font_name, font_size_pt)`` of the first non-empty run."""
    if not para.runs:
        return None, None
    for run in para.runs:
        if run.text.strip():
            font_name = run.font.name
            font_size = run.font.size.pt if run.font.size else None
            return font_name, font_size
    return None, None


def english_font(font_name: str, config) -> str:
    """Resolve the western font name for a given Chinese font."""
    if config.get('use_custom_english_font', False):
        return config.get('english_font') or font_name
    return font_name


def set_run_font(
    run,
    font_name: str,
    size_pt,
    font_op,
    config,
    set_color: bool = False,
) -> None:
    """Set western + east-asian font, size, and optionally color on a run."""
    font_op.set_font(
        run,
        font_name,
        english_font(font_name, config),
        size_pt,
        set_color=set_color,
    )


def apply_font_to_runs(
    para,
    font_name: str,
    size_pt,
    font_op,
    config,
    set_color: bool = False,
) -> None:
    """Apply the configured font / size to every run in ``para``."""
    font_op.apply_to_paragraph(
        para,
        font_name,
        english_font(font_name, config),
        size_pt,
        set_color=set_color,
    )


def strip_leading_whitespace(para, logger) -> None:
    """Remove runs that consist solely of leading whitespace."""
    if not para.runs:
        return
    while para.runs:
        first_run = para.runs[0]
        r_elem = first_run._r
        children = [child for child in r_elem if child.tag != qn('w:rPr')]
        only_text = all(child.tag == qn('w:t') for child in children)
        if not only_text or first_run.text.strip():
            break
        para._p.remove(r_elem)

    if not para.runs:
        return
    first_run = para.runs[0]
    original_text = first_run.text
    stripped_text = original_text.lstrip()
    if original_text != stripped_text:
        first_run.text = stripped_text
        logger.info("  > 已移除段落前的多余空格。")


def reset_pagination_properties(para) -> None:
    """Clear widow/control and keep-together flags on ``para``."""
    para.paragraph_format.widow_control = False
    para.paragraph_format.keep_with_next = False
    para.paragraph_format.keep_lines_together = False
    para.paragraph_format.page_break_before = False
    para.paragraph_format.keep_together = False


def get_outline_level(para):
    """Return the current outline level (1-based) of ``para``, or ``None``."""
    pPr = para._p.get_or_add_pPr()
    outlineLvl = pPr.find(qn('w:outlineLvl'))
    if outlineLvl is not None:
        val = outlineLvl.get(qn('w:val'))
        if val is not None:
            return int(val)
    return None


def set_outline_level(para, level, config, logger) -> Optional[int]:
    """Set the outline level (1-9) of ``para``.

    Returns the previous level (0-based) so callers can report the change,
    or ``None`` if the level was out of range.
    """
    if level < 1 or level > 9:
        logger.info(f"  > 警告：大纲级别 {level} 超出范围 (1-9)，已跳过设置")
        return None
    original_level = get_outline_level(para)
    pPr = para._p.get_or_add_pPr()
    outlineLvl = pPr.find(qn('w:outlineLvl'))
    if outlineLvl is None:
        outlineLvl = OxmlElement('w:outlineLvl')
        pPr.append(outlineLvl)
    outlineLvl.set(qn('w:val'), str(level - 1))
    return original_level


def format_heading(para, level, config, logger) -> None:
    """Set the outline level of ``para`` to ``level`` (1-9) and log."""
    if not config.get('set_outline', True):
        return
    text_preview = para.text.strip()[:30].replace("\n", " ")
    original_level = set_outline_level(para, level, config, logger)
    if original_level is not None:
        logger.info(
            f"  > 大纲级别: Lv{original_level + 1} → Lv{level} (覆盖) - "
            f"\"{text_preview}...\""
        )
    else:
        logger.info(
            f"  > 大纲级别: 无 → Lv{level} (新设) - \"{text_preview}...\""
        )


def apply_text_indent_and_align(para, config) -> None:
    """Apply the standard left/right indent and justify alignment."""
    pf = para.paragraph_format
    pf.first_line_indent = None
    pf.left_indent = Cm(config.get('left_indent_cm', 2.8))
    pf.right_indent = Cm(config.get('right_indent_cm', 2.6))

    ind = para._p.get_or_add_pPr().get_or_add_ind()
    ind.attrib.pop(qn('w:leftChars'), None)
    ind.attrib.pop(qn('w:hanging'), None)
    ind.attrib.pop(qn('w:hangingChars'), None)
    ind.attrib.pop(qn('w:firstLine'), None)
    ind.set(qn("w:firstLineChars"), "200")

    para.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
