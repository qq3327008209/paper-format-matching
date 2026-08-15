"""Paragraph classification and formatting.

Refactored from the legacy ``FormatEngine`` paragraph-formatting logic.  This
module owns:

- ``_find_title_paragraphs`` — locate the document title / subtitle block
  indices.
- ``_apply_para_format`` — classify a single paragraph (h1 / h2 / h3 / h4 /
  body / attachment / picture-text) and apply the configured font, size,
  alignment, indent and outline level.

The module is deliberately self-contained: it imports only ``python-docx``,
the standard library and sibling ``format.ops`` modules.
"""

from __future__ import annotations

from typing import Optional

from docx.document import Document as _Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.oxml.table import CT_Tbl
from docx.oxml.text.paragraph import CT_P
from docx.shared import Cm, Pt, RGBColor
from docx.table import Table
from docx.text.paragraph import Paragraph

from format.ops.font import FontOp
from format.ops.heading import (
    RE_ATTACHMENT,
    RE_H2_INLINE_TITLE,
    RE_HEADING_H1,
    RE_HEADING_H2,
    RE_HEADING_H3,
    RE_HEADING_H4,
    RE_TITLE_H1,
    RE_TITLE_H2,
)
from format.ops.paragraph import ParagraphOp
from format.ops.symbol_normalizer import (
    has_chinese,
    has_drawing_or_pict,
    has_embedded_object,
    has_field_codes,
    is_after_digit_or_latin,
    is_before_digit_or_latin,
)
from format.ops.space import redistribute_text_to_runs


# ---------------------------------------------------------------------------
# ParagraphFormatter
# ---------------------------------------------------------------------------

class ParagraphFormatter:
    """Classify and format paragraphs, and locate document titles.

    Refactored from the paragraph-formatting portion of the legacy
    ``FormatEngine``.  Each instance is constructed with a
    ``format.config.Config`` and composes ``FontOp`` / ``HeadingOp`` /
    ``ParagraphOp`` internally.
    """

    def __init__(self, config, logger) -> None:
        """Initialise the formatter.

        Args:
            config: A ``format.config.Config`` instance.
            logger: A ``logging.Logger`` used for progress messages.
        """
        self.config = config
        self._logger = logger
        self._font_op = FontOp(config)
        self._heading_op = self._make_heading_op(config)
        self._para_op = ParagraphOp(config)

    def _make_heading_op(self, config):
        """Return a HeadingOp instance.

        Indirected through a helper so tests can patch it if needed.
        """
        from format.ops.heading import HeadingOp
        return HeadingOp(config)

    def _log(self, message: str) -> None:
        self._logger.info(message)

    # ------------------------------------------------------------------
    # Font helpers (mirror the legacy _set_run_font / _apply_font_to_runs)
    # ------------------------------------------------------------------
    def _english_font(self, font_name: str) -> str:
        """Resolve the western font name for a given Chinese font."""
        if self.config.get('use_custom_english_font', False):
            return self.config.get('english_font') or font_name
        return font_name

    def _set_run_font(
        self,
        run,
        font_name: str,
        size_pt,
        set_color: bool = False,
    ) -> None:
        """Set western + east-asian font, size, and optionally color on a run."""
        self._font_op.set_font(
            run,
            font_name,
            self._english_font(font_name),
            size_pt,
            set_color=set_color,
        )

    def _apply_font_to_runs(
        self,
        para,
        font_name: str,
        size_pt,
        set_color: bool = False,
    ) -> None:
        """Apply the configured font / size to every run in ``para``."""
        self._font_op.apply_to_paragraph(
            para,
            font_name,
            self._english_font(font_name),
            size_pt,
            set_color=set_color,
        )

    def _get_paragraph_font_info(self, para):
        """Return ``(font_name, font_size_pt)`` of the first non-empty run."""
        if not para.runs:
            return None, None
        for run in para.runs:
            if run.text.strip():
                font_name = run.font.name
                font_size = run.font.size.pt if run.font.size else None
                return font_name, font_size
        return None, None

    @staticmethod
    def _get_paragraph_alignment(para):
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

    # ------------------------------------------------------------------
    # Block iteration
    # ------------------------------------------------------------------
    @staticmethod
    def _iter_block_items(parent):
        """Yield ``Paragraph`` and ``Table`` objects in document order."""
        parent_elm = parent.element.body if isinstance(parent, _Document) else parent._tc
        for child in parent_elm.iterchildren():
            if isinstance(child, CT_P):
                yield Paragraph(child, parent)
            elif isinstance(child, CT_Tbl):
                yield Table(child, parent)

    # ------------------------------------------------------------------
    # Whitespace / pagination helpers
    # ------------------------------------------------------------------
    def _strip_leading_whitespace(self, para) -> None:
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
            self._log("  > 已移除段落前的多余空格。")

    @staticmethod
    def _reset_pagination_properties(para) -> None:
        para.paragraph_format.widow_control = False
        para.paragraph_format.keep_with_next = False
        para.paragraph_format.keep_lines_together = False
        para.paragraph_format.page_break_before = False
        para.paragraph_format.keep_together = False

    # ------------------------------------------------------------------
    # Outline level
    # ------------------------------------------------------------------
    def _get_outline_level(self, para):
        pPr = para._p.get_or_add_pPr()
        outlineLvl = pPr.find(qn('w:outlineLvl'))
        if outlineLvl is not None:
            val = outlineLvl.get(qn('w:val'))
            if val is not None:
                return int(val)
        return None

    def _set_outline_level(self, para, level) -> Optional[int]:
        if level < 1 or level > 9:
            self._log(f"  > 警告：大纲级别 {level} 超出范围 (1-9)，已跳过设置")
            return None
        original_level = self._get_outline_level(para)
        pPr = para._p.get_or_add_pPr()
        outlineLvl = pPr.find(qn('w:outlineLvl'))
        if outlineLvl is None:
            outlineLvl = OxmlElement('w:outlineLvl')
            pPr.append(outlineLvl)
        outlineLvl.set(qn('w:val'), str(level - 1))
        return original_level

    def _format_heading(self, para, level) -> None:
        """Set the outline level of a paragraph (1-9)."""
        if not self.config.get('set_outline', True):
            return
        text_preview = para.text.strip()[:30].replace("\n", " ")
        original_level = self._set_outline_level(para, level)
        if original_level is not None:
            self._log(f"  > 大纲级别: Lv{original_level + 1} → Lv{level} (覆盖) - \"{text_preview}...\"")
        else:
            self._log(f"  > 大纲级别: 无 → Lv{level} (新设) - \"{text_preview}...\"")

    def _apply_text_indent_and_align(self, para) -> None:
        pf = para.paragraph_format
        pf.first_line_indent = None
        pf.left_indent = Cm(self.config.get('left_indent_cm', 2.8))
        pf.right_indent = Cm(self.config.get('right_indent_cm', 2.6))

        ind = para._p.get_or_add_pPr().get_or_add_ind()
        ind.attrib.pop(qn('w:leftChars'), None)
        ind.attrib.pop(qn('w:hanging'), None)
        ind.attrib.pop(qn('w:hangingChars'), None)
        ind.attrib.pop(qn('w:firstLine'), None)
        ind.set(qn("w:firstLineChars"), "200")

        para.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY

    # ------------------------------------------------------------------
    # Title / subtitle detection
    # ------------------------------------------------------------------
    def find_title_paragraphs(self, doc, is_from_txt: bool, start_index: int = 0):
        """Find the main title and subtitle paragraph block indices.

        Returns:
            ``(title_indices, subtitle_indices)`` — lists of indices into the
            flat block list returned by ``_iter_block_items``.
        """
        all_blocks = list(self._iter_block_items(doc))

        first_title_idx = -1

        if is_from_txt:
            self._log("文档源自 TXT，采用智能规则查找题目...")
            for idx in range(start_index, len(all_blocks)):
                block = all_blocks[idx]
                if isinstance(block, Paragraph) and block.text.strip():
                    text_to_check = block.text.strip()
                    if RE_TITLE_H1.match(text_to_check) or RE_TITLE_H2.match(text_to_check):
                        self._log(f"  > 首个非空行 (块 {idx + 1}) 符合标题格式，认定本文档无独立题目。")
                        return [], []
                    else:
                        self._log(f"  > 在块 {idx + 1} 发现首个非空段落，认定为题目首行。")
                        first_title_idx = idx
                        break
        else:
            self._log("正在预扫描以确定居中题目位置...")
            for idx in range(start_index, len(all_blocks)):
                block = all_blocks[idx]
                if not isinstance(block, Paragraph) or not block.text.strip():
                    continue
                para = block
                text_to_check = para.text.lstrip()
                if RE_TITLE_H1.match(text_to_check) or RE_TITLE_H2.match(text_to_check):
                    self._log("  > 发现一级/二级标题，在此之前未找到居中题目。")
                    return [], []
                if self._get_paragraph_alignment(para) == WD_ALIGN_PARAGRAPH.CENTER:
                    self._log(f"  > 在块 {idx + 1} 发现潜在题目首行。")
                    first_title_idx = idx
                    break

        if first_title_idx == -1:
            self._log("  > 扫描结束，未能找到题目。")
            return [], []

        first_title_para = all_blocks[first_title_idx]
        title_font, title_size = self._get_paragraph_font_info(first_title_para)

        title_indices = [first_title_idx]
        idx = first_title_idx + 1

        while idx < len(all_blocks):
            block = all_blocks[idx]
            if not isinstance(block, Paragraph):
                break

            para = block
            text = para.text.strip()

            if not text:
                self._log(f"  > 在块 {idx + 1} 遇到空行，标题识别结束。")
                break

            if self._get_paragraph_alignment(para) != WD_ALIGN_PARAGRAPH.CENTER:
                break

            para_font, para_size = self._get_paragraph_font_info(para)
            if para_font == title_font and para_size == title_size:
                self._log(f"  > 块 {idx + 1} 也是标题行（居中且字体字号相同）。")
                title_indices.append(idx)
                idx += 1
            else:
                break

        self._log(f"  > 共识别到 {len(title_indices)} 行标题。")

        subtitle_indices = []
        subtitle_start_idx = idx

        while subtitle_start_idx < len(all_blocks):
            block = all_blocks[subtitle_start_idx]
            if isinstance(block, Paragraph) and block.text.strip():
                break
            if isinstance(block, Paragraph):
                subtitle_start_idx += 1
            else:
                break

        if subtitle_start_idx < len(all_blocks):
            block = all_blocks[subtitle_start_idx]
            if isinstance(block, Paragraph):
                para = block
                text = para.text.strip()

                if text and self._get_paragraph_alignment(para) == WD_ALIGN_PARAGRAPH.CENTER:
                    para_font, para_size = self._get_paragraph_font_info(para)
                    if para_font != title_font or para_size != title_size:
                        self._log(f"  > 在块 {subtitle_start_idx + 1} 发现副标题首行（居中且字体字号与标题不同）。")
                        subtitle_indices.append(subtitle_start_idx)

                        subtitle_font, subtitle_size = para_font, para_size
                        idx = subtitle_start_idx + 1

                        while idx < len(all_blocks):
                            block = all_blocks[idx]
                            if not isinstance(block, Paragraph):
                                break

                            para = block
                            text = para.text.strip()

                            if not text:
                                self._log(f"  > 在块 {idx + 1} 遇到空行，副标题识别结束。")
                                break

                            if self._get_paragraph_alignment(para) != WD_ALIGN_PARAGRAPH.CENTER:
                                break

                            para_font, para_size = self._get_paragraph_font_info(para)
                            if para_font == subtitle_font and para_size == subtitle_size:
                                self._log(f"  > 块 {idx + 1} 也是副标题行（居中且字体字号相同）。")
                                subtitle_indices.append(idx)
                                idx += 1
                            else:
                                break

                        self._log(f"  > 共识别到 {len(subtitle_indices)} 行副标题。")

        return title_indices, subtitle_indices

    # ------------------------------------------------------------------
    # Paragraph classification & formatting (the big one)
    # ------------------------------------------------------------------
    def apply_para_format(
        self,
        block,
        font_op,
        para_op,
        is_from_txt: bool,
        apply_color: bool,
    ) -> None:
        """Classify and format a single paragraph block.

        ``font_op`` / ``para_op`` are accepted to match the Pipeline contract
        but the actual formatting is applied directly via this formatter's
        helpers (mirroring the legacy behaviour).
        """
        if not isinstance(block, Paragraph):
            return
        para = block
        if not para.text.strip():
            return

        body_font = self.config.get('body_font', '仿宋_GB2312')
        body_size = self.config.get('body_size', 16)
        h1_font = self.config.get('h1_font', '黑体')
        h1_size = self.config.get('h1_size', 16)
        h2_font = self.config.get('h2_font', '楷体_GB2312')
        h2_size = self.config.get('h2_size', 16)
        attachment_font = self.config.get('attachment_font', '仿宋_GB2312')
        attachment_size = self.config.get('attachment_size', 16)

        original_text = para.text
        text_to_check = para.text.lstrip()
        text_stripped = para.text.strip()
        leading_space_count = len(original_text) - len(text_to_check)
        para_text_preview = text_to_check[:30].replace("\n", " ")

        # Base line spacing for the paragraph
        spacing = para._p.get_or_add_pPr().get_or_add_spacing()
        spacing.set(qn('w:beforeAutospacing'), '0')
        spacing.set(qn('w:afterAutospacing'), '0')
        para.paragraph_format.space_before = Pt(0)
        para.paragraph_format.space_after = Pt(0)
        line_spacing = self.config.get('line_spacing', 28) or 28
        para.paragraph_format.line_spacing = Pt(line_spacing)

        # Paragraphs that carry pictures / embedded objects: format text only
        is_pic = has_drawing_or_pict(para)
        is_embedded_obj = has_embedded_object(para)
        if is_pic or is_embedded_obj:
            log_msg = "图片" if is_pic else "嵌入对象"
            self._log(f"段落: {log_msg} - 仅格式化文字")
            if RE_HEADING_H1.match(text_to_check):
                self._log(f"  > 文字识别为一级标题: \"{para_text_preview}...\"")
                self._apply_font_to_runs(para, h1_font, h1_size, set_color=apply_color)
            elif RE_HEADING_H2.match(text_to_check):
                self._log(f"  > 文字识别为二级标题: \"{para_text_preview}...\"")
                self._apply_font_to_runs(para, h2_font, h2_size, set_color=apply_color)
            elif RE_HEADING_H3.match(text_to_check):
                self._log(f"  > 文字识别为三级标题: \"{para_text_preview}...\"")
                self._apply_font_to_runs(para, body_font, body_size, set_color=apply_color)
            elif RE_HEADING_H4.match(text_to_check):
                self._log(f"  > 文字识别为四级标题: \"{para_text_preview}...\"")
                self._apply_font_to_runs(para, body_font, body_size, set_color=apply_color)
            elif text_to_check:
                self._log(f"  > 文字识别为正文: \"{para_text_preview}...\"")
                self._apply_font_to_runs(para, body_font, body_size, set_color=apply_color)
            return

        # Attachment marker
        is_attachment_enabled = self.config.get('enable_attachment_formatting', True)
        is_attachment_candidate = False
        if is_from_txt:
            if RE_ATTACHMENT.match(text_stripped):
                is_attachment_candidate = True
        else:
            align = self._get_paragraph_alignment(para)
            if align in (WD_ALIGN_PARAGRAPH.LEFT, WD_ALIGN_PARAGRAPH.JUSTIFY, None) and RE_ATTACHMENT.match(text_stripped):
                is_attachment_candidate = True

        if is_attachment_enabled and is_attachment_candidate:
            self._log(f"段落: 附件标识 - \"{para_text_preview}...\"")
            self._strip_leading_whitespace(para)
            self._apply_font_to_runs(para, attachment_font, attachment_size, set_color=apply_color)
            self._reset_pagination_properties(para)
            para.paragraph_format.page_break_before = True
            para.paragraph_format.left_indent = Pt(0)
            para.paragraph_format.first_line_indent = None
            ind = para._p.get_or_add_pPr().get_or_add_ind()
            ind.set(qn("w:firstLineChars"), "0")
            para.alignment = WD_ALIGN_PARAGRAPH.LEFT
            self._format_heading(para, 1)
            return

        if RE_HEADING_H1.match(text_to_check):
            self._log(f"段落: 一级标题 - \"{para_text_preview}...\"")
            self._strip_leading_whitespace(para)
            self._format_heading(para, 1)
            self._apply_font_to_runs(para, h1_font, h1_size, set_color=apply_color)
            self._apply_text_indent_and_align(para)
            self._reset_pagination_properties(para)

        elif RE_HEADING_H2.match(text_to_check):
            self._log(f"段落: 二级标题 - \"{para_text_preview}...\"")
            self._strip_leading_whitespace(para)

            parts = para.text.split('。', 1)

            if len(parts) == 2 and parts[1].strip():
                self._log("  > 检测到二级标题与正文在同一段落，执行段内格式拆分。")
                title_len = len(parts[0]) + 1

                original_runs = []
                for r in para.runs:
                    original_runs.append({
                        'text': r.text, 'bold': r.bold, 'italic': r.italic,
                        'underline': r.underline, 'font_color': r.font.color.rgb,
                    })

                para.clear()

                char_count = 0
                for run_info in original_runs:
                    run_text = run_info['text']
                    run_end_pos = char_count + len(run_text)

                    title_run, body_run, new_run = None, None, None

                    if run_end_pos <= title_len:
                        new_run = para.add_run(run_text)
                        self._set_run_font(new_run, h2_font, h2_size, set_color=apply_color)

                    elif char_count >= title_len:
                        new_run = para.add_run(run_text)
                        self._set_run_font(new_run, body_font, body_size, set_color=apply_color)

                    else:
                        split_index = title_len - char_count
                        title_part = run_text[:split_index]
                        body_part = run_text[split_index:]

                        if title_part:
                            title_run = para.add_run(title_part)
                            self._set_run_font(title_run, h2_font, h2_size, set_color=apply_color)
                        if body_part:
                            body_run = para.add_run(body_part)
                            self._set_run_font(body_run, body_font, body_size, set_color=apply_color)

                    runs_to_format = [r for r in [title_run, body_run] if r] or ([new_run] if new_run else [])
                    for r in runs_to_format:
                        if r:
                            r.bold = run_info['bold']
                            r.italic = run_info['italic']
                            r.underline = run_info['underline']
                            if run_info['font_color']:
                                r.font.color.rgb = run_info['font_color']

                    char_count = run_end_pos

                self._format_heading(para, 2)
                self._apply_text_indent_and_align(para)
                self._reset_pagination_properties(para)

            else:
                match = RE_H2_INLINE_TITLE.match(text_to_check)
                if match and not (text_to_check.startswith('（') and text_to_check.strip().endswith('）')):
                    self._log("  > 已将二级标题的括号统一为中文括号。")
                    for r in para.runs:
                        r.text = r.text.replace('(', '（', 1).replace(')', '）', 1)
                self._format_heading(para, 2)
                self._apply_font_to_runs(para, h2_font, h2_size, set_color=apply_color)
                self._apply_text_indent_and_align(para)
                self._reset_pagination_properties(para)

        elif RE_HEADING_H3.match(text_to_check):
            self._log(f"段落: 三级标题 - \"{para_text_preview}...\"")
            self._strip_leading_whitespace(para)
            self._format_heading(para, 3)
            self._apply_font_to_runs(para, body_font, body_size, set_color=apply_color)
            self._apply_text_indent_and_align(para)
            self._reset_pagination_properties(para)

        elif RE_HEADING_H4.match(text_to_check):
            self._log(f"段落: 四级标题 - \"{para_text_preview}...\"")
            self._strip_leading_whitespace(para)
            self._format_heading(para, 4)
            self._apply_font_to_runs(para, body_font, body_size, set_color=apply_color)
            self._apply_text_indent_and_align(para)
            self._reset_pagination_properties(para)

        elif not is_from_txt:
            para_alignment = self._get_paragraph_alignment(para)
            if para_alignment in (WD_ALIGN_PARAGRAPH.CENTER, WD_ALIGN_PARAGRAPH.RIGHT):
                align_text = "居中" if para_alignment == WD_ALIGN_PARAGRAPH.CENTER else "右对齐"
                self._log(f"段落: {align_text}正文 - 保留原对齐")
                self._apply_font_to_runs(para, body_font, body_size, set_color=apply_color)
                self._reset_pagination_properties(para)
            elif leading_space_count > 5:
                self._log(f"段落: 正文 (保留前导空格) - \"{para_text_preview}...\"")
                self._apply_font_to_runs(para, body_font, body_size, set_color=apply_color)
                para.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
                self._reset_pagination_properties(para)
            elif (para.paragraph_format.first_line_indent is None or para.paragraph_format.first_line_indent.pt == 0) and leading_space_count == 0:
                self._log(f"段落: 正文 (保留0缩进) - \"{para_text_preview}...\"")
                self._apply_font_to_runs(para, body_font, body_size, set_color=apply_color)
                para.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
                self._reset_pagination_properties(para)
            else:
                self._log(f"段落: 正文 (应用标准缩进) - \"{para_text_preview}...\"")
                self._strip_leading_whitespace(para)
                self._apply_font_to_runs(para, body_font, body_size, set_color=apply_color)
                self._apply_text_indent_and_align(para)
                self._reset_pagination_properties(para)
        else:
            self._log(f"段落: 正文 (源自TXT，强制缩进) - \"{para_text_preview}...\"")
            self._strip_leading_whitespace(para)
            self._apply_font_to_runs(para, body_font, body_size, set_color=apply_color)
            self._apply_text_indent_and_align(para)
            self._reset_pagination_properties(para)
