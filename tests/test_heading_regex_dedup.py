"""Import-consistency test: heading/title/attachment regexes must be defined
ONCE (in format.ops.heading) and re-imported everywhere else.

Originally this test guarded ``format.engine`` from re-defining the regexes
locally. After the engine split (round-1 refactor) ``format.engine`` no longer
imports heading regexes at all — it is a thin facade with no ``format``-
internal constants of its own. This file is therefore kept as a smoke test
verifying that :mod:`format.ops.heading` is the single source of truth for
heading regexes.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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
