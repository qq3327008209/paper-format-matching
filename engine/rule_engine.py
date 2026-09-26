"""Universal rule engine (domain-agnostic).

The engine only understands the schema-defined rule format and does not
contain any domain knowledge.  Switching domains requires only replacing
the YAML rule file -- engine code stays unchanged.

Rule file structure is documented in schema.md.
"""

import ast
import logging
import os
import re
from typing import Any, Optional

import yaml

from ._whitelist import safe_eval

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

        Delegates to :func:`engine._whitelist.safe_eval`, which enforces a
        strict node-type whitelist (no attribute access, calls, subscripts,
        lambdas, comprehensions, etc.) and returns ``False`` for anything
        outside the whitelist or any evaluation error.
        """
        return safe_eval(tree)


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
        self.coverage: dict = {}

    @staticmethod
    def _load_rules(rules_path: str) -> dict:
        """Load a YAML rule file and return a normalised dict."""
        with open(rules_path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        return {
            "schema": data.get("schema", "contract-v1"),
            "domain": data.get("domain", ""),
            **{k: data.get(k) or [] for k in ("segments", "rules", "skip_files")},
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
        self.coverage = self._build_coverage(paragraphs, labels, segment_to_indices)
        return self.errors

    def _build_coverage(self, paragraphs: list, labels: list,
                        segment_to_indices: dict) -> dict:
        """Measure how well the rule file attached to this document.

        Empty paragraphs never match any segment (``_classify_paragraphs`` only
        labels paragraphs with text), so coverage is measured over non-empty
        paragraphs only.  This is domain-agnostic: it measures attachment
        quality, never whether the document is compliant.
        """
        segments = self.rules_data.get("segments", [])
        rules = self.rules_data.get("rules", [])
        matched = {sid: len(ids) for sid, ids in segment_to_indices.items()}
        declared = [s.get("id") for s in segments if s.get("id")]
        unmatched_segments = [sid for sid in declared if not matched.get(sid)]
        dead_rules = [
            r for r in rules
            if r.get("segment") and not matched.get(r.get("segment"))
        ]
        # 点名死规则挂的段类，写规则的人才能据此定位到具体哪条 match 失效。
        dead_rule_segments = sorted({r.get("segment") for r in dead_rules})

        text_total = 0
        unlabeled = 0
        for i, para in enumerate(paragraphs):
            if not (para.get("text") or "").strip():
                continue
            text_total += 1
            if labels[i] is None:
                unlabeled += 1
        return {
            "total_paragraphs": len(paragraphs),
            "text_paragraphs": text_total,
            "unlabeled_paragraphs": unlabeled,
            "segment_match_counts": matched,
            "unmatched_segments": unmatched_segments,
            "dead_rules": len(dead_rules),
            "dead_rule_segments": dead_rule_segments,
            "total_rules": len(rules),
        }

    def coverage_warnings(self) -> list:
        """Return human-readable advisories about rule-file attachment quality.

        These are advisories, never errors: a mismatched rule file must not
        masquerade as either a compliant document (0 errors) or a document full
        of violations (hundreds of false positives).  An empty list means no
        attachment problem was detected, not that every rule ran.

        Thresholds are about attachment, not compliance: if more than half the
        document's paragraphs fall outside every declared segment, the rule file
        is almost certainly aimed at a different document shape.

        Rule files with an ``else`` fallback segment absorb every non-empty
        paragraph, so unlabelled-paragraph counts stay at zero and say nothing
        about attachment.  In that case the dead segments and dead rules are the
        real signal, and are always reported regardless of error count.
        """
        cov = self.coverage
        warnings: list[str] = []
        if not cov:
            return warnings
        text_total = cov.get("text_paragraphs", 0)
        if not text_total:
            return warnings
        unlabeled = cov.get("unlabeled_paragraphs", 0)
        real_errors = [e for e in self.errors if e.get("type") != "missing"]

        if unlabeled == text_total:
            warnings.append(
                f"规则未命中任何段落（0/{text_total} 段被归类）：这份规则文件很可能"
                "与本文档不匹配，0 条错误不代表格式合规"
            )
        elif not real_errors and unlabeled * 2 >= text_total:
            warnings.append(
                f"规则覆盖率过低：仅 {text_total - unlabeled}/{text_total} 段被归类，"
                "0 条错误不代表格式合规，请确认规则文件与本文档匹配"
            )
        elif not real_errors and unlabeled:
            warnings.append(
                f"未发现格式错误，但有 {unlabeled}/{text_total} 段未被任何段类覆盖；"
                "若规则本应覆盖全文，请检查规则文件"
            )

        # 0 覆盖时"哪些段类没匹配上"已被上面的总覆盖率告警包含，不重复点名。
        if cov.get("unmatched_segments") and unlabeled != text_total:
            warnings.append(
                "以下段类在本文中未匹配到任何段落："
                f"{'、'.join(cov['unmatched_segments'])}"
            )
        if cov.get("dead_rules"):
            # 已声明但没匹配上的段类在上面已经点过名，这里只补充"根本没声明过"
            # 的那种——那属于规则文件自身写错了 segment id。
            unmatched_set = set(cov.get("unmatched_segments") or [])
            ghost_ids = [
                s for s in (cov.get("dead_rule_segments") or [])
                if s not in unmatched_set
            ]
            detail = (
                "，其中 " + "、".join(str(s) for s in ghost_ids) + " 未在 segments 中声明"
                if ghost_ids else ""
            )
            warnings.append(
                f"{cov['dead_rules']}/{cov['total_rules']} 条规则的目标段落不存在"
                f"{detail}，已按未匹配跳过"
            )
        return warnings

    # -- op dispatch table --
    # Each handler receives (rule, indices, paragraphs, labels) and is
    # responsible for extracting the fields it needs from ``rule``.  Adding a
    # new op only requires appending a row here; the handler method itself
    # lives alongside the other ``_op_*`` implementations below.

    _OP_TABLE = {
        "eq": lambda self, rule, indices, paragraphs, labels: self._op_eq(
            rule.get("segment"), indices, paragraphs,
            rule.get("field"), rule.get("value"),
            rule.get("tol", 0), rule.get("msg", ""),
            rule.get("format_action"), rule.get("value"),
        ),
        "in": lambda self, rule, indices, paragraphs, labels: self._op_in(
            rule.get("segment"), indices, paragraphs,
            rule.get("field"), rule.get("value"),
            rule.get("msg", ""),
            rule.get("format_action"),
            (
                rule.get("value")[0]
                if rule.get("value") and isinstance(rule.get("value"), list)
                else rule.get("value")
            ),
        ),
        "regex": lambda self, rule, indices, paragraphs, labels: self._op_regex(
            rule.get("segment"), indices, paragraphs,
            rule.get("field"), rule.get("value"),
            rule.get("msg", ""),
            rule.get("format_action"), rule.get("value"),
        ),
        "range": lambda self, rule, indices, paragraphs, labels: self._op_range(
            rule.get("segment"), indices, paragraphs,
            rule.get("field"), rule.get("value"),
            rule.get("msg", ""),
        ),
        "count_min": lambda self, rule, indices, paragraphs, labels: self._op_count_min(
            rule.get("segment"), indices, rule.get("value", 0), rule.get("msg", ""),
        ),
        "count_max": lambda self, rule, indices, paragraphs, labels: self._op_count_max(
            rule.get("segment"), indices, rule.get("value", 0), rule.get("msg", ""),
        ),
        "exists": lambda self, rule, indices, paragraphs, labels: self._op_exists(
            rule.get("segment"), indices, rule.get("msg", ""),
        ),
        # 序列编号连续性校验：使用标准 (rule, indices, paragraphs, labels) 签名
        "sequence_consecutive": lambda self, rule, indices, paragraphs, labels: self._op_sequence_consecutive(
            rule, indices, paragraphs, labels,
        ),
        # 文档级 op：仅需 rule，不依赖段索引
        "page_margins": lambda self, rule, indices, paragraphs, labels: self._op_page_margins(rule),
        "page_size": lambda self, rule, indices, paragraphs, labels: self._op_page_size(rule),
        # 需要 segment_to_indices 的文档级 op：lambda 多接收一个 stni 参数，
        # 由 _execute_rule 在分发时额外传入
        "cross_ref_order": lambda self, rule, indices, paragraphs, labels, stni: self._op_cross_ref_order(
            rule, stni, paragraphs,
        ),
        "document_structure": lambda self, rule, indices, paragraphs, labels, stni: self._op_document_structure(
            rule, stni,
        ),
    }

    def _execute_rule(
        self,
        rule: dict,
        segment_to_indices: dict,
        paragraphs: list,
        labels: list,
    ) -> None:
        """Dispatch a single rule to the appropriate op handler via ``_OP_TABLE``."""
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

        handler = self._OP_TABLE.get(op)
        if handler is None:
            return
        # cross_ref_order / document_structure 是文档级 op，需要 segment_to_indices
        # 故 lambda 多接收一个 stni 参数
        if op in ("cross_ref_order", "document_structure"):
            handler(self, rule, indices, paragraphs, labels, segment_to_indices)
            return
        handler(self, rule, indices, paragraphs, labels)

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
                # first_line_chars 回退：用 first_line_indent (cm) 推算字符数。
                # 五号(10.5pt) 字号下 1 字符 ≈ 0.37cm；若规则要求 0 字符而
                # 段也无任何首行缩进，则视为通过（标题段不缩进的常见情况）。
                if field == "first_line_chars":
                    fallback = self._first_line_chars_fallback(para, value)
                    if fallback is not None:
                        if fallback == value:
                            continue
                        self._add_error(
                            seg_id, para, idx, msg or
                            f"{field} should be {value}, got (inferred) {fallback}",
                            action=action, action_value=action_value,
                        )
                        continue
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

    @staticmethod
    def _first_line_chars_fallback(para: dict, expected: Any) -> Optional[int]:
        """Infer first_line_chars from first_line_indent (cm) when the
        ``w:firstLineChars`` attribute is absent.

        Returns:
            0 if the paragraph has no measurable first-line indent (covers
            the common case of标题段 not being indented).
            200 if the paragraph clearly has a ~2-character indent (0.6cm
            or more at 五号字).
            None if no inference can be made (caller should report missing).

        Note:
            Word 用 ``w:firstLineChars`` 存储字符单位缩进，但有些文档只设
            ``w:firstLine`` 磅值。这里按段字号近似换算回字符单位，并允许
            ±80 单位（约 0.8 字符）的容差——避免因字号定义、字符宽度
            计算差异（Word 默认 1 字符宽 = 字号磅值，但实际可能受字体影响）
            导致的浮点误差误报。
        """
        indent_cm = para.get("first_line_indent")
        if indent_cm is None:
            # 也没有磅值缩进属性 → 视为 0 字符（适用于标题段）
            return 0 if expected == 0 else None
        if not isinstance(indent_cm, (int, float)):
            return None
        if indent_cm < 0.01:
            return 0
        # 按段字号近似推算：1 字符宽 ≈ 字号 pt × 0.0353 cm/pt
        size = para.get("size") or 10.5
        char_cm = size * 0.0353
        if char_cm <= 0:
            return None
        chars = round(indent_cm / char_cm * 100)  # 转回 1/100 字符单位
        # 如果推算值与期望值差距在容差内（±80 单位 ≈ 0.8 字符），
        # 直接采用期望值，避免浮点/字体宽度差异误报。
        if isinstance(expected, (int, float)) and abs(chars - expected) <= 80:
            return int(expected)
        return chars

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

    # ---- 新增 op 实现 ----

    def _op_sequence_consecutive(
        self, rule: dict, indices: list, paragraphs: list, labels: list,
    ) -> None:
        """检查段内编号是否从 start 开始连续递增；可选父段嵌套校验。

        rule 字段：
          - pattern: 必填，正则表达式，取第一个捕获组作为编号
          - start:   起始值，默认 1
          - unique:  是否要求编号唯一，默认 True
          - parent_segment: 可选，父段 segment_id（用于嵌套编号如 1.1/1.2
            或图表按章编序 图N.M）。指定后按父段分组，每组内独立连续。
          - parent_pattern: 可选，父段编号提取正则（默认与 pattern 相同）。
            用于父段编号格式与子段不同的情况。
          - msg:     错误描述（覆盖默认提示）

        当 parent_segment 指定时：
        - 对每个子段，找到"最近的父段"（父段索引 < 当前段索引的最大者），
          取其编号作为分组键。
        - 同一分组键下的子段编号必须从 start 开始连续递增。
        - 不同分组键之间独立计数（如第1章的图1-1和第3章的图3-1不冲突）。
        """
        seg_id = rule.get("segment")
        pattern = rule.get("pattern")
        start = rule.get("start", 1)
        unique = rule.get("unique", True)
        parent_segment = rule.get("parent_segment")
        parent_pattern = rule.get("parent_pattern", pattern)
        msg = rule.get("msg", "")

        if not pattern:
            return
        try:
            regex = re.compile(pattern)
        except re.error as exc:
            self.errors.append({
                "type": "rule_error",
                "description": f"Invalid regex {pattern}: {exc}",
                "location": f"sequence_consecutive {seg_id}",
                "paragraph_index": None,
                "segment": seg_id,
            })
            return

        # 提取当前 segment 的 (段索引, 编号) 列表
        numbered: list[tuple[int, int]] = []
        for idx in indices:
            text = paragraphs[idx].get("text", "") or ""
            m = regex.search(text)
            if not m:
                continue
            try:
                num = int(m.group(1))
            except (ValueError, IndexError):
                continue
            numbered.append((idx, num))

        if not numbered:
            return

        # ---- 有父段：按父段分组，每组内独立检查连续性 ----
        if parent_segment:
            try:
                parent_regex = re.compile(parent_pattern)
            except re.error:
                parent_regex = regex

            # 收集父段 (段索引, 父编号) 列表
            parent_indices_list = [
                i for i, lab in enumerate(labels) if lab == parent_segment
            ]
            parent_numbers: list[tuple[int, int]] = []
            for pidx in parent_indices_list:
                ptext = paragraphs[pidx].get("text", "") or ""
                pm = parent_regex.search(ptext)
                if not pm:
                    continue
                try:
                    pnum = int(pm.group(1))
                except (ValueError, IndexError):
                    continue
                parent_numbers.append((pidx, pnum))

            # 为每个子段找最近的父段（用父段索引作为分组键，避免不同章节
            # 父段编号相同时误合并——例如 h2 "1.1" / "2.1" / "3.1" 都提取
            # 出编号 1，若按编号分组合把所有章节的 h3 混在一起检查）
            def _recent_parent_idx(idx: int) -> Optional[int]:
                recent: Optional[int] = None
                for pidx, _pnum in parent_numbers:
                    if pidx < idx:
                        recent = pidx
                    else:
                        break
                return recent

            # 按父段索引分组：每个父段独立计数
            groups: dict[int, list[tuple[int, int]]] = {}
            for idx, num in numbered:
                key = _recent_parent_idx(idx)
                if key is None:
                    # 无父段覆盖（如封面图、摘要图），归入 -1 组不检查
                    key = -1
                groups.setdefault(key, []).append((idx, num))

            # 每组内独立检查连续性 + 唯一性
            for key, items in groups.items():
                if key == -1:
                    continue  # 无父段覆盖的段跳过
                self._check_seq_in_group(
                    items, seg_id, start, unique, msg, paragraphs,
                )
            return

        # ---- 无父段：全局检查连续性 + 唯一性 ----
        self._check_seq_in_group(
            numbered, seg_id, start, unique, msg, paragraphs,
        )

    def _check_seq_in_group(
        self,
        items: list[tuple[int, int]],
        seg_id: str,
        start: int,
        unique: bool,
        msg: str,
        paragraphs: list,
    ) -> None:
        """对一组 (段索引, 编号) 检查从 start 开始的连续性和唯一性。"""
        seen: set[int] = set()
        expected = start
        for idx, num in items:
            if unique and num in seen:
                para = paragraphs[idx]
                self._add_error(
                    seg_id, para, idx,
                    msg or f"编号 {num} 重复",
                )
                continue
            seen.add(num)
            if num != expected:
                if num > expected:
                    # 跳号：报告缺少的编号
                    self.errors.append({
                        "type": "sequence",
                        "description": msg or f"缺少编号 {expected}",
                        "location": seg_id,
                        "paragraph_index": None,
                        "segment": seg_id,
                    })
                # 重置预期值为当前编号 + 1，继续校验后续是否连续
                expected = num + 1
            else:
                expected += 1

    def _op_cross_ref_order(
        self, rule: dict, segment_to_indices: dict, paragraphs: list,
    ) -> None:
        """检查图/表引用是否在对应 caption 之后出现（不允许前向引用）。

        rule 字段：
          - ref_segment:     扫描此 segment 的引用
          - caption_segment: 对应 caption segment
          - pattern:         引用 regex，需捕获 (kind, n1, n2) 三组
          - msg:             错误描述
        """
        seg_id = rule.get("segment")
        ref_segment = rule.get("ref_segment")
        caption_segment = rule.get("caption_segment")
        pattern = rule.get("pattern")
        msg = rule.get("msg", "")

        if not pattern or not ref_segment or not caption_segment:
            return
        try:
            regex = re.compile(pattern)
        except re.error as exc:
            self.errors.append({
                "type": "rule_error",
                "description": f"Invalid regex {pattern}: {exc}",
                "location": "cross_ref_order",
                "paragraph_index": None,
                "segment": seg_id,
            })
            return

        # 收集所有引用 (kind, n1, n2, ref_idx)
        refs: list[tuple[str, str, str, int]] = []
        for idx in segment_to_indices.get(ref_segment, []):
            text = paragraphs[idx].get("text", "") or ""
            for m in regex.finditer(text):
                groups = m.groups()
                if len(groups) < 3:
                    continue
                refs.append((groups[0], groups[1], groups[2], idx))

        # 收集所有 caption (kind, n1, n2, caption_idx)
        caption_map: dict[tuple[str, str, str], int] = {}
        for idx in segment_to_indices.get(caption_segment, []):
            text = paragraphs[idx].get("text", "") or ""
            m = regex.search(text)
            if not m:
                continue
            groups = m.groups()
            if len(groups) < 3:
                continue
            caption_map[(groups[0], groups[1], groups[2])] = idx

        for kind, n1, n2, ref_idx in refs:
            cap_idx = caption_map.get((kind, n1, n2))
            if cap_idx is None:
                continue
            if cap_idx < ref_idx:
                self.errors.append({
                    "type": "cross_ref",
                    "description": msg or (
                        f"{kind}{n1}.{n2} 出现在引述之前"
                        f"（引用在段{ref_idx}，图在段{cap_idx}）"
                    ),
                    "location": f"段 {ref_idx} -> 段 {cap_idx}",
                    "paragraph_index": paragraphs[ref_idx].get(
                        "paragraph_index", ref_idx,
                    ),
                    "segment": ref_segment,
                })

    def _op_document_structure(
        self, rule: dict, segment_to_indices: dict,
    ) -> None:
        """检查文档段结构是否齐全且顺序正确。

        rule 字段：
          - segments: 必填，期望的段 id 顺序列表
          - msg:      错误描述
        """
        segments = rule.get("segments")
        msg = rule.get("msg", "")

        if not segments or not isinstance(segments, list):
            return

        # 1. 检查每个 segment 是否存在，记录其最小段索引
        seg_min_idx: list[tuple[str, int]] = []
        for seg in segments:
            idxs = segment_to_indices.get(seg, [])
            if not idxs:
                self.errors.append({
                    "type": "structure",
                    "description": msg or f"缺少 {seg} 部分",
                    "location": seg,
                    "paragraph_index": None,
                    "segment": seg,
                })
                continue
            seg_min_idx.append((seg, min(idxs)))

        # 2. 检查顺序（最小索引严格递增）
        for i in range(1, len(seg_min_idx)):
            prev_seg, prev_idx = seg_min_idx[i - 1]
            cur_seg, cur_idx = seg_min_idx[i]
            if cur_idx <= prev_idx:
                self.errors.append({
                    "type": "structure",
                    "description": msg or f"{cur_seg} 应在 {prev_seg} 之前",
                    "location": f"{prev_seg} -> {cur_seg}",
                    "paragraph_index": None,
                    "segment": cur_seg,
                })

    def _op_page_margins(self, rule: dict) -> None:
        """检查页边距是否符合规范。

        rule 字段：
          - value:   dict {left, top, bottom, right}，期望值（单位 cm）
          - tol:     容差，默认 0.1
          - section: 节索引，默认 0
          - msg:     错误描述
        """
        value = rule.get("value") or {}
        tol = rule.get("tol", 0.1)
        section = rule.get("section", 0)
        msg = rule.get("msg", "")
        seg_id = rule.get("segment")

        sections = self.doc_data.get("sections") or []
        if not sections or section >= len(sections) or section < -len(sections):
            self.errors.append({
                "type": "rule_error",
                "description": f"section {section} 超出范围",
                "location": "page_margins",
                "paragraph_index": None,
                "segment": seg_id,
            })
            return
        section_data = sections[section]
        margins = section_data.get("margins") or {}

        for side in ("left", "top", "bottom", "right"):
            expected = value.get(side)
            if expected is None:
                continue
            actual = margins.get(side)
            if actual is None:
                self.errors.append({
                    "type": "missing",
                    "description": f"页边距 {side} 无法读取",
                    "location": f"section {section} {side}",
                    "paragraph_index": None,
                    "segment": seg_id,
                })
                continue
            if abs(actual - expected) > tol:
                self.errors.append({
                    "type": "format",
                    "description": msg or (
                        f"页边距 {side} 应为 {expected}"
                        f"（容差±{tol}），实际 {actual}"
                    ),
                    "location": f"section {section} {side}",
                    "paragraph_index": None,
                    "segment": seg_id,
                })

    def _op_page_size(self, rule: dict) -> None:
        """检查纸张尺寸是否符合规范。

        rule 字段：
          - value:   dict {width, height}，期望值（单位 cm）
          - tol:     容差，默认 0.1
          - section: 节索引，默认 0
          - msg:     错误描述
        """
        value = rule.get("value") or {}
        tol = rule.get("tol", 0.1)
        section = rule.get("section", 0)
        msg = rule.get("msg", "")
        seg_id = rule.get("segment")

        sections = self.doc_data.get("sections") or []
        if not sections or section >= len(sections) or section < -len(sections):
            self.errors.append({
                "type": "rule_error",
                "description": f"section {section} 超出范围",
                "location": "page_size",
                "paragraph_index": None,
                "segment": seg_id,
            })
            return
        section_data = sections[section]

        for dim, key in (("width", "page_width"), ("height", "page_height")):
            expected = value.get(dim)
            if expected is None:
                continue
            actual = section_data.get(key)
            if actual is None:
                self.errors.append({
                    "type": "missing",
                    "description": f"纸张 {dim} 无法读取",
                    "location": f"section {section} {dim}",
                    "paragraph_index": None,
                    "segment": seg_id,
                })
                continue
            if abs(actual - expected) > tol:
                self.errors.append({
                    "type": "format",
                    "description": msg or (
                        f"纸张 {dim} 应为 {expected}"
                        f"（容差±{tol}），实际 {actual}"
                    ),
                    "location": f"section {section} {dim}",
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

        Special-case suppression (returns without recording):
          - ``first_line_chars``: when the paragraph has a non-zero
            ``first_line_indent`` (cm), the document set the indent via the
            ``w:firstLine``磅值 attribute instead of ``w:firstLineChars``
            字符单位. The indent IS present, just expressed differently --
            suppress the warning to avoid noise.
          - ``font_en_most``: when the paragraph text contains no Latin
            letters, there is no English run to extract a font from --
            suppress the warning.
          - ``font_cn_most``: when the paragraph text contains no CJK
            characters, there is no Chinese run to extract a font from --
            suppress the warning.
        """
        # ---- 字段级抑制：用替代字段/文本特征判定"实际无问题" ----
        if field == "first_line_chars":
            indent_cm = para.get("first_line_indent")
            if isinstance(indent_cm, (int, float)) and indent_cm > 0.01:
                return
        elif field == "font_en_most":
            text = para.get("text") or ""
            if not re.search(r"[A-Za-z]", text):
                # 段落无任何拉丁字母 → 没有英文 run，font_en_most 必然为 None
                return
        elif field == "font_cn_most":
            text = para.get("text") or ""
            if not re.search(r"[\u4e00-\u9fff]", text):
                # 段落无任何 CJK 字符 → 没有中文 run，font_cn_most 必然为 None
                return

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

# ---------------------------------------------------------------------------
# Advisory classification
# ---------------------------------------------------------------------------

# 覆盖率告警里真正指向"规则文件与文档不匹配"的关键词。
#
# 死规则 / 未匹配段类只是信息（一份写得好的规则也可能声明了本文档不存在的
# 段类，比如"目录""页眉页脚"），不能拿来对合规性下结论；只有 0 覆盖和低覆盖
# 两条告警才构成"这份规则根本没挂上这份文档"的判据。
_MISMATCH_MARKERS = ("不匹配", "覆盖率过低")


def mismatch_warnings(warnings):
    """Return the coverage advisories that imply a rules/document mismatch.

    Used by the CLI and GUI to decide whether a clean result may be reported as
    "通过".  Rule-file advisories about never-run rules are deliberately
    excluded: they are actionable facts, not a verdict on the document.
    """
    return [w for w in (warnings or []) if any(m in w for m in _MISMATCH_MARKERS)]
