"""Universal rule engine (domain-agnostic).

The engine only understands the schema-defined rule format and does not
contain any domain knowledge.  Switching domains requires only replacing
the YAML rule file -- engine code stays unchanged.

Rule file structure is documented in schema.md.
"""

import ast
import logging
import operator
import os
import re
from typing import Any, Optional

import yaml

logger = logging.getLogger(__name__)

# ---- Font alias table ----

FONT_ALIASES: dict[str, list[str]] = {
    "宋体": ["宋体", "SimSun", "SimSun-ExtB", "simsun", "宋体_GB2312"],
    "黑体": ["黑体", "SimHei", "SimHei-ExtB", "simhei", "黑体_GB2312", "STHeiti"],
    "楷体": ["楷体", "KaiTi", "KaiTi_GB2312", "kaiti", "楷体_GB2312", "STKaiti"],
    "仿宋": ["仿宋", "FangSong", "FangSong_GB2312", "fangsong", "STFangsong"],
    "微软雅黑": ["微软雅黑", "Microsoft YaHei", "MicrosoftYaHei", "msyh"],
    "Times New Roman": [
        "Times New Roman", "TimesNewRoman", "times",
        "TimesNewRomanPSMT", "TimesNewRomanPS-BoldMT",
    ],
    "Arial": ["Arial", "arial"],
    "Calibri": ["Calibri", "calibri", "Calibri-Bold"],
}


def normalize_font(font_name: Optional[str]) -> Optional[str]:
    """Normalise a font name to its canonical Chinese alias.

    E.g. ``SimSun`` -> ``'宋体'``.  Unknown names are returned unchanged.

    Args:
        font_name: Raw font name string.

    Returns:
        Normalised font name or ``None``.
    """
    if not font_name:
        return font_name
    font_lower = font_name.lower().strip()
    for canonical, aliases in FONT_ALIASES.items():
        for alias in aliases:
            alias_lower = alias.lower()
            if (alias_lower == font_lower
                    or font_lower in alias_lower
                    or alias_lower in font_lower):
                return canonical
    return font_name


# ---- Match-expression parser ----


class _FieldSubstitutor(ast.NodeTransformer):
    """Replace every ``ast.Name`` in a match-expression AST with an
    ``ast.Constant`` carrying the actual value from a paragraph dict.

    Python keywords (``and``/``or``/``not``/``in``/``is``/``True``/``False``/
    ``None``) parse as operator / constant nodes, never as ``Name`` nodes,
    so they are never mistaken for field references.  List literals such as
    ``["left", "justify"]`` therefore work correctly without any string-level
    masking.  Unknown field names resolve to ``None``.
    """

    def __init__(self, para: dict) -> None:
        self.para = para

    def visit_Name(self, node: ast.Name) -> ast.Constant:
        value = self.para.get(node.id)
        if isinstance(value, str):
            return ast.Constant(value=value)
        if isinstance(value, (int, float, bool)):
            return ast.Constant(value=value)
        if value is None:
            return ast.Constant(value=None)
        # For non-primitive values (lists etc.), fall back to repr as a
        # string so the expression remains valid Python.
        return ast.Constant(value=str(value))


class MatchParser:
    """Parse match expressions in segment definitions, returning bool.

    Supported syntax:
      - Field references: index, size, font, align, text, style,
        heading_level, in_table, page
      - Comparisons: > < >= <= == !=
      - Logic: and or not
      - Regex: text =~ 'pattern'
      - Chapter scope: after_h1('title') until next h1
      - Fallback: else
    """

    _AFTER_H1_RE = re.compile(
        r"after_h1\(['\"](.+?)['\"]\)\s*until\s+(next\s+h1|end)",
        re.IGNORECASE,
    )

    def __init__(self, expression: str) -> None:
        """
        Args:
            expression: Raw match expression string.

        Raises:
            ValueError: If the expression is empty.
        """
        self.expression = expression.strip()
        if not self.expression:
            raise ValueError("match expression cannot be empty")
        self._after_h1_clause: tuple[str, str] | None = None
        m = self._AFTER_H1_RE.search(self.expression)
        if m:
            self._after_h1_clause = (m.group(1), m.group(2).strip().lower())
            self.expression = (
                self._AFTER_H1_RE.sub("", self.expression).strip()
            )
            self.expression = self.expression.replace("and", "").strip()
            if not self.expression:
                self.expression = "True"

    def matches(
        self,
        para: dict,
        para_index: int,
        paragraphs: list,
        h1_ranges: dict,
    ) -> bool:
        """Check whether the paragraph matches this segment.

        Args:
            para: Paragraph dictionary.
            para_index: Index of the paragraph in the document.
            paragraphs: Full paragraph list.
            h1_ranges: {h1_title: (start_idx, end_idx)} for chapter scopes.

        Returns:
            True if the paragraph matches.
        """
        expr_lower = self.expression.lower()
        if expr_lower == "else":
            return True
        if expr_lower.startswith("else"):
            rest = self.expression[4:].strip()
            if rest.lower().startswith("and"):
                rest = rest[3:].strip()
            if not rest:
                return True
            old_expr = self.expression
            self.expression = rest
            try:
                result = self._eval_expression(para, para_index)
            finally:
                self.expression = old_expr
            return result
        if self._after_h1_clause:
            title, end_kind = self._after_h1_clause
            range_info = None
            for h1_title, r in h1_ranges.items():
                if title in h1_title or h1_title in title:
                    range_info = r
                    break
            if range_info is None:
                return False
            start_idx, end_idx = range_info
            if not (start_idx <= para_index <= end_idx):
                return False
            if self.expression == "True":
                return True
        try:
            return self._eval_expression(para, para_index)
        except Exception:
            return False

    def _eval_expression(self, para: dict, para_index: int) -> bool:
        """Evaluate a plain match expression against paragraph data.

        The expression is pre-processed in two ways that are custom to the
        match language (not valid Python on their own):

        * ``regex('pattern')``  → ``True``/``False`` (checks ``para['text']``)
        * ``field =~ 'pattern'`` → ``True``/``False`` (checks ``para[field]``)

        After that, the expression is valid Python and is parsed into an AST.
        Field references (``index``, ``size``, ``font``, ...) appear as
        ``ast.Name`` nodes and are replaced with ``ast.Constant`` nodes
        carrying the actual value from ``para``.  Python keywords such as
        ``and``/``or``/``not``/``in``/``is`` parse as operator nodes, never as
        ``Name`` nodes, so they are never mistaken for field references --
        list literals like ``["left", "justify"]`` are therefore handled
        correctly without any string-level masking.

        The substituted AST is evaluated by :meth:`_eval_ast` using a strict
        node-type whitelist.
        """
        expr = self.expression

        # regex('pattern') shorthand -> checks para['text']
        regex_func_pat = r"regex\(\s*(['\"])(.+?)\1\s*\)"
        expr = re.sub(
            regex_func_pat,
            lambda m: "True" if bool(re.search(m.group(2), str(para.get("text") or "")))
            else "False",
            expr,
        )

        # field =~ 'pattern'
        regex_pat = r"(\w+)\s*=~\s*(['\"])(.+?)\2"
        expr = re.sub(
            regex_pat,
            lambda m: "True" if bool(re.search(m.group(3), str(para.get(m.group(1)))))
            else "False",
            expr,
        )

        try:
            tree = ast.parse(expr, mode="eval")
        except SyntaxError:
            return False

        # Replace every Name node (field reference) with a Constant node
        # carrying the actual value from the paragraph dict.
        tree = _FieldSubstitutor(para).visit(tree)
        ast.fix_missing_locations(tree)
        return self._eval_ast(tree)

    @staticmethod
    def _eval_ast(tree: ast.AST) -> bool:
        """Evaluate a pre-substituted match-expression AST.

        Only a strict whitelist of AST node types is permitted.  Anything
        outside the whitelist (attribute access, calls, subscripts, lambdas,
        comprehensions, etc.) causes the expression to evaluate to ``False``
        -- no Python builtins are ever reached.
        """
        # ast.Num / ast.Str / ast.NameConstant were deprecated in 3.8 and
        # removed in 3.12; use getattr so the code works across versions.
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

        def _value(node: ast.AST) -> object:
            """Recursively evaluate an allowed leaf/container node."""
            if isinstance(node, ast.Constant):
                return node.value
            if isinstance(node, _num):       # py<3.8
                return node.n
            if isinstance(node, _str):       # py<3.8
                return node.s
            if isinstance(node, _nc):  # py<3.8
                return node.value
            if isinstance(node, ast.Name):
                # By the time we reach here, all Name nodes have been
                # substituted with Constant nodes by _FieldSubstitutor.
                # A remaining Name is an unexpected reference -> reject.
                return False
            if isinstance(node, (ast.List, ast.Tuple)):
                return type(node.elts)([_value(e) for e in node.elts])
            if isinstance(node, ast.Set):
                return {_value(e) for e in node.elts}
            if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
                return not _value(node.operand)
            raise ValueError(f"disallowed node in value position: {type(node).__name__}")

        _CMP_OP = {
            ast.Eq: operator.eq, ast.NotEq: operator.ne,
            ast.Lt: operator.lt, ast.LtE: operator.le,
            ast.Gt: operator.gt, ast.GtE: operator.ge,
            ast.Is: operator.is_, ast.IsNot: operator.is_not,
        }

        def _check(node: ast.AST) -> bool:
            # expr_context nodes (Load/Store/Del) are structural markers
            # attached to Name/List/Tuple/Subscript; they carry no semantics
            # and are harmless, so skip them rather than listing them.
            if isinstance(node, ast.expr_context):
                return True
            if type(node) not in _ALLOWED_NODES:
                return False
            for _child in ast.iter_child_nodes(node):
                if not _check(_child):
                    return False
            return True

        def _eval_cmp(node: ast.Compare) -> bool:
            left = _value(node.left)
            for op_cls, comparator in zip(node.ops, node.comparators):
                right = _value(comparator)
                # ``in`` / ``not in`` are handled inline: membership requires
                # the right operand to be an actual container.
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

        if not _check(tree):
            return False

        try:
            return bool(_eval(tree.body))
        except Exception:
            return False


# ---- Rule execution engine ----


class RuleEngine:
    """Execute YAML-defined rules against extracted document data."""

    def __init__(
        self,
        doc_data: dict,
        rules_path: Optional[str] = None,
        rules_data: Optional[dict] = None,
    ) -> None:
        """
        Args:
            doc_data: Extracted document data from a DocxExtractor etc.
            rules_path: Path to a YAML rule file.
            rules_data: Rule dict (takes priority over ``rules_path``).
        """
        self.doc_data = doc_data
        if rules_data is not None:
            self.rules_data: dict = rules_data
        elif rules_path:
            self.rules_data = self._load_rules(rules_path)
        else:
            self.rules_data = {"segments": [], "rules": [], "skip_files": []}
        self.errors: list[dict] = []
        self._segment_labels: list[Optional[str]] = []

    @staticmethod
    def _load_rules(rules_path: str) -> dict:
        """Load a YAML rule file and return a normalised dict."""
        with open(rules_path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        return {
            "schema": data.get("schema", "contract-v1"),
            "domain": data.get("domain", ""),
            "segments": data.get("segments") or [],
            "rules": data.get("rules") or [],
            "skip_files": data.get("skip_files") or [],
        }

    # -- paragraph classification --

    def _classify_paragraphs(self) -> list:
        """Assign a segment_id label to each paragraph.

        Returns:
            List of segment_id strings (``None`` if no segment matched).
        """
        paragraphs = self.doc_data.get("paragraphs", [])
        segments = self.rules_data.get("segments", [])

        parsers = []
        for seg in segments:
            seg_id = seg.get("id")
            match_expr = seg.get("match", "")
            try:
                parser = MatchParser(match_expr)
            except Exception:
                parser = None
            parsers.append((seg_id, parser))

        h1_ranges = self._compute_h1_ranges(paragraphs)
        labels: list[Optional[str]] = []
        for i, para in enumerate(paragraphs):
            label = None
            text = (para.get("text") or "").strip()
            if text:
                for seg_id, parser in parsers:
                    if parser is None:
                        continue
                    try:
                        if parser.matches(para, i, paragraphs, h1_ranges):
                            label = seg_id
                            break
                    except Exception:
                        continue
            labels.append(label)
        self._segment_labels = labels
        return labels

    @staticmethod
    def _compute_h1_ranges(paragraphs: list) -> dict:
        """Compute title ranges for chapter-scope matching.

        Returns:
            {title_text: (start_idx, end_idx)}
        """
        STANDALONE_TITLES = {
            "摘要", "abstract", "关键词", "keywords",
            "目录", "contents", "tableofcontents",
            "参考文献", "references", "bibliography",
            "致谢", "acknowledgments", "acknowledgement",
            "附录", "appendix",
        }

        h1_indices: list[tuple[int, str]] = []
        for i, p in enumerate(paragraphs):
            text = (p.get("text") or "").strip()
            if not text or len(text) > 30:
                continue
            if re.search(r"\t\s*\d+\s*$", text) or re.search(r"\.{3,}\s*\d+\s*$", text):
                continue
            style = (p.get("style") or "").lower()
            heading_level = p.get("heading_level", 0)
            size = p.get("size") or 0
            text_lower = text.lower().replace(" ", "")

            is_section = (
                heading_level >= 1
                or "heading" in style
                or "标题" in style
                or bool(re.match(r"^第[一二三四五六七八九十\d]+章", text))
                or bool(re.match(r"^\d+\s", text))
                or (size >= 14 and len(text) <= 20)
                or text_lower in STANDALONE_TITLES
                or any(text_lower.startswith(t) for t in STANDALONE_TITLES)
            )
            if is_section:
                h1_indices.append((i, text))

        ranges: dict[str, tuple[int, int]] = {}
        for j, (idx, title) in enumerate(h1_indices):
            end_idx = h1_indices[j + 1][0] - 1 if j + 1 < len(h1_indices) else len(paragraphs) - 1
            ranges[title.replace(" ", "").strip()] = (idx, end_idx)
        return ranges

    # -- rule execution --

    def validate(self) -> list:
        """Execute all rules and return a list of error dicts.

        Each error dict may carry ``action`` / ``action_value`` fields when the
        originating rule declares a ``format_action`` -- the repair path in
        ``cli._check_fix_core`` reads these directly instead of re-parsing the
        rule file.
        """
        self.errors = []
        self._missing_seen: set[tuple[str, str]] = set()
        paragraphs = self.doc_data.get("paragraphs", [])
        labels = self._classify_paragraphs()

        # Build segment->indices mapping
        segment_to_indices: dict[str, list[int]] = {}
        for i, label in enumerate(labels):
            if label is None:
                continue
            segment_to_indices.setdefault(label, []).append(i)

        source_format = self.doc_data.get("source_format", "")
        for rule in self.rules_data.get("rules", []):
            file_types = rule.get("file_types")
            if file_types and source_format and source_format not in file_types:
                continue
            try:
                self._execute_rule(rule, segment_to_indices, paragraphs, labels)
            except Exception as exc:
                self.errors.append({
                    "type": "rule_error",
                    "description": f"Rule execution error: {exc}",
                    "location": f"Rule: {rule}",
                    "paragraph_index": None,
                })
        return self.errors

    def _execute_rule(
        self,
        rule: dict,
        segment_to_indices: dict,
        paragraphs: list,
        labels: list,
    ) -> None:
        """Dispatch a single rule to the appropriate op handler."""
        seg_id = rule.get("segment")
        op = rule.get("op")
        if not seg_id or not op:
            return
        if op == "skip":
            return

        indices = segment_to_indices.get(seg_id, [])
        if op in ("before", "after"):
            self._execute_order_rule(rule, segment_to_indices)
            return

        # format_action is only meaningful for per-paragraph value checks
        # (eq / in / regex). Structural ops (range / count_* / exists /
        # order) cannot be repaired on a per-paragraph basis.
        action = rule.get("format_action") if op in ("eq", "in", "regex") else None
        # For `in` op the rule value is a list; repair targets the first
        # allowed value. Callers writing repairable rules should keep the
        # canonical value first in the list.
        action_value = rule.get("value")
        if op == "in" and action_value is not None and isinstance(action_value, list):
            action_value = action_value[0] if action_value else None

        if op == "eq":
            self._op_eq(
                seg_id, indices, paragraphs,
                rule.get("field"), rule.get("value"),
                rule.get("tol", 0), rule.get("msg", ""),
                action, action_value,
            )
        elif op == "in":
            self._op_in(
                seg_id, indices, paragraphs,
                rule.get("field"), rule.get("value"),
                rule.get("msg", ""),
                action, action_value,
            )
        elif op == "regex":
            self._op_regex(
                seg_id, indices, paragraphs,
                rule.get("field"), rule.get("value"),
                rule.get("msg", ""),
                action, action_value,
            )
        elif op == "range":
            self._op_range(
                seg_id, indices, paragraphs,
                rule.get("field"), rule.get("value"),
                rule.get("msg", ""),
            )
        elif op == "count_min":
            self._op_count_min(seg_id, indices, rule.get("value", 0), rule.get("msg", ""))
        elif op == "count_max":
            self._op_count_max(seg_id, indices, rule.get("value", 0), rule.get("msg", ""))
        elif op == "exists":
            self._op_exists(seg_id, indices, rule.get("msg", ""))

    # -- op implementations --

    def _op_eq(
        self, seg_id: str, indices: list, paragraphs: list,
        field: str, value: Any, tol: float, msg: str,
        action: Optional[str] = None, action_value: Any = None,
    ) -> None:
        """Check exact equality with optional tolerance."""
        for idx in indices:
            para = paragraphs[idx]
            text = para.get("text", "")
            if not text or not text.strip():
                continue
            actual = para.get(field)
            if actual is None or actual == "":
                self._add_missing(seg_id, para, idx, field)
                continue
            if field == "line_spacing" and isinstance(actual, (int, float)) and actual > 5:
                continue
            if field == "font":
                if normalize_font(str(actual)) == normalize_font(str(value)):
                    continue
                self._add_error(seg_id, para, idx, msg or
                                f"{field} should be {value}, got {actual}",
                                action=action, action_value=action_value)
            elif isinstance(actual, (int, float)) and isinstance(value, (int, float)):
                if abs(actual - value) <= tol:
                    continue
                self._add_error(seg_id, para, idx, msg or
                                f"{field} should be {value} (tol±{tol}), got {actual}",
                                action=action, action_value=action_value)
            else:
                if actual == value:
                    continue
                self._add_error(seg_id, para, idx, msg or
                                f"{field} should be {value}, got {actual}",
                                action=action, action_value=action_value)

    def _op_in(
        self, seg_id: str, indices: list, paragraphs: list,
        field: str, value: Any, msg: str,
        action: Optional[str] = None, action_value: Any = None,
    ) -> None:
        """Check that field value is within an allowed list."""
        if not isinstance(value, list):
            value = [value]
        value_norm = [normalize_font(str(v)) for v in value] if field == "font" else value
        for idx in indices:
            para = paragraphs[idx]
            text = para.get("text", "")
            if not text or not text.strip():
                continue
            actual = para.get(field)
            if actual is None or actual == "":
                self._add_missing(seg_id, para, idx, field)
                continue
            if field == "font" and actual:
                if normalize_font(str(actual)) in value_norm:
                    continue
            else:
                if actual in value:
                    continue
            self._add_error(seg_id, para, idx, msg or
                            f"{field} should be one of {value}, got {actual}",
                            action=action, action_value=action_value)

    def _op_regex(
        self, seg_id: str, indices: list, paragraphs: list,
        field: str, pattern: str, msg: str,
        action: Optional[str] = None, action_value: Any = None,
    ) -> None:
        """Check that field value matches a regex pattern."""
        try:
            regex = re.compile(pattern)
        except re.error as exc:
            self.errors.append({
                "type": "rule_error",
                "description": f"Invalid regex {pattern}: {exc}",
                "location": f"regex {field}",
                "paragraph_index": None,
            })
            return
        for idx in indices:
            para = paragraphs[idx]
            actual = para.get(field)
            if actual is None:
                self._add_missing(seg_id, para, idx, field)
                continue
            if regex.search(str(actual)):
                continue
            self._add_error(seg_id, para, idx, msg or
                            f"{field} should match {pattern}, got {actual}",
                            action=action, action_value=action_value)

    def _op_range(
        self, seg_id: str, indices: list, paragraphs: list,
        field: str, value: Any, msg: str,
    ) -> None:
        """Check that a numeric field falls within [min, max]."""
        if not isinstance(value, list) or len(value) != 2:
            self.errors.append({
                "type": "rule_error",
                "description": "range op value must be [min, max]",
                "location": f"range {field}",
                "paragraph_index": None,
            })
            return
        min_val, max_val = value
        for idx in indices:
            para = paragraphs[idx]
            actual = para.get(field)
            if actual is None or not isinstance(actual, (int, float)):
                if actual is None:
                    self._add_missing(seg_id, para, idx, field)
                continue
            if min_val <= actual <= max_val:
                continue
            self._add_error(seg_id, para, idx, msg or
                            f"{field} should be in {min_val}-{max_val}, got {actual}")

    def _op_count_min(self, seg_id: str, indices: list, value: int, msg: str) -> None:
        """Check minimum segment count."""
        if len(indices) < value:
            self.errors.append({
                "type": "count",
                "description": msg or f"{seg_id} count {len(indices)} < {value}",
                "location": seg_id,
                "paragraph_index": None,
                "segment": seg_id,
            })

    def _op_count_max(self, seg_id: str, indices: list, value: int, msg: str) -> None:
        """Check maximum segment count."""
        if len(indices) > value:
            self.errors.append({
                "type": "count",
                "description": msg or f"{seg_id} count {len(indices)} > {value}",
                "location": seg_id,
                "paragraph_index": None,
                "segment": seg_id,
            })

    def _op_exists(self, seg_id: str, indices: list, msg: str) -> None:
        """Check that a segment exists."""
        if len(indices) == 0:
            self.errors.append({
                "type": "exists",
                "description": msg or f"Missing segment: {seg_id}",
                "location": seg_id,
                "paragraph_index": None,
                "segment": seg_id,
            })

    def _add_error(
        self, seg_id: str, para: dict, para_idx: int, msg: str,
        err_type: str = "format",
        action: Optional[str] = None,
        action_value: Any = None,
    ) -> None:
        """Append a format error to the engine's error list.

        When the originating rule declares a ``format_action``, ``action`` and
        ``action_value`` are carried on the error dict so the repair path can
        apply the fix without re-parsing the rule file.
        """
        if para.get("page") is not None:
            loc = f"Page {para.get('page')}, Paragraph {para_idx}"
        else:
            loc = f"Paragraph {para_idx}"
        err: dict = {
            "type": err_type,
            "description": msg,
            "location": loc,
            "paragraph_index": para.get("paragraph_index", para_idx),
            "segment": seg_id,
        }
        if action:
            err["action"] = action
            err["action_value"] = action_value
        self.errors.append(err)

    def _add_missing(
        self, seg_id: str, para: dict, para_idx: int, field: str,
    ) -> None:
        """Record a missing-field warning.

        A missing field means the extractor could not read the value -- it is
        NOT the same as "the field is compliant". Emitting a ``missing``-type
        error makes this visible in the report instead of silently treating
        the paragraph as passing.

        Duplicate missing reports for the same (paragraph_index, field) are
        deduplicated to avoid flooding the report when multiple rules check
        the same field on the same paragraph.
        """
        pidx = para.get("paragraph_index", para_idx)
        dedup_key = (str(pidx), field)
        if dedup_key in self._missing_seen:
            return
        self._missing_seen.add(dedup_key)
        loc = (
            f"Page {para.get('page')}, Paragraph {pidx}"
            if para.get("page") is not None
            else f"Paragraph {pidx}"
        )
        self.errors.append({
            "type": "missing",
            "description": (
                f"字段 '{field}' 无法读取（提取器未返回该字段，"
                "可能是文档格式限制或该段落无此属性）"
            ),
            "location": loc,
            "paragraph_index": pidx,
            "segment": seg_id,
            "field": field,
        })

    def _execute_order_rule(self, rule: dict, segment_to_indices: dict) -> None:
        """Check before/after ordering between two segments."""
        seg_id = rule.get("segment")
        op = rule.get("op")
        target = rule.get("target")
        msg = rule.get("msg", "")
        if not target:
            return
        my_idx = segment_to_indices.get(seg_id, [])
        tgt_idx = segment_to_indices.get(target, [])
        if not my_idx or not tgt_idx:
            return
        if op == "before" and my_idx[0] >= tgt_idx[0]:
            self.errors.append({
                "type": "order",
                "description": msg or f"{seg_id} should be before {target}",
                "location": f"{seg_id} -> {target}",
                "paragraph_index": None,
                "segment": seg_id,
            })
        elif op == "after" and my_idx[0] <= tgt_idx[0]:
            self.errors.append({
                "type": "order",
                "description": msg or f"{seg_id} should be after {target}",
                "location": f"{seg_id} -> {target}",
                "paragraph_index": None,
                "segment": seg_id,
            })
