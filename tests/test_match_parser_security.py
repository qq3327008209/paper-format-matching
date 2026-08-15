"""Security tests for MatchParser._safe_eval AST whitelist.

Every expression below is crafted to attempt code injection / sandbox escape.
All MUST evaluate to False (never raise, never execute side effects).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from engine.rule_engine import MatchParser


ATTACKS = [
    # Direct builtins access
    '__import__("os").system("echo pwned")',
    'eval("1")',
    'exec("1")',
    'compile("1", "", "eval")',
    # Indirect via dunder chains
    '().__class__.__mro__[1].__subclasses__()',
    '(1).__class__.__mro__',
    '().__class__.__base__.__subclasses__()',
    'getattr(__builtins__, "eval")',
    'vars()["__builtins__"]',
    'globals()["__builtins__"]',
    # Attribute access on fields
    'size.__class__.__name__',
    'font.upper()',
    'text[0:1]',
    # Call nodes
    'len("abc")',
    'print("pwned")',
    'open("/etc/passwd").read()',
    # Lambda
    '(lambda: 1)()',
    'lambda: __import__("os")',
    # Comprehensions
    '[x for x in __class__.__mro__]',
    '{x for x in [1,2,3]}',
    '{x: x for x in [1]}',
    '(x for x in [1])',
    # Generator expression
    'any(x for x in [True])',
    # Semicolon injection (original blacklist target)
    'size; __import__("os").system("x")',
    '1; import os',
    # import statement
    'import os',
    'import os as o',
    'from os import system',
    # Assignment expressions (walrus)
    '(x := __import__("os"))',
    # Await
    'await __import__("os").system("x")',
    # Yield
    'yield __import__("os")',
    # Raise
    'raise Exception("x")',
    # Delete
    'del __builtins__',
    # Return
    'return 1',
    # With statement
    'with open("x"): pass',
    # While/for
    'while True: break',
    'for x in [1]: pass',
    # Assert
    'assert False',
    # Match (py3.10+)
    'match x:\n    case _: pass',
    # Class definition
    'class X: pass',
    # Function definition
    'def f(): pass',
    # Decorators
    '@f\ndef g(): pass',
    # Type annotation with side-effect
    'x: int = __import__("os")',
    # Numeric tricks (BinOp not allowed; plain constants are harmless)
    '1e1000 * 1e1000',
    # Unicode confusables in field names
    'sіze > 10',  # Cyrillic і
    # Nested attribute on a name that resolves to a string
    'font.capitalize()',
    # Subscript on list
    '[1,2,3][0]',
    # Slice
    '"abc"[1:2]',
    # Starred
    '*[1,2]',
    # Bool short-circuit with side effect in un-evaluated branch is fine;
    # but verify the evaluated branch can't call:
    'True and __import__("os")',
    'False or __import__("os")',
]


@pytest.mark.parametrize("expr", ATTACKS)
def test_attack_blocked(expr):
    """Attack expressions must evaluate to False, never raise."""
    para = {
        "text": "第一章 引言",
        "font": "宋体",
        "size": 16,
        "align": "justify",
        "index": 0,
    }
    result = MatchParser(expr).matches(para, 0, [], {})
    assert result is False, f"attack not blocked: {expr!r} -> {result}"


def test_legitimate_expressions_still_work():
    """Sanity check that valid match expressions still evaluate correctly."""
    para = {"text": "第一章 引言", "font": "宋体", "size": 16, "align": "justify", "index": 0}

    assert MatchParser("else").matches(para, 0, [], {}) is True
    assert MatchParser("size >= 18").matches(para, 0, [], {}) is False
    assert MatchParser("size >= 10").matches(para, 0, [], {}) is True
    assert MatchParser('font == "宋体"').matches(para, 0, [], {}) is True
    assert MatchParser('font == "黑体"').matches(para, 0, [], {}) is False
    assert MatchParser("index < 3 and size >= 10").matches(para, 0, [], {}) is True
    assert MatchParser("index < 3 and size >= 100").matches(para, 0, [], {}) is False
    assert MatchParser("index >= 3 or size >= 10").matches(para, 0, [], {}) is True
    assert MatchParser("not in_table").matches({"in_table": False}, 0, [], {}) is True
    assert MatchParser("not in_table").matches({"in_table": True}, 0, [], {}) is False
    assert MatchParser('align in ["left", "justify"]').matches(para, 0, [], {}) is True
    assert MatchParser('align in ["left", "justify"]').matches(
        {"align": "center"}, 0, [], {}
    ) is False
    # membership with non-list right-hand side should be False, not raise
    assert MatchParser("size in 5").matches(para, 0, [], {}) is False
