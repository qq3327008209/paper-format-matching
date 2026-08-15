"""Tests for the structural ops: before / after / count_min / count_max / exists.
These ops operate on segment counts / ordering rather than per-paragraph values,
so they never carry action / action_value.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import RuleEngine


def _engine(paragraphs, rules_data):
    # Match expressions reference the `index` field, so every synthetic
    # paragraph must carry it (the real docx extractor always sets it).
    enriched = []
    for i, para in enumerate(paragraphs):
        enriched.append({"index": para.get("index", para.get("paragraph_index", i)),
                         **para})
    return RuleEngine(
        doc_data={"paragraphs": enriched, "source_format": "docx"},
        rules_data=rules_data,
    )


# ---- exists ----

def test_exists_passes_when_segment_present():
    rules = {
        "segments": [{"id": "abstract", "match": "index == 0"}],
        "rules": [{"segment": "abstract", "op": "exists"}],
    }
    eng = _engine([{"text": "摘要", "paragraph_index": 0}], rules)
    assert eng.validate() == []


def test_exists_fails_when_segment_absent():
    # Use a match that no paragraph satisfies (index 99 does not exist),
    # so the "abstract" segment is genuinely absent.
    rules = {
        "segments": [{"id": "abstract", "match": "index == 99"}],
        "rules": [{"segment": "abstract", "op": "exists",
                   "msg": "缺少摘要章节"}],
    }
    eng = _engine([{"text": "正文", "paragraph_index": 0}], rules)
    errors = eng.validate()
    assert len(errors) == 1
    assert errors[0]["type"] == "exists"
    assert errors[0]["segment"] == "abstract"


# ---- count_min ----

def test_count_min_passes_when_enough():
    rules = {
        "segments": [{"id": "h1", "match": "else"}],
        "rules": [{"segment": "h1", "op": "count_min", "value": 2}],
    }
    eng = _engine([
        {"text": "第一章", "paragraph_index": 0},
        {"text": "第二章", "paragraph_index": 1},
        {"text": "第三章", "paragraph_index": 2},
    ], rules)
    assert eng.validate() == []


def test_count_min_fails_when_too_few():
    rules = {
        "segments": [{"id": "h1", "match": "else"}],
        "rules": [{"segment": "h1", "op": "count_min", "value": 5,
                   "msg": "一级标题不足5个"}],
    }
    eng = _engine([
        {"text": "第一章", "paragraph_index": 0},
        {"text": "第二章", "paragraph_index": 1},
    ], rules)
    errors = eng.validate()
    assert len(errors) == 1
    assert errors[0]["type"] == "count"


# ---- count_max ----

def test_count_max_passes_when_within_limit():
    rules = {
        "segments": [{"id": "h1", "match": "else"}],
        "rules": [{"segment": "h1", "op": "count_max", "value": 10}],
    }
    eng = _engine([
        {"text": "第一章", "paragraph_index": 0},
        {"text": "第二章", "paragraph_index": 1},
    ], rules)
    assert eng.validate() == []


def test_count_max_fails_when_exceeded():
    rules = {
        "segments": [{"id": "h1", "match": "else"}],
        "rules": [{"segment": "h1", "op": "count_max", "value": 1,
                   "msg": "一级标题超过1个"}],
    }
    eng = _engine([
        {"text": "第一章", "paragraph_index": 0},
        {"text": "第二章", "paragraph_index": 1},
    ], rules)
    errors = eng.validate()
    assert len(errors) == 1
    assert errors[0]["type"] == "count"


# ---- before ----

def test_before_passes_when_in_order():
    rules = {
        "segments": [
            {"id": "abstract", "match": "index == 0"},
            {"id": "body", "match": "index == 1"},
        ],
        "rules": [{"segment": "abstract", "op": "before", "target": "body"}],
    }
    eng = _engine([
        {"text": "摘要", "paragraph_index": 0},
        {"text": "正文", "paragraph_index": 1},
    ], rules)
    assert eng.validate() == []


def test_before_fails_when_out_of_order():
    # abstract is at index 1, body is at index 0 → abstract is NOT before body
    rules = {
        "segments": [
            {"id": "abstract", "match": "index == 1"},
            {"id": "body", "match": "index == 0"},
        ],
        "rules": [{"segment": "abstract", "op": "before", "target": "body",
                   "msg": "摘要应在正文之前"}],
    }
    eng = _engine([
        {"text": "正文", "paragraph_index": 0},
        {"text": "摘要", "paragraph_index": 1},
    ], rules)
    errors = eng.validate()
    assert len(errors) == 1
    assert errors[0]["type"] == "order"


# ---- after ----

def test_after_passes_when_in_order():
    rules = {
        "segments": [
            {"id": "body", "match": "index == 0"},
            {"id": "reference", "match": "index == 1"},
        ],
        "rules": [{"segment": "reference", "op": "after", "target": "body"}],
    }
    eng = _engine([
        {"text": "正文", "paragraph_index": 0},
        {"text": "参考文献", "paragraph_index": 1},
    ], rules)
    assert eng.validate() == []


def test_after_fails_when_out_of_order():
    # reference is at index 0, body is at index 1 → reference is NOT after body
    rules = {
        "segments": [
            {"id": "body", "match": "index == 1"},
            {"id": "reference", "match": "index == 0"},
        ],
        "rules": [{"segment": "reference", "op": "after", "target": "body",
                   "msg": "参考文献应在正文之后"}],
    }
    eng = _engine([
        {"text": "参考文献", "paragraph_index": 0},
        {"text": "正文", "paragraph_index": 1},
    ], rules)
    errors = eng.validate()
    assert len(errors) == 1
    assert errors[0]["type"] == "order"


def test_order_skips_when_target_segment_absent():
    """before/after should not error when the target segment has no paragraphs."""
    rules = {
        "segments": [
            {"id": "abstract", "match": "index == 0"},
            {"id": "body", "match": "else"},
        ],
        "rules": [{"segment": "abstract", "op": "before", "target": "body"}],
    }
    # No body paragraphs at all
    eng = _engine([{"text": "摘要", "paragraph_index": 0}], rules)
    assert eng.validate() == []
