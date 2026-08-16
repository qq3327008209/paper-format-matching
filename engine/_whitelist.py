"""AST node whitelist for ``MatchParser._eval_ast``.

The whitelist is the only thing standing between user-supplied YAML match
expressions and arbitrary Python execution.  Everything here is deliberately
boring: no ``eval`` / ``exec`` / ``__import__``, no attribute access, no
calls, no subscripts, no comprehensions, no lambdas.  Anything outside the
whitelist causes the expression to evaluate to ``False``.

The public entry point is :func:`safe_eval`, which returns ``False`` for any
expression that fails the whitelist check or raises during evaluation.
"""

from __future__ import annotations

import ast
import operator
from typing import Any


# ``ast.Num`` / ``ast.Str`` / ``ast.NameConstant`` were deprecated in 3.8 and
# removed in 3.12; use ``getattr`` so the whitelist works across versions.
_num = getattr(ast, "Num", ast.Constant)
_str = getattr(ast, "Str", ast.Constant)
_nc = getattr(ast, "NameConstant", ast.Constant)

_ALLOWED_NODES: frozenset[type[ast.AST]] = frozenset({
    ast.Expression, ast.Compare, ast.Eq, ast.NotEq,
    ast.Lt, ast.LtE, ast.Gt, ast.GtE,
    ast.In, ast.NotIn, ast.Is, ast.IsNot,
    ast.BoolOp, ast.And, ast.Or,
    ast.UnaryOp, ast.Not,
    ast.Name, ast.Constant,
    ast.List, ast.Tuple, ast.Set,
    _num, _str, _nc,
})

_CMP_OP: dict[type[ast.AST], Any] = {
    ast.Eq: operator.eq, ast.NotEq: operator.ne,
    ast.Lt: operator.lt, ast.LtE: operator.le,
    ast.Gt: operator.gt, ast.GtE: operator.ge,
    ast.Is: operator.is_, ast.IsNot: operator.is_not,
}


def _value(node: ast.AST) -> object:
    """Recursively evaluate an allowed leaf / container node.

    By the time we reach here, every ``Name`` node has been substituted with
    a ``Constant`` node by ``_FieldSubstitutor``.  A remaining ``Name`` is an
    unexpected reference and is rejected.
    """
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, _num):       # py<3.8
        return node.n
    if isinstance(node, _str):       # py<3.8
        return node.s
    if isinstance(node, _nc):        # py<3.8
        return node.value
    if isinstance(node, ast.Name):
        return False
    if isinstance(node, (ast.List, ast.Tuple)):
        return type(node.elts)([_value(e) for e in node.elts])
    if isinstance(node, ast.Set):
        return {_value(e) for e in node.elts}
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return not _value(node.operand)
    raise ValueError(f"disallowed node in value position: {type(node).__name__}")


def _check(node: ast.AST) -> bool:
    """Return ``True`` if ``node`` and all descendants are in the whitelist.

    ``ast.expr_context`` nodes (Load / Store / Del) are structural markers
    attached to ``Name`` / ``List`` / ``Tuple`` / ``Subscript``; they carry
    no semantics and are harmless, so they are skipped rather than listed.
    """
    if isinstance(node, ast.expr_context):
        return True
    if type(node) not in _ALLOWED_NODES:
        return False
    for child in ast.iter_child_nodes(node):
        if not _check(child):
            return False
    return True


def _eval_cmp(node: ast.Compare) -> bool:
    """Evaluate an allowed ``ast.Compare`` node."""
    left = _value(node.left)
    for op_cls, comparator in zip(node.ops, node.comparators):
        right = _value(comparator)
        if isinstance(op_cls, ast.In):
            if not isinstance(right, (list, tuple, set, frozenset, str)):
                return False
            if left not in right:
                return False
        elif isinstance(op_cls, ast.NotIn):
            if not isinstance(right, (list, tuple, set, frozenset, str)):
                return False
            if left in right:
                return False
        else:
            op_fn = _CMP_OP.get(type(op_cls))
            if op_fn is None:
                return False
            if not op_fn(left, right):
                return False
        left = right
    return True


def _eval(node: ast.AST) -> object:
    """Evaluate an allowed expression body node."""
    if isinstance(node, ast.Compare):
        return _eval_cmp(node)
    if isinstance(node, ast.BoolOp):
        if isinstance(node.op, ast.And):
            accum = _eval(node.values[0])
            for val in node.values[1:]:
                if not accum:
                    return accum
                accum = _eval(val)
            return accum
        # Or
        accum = _eval(node.values[0])
        for val in node.values[1:]:
            if accum:
                return accum
            accum = _eval(val)
        return accum
    return _value(node)


def safe_eval(tree: ast.AST) -> bool:
    """Check the whitelist and evaluate a pre-substituted match-expression AST.

    Returns ``False`` for any expression that fails the whitelist check or
    raises during evaluation -- never propagates exceptions.
    """
    if not _check(tree):
        return False
    try:
        return bool(_eval(tree.body))
    except Exception:
        return False
