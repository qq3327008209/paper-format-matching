"""Tests for the ``after_h1('title') until next h1`` chapter-scope matcher.

This is the most complex branch in :class:`MatchParser`: a segment can declare
that it only matches paragraphs falling inside the range of a specific H1
heading (e.g. only paragraphs inside "第一章 总则", not "第二章 附则").

The tests here build paragraphs with ``heading_level=1`` so that
``_compute_h1_ranges`` recognises them as chapter boundaries.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import RuleEngine


def _engine(paragraphs, rules_data):
    # Match expressions reference the `index` field; the real docx extractor
    # always sets it.
    enriched = []
    for i, para in enumerate(paragraphs):
        enriched.append({"index": para.get("index", para.get("paragraph_index", i)),
                         **para})
    return RuleEngine(
        doc_data={"paragraphs": enriched, "source_format": "docx"},
        rules_data=rules_data,
    )


# ---- after_h1 matches paragraphs inside the named chapter ----

def test_after_h1_matches_only_inside_named_chapter():
    """Paragraphs in chapter 2 must NOT match a segment scoped to chapter 1."""
    rules = {
        "segments": [
            {"id": "ch1_body",
             "match": "after_h1('第一章') until next h1"},
            {"id": "ch2_body",
             "match": "after_h1('第二章') until next h1"},
        ],
        "rules": [
            {"segment": "ch1_body", "field": "font", "op": "eq",
             "value": "黑体", "msg": "第一章正文应为黑体"},
        ],
    }
    eng = _engine([
        {"text": "第一章 总则", "paragraph_index": 0, "heading_level": 1, "font": "黑体"},
        {"text": "第一条",     "paragraph_index": 1, "font": "黑体"},
        {"text": "第二条",     "paragraph_index": 2, "font": "宋体"},
        {"text": "第二章 附则", "paragraph_index": 3, "heading_level": 1, "font": "黑体"},
        # This paragraph is in chapter 2; it must NOT be checked by the
        # ch1_body rule even though its font is also 宋体.
        {"text": "第三条",     "paragraph_index": 4, "font": "宋体"},
    ], rules)
    errors = eng.validate()
    # Only paragraph_index 2 (第二条, in chapter 1, font=宋体) should error.
    assert len(errors) == 1
    assert errors[0]["paragraph_index"] == 2
    assert errors[0]["segment"] == "ch1_body"


# ---- after_h1 with `until end` (scope lasts to document end) ----

def test_after_h1_until_end_covers_rest_of_document():
    """`until end` scopes the segment to the last H1 through EOF."""
    rules = {
        "segments": [
            {"id": "appendix", "match": "after_h1('附录') until end"},
        ],
        "rules": [
            {"segment": "appendix", "field": "font", "op": "eq",
             "value": "楷体", "msg": "附录字体应为楷体"},
        ],
    }
    eng = _engine([
        {"text": "第一章 总则", "paragraph_index": 0,
         "style": "Heading 1", "font": "楷体"},
        {"text": "正文",       "paragraph_index": 1, "font": "宋体"},
        {"text": "附录",       "paragraph_index": 2,
         "style": "Heading 1", "font": "楷体"},
        # Content paragraphs must NOT themselves look like H1 headings
        # (the h1-range detector treats any text starting with a standalone
        # title keyword as a section heading).
        {"text": "附表一",     "paragraph_index": 3, "font": "宋体"},
        {"text": "补充说明",   "paragraph_index": 4, "font": "宋体"},
    ], rules)
    errors = eng.validate()
    # Both appendix content paragraphs (index 3 and 4) have wrong font.
    assert len(errors) == 2
    assert {e["paragraph_index"] for e in errors} == {3, 4}


# ---- after_h1: no matching chapter -> segment has no paragraphs ----

def test_after_h1_no_matching_chapter_segment_absent():
    """If no H1 title contains the target string, the segment matches nothing."""
    rules = {
        "segments": [
            {"id": "ghost", "match": "after_h1('不存在的章节') until next h1"},
        ],
        "rules": [
            {"segment": "ghost", "field": "font", "op": "eq",
             "value": "黑体"},
        ],
    }
    eng = _engine([
        {"text": "第一章", "paragraph_index": 0, "heading_level": 1},
        {"text": "正文",   "paragraph_index": 1, "font": "宋体"},
    ], rules)
    errors = eng.validate()
    # The ghost segment matched no paragraphs, so the eq rule has nothing to
    # check.  No format error.
    assert len(errors) == 0


# ---- after_h1: title matching is substring-based ----

def test_after_h1_matches_by_substring():
    """The scope lookup uses `in` (substring), not exact equality."""
    rules = {
        "segments": [
            {"id": "ch1_scope", "match": "after_h1('总则') until next h1"},
        ],
        "rules": [
            {"segment": "ch1_scope", "field": "font", "op": "eq",
             "value": "黑体", "msg": "总则章节正文应为黑体"},
        ],
    }
    eng = _engine([
        {"text": "第一章 总则概述", "paragraph_index": 0, "heading_level": 1, "font": "黑体"},
        {"text": "第一条",         "paragraph_index": 1, "font": "宋体"},
        {"text": "第二章 分则",    "paragraph_index": 2, "heading_level": 1, "font": "黑体"},
        {"text": "第二条",         "paragraph_index": 3, "font": "宋体"},
    ], rules)
    errors = eng.validate()
    # Only paragraph_index 1 is inside "第一章 总则概述" scope and wrong font.
    assert len(errors) == 1
    assert errors[0]["paragraph_index"] == 1


# ---- after_h1 combined with an additional field condition ----

def test_after_h1_with_and_condition():
    """after_h1 scope can be ANDed with a field expression."""
    rules = {
        "segments": [
            {"id": "ch1_article",
             "match": "after_h1('第一章') until next h1 and text =~ \"^第\""},
        ],
        "rules": [
            {"segment": "ch1_article", "field": "font", "op": "eq",
             "value": "黑体", "msg": "第一章条文应为黑体"},
        ],
    }
    eng = _engine([
        {"text": "第一章 总则", "paragraph_index": 0,
         "heading_level": 1, "font": "黑体"},
        # In chapter 1, starts with 第 → should match segment
        {"text": "第一条 立法目的", "paragraph_index": 1, "font": "宋体"},
        # In chapter 1 but text does not start with 第 → not this segment
        {"text": "说明文字",        "paragraph_index": 2, "font": "宋体"},
        {"text": "第二章 附则", "paragraph_index": 3,
         "heading_level": 1, "font": "黑体"},
    ], rules)
    errors = eng.validate()
    assert len(errors) == 1
    assert errors[0]["paragraph_index"] == 1
