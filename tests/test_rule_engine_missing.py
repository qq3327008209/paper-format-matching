"""Tests that missing / unreadable fields produce a `missing`-type error
rather than being silently treated as compliant.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import RuleEngine


def _engine(paragraphs, rules_data):
    doc_data = {"paragraphs": paragraphs, "source_format": "docx"}
    return RuleEngine(doc_data=doc_data, rules_data=rules_data)


def _body_rules(field="font", op="eq", value="黑体"):
    return {
        "segments": [{"id": "body", "match": "else"}],
        "rules": [{"segment": "body", "field": field, "op": op, "value": value}],
    }


def test_missing_font_emits_missing_error():
    """Paragraph has text but no font field -> missing error, not silent pass."""
    eng = _engine(
        [{"text": "正文段落", "paragraph_index": 0}],
        _body_rules(field="font"),
    )
    errors = eng.validate()
    missing = [e for e in errors if e.get("type") == "missing"]
    assert len(missing) == 1
    assert missing[0].get("field") == "font"


def test_missing_size_emits_missing_error():
    eng = _engine(
        [{"text": "正文段落", "paragraph_index": 0}],
        _body_rules(field="size", value=12),
    )
    errors = eng.validate()
    missing = [e for e in errors if e.get("type") == "missing"]
    assert len(missing) == 1
    assert missing[0].get("field") == "size"


def test_missing_align_emits_missing_error():
    eng = _engine(
        [{"text": "正文段落", "paragraph_index": 0}],
        _body_rules(field="align", value="justify"),
    )
    errors = eng.validate()
    missing = [e for e in errors if e.get("type") == "missing"]
    assert len(missing) == 1
    assert missing[0].get("field") == "align"


def test_present_field_does_not_emit_missing():
    """When the field is present, no missing error; only a format error if wrong."""
    eng = _engine(
        [{"text": "正文段落", "font": "宋体", "paragraph_index": 0}],
        _body_rules(field="font"),
    )
    errors = eng.validate()
    missing = [e for e in errors if e.get("type") == "missing"]
    assert len(missing) == 0
    # Should still report a format error (font is 宋体, expected 黑体)
    assert any(e.get("type") == "format" for e in errors)


def test_empty_string_font_emits_missing():
    """Empty-string font is treated the same as None (unreadable)."""
    eng = _engine(
        [{"text": "正文段落", "font": "", "paragraph_index": 0}],
        _body_rules(field="font"),
    )
    errors = eng.validate()
    missing = [e for e in errors if e.get("type") == "missing"]
    assert len(missing) == 1


def test_missing_deduplicated_for_same_field():
    """Two rules checking the same missing field on the same paragraph should
    produce only ONE missing error (not two)."""
    rules = {
        "segments": [{"id": "body", "match": "else"}],
        "rules": [
            {"segment": "body", "field": "font", "op": "eq", "value": "黑体"},
            {"segment": "body", "field": "font", "op": "eq", "value": "宋体"},
        ],
    }
    eng = _engine(
        [{"text": "正文段落", "paragraph_index": 0}],
        rules,
    )
    errors = eng.validate()
    missing = [e for e in errors if e.get("type") == "missing"]
    assert len(missing) == 1


def test_missing_for_different_fields_not_deduplicated():
    """Missing font and missing size on the same paragraph are two distinct
    errors."""
    rules = {
        "segments": [{"id": "body", "match": "else"}],
        "rules": [
            {"segment": "body", "field": "font", "op": "eq", "value": "黑体"},
            {"segment": "body", "field": "size", "op": "eq", "value": 16},
        ],
    }
    eng = _engine(
        [{"text": "正文段落", "paragraph_index": 0}],
        rules,
    )
    errors = eng.validate()
    missing = [e for e in errors if e.get("type") == "missing"]
    assert len(missing) == 2


def test_missing_error_has_location():
    eng = _engine(
        [{"text": "正文段落", "paragraph_index": 5, "page": 2}],
        _body_rules(field="font"),
    )
    errors = eng.validate()
    missing = [e for e in errors if e.get("type") == "missing"]
    assert "Page 2" in missing[0].get("location", "")
    assert "Paragraph 5" in missing[0].get("location", "")
