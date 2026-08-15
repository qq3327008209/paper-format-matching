"""Chinese punctuation and symbol normalization.

Refactored from the legacy ``FormatEngine`` symbol-normalization logic.  The
module exposes both module-level functions (for use by other ``format.ops``
modules) and a :class:`SymbolNormalizer` facade that mirrors the original
``FormatEngine`` method names so the engine can delegate to it transparently.
"""

from __future__ import annotations

import re

from docx.oxml.ns import qn

from format.ops.space import redistribute_text_to_runs


# ---------------------------------------------------------------------------
# Module-level OOXML / character inspection helpers
# These are used by both SymbolNormalizer and ParagraphFormatter, so they
# live at module level to avoid circular imports.
# ---------------------------------------------------------------------------

def paragraph_has_ooxml(para, tag_name: str) -> bool:
    """Return True when ``para``'s XML tree contains ``tag_name``."""
    return para._p.find('.//' + qn(tag_name)) is not None


def has_field_codes(para) -> bool:
    """Return True when the paragraph carries Word field codes."""
    return (
        paragraph_has_ooxml(para, 'w:fldChar')
        or paragraph_has_ooxml(para, 'w:instrText')
    )


def has_drawing_or_pict(para) -> bool:
    """Return True when the paragraph carries a drawing or pict element."""
    return (
        paragraph_has_ooxml(para, 'w:drawing')
        or paragraph_has_ooxml(para, 'w:pict')
    )


def has_embedded_object(para) -> bool:
    """Return True when the paragraph carries an embedded OLE object."""
    return paragraph_has_ooxml(para, 'w:object')


RE_HAS_CHINESE = re.compile(r'[一-鿿]')


def has_chinese(text: str) -> bool:
    """Return True when ``text`` contains any CJK character."""
    return bool(RE_HAS_CHINESE.search(text or ''))


def is_digit_or_latin(ch: str) -> bool:
    """Return True for ASCII/fullwidth digits and latin letters."""
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


def prev_non_space(text: str, index: int) -> str:
    """Return the nearest non-space character before ``index`` (or '')."""
    i = index - 1
    while i >= 0 and text[i].isspace():
        i -= 1
    return text[i] if i >= 0 else ''


def next_non_space(text: str, index: int) -> str:
    """Return the nearest non-space character after ``index`` (or '')."""
    i = index + 1
    while i < len(text) and text[i].isspace():
        i += 1
    return text[i] if i < len(text) else ''


def is_after_digit_or_latin(text: str, index: int) -> bool:
    """Return True when the char immediately before ``index`` is digit/latin."""
    return is_digit_or_latin(prev_non_space(text, index))


def is_before_digit_or_latin(text: str, index: int) -> bool:
    """Return True when the char immediately after ``index`` is digit/latin."""
    return is_digit_or_latin(next_non_space(text, index))


# ---------------------------------------------------------------------------
# SymbolNormalizer
# ---------------------------------------------------------------------------

class SymbolNormalizer:
    """Normalize Chinese punctuation, ellipsis, brackets and quotes.

    Refactored from the symbol-normalization portion of the legacy
    ``FormatEngine``.  Each instance is stateless; the class exists to mirror
    the original method-based API so ``FormatEngine`` can delegate to it.
    """

    # ------------------------------------------------------------------
    # Ellipsis
    # ------------------------------------------------------------------
    @staticmethod
    def normalize_ellipsis(text: str) -> str:
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

            if j - i >= 3 and not is_after_digit_or_latin(text, i):
                chars.append('……')
            else:
                chars.append(text[i:j])
            i = j

        return ''.join(chars)

    # ------------------------------------------------------------------
    # Simple punctuation (, . ; : ? !)
    # ------------------------------------------------------------------
    @staticmethod
    def normalize_simple_punctuation(text: str) -> str:
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
            if is_after_digit_or_latin(text, i):
                continue
            chars[i] = replacements[ch]
        return ''.join(chars)

    # ------------------------------------------------------------------
    # Bracket pairing
    # ------------------------------------------------------------------
    @staticmethod
    def normalize_bracket_pairs(text: str) -> str:
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
                    if not has_chinese(content):
                        continue
                    if is_after_digit_or_latin(result, open_index):
                        continue
                    if is_before_digit_or_latin(result, i):
                        continue
                    chars[open_index] = open_chars[chars[open_index]]
                    chars[i] = close_chars[ch]
            result = ''.join(chars)

        return result

    # ------------------------------------------------------------------
    # Quote pairing
    # ------------------------------------------------------------------
    @staticmethod
    def normalize_quote_pairs(
        text: str,
        quote_chars,
        left_quote: str,
        right_quote: str,
        skip_inner_latin: bool = False,
    ) -> str:
        chars = list(text)
        open_index = None

        for i, ch in enumerate(chars):
            if ch not in quote_chars:
                continue

            prev_is_latin = is_after_digit_or_latin(text, i)
            next_is_latin = is_before_digit_or_latin(text, i)
            if skip_inner_latin and prev_is_latin and next_is_latin:
                continue

            if open_index is None:
                if prev_is_latin:
                    continue
                open_index = i
                continue

            content = ''.join(chars[open_index + 1:i])
            should_normalize = (
                has_chinese(content)
                and not is_after_digit_or_latin(text, open_index)
                and not is_before_digit_or_latin(text, i)
            )
            if should_normalize:
                chars[open_index] = left_quote
                chars[i] = right_quote
            open_index = None

        return ''.join(chars)

    # ------------------------------------------------------------------
    # Full symbol pipeline
    # ------------------------------------------------------------------
    @staticmethod
    def normalize_symbols_in_text(text: str) -> str:
        if not text or not has_chinese(text):
            return text

        result = SymbolNormalizer.normalize_bracket_pairs(text)
        result = SymbolNormalizer.normalize_ellipsis(result)
        # Double quotes: match ASCII " plus the common curly variants and
        # convert to Chinese-style curly quotes (U+201C / U+201D).
        # All non-ASCII characters are constructed via chr() so the source
        # file contains only ASCII string literals (no tokenizer ambiguity).
        _dq = [
            '"',
            chr(0x201C), chr(0x201D),   # " "
            chr(0x201E), chr(0x201F),   # „ ‟
            chr(0x300C), chr(0x300D),   # 「 」
        ]
        _ldq, _rdq = chr(0x201C), chr(0x201D)
        result = SymbolNormalizer.normalize_quote_pairs(
            result, _dq, _ldq, _rdq,
        )
        # Single quotes: match ASCII ' plus curly variants, convert to
        # U+2018 / U+2019.  skip_inner_latin avoids corrupting things like
        # "don't" inside otherwise-Chinese text.
        _sq = [
            "'",
            chr(0x2018), chr(0x2019),   # ' '
            chr(0x201A), chr(0x201B),   # ‚ ‛
        ]
        _lsq, _rsq = chr(0x2018), chr(0x2019)
        result = SymbolNormalizer.normalize_quote_pairs(
            result, _sq, _lsq, _rsq, skip_inner_latin=True,
        )
        result = SymbolNormalizer.normalize_simple_punctuation(result)
        return result

    # ------------------------------------------------------------------
    # Paragraph / document level
    # ------------------------------------------------------------------
    def normalize_paragraph_symbols(self, para) -> bool:
        """Normalize symbols in a single paragraph.  Returns True if changed."""
        text = para.text
        if not text.strip() or not para.runs:
            return False
        if has_field_codes(para):
            return False

        normalized = self.normalize_symbols_in_text(text)
        if normalized == text:
            return False

        redistribute_text_to_runs(para.runs, normalized)
        return True

    def normalize_document_symbols(self, doc) -> int:
        """Normalize Chinese punctuation across paragraphs and table cells.

        Returns the number of paragraphs whose text was changed.
        """
        changes = 0

        for para in doc.paragraphs:
            if self.normalize_paragraph_symbols(para):
                changes += 1

        for table in doc.tables:
            for row in table.rows:
                for cell in row.cells:
                    for para in cell.paragraphs:
                        if self.normalize_paragraph_symbols(para):
                            changes += 1

        return changes
