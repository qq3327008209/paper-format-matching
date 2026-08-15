"""Tests that error dicts carry action / action_value for repairable rules,
and that structural ops (range / count / order / exists) do NOT carry action.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import RuleEngine


def _make_engine(rules_data, paragraphs=None):
    if paragraphs is None:
        paragraphs = [
            {"text": "正文段落", "font": "宋体", "size": 12,
             "align": "justify", "paragraph_index": 0},
        ]
    # Match expressions reference the `index` field, so every synthetic
    # paragraph must carry it (the real docx extractor always sets it).
    enriched = []
    for i, para in enumerate(paragraphs):
        enriched.append({"index": para.get("index", para.get("paragraph_index", i)),
                         **para})
    doc_data = {"paragraphs": enriched, "source_format": "docx"}
    return RuleEngine(
        doc_data=doc_data,
        rules_data=rules_data,
    )


def _body_rules(value="黑体", **kwargs):
    """Build a segments+rules dict with one body segment and one rule."""
    rules = {
        "segments": [{"id": "body", "match": "else"}],
        "rules": [
            {"segment": "body", "field": "font", "op": "eq",
             "value": value, "format_action": "set_font"},
        ],
    }
    rules["rules"][0].update(kwargs)
    return rules


def test_eq_error_carries_action():
    eng = _make_engine(_body_rules())
    errors = eng.validate()
    assert len(errors) == 1
    assert errors[0].get("action") == "set_font"
    assert errors[0].get("action_value") == "黑体"


def test_in_error_carries_first_value():
    rules = {
        "segments": [{"id": "body", "match": "else"}],
        "rules": [
            {"segment": "body", "field": "font", "op": "in",
             "value": ["仿宋_GB2312", "仿宋"],
             "format_action": "set_font"},
        ],
    }
    eng = _make_engine(rules)
    errors = eng.validate()
    assert len(errors) == 1
    assert errors[0].get("action") == "set_font"
    # in op: action_value should be the FIRST list element (the preferred value)
    assert errors[0].get("action_value") == "仿宋_GB2312"


def test_regex_error_carries_action():
    rules = {
        "segments": [{"id": "h1", "match": "else"}],
        "rules": [
            {"segment": "h1", "field": "text", "op": "regex",
             "value": "^第", "format_action": "set_font"},
        ],
    }
    eng = _make_engine(rules, paragraphs=[
        {"text": "附录", "paragraph_index": 0},
    ])
    errors = eng.validate()
    assert len(errors) == 1
    assert errors[0].get("action") == "set_font"
    assert errors[0].get("action_value") == "^第"


def test_range_error_has_no_action():
    rules = {
        "segments": [{"id": "body", "match": "else"}],
        "rules": [
            {"segment": "body", "field": "line_spacing", "op": "range",
             "value": [1.0, 1.5],
             "format_action": "set_line_spacing"},  # should be ignored
        ],
    }
    eng = _make_engine(rules, paragraphs=[
        {"text": "正文", "line_spacing": 3.0, "paragraph_index": 0},
    ])
    errors = eng.validate()
    assert len(errors) == 1
    assert "action" not in errors[0]


def test_count_min_error_has_no_action():
    rules = {
        "segments": [{"id": "h1", "match": "else"}],
        "rules": [
            {"segment": "h1", "op": "count_min", "value": 5,
             "format_action": "set_font"},  # should be ignored
        ],
    }
    eng = _make_engine(rules, paragraphs=[
        {"text": "第一章", "paragraph_index": 0},
    ])
    errors = eng.validate()
    assert len(errors) == 1
    assert "action" not in errors[0]


def test_count_max_error_has_no_action():
    rules = {
        "segments": [{"id": "h1", "match": "else"}],
        "rules": [
            {"segment": "h1", "op": "count_max", "value": 0,
             "format_action": "set_font"},
        ],
    }
    eng = _make_engine(rules, paragraphs=[
        {"text": "第一章", "paragraph_index": 0},
    ])
    errors = eng.validate()
    assert len(errors) == 1
    assert "action" not in errors[0]


def test_exists_error_has_no_action():
    # Use a match that no paragraph satisfies so the segment is absent.
    rules = {
        "segments": [{"id": "abstract", "match": "index == 99"}],
        "rules": [
            {"segment": "abstract", "op": "exists",
             "format_action": "set_font"},
        ],
    }
    eng = _make_engine(rules, paragraphs=[
        {"text": "第一章", "paragraph_index": 0},
    ])
    errors = eng.validate()
    assert len(errors) == 1
    assert "action" not in errors[0]


def test_order_error_has_no_action():
    # abstract is at index 1, body is at index 0 → abstract is NOT before body.
    rules = {
        "segments": [
            {"id": "abstract", "match": "index == 1"},
            {"id": "body", "match": "index == 0"},
        ],
        "rules": [
            {"segment": "abstract", "op": "before", "target": "body",
             "format_action": "set_font"},
        ],
    }
    eng = _make_engine(rules, paragraphs=[
        {"text": "正文", "paragraph_index": 0},
        {"text": "摘要", "paragraph_index": 1},
    ])
    errors = eng.validate()
    assert len(errors) == 1
    assert "action" not in errors[0]


def test_rule_without_format_action_has_no_action_field():
    """A rule with no format_action must not add an action key."""
    rules = _body_rules()
    del rules["rules"][0]["format_action"]
    eng = _make_engine(rules)
    errors = eng.validate()
    assert len(errors) == 1
    assert "action" not in errors[0]


def test_compliant_rule_produces_no_error():
    eng = _make_engine(_body_rules(value="宋体"))
    errors = eng.validate()
    assert len(errors) == 0
