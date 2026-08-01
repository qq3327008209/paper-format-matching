import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from engine import RuleEngine, MatchParser, normalize_font, get_extractor

SAMPLE_DOCX = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "tests", "fixtures", "sample.docx",
)
RULES_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "config", "rules_lunwen.yaml",
)


def test_normalize_font():
    assert normalize_font("SimSun") == "宋体"
    assert normalize_font("FangSong_GB2312") == "仿宋"
    assert normalize_font("unknown") == "unknown"


def test_match_parser_else():
    assert MatchParser("else").matches({}, 0, [], {}) is True


def test_match_parser_regex():
    parser = MatchParser("regex('^第')")
    assert parser.matches({"text": "第一章 引言"}, 0, [], {}) is True
    assert parser.matches({"text": "hello"}, 0, [], {}) is False


def test_add_error_records():
    para = {"text": "test", "font": "宋体", "size": 12, "paragraph_index": 0}
    eng = RuleEngine(
        rules_data={"segments": [{"id": "body", "match": "else"}], "rules": []},
        doc_data={"paragraphs": [para], "source_format": "docx"},
    )
    eng._add_error("body", para, 0, "test error")
    assert len(eng.errors) == 1
    assert eng.errors[0]["segment"] == "body"
    assert "description" in eng.errors[0]


@pytest.mark.skipif(
    not (os.path.exists(SAMPLE_DOCX) and os.path.exists(RULES_PATH)),
    reason="sample docx or rules file not found",
)
def test_validate_finds_errors():
    extractor = get_extractor(SAMPLE_DOCX)
    doc_data = extractor.extract(SAMPLE_DOCX)
    eng = RuleEngine(doc_data, rules_path=RULES_PATH)
    errors = eng.validate()
    assert len(errors) > 0


def test_op_eq_font():
    doc_data = {
        "paragraphs": [
            {"text": "测试正文", "font": "宋体", "size": 12, "paragraph_index": 0}
        ],
        "source_format": "docx",
    }
    rules_data = {
        "segments": [{"id": "body", "match": "else"}],
        "rules": [
            {"segment": "body", "field": "font", "op": "eq", "value": "黑体"}
        ],
    }
    eng = RuleEngine(doc_data, rules_data=rules_data)
    errors = eng.validate()
    assert len(errors) > 0
    assert any(e.get("segment") == "body" for e in errors)
