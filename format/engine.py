# -*- coding: utf-8 -*-
"""Core formatting engine.

``FormatEngine`` is the central object that loads a document, classifies and
formats paragraphs/tables, and applies page setup, driven by the unified
``format.config.Config`` schema.

The module is fully self-contained: it only depends on python-docx, the
standard library, and sibling modules under ``format``.
"""

import logging
import os
import re

from docx import Document
from docx.document import Document as _Document
from docx.enum.table import WD_ROW_HEIGHT_RULE
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.oxml.table import CT_Tbl
from docx.oxml.text.paragraph import CT_P
from docx.shared import Cm, Pt, RGBColor
from docx.table import Table
from docx.text.paragraph import Paragraph

from format.converters.office import convert_to_docx

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------
# Module-level regex patterns
# ---------------------------------------------------------------------
RE_HAS_CHINESE = re.compile(r'[\u4e00-\u9fff]')

# Markdown cleanup patterns
RE_MD_IMAGE = re.compile(r'!\[([^\]]*)\]\([^)]+\)')
RE_MD_LINK = re.compile(r'\[([^\]]+)\]\([^)]+\)')
RE_MD_HTML_TAG = re.compile(r'<[^>]+>')
RE_MD_INLINE_CODE = re.compile(r'`([^`]+)`')
RE_MD_BOLD_ASTERISK = re.compile(r'\*\*(.*?)\*\*')
RE_MD_BOLD_UNDERSCORE = re.compile(r'__(.*?)__')
RE_MD_HEADER = re.compile(r'^\s*#+\s+(.*)')
RE_MD_BLOCKQUOTE = re.compile(r'^\s*>\s?(.*)')
RE_MD_HORIZONTAL_RULE = re.compile(r'^\s*[-*_]{3,}\s*$')
RE_MD_UNORDERED_LIST = re.compile(r'^\s*[*+-]\s')
RE_MD_BULLET_WITH_CONTENT = re.compile(r'^(\s*[*+-]\s)(.*)')
RE_MD_EMPHASIS_ASTERISK = re.compile(r'(?<!\\)\*([^\s*][^*]*?)(?<!\\)\*')

# Numeric / currency helpers for table cells
RE_CURRENCY_PREFIX = re.compile(r'^[¥￥$]')
RE_CURRENCY_SUFFIX = re.compile(r'(元|万元|亿元)$')
RE_NUMERIC_TABLE_TEXT = re.compile(r'^[-+]?(?:\d+(?:\.\d+)?|\.\d+)%?$')

# Heading / title classification patterns
CHINESE_NUM_PATTERN = r'[一二三四五六七八九十百千万零]+'
RE_TITLE_H1 = re.compile(r'^' + CHINESE_NUM_PATTERN + r'\s*、')
RE_TITLE_H2 = re.compile(r'^[（\(]' + CHINESE_NUM_PATTERN + r'[）\)]')
RE_HEADING_H1 = re.compile(r'^[一二三四五六七八九十百千万零]+\s*、')
RE_HEADING_H2 = re.compile(r'^[（\(][一二三四五六七八九十百千万零]+[）\)]')
RE_HEADING_H3 = re.compile(r'^\d+\s*[\.．]')
RE_HEADING_H4 = re.compile(r'^[（\(]\d+[）\)]')
RE_ATTACHMENT = re.compile(r'^附件\s*(\d+|[一二三四五六七八九十百千万零]+)?\s*[:：]?$')
RE_H2_INLINE_TITLE = re.compile(r'^[（\(](.+?)[）\)](.*)', re.DOTALL)


class FormatEngine:
    """Core formatting engine.

    Owns a :class:`format.config.Config` instance and exposes the methods that
    :class:`format.pipeline.Pipeline` drives to format a document.
    """

    def __init__(self, config):
        """Initialize the engine.

        Args:
            config: A :class:`format.config.Config` instance providing all
                formatting parameters via ``get``/``get_nested``/``merged``.
        """
        self.config = config

    # ------------------------------------------------------------------
    # Logging helper
    # ------------------------------------------------------------------
    def _log(self, message):
        logger.info(message)

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------
    def run(self, input_path, output_path):
        """Run the full formatting pipeline.

        ``Pipeline`` is imported lazily to avoid a circular import
        (``pipeline`` imports ``FormatEngine`` for type-checking).
        """
        from .pipeline import Pipeline
        return Pipeline(self).run(input_path, output_path)

    # ------------------------------------------------------------------
    # Document loading
    # ------------------------------------------------------------------
    def load_document(self, input_path):
        """Load a source document into a python-docx ``Document``.

        ``.doc``/``.wps``/``.docx`` files are converted to a temporary
        ``.docx`` via :func:`format.converters.office.convert_to_docx`.
        ``.txt``/``.md`` files are read, cleaned and turned into an in-memory
        ``Document``.
        """
        file_ext = os.path.splitext(input_path)[1].lower()

        if file_ext in ('.docx', '.doc', '.wps'):
            self._log("正在加载并转换文档为 .docx ...")
            docx_path = convert_to_docx(input_path)
            return Document(docx_path)

        if file_ext == '.txt':
            self._log("检测到 .txt 文件，正在创建 .docx ...")
            text_content = self._read_text_file(input_path)
            text_content = self._normalize_text_blank_lines(text_content)
            doc = Document()
            for line in text_content.split('\n'):
                doc.add_paragraph(line.strip())
            self._log("TXT 转换完成。")
            return doc

        if file_ext == '.md':
            self._log("检测到 .md 文件，正在清理 Markdown 标记并创建 .docx ...")
            raw_text = self._read_text_file(input_path)
            cleaned_text = self._clean_markdown(raw_text)
            cleaned_text = self._normalize_text_blank_lines(cleaned_text)
            doc = Document()
            for line in cleaned_text.split('\n'):
                doc.add_paragraph(line.strip())
            self._log("Markdown 转换完成。")
            return doc

        raise ValueError(f"不支持的文件格式: {file_ext}")

    def _file_is_text(self, input_path):
        """Return True when the source is a plain-text format (.txt/.md)."""
        return os.path.splitext(input_path)[1].lower() in ('.txt', '.md')

    # ------------------------------------------------------------------
    # Text file reading & Markdown cleanup
    # ------------------------------------------------------------------
    @staticmethod
    def _read_text_file(path):
        """Try multiple encodings to read a text file."""
        for enc in ['utf-8', 'utf-8-sig', 'gbk', 'gb18030']:
            try:
                with open(path, 'r', encoding=enc) as f:
                    return f.read()
            except UnicodeDecodeError:
                continue
        with open(path, 'r', encoding='utf-8', errors='ignore') as f:
            return f.read()

    @staticmethod
    def _clean_markdown(text):
        """Clean Markdown content to plain text, preserving list numbering."""
        if not text:
            return ""

        # Global inline element replacements
        text = RE_MD_IMAGE.sub(r'\1', text)
        text = RE_MD_LINK.sub(r'\1', text)
        text = RE_MD_HTML_TAG.sub('', text)
        text = RE_MD_INLINE_CODE.sub(r'\1', text)
        text = RE_MD_BOLD_ASTERISK.sub(r'\1', text)
        text = RE_MD_BOLD_UNDERSCORE.sub(r'\1', text)

        lines = text.split('\n')
        new_lines = []

        for line in lines:
            cleaned_line = line

            header_match = RE_MD_HEADER.match(cleaned_line)
            if header_match:
                cleaned_line = header_match.group(1)

            blockquote_match = RE_MD_BLOCKQUOTE.match(cleaned_line)
            if blockquote_match:
                cleaned_line = blockquote_match.group(1)

            if RE_MD_HORIZONTAL_RULE.match(cleaned_line):
                cleaned_line = ""

            is_bullet = RE_MD_UNORDERED_LIST.match(cleaned_line)

            def remove_stars(m):
                return m.group(1)

            if is_bullet:
                bullet_match = RE_MD_BULLET_WITH_CONTENT.match(cleaned_line)
                if bullet_match:
                    marker = bullet_match.group(1)
                    content = bullet_match.group(2)
                    content = RE_MD_EMPHASIS_ASTERISK.sub(remove_stars, content)
                    cleaned_line = marker + content
            else:
                cleaned_line = RE_MD_EMPHASIS_ASTERISK.sub(remove_stars, cleaned_line)

            new_lines.append(cleaned_line)

        return '\n'.join(new_lines)

    @staticmethod
    def _remove_blank_lines_from_text(text, keep_single_blank_lines=False):
        """Normalize blank lines: 2+ -> 1; single deleted by default."""
        if not text:
            return text

        lines = text.split('\n')
        result = []
        blank_count = 0

        for line in lines:
            if line.strip() == '':
                blank_count += 1
            else:
                if blank_count >= 2 or (blank_count == 1 and keep_single_blank_lines):
                    result.append('')
                result.append(line)
                blank_count = 0

        if blank_count >= 2 or (blank_count == 1 and keep_single_blank_lines):
            result.append('')

        return '\n'.join(result)

    def _normalize_text_blank_lines(self, text):
        mode = self.config.get('blank_line_mode', 'delete_single')
        if mode in ('preserve', 'none', '不改动任何空行'):
            return text
        keep_single = mode in (
            'keep_single', 'compress', '保留单个空行，多个空行保留至1个空行',
        )
        if not self.config.get('remove_blank_lines', True):
            keep_single = True
        return self._remove_blank_lines_from_text(
            text, keep_single_blank_lines=keep_single,
        )

    # ------------------------------------------------------------------
    # Conservative Chinese punctuation normalization
    # ------------------------------------------------------------------
    @staticmethod
    def _paragraph_has_ooxml(para, tag_name):
        return para._p.find('.//' + qn(tag_name)) is not None

    @classmethod
    def _has_field_codes(cls, para):
        return (
            cls._paragraph_has_ooxml(para, 'w:fldChar')
            or cls._paragraph_has_ooxml(para, 'w:instrText')
        )

    @classmethod
    def _has_drawing_or_pict(cls, para):
        return (
            cls._paragraph_has_ooxml(para, 'w:drawing')
            or cls._paragraph_has_ooxml(para, 'w:pict')
        )

    @classmethod
    def _has_embedded_object(cls, para):
        return cls._paragraph_has_ooxml(para, 'w:object')

    def _has_drawing(self, block):
        """Return True when a paragraph block carries a drawing/picture."""
        if isinstance(block, Paragraph):
            return self._has_drawing_or_pict(block)
        return False

    @staticmethod
    def _has_chinese(text):
        return bool(RE_HAS_CHINESE.search(text or ''))

    @staticmethod
    def _is_digit_or_latin(ch):
        if not ch:
            return False
        code = ord(ch)
        return (
            ch.isdigit()
            or ('A' <= ch <= 'Z')
            or ('a' <= ch <= 'z')
            or (0xFF10 <= code <= 0xFF19)
            or (0xFF21 <= code <= 0xFF3A)
            or (0xFF41 <= code <= 0xFF5A)
        )

    @staticmethod
    def _prev_non_space(text, index):
        i = index - 1
        while i >= 0 and text[i].isspace():
            i -= 1
        return text[i] if i >= 0 else ''

    @staticmethod
    def _next_non_space(text, index):
        i = index + 1
        while i < len(text) and text[i].isspace():
            i += 1
        return text[i] if i < len(text) else ''

    @classmethod
    def _is_after_digit_or_latin(cls, text, index):
        return cls._is_digit_or_latin(cls._prev_non_space(text, index))

    @classmethod
    def _is_before_digit_or_latin(cls, text, index):
        return cls._is_digit_or_latin(cls._next_non_space(text, index))

    @classmethod
    def _normalize_ellipsis(cls, text):
        dot_chars = {'.', '．', '。'}
        chars = []
        i = 0
        while i < len(text):
            ch = text[i]
            if ch not in dot_chars:
                chars.append(ch)
                i += 1
                continue

            j = i + 1
            while j < len(text) and text[j] in dot_chars:
                j += 1

            if j - i >= 3 and not cls._is_after_digit_or_latin(text, i):
                chars.append('……')
            else:
                chars.append(text[i:j])
            i = j

        return ''.join(chars)

    @classmethod
    def _normalize_simple_punctuation(cls, text):
        replacements = {
            ',': '，',
            '.': '。',
            '．': '。',
            ';': '；',
            ':': '：',
            '?': '？',
            '!': '！',
        }
        chars = list(text)
        for i, ch in enumerate(chars):
            if ch not in replacements:
                continue
            if ch in {'.', '．', '。'}:
                prev_is_dot = i > 0 and text[i - 1] in {'.', '．', '。'}
                next_is_dot = i + 1 < len(text) and text[i + 1] in {'.', '．', '。'}
                if prev_is_dot or next_is_dot:
                    continue
            if cls._is_after_digit_or_latin(text, i):
                continue
            chars[i] = replacements[ch]
        return ''.join(chars)

    @classmethod
    def _normalize_bracket_pairs(cls, text):
        pair_sets = [
            ({'(': '（', '（': '（'}, {')': '）', '）': '）'}),
            ({'[': '［', '［': '［'}, {']': '］', '］': '］'}),
        ]
        result = text

        for open_chars, close_chars in pair_sets:
            chars = list(result)
            stack = []
            for i, ch in enumerate(chars):
                if ch in open_chars:
                    stack.append(i)
                elif ch in close_chars and stack:
                    open_index = stack.pop()
                    content = ''.join(chars[open_index + 1:i])
                    if not cls._has_chinese(content):
                        continue
                    if cls._is_after_digit_or_latin(result, open_index):
                        continue
                    if cls._is_before_digit_or_latin(result, i):
                        continue
                    chars[open_index] = open_chars[chars[open_index]]
                    chars[i] = close_chars[ch]
            result = ''.join(chars)

        return result

    @classmethod
    def _normalize_quote_pairs(cls, text, quote_chars, left_quote, right_quote, skip_inner_latin=False):
        chars = list(text)
        open_index = None

        for i, ch in enumerate(chars):
            if ch not in quote_chars:
                continue

            prev_is_latin = cls._is_after_digit_or_latin(text, i)
            next_is_latin = cls._is_before_digit_or_latin(text, i)
            if skip_inner_latin and prev_is_latin and next_is_latin:
                continue

            if open_index is None:
                if prev_is_latin:
                    continue
                open_index = i
                continue

            content = ''.join(chars[open_index + 1:i])
            should_normalize = (
                cls._has_chinese(content)
                and not cls._is_after_digit_or_latin(text, open_index)
                and not cls._is_before_digit_or_latin(text, i)
            )
            if should_normalize:
                chars[open_index] = left_quote
                chars[i] = right_quote
            open_index = None

        return ''.join(chars)

    @classmethod
    def _normalize_symbols_in_text(cls, text):
        if not text or not cls._has_chinese(text):
            return text

        result = cls._normalize_bracket_pairs(text)
        result = cls._normalize_ellipsis(result)
        result = cls._normalize_quote_pairs(
            result,
            {'"', '“', '”', '„', '‟', '「', '」'},
            '“',
            '”',
        )
        result = cls._normalize_quote_pairs(
            result,
            {"'", '‘', '’', '‚', '‛'},
            '‘',
            '’',
            skip_inner_latin=True,
        )
        result = cls._normalize_simple_punctuation(result)
        return result

    @staticmethod
    def _redistribute_text_to_runs(runs, new_full_text):
        if not runs:
            return

        run_lengths = [len(run.text) for run in runs]
        if len(new_full_text) == sum(run_lengths):
            pos = 0
            for run, length in zip(runs, run_lengths):
                run.text = new_full_text[pos:pos + length]
                pos += length
            return

        runs[0].text = new_full_text
        for run in runs[1:]:
            run.text = ''

    def _normalize_paragraph_symbols(self, para):
        text = para.text
        if not text.strip() or not para.runs:
            return False
        if self._has_field_codes(para):
            return False

        normalized = self._normalize_symbols_in_text(text)
        if normalized == text:
            return False

        self._redistribute_text_to_runs(para.runs, normalized)
        return True

    def _normalize_document_symbols(self, doc):
        """Normalize Chinese punctuation across paragraphs and table cells."""
        changes = 0

        for para in doc.paragraphs:
            if self._normalize_paragraph_symbols(para):
                changes += 1

        for table in doc.tables:
            for row in table.rows:
                for cell in row.cells:
                    for para in cell.paragraphs:
                        if self._normalize_paragraph_symbols(para):
                            changes += 1

        return changes

    # ------------------------------------------------------------------
    # Block iteration & font helpers
    # ------------------------------------------------------------------
    def _iter_block_items(self, parent):
        """Yield Paragraph and Table objects in document order."""
        parent_elm = parent.element.body if isinstance(parent, _Document) else parent._tc
        for child in parent_elm.iterchildren():
            if isinstance(child, CT_P):
                yield Paragraph(child, parent)
            elif isinstance(child, CT_Tbl):
                yield Table(child, parent)

    def _set_run_font(self, run, font_name, size_pt, set_color=False):
        """Set western + east-asian font, size, and optionally color on a run."""
        run.font.size = Pt(size_pt)
        if set_color:
            run.font.color.rgb = RGBColor(0, 0, 0)
        rPr = run._r.get_or_add_rPr()
        rFonts = rPr.get_or_add_rFonts()
        for theme_attr in ('w:eastAsiaTheme', 'w:asciiTheme', 'w:hAnsiTheme', 'w:cstheme', 'w:csTheme'):
            rFonts.attrib.pop(qn(theme_attr), None)
        rFonts.set(qn('w:eastAsia'), font_name)
        # Western font: use a custom english font only when explicitly enabled
        en_font = font_name
        if self.config.get('use_custom_english_font', False):
            en_font = self.config.get('english_font') or font_name
        run.font.name = en_font
        rFonts.set(qn('w:ascii'), en_font)
        rFonts.set(qn('w:hAnsi'), en_font)

    def _apply_font_to_runs(self, para, font_name, size_pt, set_color=False):
        for run in para.runs:
            self._set_run_font(run, font_name, size_pt, set_color=set_color)

    def _get_paragraph_font_info(self, para):
        """Return (font_name, font_size_pt) of the first non-empty run."""
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

    def _strip_leading_whitespace(self, para):
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

    def _reset_pagination_properties(self, para):
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

    def _set_outline_level(self, para, level):
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

    def _format_heading(self, para, level):
        """Set the outline level of a paragraph (1-9)."""
        if not self.config.get('set_outline', True):
            return
        text_preview = para.text.strip()[:30].replace("\n", " ")
        original_level = self._set_outline_level(para, level)
        if original_level is not None:
            self._log(f"  > 大纲级别: Lv{original_level + 1} → Lv{level} (覆盖) - \"{text_preview}...\"")
        else:
            self._log(f"  > 大纲级别: 无 → Lv{level} (新设) - \"{text_preview}...\"")

    def _apply_text_indent_and_align(self, para):
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
    def _find_title_paragraphs(self, doc, is_from_txt, start_index=0):
        """Find the main title and subtitle paragraph block indices.

        Returns:
            (title_indices, subtitle_indices)
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
    # Paragraph classification & formatting
    # ------------------------------------------------------------------
    def _apply_para_format(self, block, font_op, para_op, is_from_txt, apply_color):
        """Classify and format a single paragraph block.

        ``font_op``/``para_op`` are accepted to match the Pipeline contract;
        the formatting is applied directly via the engine helpers.
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
        is_pic = self._has_drawing_or_pict(para)
        is_embedded_obj = self._has_embedded_object(para)
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

    # ------------------------------------------------------------------
    # Table formatting
    # ------------------------------------------------------------------
    def _get_or_add_table_pr(self, table):
        tbl = table._tbl
        tbl_pr = tbl.tblPr
        if tbl_pr is None:
            tbl_pr = OxmlElement('w:tblPr')
            tbl.insert(0, tbl_pr)
        return tbl_pr

    def _set_table_borders(self, table, size_pt=0.5, color="000000"):
        size = max(1, int(float(size_pt) * 8))
        tbl_pr = self._get_or_add_table_pr(table)
        borders = tbl_pr.find(qn('w:tblBorders'))
        if borders is None:
            borders = OxmlElement('w:tblBorders')
            tbl_pr.append(borders)
        else:
            for child in list(borders):
                borders.remove(child)

        for edge in ('top', 'left', 'bottom', 'right', 'insideH', 'insideV'):
            elem = OxmlElement(f'w:{edge}')
            elem.set(qn('w:val'), 'single')
            elem.set(qn('w:sz'), str(size))
            elem.set(qn('w:space'), '0')
            elem.set(qn('w:color'), color)
            borders.append(elem)

    def _set_cell_borders(self, cell, size_pt=0.5, color="000000"):
        size = max(1, int(float(size_pt) * 8))
        tc = cell._tc
        tc_pr = tc.tcPr
        if tc_pr is None:
            tc_pr = OxmlElement('w:tcPr')
            tc.insert(0, tc_pr)

        borders = tc_pr.find(qn('w:tcBorders'))
        if borders is None:
            borders = OxmlElement('w:tcBorders')
            tc_pr.append(borders)
        else:
            for child in list(borders):
                borders.remove(child)

        for edge in ('top', 'left', 'bottom', 'right'):
            elem = OxmlElement(f'w:{edge}')
            elem.set(qn('w:val'), 'single')
            elem.set(qn('w:sz'), str(size))
            elem.set(qn('w:space'), '0')
            elem.set(qn('w:color'), color)
            borders.append(elem)

    def _set_table_cell_margins(self, table, top_cm=0.0, bottom_cm=0.0, left_cm=0.05, right_cm=0.05):
        tbl_pr = self._get_or_add_table_pr(table)
        cell_mar = tbl_pr.find(qn('w:tblCellMar'))
        if cell_mar is None:
            cell_mar = OxmlElement('w:tblCellMar')
            tbl_pr.append(cell_mar)

        def set_side(tag, cm_value):
            node = cell_mar.find(qn(f'w:{tag}'))
            if node is None:
                node = OxmlElement(f'w:{tag}')
                cell_mar.append(node)
            node.set(qn('w:type'), 'dxa')
            node.set(qn('w:w'), str(int(Cm(float(cm_value)).twips)))

        set_side('top', top_cm)
        set_side('bottom', bottom_cm)
        set_side('left', left_cm)
        set_side('right', right_cm)

    def _set_table_width_percent(self, table, percent=100):
        percent = max(1, min(100, int(float(percent))))
        tbl_pr = self._get_or_add_table_pr(table)
        tbl_w = tbl_pr.find(qn('w:tblW'))
        if tbl_w is None:
            tbl_w = OxmlElement('w:tblW')
            tbl_pr.append(tbl_w)
        tbl_w.set(qn('w:type'), 'pct')
        tbl_w.set(qn('w:w'), str(percent * 50))

    def _set_table_indent(self, table, indent_twips=0):
        tbl_pr = self._get_or_add_table_pr(table)
        tbl_ind = tbl_pr.find(qn('w:tblInd'))
        if tbl_ind is None:
            tbl_ind = OxmlElement('w:tblInd')
            tbl_pr.append(tbl_ind)
        tbl_ind.set(qn('w:type'), 'dxa')
        tbl_ind.set(qn('w:w'), str(int(indent_twips)))

    @staticmethod
    def _table_text_weight(text):
        weight = 0.0
        for ch in text:
            weight += 0.5 if ord(ch) < 128 else 1.0
        return weight

    @staticmethod
    def _normalize_table_pcts(weights, min_pct, max_pct):
        total = sum(weights) or 1.0
        pcts = [w / total * 100 for w in weights]
        for i, value in enumerate(pcts):
            if value < min_pct:
                pcts[i] = min_pct
            elif value > max_pct:
                pcts[i] = max_pct
        total = sum(pcts) or 1.0
        return [value / total * 100 for value in pcts]

    def _set_table_col_widths_by_content(self, table, min_pct=8, max_pct=45):
        if not table.rows:
            return
        col_count = max(len(row.cells) for row in table.rows)
        if col_count == 0:
            return

        min_pct = max(1.0, float(min_pct))
        max_pct = max(min_pct, float(max_pct))
        max_weights = [1.0] * col_count
        for row in table.rows:
            for col_idx, cell in enumerate(row.cells):
                text = ''.join(p.text for p in cell.paragraphs).strip()
                if text:
                    max_weights[col_idx] = max(max_weights[col_idx], self._table_text_weight(text))

        pcts = self._normalize_table_pcts(max_weights, min_pct, max_pct)
        tbl = table._tbl
        tbl_grid = tbl.tblGrid
        if tbl_grid is None:
            tbl_grid = OxmlElement('w:tblGrid')
            tbl.insert(0, tbl_grid)
        else:
            for child in list(tbl_grid):
                tbl_grid.remove(child)

        for pct in pcts:
            grid_col = OxmlElement('w:gridCol')
            grid_col.set(qn('w:w'), str(int(pct * 50)))
            tbl_grid.append(grid_col)

        for row in table.rows:
            for col_idx, cell in enumerate(row.cells):
                tc = cell._tc
                tc_pr = tc.tcPr
                if tc_pr is None:
                    tc_pr = OxmlElement('w:tcPr')
                    tc.insert(0, tc_pr)
                tc_w = tc_pr.find(qn('w:tcW'))
                if tc_w is None:
                    tc_w = OxmlElement('w:tcW')
                    tc_pr.append(tc_w)
                tc_w.set(qn('w:type'), 'pct')
                tc_w.set(qn('w:w'), str(int(pcts[col_idx] * 50)))

    @staticmethod
    def _is_numeric_table_text(text):
        text = (text or '').strip()
        if not text:
            return False
        text = text.replace(',', '').replace('，', '').replace('％', '%')
        text = RE_CURRENCY_PREFIX.sub('', text)
        text = RE_CURRENCY_SUFFIX.sub('', text)
        return RE_NUMERIC_TABLE_TEXT.match(text) is not None

    @staticmethod
    def _is_short_table_text(text, max_len=4):
        text = (text or '').strip()
        return 0 < len(text) <= int(max_len)

    @staticmethod
    def _config_float(config, key, default):
        value = config.get(key, default)
        try:
            if value == '':
                return default
            return float(value)
        except (TypeError, ValueError):
            return default

    def _format_tables(self, doc, apply_color=True):
        """Format every table: borders, widths, row height, fonts, alignment."""
        if not self.config.get('enable_table_formatting', True):
            self._log("表格自动调整未启用，跳过表格内容格式化。")
            return

        tables = list(doc.tables)
        if not tables:
            self._log("未发现表格，跳过表格内容格式化。")
            return

        table_font = self.config.get('table_font', self.config.get('body_font', '仿宋_GB2312'))
        table_header_font = self.config.get('table_header_font', table_font)
        table_size = self._config_float(self.config, 'table_size', self.config.get('body_size', 12))
        table_line_spacing = self._config_float(self.config, 'table_line_spacing', 22)
        row_height_cm = self._config_float(self.config, 'table_row_height_cm', 0.7)
        border_size_pt = self._config_float(self.config, 'table_border_size_pt', 0.5)
        width_percent = self._config_float(self.config, 'table_width_percent', 100)
        col_min_pct = self._config_float(self.config, 'table_col_min_pct', 8)
        col_max_pct = self._config_float(self.config, 'table_col_max_pct', 45)
        short_text_len = self._config_float(self.config, 'table_short_text_len', 4)
        auto_col_width = self.config.get('table_auto_col_width', True)
        header_bold = self.config.get('table_header_bold', True)
        smart_align = self.config.get('table_smart_align', False)
        unified_borders = self.config.get('table_unified_borders', True)

        self._log(f"开始格式化表格内容（共 {len(tables)} 个）...")
        for table_idx, table in enumerate(tables, start=1):
            self._log(f"  > 表格 {table_idx}: 调整宽度、行高、字体和单元格格式")
            table.autofit = not auto_col_width
            self._set_table_width_percent(table, width_percent)
            self._set_table_indent(table, 0)
            self._set_table_cell_margins(table)
            if unified_borders:
                self._set_table_borders(table, size_pt=border_size_pt)
            if auto_col_width:
                self._set_table_col_widths_by_content(table, min_pct=col_min_pct, max_pct=col_max_pct)

            serial_col_idx = None
            if table.rows:
                for col_idx, cell in enumerate(table.rows[0].cells):
                    head_text = ''.join(p.text for p in cell.paragraphs).strip()
                    if '序号' in head_text or head_text == '序':
                        serial_col_idx = col_idx
                        break

            for row_idx, row in enumerate(table.rows):
                if row_height_cm > 0:
                    row.height = Cm(row_height_cm)
                    row.height_rule = WD_ROW_HEIGHT_RULE.AT_LEAST

                for col_idx, cell in enumerate(row.cells):
                    if unified_borders:
                        self._set_cell_borders(cell, size_pt=border_size_pt)

                    cell_text = ''.join(p.text for p in cell.paragraphs).strip()
                    for para in cell.paragraphs:
                        if para.text.strip():
                            for run in para.runs:
                                font_name = table_header_font if row_idx == 0 else table_font
                                self._set_run_font(run, font_name, table_size, set_color=apply_color)
                                if row_idx == 0 and header_bold:
                                    run.font.bold = True

                        para.paragraph_format.first_line_indent = Pt(0)
                        para.paragraph_format.space_before = Pt(0)
                        para.paragraph_format.space_after = Pt(0)
                        if table_line_spacing > 0:
                            para.paragraph_format.line_spacing_rule = WD_LINE_SPACING.EXACTLY
                            para.paragraph_format.line_spacing = Pt(table_line_spacing)
                        else:
                            para.paragraph_format.line_spacing_rule = WD_LINE_SPACING.SINGLE

                        if smart_align:
                            if row_idx == 0:
                                para.alignment = WD_ALIGN_PARAGRAPH.CENTER
                            elif '合计' in cell_text or '总计' in cell_text:
                                para.alignment = WD_ALIGN_PARAGRAPH.CENTER
                            elif serial_col_idx is not None and col_idx == serial_col_idx:
                                para.alignment = WD_ALIGN_PARAGRAPH.CENTER
                            elif self._is_numeric_table_text(cell_text):
                                para.alignment = WD_ALIGN_PARAGRAPH.RIGHT
                            elif self._is_short_table_text(cell_text, short_text_len):
                                para.alignment = WD_ALIGN_PARAGRAPH.CENTER
                            else:
                                para.alignment = WD_ALIGN_PARAGRAPH.LEFT

    # ------------------------------------------------------------------
    # Page setup & page numbers
    # ------------------------------------------------------------------
    def _create_page_number(self, paragraph):
        """Append a page-number field to a footer paragraph.

        The field style is driven by ``page_number_style`` ('dash',
        'page_text', 'page_total', or plain) and the font/size by
        ``page_number_font``/``page_number_size``.
        """
        font_name = self.config.get('page_number_font', '宋体')
        font_size = self.config.get('page_number_size', 14)
        style = self.config.get('page_number_style', 'dash')

        def _field(instruction):
            run = paragraph.add_run()
            self._set_run_font(run, font_name, font_size, set_color=True)
            begin = OxmlElement('w:fldChar')
            begin.set(qn('w:fldCharType'), 'begin')
            instr_text = OxmlElement('w:instrText')
            instr_text.set(qn('xml:space'), 'preserve')
            instr_text.text = instruction
            end = OxmlElement('w:fldChar')
            end.set(qn('w:fldCharType'), 'end')
            run._r.extend([begin, instr_text, end])

        if style == 'dash':
            self._set_run_font(paragraph.add_run('— '), font_name, font_size, set_color=True)
            _field(' PAGE ')
            self._set_run_font(paragraph.add_run(' —'), font_name, font_size, set_color=True)
        elif style == 'page_text':
            self._set_run_font(paragraph.add_run('第 '), font_name, font_size, set_color=True)
            _field(' PAGE ')
            self._set_run_font(paragraph.add_run(' 页'), font_name, font_size, set_color=True)
        elif style == 'page_total':
            _field(' PAGE ')
            self._set_run_font(paragraph.add_run(' / '), font_name, font_size, set_color=True)
            _field(' NUMPAGES ')
        else:
            _field(' PAGE ')

    def _apply_page_setup(self, doc, is_from_txt=False):
        """Apply page margins, A4 size and footer page numbers."""
        self._log("正在应用页面边距和页码设置...")

        margin_top = self.config.get('margin_top', 3.7)
        margin_bottom = self.config.get('margin_bottom', 3.5)
        margin_left = self.config.get('margin_left', 2.8)
        margin_right = self.config.get('margin_right', 2.6)
        footer_distance = self.config.get('footer_distance', 1.5)
        should_set_a4 = is_from_txt or self.config.get('force_a4', False)
        add_page_num = self.config.get('page_number', True)
        position = self.config.get('page_number_position', 'outside')

        for section in doc.sections:
            section.top_margin = Cm(margin_top)
            section.bottom_margin = Cm(margin_bottom)
            section.left_margin = Cm(margin_left)
            section.right_margin = Cm(margin_right)
            section.footer_distance = Cm(footer_distance)

            if should_set_a4:
                section.page_width = Cm(21)
                section.page_height = Cm(29.7)

            if not add_page_num:
                continue

            if position == 'outside':
                doc.settings.odd_and_even_pages_header_footer = True
                section.odd_and_even_pages_header_footer = True

                footer_odd = section.footer
                p_odd = footer_odd.paragraphs[0] if footer_odd.paragraphs else footer_odd.add_paragraph()
                p_odd.clear()
                p_odd.alignment = WD_ALIGN_PARAGRAPH.RIGHT
                self._create_page_number(p_odd)

                footer_even = section.even_page_footer
                p_even = footer_even.paragraphs[0] if footer_even.paragraphs else footer_even.add_paragraph()
                p_even.clear()
                p_even.alignment = WD_ALIGN_PARAGRAPH.LEFT
                self._create_page_number(p_even)
            else:
                align_map = {
                    'center': WD_ALIGN_PARAGRAPH.CENTER,
                    'left': WD_ALIGN_PARAGRAPH.LEFT,
                    'right': WD_ALIGN_PARAGRAPH.RIGHT,
                }
                align = align_map.get(position, WD_ALIGN_PARAGRAPH.CENTER)
                footer = section.footer
                p = footer.paragraphs[0] if footer.paragraphs else footer.add_paragraph()
                p.clear()
                p.alignment = align
                self._create_page_number(p)

        if should_set_a4:
            self._log("  > 已将页面大小设置为 A4。")
