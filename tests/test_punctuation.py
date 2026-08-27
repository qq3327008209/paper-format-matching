"""Tests for the Chinese punctuation normalisation helpers.

Covers: ellipsis, bracket pairing, quote pairing, simple punctuation,
and the full ``normalize_symbols_in_text`` pipeline.  Boundary cases include
nested quotes, latin-adjacent punctuation, and content-within-brackets.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from format.ops.symbol_normalizer import SymbolNormalizer


# ---- Ellipsis ----

def test_three_dots_become_ellipsis():
    assert SymbolNormalizer.normalize_ellipsis("等等...") == "等等……"


def test_six_dots_become_single_ellipsis():
    assert SymbolNormalizer.normalize_ellipsis("等等......") == "等等……"


def test_two_dots_unchanged():
    # Two dots are not enough to form an ellipsis
    assert SymbolNormalizer.normalize_ellipsis("等等..") == "等等.."


def test_ellipsis_after_latin_unchanged():
    # After a real latin letter / digit, dots stay as-is (not ellipsis).
    assert SymbolNormalizer.normalize_ellipsis("ABC...") == "ABC..."


def test_ellipsis_after_digit_unchanged():
    assert SymbolNormalizer.normalize_ellipsis("v1.0...") == "v1.0..."


def test_mixed_dot_chars():
    # Mix of ASCII and full-width dots
    result = SymbolNormalizer.normalize_ellipsis("等等。．.")
    assert "……" in result


# ---- Bracket pairing ----

def test_ascii_brackets_around_chinese_become_fullwidth():
    result = SymbolNormalizer.normalize_bracket_pairs("说明(中文内容)")
    assert "（" in result and "）" in result
    assert "(" not in result and ")" not in result


def test_brackets_around_pure_latin_unchanged():
    # Brackets around pure latin content should NOT be converted
    result = SymbolNormalizer.normalize_bracket_pairs("说明(README)")
    assert "(" in result and ")" in result


def test_nested_brackets():
    result = SymbolNormalizer.normalize_bracket_pairs("项目(包含(子项目)测试)")
    # Both pairs should be converted since they contain chinese
    assert "（" in result and "）" in result


# ---- Quote pairing ----

def test_ascii_double_quotes_around_chinese_become_curly():
    result = SymbolNormalizer.normalize_quote_pairs(
        '他说"你好"',
        {'"', '“', '”', '„', '‟', '「', '」'},
        '“', '”',
    )
    assert '“' in result  # left double quote
    assert '”' in result  # right double quote


def test_single_quotes_around_chinese_become_curly():
    result = SymbolNormalizer.normalize_quote_pairs(
        "他说'你好'",
        {"'", '‘', '’', '‚', '‛'},
        '‘',
        '’',
        skip_inner_latin=True,
    )
    assert '‘' in result and '’' in result


def test_single_quotes_around_latin_skipped():
    # With skip_inner_latin=True, quotes around pure latin are left alone
    result = SymbolNormalizer.normalize_quote_pairs(
        "it's fine",
        {"'", '‘', '’', '‚', '‛'},
        '‘',
        '’',
        skip_inner_latin=True,
    )
    # The apostrophe in "it's" should remain a plain apostrophe
    assert "'" in result


# ---- Simple punctuation ----

def test_comma_becomes_chinese():
    assert SymbolNormalizer.normalize_simple_punctuation("你好,世界") == "你好，世界"


def test_period_becomes_chinese():
    assert SymbolNormalizer.normalize_simple_punctuation("你好.world") == "你好。world"


def test_period_between_digits_unchanged():
    # Decimal point between digits should NOT be converted
    assert SymbolNormalizer.normalize_simple_punctuation("3.14") == "3.14"


def test_question_mark_becomes_chinese():
    assert SymbolNormalizer.normalize_simple_punctuation("你好吗?") == "你好吗？"


def test_exclamation_mark_becomes_chinese():
    assert SymbolNormalizer.normalize_simple_punctuation("太好了!") == "太好了！"


def test_semicolon_colon_become_chinese():
    # Simple punctuation conversion only fires when the text contains Chinese.
    assert SymbolNormalizer.normalize_simple_punctuation("你好;世界:你好") == "你好；世界：你好"


def test_consecutive_dots_not_replaced_as_period():
    # A period that is adjacent to another period should not be replaced
    # (it's part of an ellipsis-like sequence, handled elsewhere)
    assert "。" not in SymbolNormalizer.normalize_simple_punctuation("等等...")


# ---- Full pipeline ----

def test_normalize_symbols_in_text_full():
    result = SymbolNormalizer.normalize_symbols_in_text('他说"你好,世界"...')
    # Quotes converted
    assert '“' in result and '”' in result
    # Comma converted
    assert '，' in result
    # Ellipsis
    assert '……' in result


def test_normalize_symbols_skips_no_chinese():
    # Text without any CJK should be returned unchanged
    text = 'He said "hello, world"...'
    assert SymbolNormalizer.normalize_symbols_in_text(text) == text
