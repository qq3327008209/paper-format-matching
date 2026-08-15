"""Import-consistency test: heading/title/attachment regexes must be defined
ONCE (in format.ops.heading) and re-imported everywhere else.

This guards against the regression where a developer copies a regex definition
into format.engine (or any other module), creating a second source of truth
that silently diverges.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import format.engine
import format.ops.heading


HEADING_REGEX_NAMES = [
    "CHINESE_NUM_PATTERN",
    "RE_TITLE_H1",
    "RE_TITLE_H2",
    "RE_HEADING_H1",
    "RE_HEADING_H2",
    "RE_HEADING_H3",
    "RE_HEADING_H4",
    "RE_ATTACHMENT",
    "RE_H2_INLINE_TITLE",
]


def test_heading_regexes_are_imported_not_redefined():
    """Every heading regex in format.engine must BE the same object as the
    one in format.ops.heading (identity, not just equality)."""
    for name in HEADING_REGEX_NAMES:
        engine_val = getattr(format.engine, name, None)
        heading_val = getattr(format.ops.heading, name, None)
        assert engine_val is not None, f"{name} missing from format.engine"
        assert heading_val is not None, f"{name} missing from format.ops.heading"
        assert engine_val is heading_val, (
            f"{name} is redefined in format.engine rather than imported from "
            f"format.ops.heading -- this creates a second source of truth"
        )


def test_heading_module_exports_all_regexes():
    """format.ops.heading must export every heading regex."""
    for name in HEADING_REGEX_NAMES:
        assert hasattr(format.ops.heading, name), (
            f"{name} missing from format.ops.heading"
        )


def test_regex_patterns_are_functional():
    """Smoke test: the shared regexes actually match what they claim to."""
    assert format.ops.heading.RE_HEADING_H1.match("一、总则")
    assert format.ops.heading.RE_HEADING_H1.match("十、附则")
    assert not format.ops.heading.RE_HEADING_H1.match("普通正文")

    assert format.ops.heading.RE_HEADING_H2.match("（一）总则")
    assert format.ops.heading.RE_HEADING_H2.match("(一)总则")
    assert not format.ops.heading.RE_HEADING_H2.match("普通正文")

    assert format.ops.heading.RE_HEADING_H3.match("1. 正文")
    assert format.ops.heading.RE_HEADING_H3.match("1．全角点")
    assert not format.ops.heading.RE_HEADING_H3.match("普通正文")

    assert format.ops.heading.RE_HEADING_H4.match("（1）正文")
    assert not format.ops.heading.RE_HEADING_H4.match("普通正文")

    assert format.ops.heading.RE_ATTACHMENT.match("附件1：")
    assert format.ops.heading.RE_ATTACHMENT.match("附件：")
    assert not format.ops.heading.RE_ATTACHMENT.match("普通正文")

    m = format.ops.heading.RE_H2_INLINE_TITLE.match("（一）标题内容")
    assert m is not None
    assert m.group(1) == "一"
    assert m.group(2) == "标题内容"
