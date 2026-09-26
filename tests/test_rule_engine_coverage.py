"""规则覆盖诊断的测试。

修复前的问题：规则文件与文档不匹配时，引擎沉默地给出结果，用户无法判断
"0 条错误"到底意味着"文档合规"还是"这份规则根本没挂上这份文档"。

实测同一份论文：

* `config/rules_lunwen.yaml`（"第X章" 编号）
    → 6 条错误，其中 7/25 条规则从头到尾从未执行
* `config/schools/rules_湖北汽院_优秀小论文.yaml`（"1 xxx" 编号）
    → 497 条错误，其中 8/80 条规则从未执行

两份结果都没有任何"这些规则在本文档上没跑过"的提示。

`RuleEngine.coverage_warnings()` 把这种"规则没挂上文档"的情况变成显式告警。
这些是诊断、不是判决：既不进 errors 列表，也不参与修复/批注定位，
更不替用户判定文档是否合规。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from engine import RuleEngine


def _doc(paragraphs, source_format="docx"):
    return {"paragraphs": [
        {"text": t, "font": "宋体", "size": 12, "paragraph_index": i}
        for i, t in enumerate(paragraphs)
    ], "source_format": source_format}


def _run(doc_data, segments, rules):
    eng = RuleEngine(doc_data, rules_data={
        "schema": "contract-v2",
        "domain": "测试",
        "segments": segments,
        "rules": rules,
    })
    errors = eng.validate()
    return eng, errors


class TestNoCoverage:
    """规则一个段落都没匹配到——最典型的错配信号。"""

    def test_zero_coverage_warns(self):
        # 不写 else 兜底段类，才会出现 0 覆盖
        eng, errors = _run(
            _doc(["1. 第一章标题", "这是正文内容。", "这是第二行正文。"]),
            [{"id": "h1", "match": "regex('^第')"}],
            [],
        )
        assert errors == []
        cov = eng.coverage_warnings()
        assert len(cov) == 1
        assert "未命中任何段落" in cov[0]
        assert "0/3" in cov[0]
        assert "不代表格式合规" in cov[0]

    def test_zero_coverage_message_names_the_ratio(self):
        eng, _ = _run(
            _doc(["a", "b"]),
            [{"id": "h1", "match": "regex('^第')"}],
            [],
        )
        (msg,) = eng.coverage_warnings()
        assert "0/2 段被归类" in msg
        # 0 覆盖时不再额外点名未匹配的段类，同一条信息不报两遍
        assert "未匹配到任何段落" not in msg

    def test_empty_document_does_not_warn(self):
        eng, _ = _run(_doc(["", "   "]), [{"id": "body", "match": "else"}], [])
        assert eng.coverage_warnings() == []
        assert eng.coverage["text_paragraphs"] == 0


class TestPartialCoverage:
    """大部分段落没被归类，但也不是 0——低覆盖率告警。"""

    def test_low_coverage_with_no_errors_warns(self):
        eng, errors = _run(
            _doc(["第一章 引言", "正文一", "正文二", "正文三"]),
            [{"id": "h1", "match": "regex('^第')"}],
            [{"segment": "h1", "field": "size", "op": "eq", "value": 12}],
        )
        assert errors == []
        cov = eng.coverage_warnings()
        assert len(cov) == 1
        assert "覆盖率过低" in cov[0]
        assert "1/4 段被归类" in cov[0]
        assert "不代表格式合规" in cov[0]

    def test_minority_unlabeled_warns_softer(self):
        """未归类段落不足一半时只提一句，不上升到覆盖率告警。"""
        eng, _ = _run(
            _doc(["第一章 a", "第二章 b", "第三章 c", "第四章 d",
                  "第五章 e", "第六章 f",
                  "正文一", "正文二", "正文三", "正文四"]),
            [{"id": "h1", "match": "regex('^第')"}],
            [],
        )
        cov = eng.coverage_warnings()
        assert len(cov) == 1
        assert "未被任何段类覆盖" in cov[0]
        assert "4/10" in cov[0]
        assert "覆盖率过低" not in cov[0]

    def test_low_coverage_suppressed_when_there_are_real_errors(self):
        """有真实格式错误时不再重复覆盖率告警，避免噪音。"""
        eng, errors = _run(
            _doc(["第一章 引言", "正文一", "正文二", "正文三"]),
            [{"id": "h1", "match": "regex('^第')"}],
            [{"segment": "h1", "field": "font", "op": "eq", "value": "黑体"}],
        )
        assert any(e["type"] != "missing" for e in errors)
        msgs = " ".join(eng.coverage_warnings())
        assert "覆盖率过低" not in msgs
        assert "未命中任何段落" not in msgs


class TestGoodCoverage:
    """规则正常挂上文档时不得产生任何告警。"""

    def test_full_coverage_no_errors_is_silent(self):
        eng, errors = _run(
            _doc(["第一章 引言", "这是正文内容。"]),
            [{"id": "body", "match": "else"}],
            [{"segment": "body", "field": "font", "op": "eq", "value": "宋体"}],
        )
        assert errors == []
        assert eng.coverage_warnings() == []

    def test_full_coverage_with_errors_is_silent(self):
        eng, errors = _run(
            _doc(["第一章 引言"]),
            [{"id": "body", "match": "else"}],
            [{"segment": "body", "field": "font", "op": "eq", "value": "黑体"}],
        )
        assert errors
        assert eng.coverage_warnings() == []


class TestUnmatchedSegmentsAndDeadRules:
    """声明了但没匹配到的段类 / 指向空段类的规则，要显式点出来。"""

    def test_unmatched_declared_segment_is_reported(self):
        eng, errors = _run(
            _doc(["这是正文内容。"]),
            [{"id": "h1", "match": "regex('^第')"}, {"id": "body", "match": "else"}],
            [{"segment": "body", "field": "font", "op": "eq", "value": "黑体"}],
        )
        assert any(e["type"] != "missing" for e in errors)
        (msg,) = eng.coverage_warnings()
        assert "h1" in msg
        assert "未匹配到任何段落" in msg

    def test_dead_rules_are_reported(self):
        eng, errors = _run(
            _doc(["这是正文内容。"]),
            [{"id": "body", "match": "else"}],
            [{"segment": "h1", "field": "font", "op": "eq", "value": "黑体"}],
        )
        assert errors == []
        (msg,) = eng.coverage_warnings()
        assert "1/1 条规则" in msg
        assert "目标段落不存在" in msg

    def test_dead_rule_on_undeclared_segment_is_named(self):
        """规则引用了 segments 里根本没声明的 id——要指名道姓。"""
        eng, _ = _run(
            _doc(["这是正文内容。"]),
            [{"id": "body", "match": "else"}],
            [{"segment": "h1", "field": "font", "op": "eq", "value": "黑体"}],
        )
        assert eng.coverage["dead_rule_segments"] == ["h1"]
        (msg,) = eng.coverage_warnings()
        assert "h1" in msg
        assert "未在 segments 中声明" in msg

    def test_dead_rule_on_unmatched_declared_segment_is_not_renamed(self):
        """已声明但没匹配上的段类，上面已经点过名，不重复说"未声明"。"""
        eng, _ = _run(
            _doc(["这是正文内容。"]),
            [{"id": "h1", "match": "regex('^第')"}, {"id": "body", "match": "else"}],
            [{"segment": "h1", "field": "font", "op": "eq", "value": "黑体"}],
        )
        msgs = eng.coverage_warnings()
        dead = [m for m in msgs if "条规则" in m]
        assert dead
        assert "未在 segments 中声明" not in dead[0]

    def test_empty_coverage_before_validate_is_silent(self):
        """validate() 之前 coverage 为空，不得抛异常或误报。"""
        eng = RuleEngine(
            doc_data=_doc(["x"]),
            rules_data={"segments": [], "rules": []},
        )
        assert eng.coverage == {}
        assert eng.coverage_warnings() == []


class TestCoverageShape:
    """coverage 字典自身的字段语义。"""

    def test_counts_exclude_empty_paragraphs(self):
        eng, _ = _run(
            _doc(["", "   ", "第一章 引言", "正文"]),
            [{"id": "body", "match": "else"}],
            [],
        )
        cov = eng.coverage
        assert cov["total_paragraphs"] == 4
        assert cov["text_paragraphs"] == 2
        assert cov["unlabeled_paragraphs"] == 0
        assert cov["segment_match_counts"] == {"body": 2}
        assert cov["unmatched_segments"] == []
        assert cov["dead_rules"] == 0
        assert cov["total_rules"] == 0

    def test_empty_paragraphs_are_the_only_reason_for_none_label(self):
        """有 else 兜底时，未归类的段落只可能是空段落——空段落不进分母。"""
        eng, _ = _run(
            _doc(["内容", ""]),
            [{"id": "body", "match": "else"}],
            [],
        )
        assert eng.coverage["unlabeled_paragraphs"] == 0
        assert eng.coverage["text_paragraphs"] == 1
        assert eng._segment_labels == ["body", None]


class TestWarningsAreNotErrors:
    """诊断必须是诊断：不进 errors，不参与批注定位。"""

    def test_warnings_do_not_pollute_errors(self):
        """0 覆盖 + 0 错误时 errors 仍为空，覆盖率条目不得混入其中。"""
        _, errors = _run(
            _doc(["1. 标题", "正文"]),
            [{"id": "h1", "match": "regex('^第')"}],
            [],
        )
        assert errors == []

    def test_errors_keep_the_documented_shape(self):
        """覆盖率诊断不改变真实错误的字段结构。"""
        _, errors = _run(
            _doc(["第一章 引言"]),
            [{"id": "body", "match": "else"}],
            [{"segment": "body", "field": "font", "op": "eq", "value": "黑体"}],
        )
        assert errors, "应当报出真实的字体错误"
        for err in errors:
            for key in ("type", "description", "location", "paragraph_index", "segment"):
                assert key in err, key


# ---------------------------------------------------------------------------
# 真实规则文件错配（原始缺陷的回归用例）
# ---------------------------------------------------------------------------

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
THESIS = os.path.join(ROOT, "input", "08-李嗣麒-毕业设计（论文）.docx")
RULES_LUNWEN = os.path.join(ROOT, "config", "rules_lunwen.yaml")
RULES_XIAOLUNWEN = os.path.join(ROOT, "config", "schools", "rules_湖北汽院_优秀小论文.yaml")

_NEEDS_REAL_FIXTURE = os.path.exists(THESIS)


# ---------------------------------------------------------------------------
# 错配判据的分类器（CLI / GUI 用它决定"能不能显示成通过"）
# ---------------------------------------------------------------------------

class TestMismatchClassifier:
    """`mismatch_warnings` 只挑出真正的错配判据。

    CLI 与 GUI 靠它决定能否把结果报成"通过"。这里最容易出的错是：
    一份写得挺好的规则也可能声明了本文档不存在的段类（如"目录""页眉页脚"），
    于是产生了死规则告警——它不能被当作文档不合规的证据。
    """

    def test_only_mismatch_verdicts_pass_through(self):
        from engine.rule_engine import mismatch_warnings
        zero_cov = (
            "规则未命中任何段落（0/328 段被归类）：这份规则文件很可能与本文档"
            "不匹配，0 条错误不代表格式合规"
        )
        low_cov = "规则覆盖率过低：仅 1/328 段被归类，0 条错误不代表格式合规"
        dead_rules = "7/25 条规则的目标段落不存在，已按未匹配跳过"
        unmatched = "以下段类在本文中未匹配到任何段落：header、toc、figure_text"
        minor = "未发现格式错误，但有 4/10 段未被任何段类覆盖；若规则本应覆盖全文，请检查规则文件"
        kept = mismatch_warnings([zero_cov, low_cov, dead_rules, unmatched, minor])
        assert kept == [zero_cov, low_cov]

    def test_undeclared_segment_is_not_a_mismatch_verdict(self):
        """"未在 segments 中声明"是规则文件的 bug，不是文档不合规的证据。

        段表里要留一个能命中的段类，否则 0 覆盖告警会一起冒出来，
        那不是我们要验证的场景。
        """
        from engine.rule_engine import mismatch_warnings
        msgs = _run(
            _doc(["正文"]),
            [{"id": "body", "match": "else"}],
            [{"segment": "ghost", "field": "font", "op": "eq", "value": "黑体"}],
        )[0].coverage_warnings()
        assert len(msgs) == 1
        assert "未在 segments 中声明" in msgs[0]
        assert mismatch_warnings(msgs) == []

    def test_unmatched_declared_segment_is_not_a_mismatch_verdict(self):
        from engine.rule_engine import mismatch_warnings
        msgs = _run(
            _doc(["正文"]),
            [{"id": "h1", "match": "regex('^第')"}, {"id": "body", "match": "else"}],
            [],
        )[0].coverage_warnings()
        assert len(msgs) == 1
        assert mismatch_warnings(msgs) == []

    def test_empty_inputs_are_empty(self):
        from engine.rule_engine import mismatch_warnings
        assert mismatch_warnings([]) == []
        assert mismatch_warnings(None) == []


class TestRealRulesMismatch:
    """同一份论文、两套规则：从未经过的规则必须自己报出来。"""

    @pytest.fixture(scope="class")
    def thesis(self):
        from engine import get_extractor
        return get_extractor(THESIS).extract(THESIS)

    @pytest.mark.skipif(not _NEEDS_REAL_FIXTURE, reason="需要 input/ 下的真实论文")
    def test_never_run_rules_are_reported(self, thesis):
        """规则文件里的 else 兜底段类会吃光所有段落，所以靠"未归类段落数"
        发现不了错配——真正有效的信号是"多少条规则从头到尾没跑过"。"""
        eng = RuleEngine(thesis, rules_path=RULES_LUNWEN)
        eng.validate()
        cov = eng.coverage
        assert cov["text_paragraphs"] > 0
        assert cov["unlabeled_paragraphs"] == 0, "else 兜底段类会归类所有非空段落"
        assert cov["dead_rules"] > 0
        assert cov["unmatched_segments"]

    @pytest.mark.skipif(not _NEEDS_REAL_FIXTURE, reason="需要 input/ 下的真实论文")
    def test_never_run_rules_are_visible_to_the_user(self, thesis):
        eng = RuleEngine(thesis, rules_path=RULES_LUNWEN)
        eng.validate()
        msgs = eng.coverage_warnings()
        assert any("条规则" in m for m in msgs), msgs
        assert any("未匹配到任何段落" in m for m in msgs), msgs
        # 不得对文档合规性下结论——这些是诊断，不是判决
        assert not any("不匹配" in m for m in msgs)

    @pytest.mark.skipif(not _NEEDS_REAL_FIXTURE, reason="需要 input/ 下的真实论文")
    def test_matching_rules_file_does_not_claim_zero_coverage(self, thesis):
        """编号口径一致的规则文件正常挂上文档，不得给出错配判决。"""
        eng = RuleEngine(thesis, rules_path=RULES_XIAOLUNWEN)
        errors = eng.validate()
        assert errors, "该校规则与本文档编号口径一致，应当报出真实错误"
        cov = eng.coverage
        assert cov["unlabeled_paragraphs"] == 0
        msgs = eng.coverage_warnings()
        assert not any("未命中任何段落" in m for m in msgs)
        assert not any("不匹配" in m for m in msgs)
