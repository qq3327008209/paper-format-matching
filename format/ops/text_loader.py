"""Text-file reading and Markdown cleanup.

Refactored from the legacy ``FormatEngine`` text-loading logic so the engine
can delegate to a dedicated helper instead of carrying ~90 lines of
text-preparation code.
"""

from __future__ import annotations

import re


# Markdown cleanup patterns.  These were module-level constants in the
# legacy ``format.engine`` module; they are moved here because they are only
# used by the Markdown cleaner.
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


class TextLoader:
    """Read text files and clean Markdown content.

    Refactored from the text-loading portion of the legacy ``FormatEngine``.
    """

    # ------------------------------------------------------------------
    # File reading
    # ------------------------------------------------------------------
    @staticmethod
    def read_text_file(path: str) -> str:
        """Try multiple encodings to read a text file.

        Encodings are tried in order: utf-8, utf-8-sig, gbk, gb18030.
        If all fail, the file is re-read with ``errors='ignore'``.
        """
        for enc in ('utf-8', 'utf-8-sig', 'gbk', 'gb18030'):
            try:
                with open(path, 'r', encoding=enc) as f:
                    return f.read()
            except UnicodeDecodeError:
                continue
        with open(path, 'r', encoding='utf-8', errors='ignore') as f:
            return f.read()

    # ------------------------------------------------------------------
    # Markdown cleanup
    # ------------------------------------------------------------------
    @staticmethod
    def clean_markdown(text: str) -> str:
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

    # ------------------------------------------------------------------
    # Blank-line normalization
    # ------------------------------------------------------------------
    @staticmethod
    def remove_blank_lines_from_text(
        text: str,
        keep_single_blank_lines: bool = False,
    ) -> str:
        """Normalize blank lines: 2+ consecutive blanks -> 1.

        By default single blank lines are removed; pass
        ``keep_single_blank_lines=True`` to preserve them.
        """
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
