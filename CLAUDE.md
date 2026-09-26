# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目定位

文档格式校验与排版系统：**YAML 规则驱动**的检查 → 报告 → 修复闭环。**引擎代码不包含任何领域知识**（"毕业论文""公文""期刊"等只存在于 `config/*.yaml`），换领域只换规则文件、引擎零改动——这是所有设计取舍的第一原则。改动引擎时若引入了领域词，属于破坏该原则。

## 常用命令

```bash
pip install -r requirements.txt      # 运行依赖（含 PySide6，GUI 主入口需要）
python gui.py                        # PySide6 图形界面（主入口）
python cli.py                        # 交互式 REPL（遗留入口，同时是 GUI 复用的业务层）

python -m pytest tests/ -q           # 全量（13 个文件 / 135 个 test 函数，参数化后 194 个用例，约 2.0s）
python -m pytest tests/test_punctuation.py -q        # 单文件
python -m pytest tests/test_rule_engine.py::test_xxx -q   # 单用例
python -m pytest tests/ -q -k sequence                 # 按关键字筛选
```

无 `pyproject.toml` / `pytest.ini` / lint 配置，也没有 makefile。打包只在 CI 上跑：推送 `v*` tag 触发 `.github/workflows/build.yml`（**仅 Windows**，先跑 pytest，再 PyInstaller + Nuitka 双产物）。本地复现 Nuitka 产物：`python -m nuitka --standalone --onefile --output-dir=dist cli.py`（参数见 workflow）。REPL 内 `/test` 跑 校验→格式化→修复→批注 端到端自检，产物落 `examples/`。

## 架构

### 校验链路：`engine/`

```
docx/doc/pdf → extractors/ (策略模式, get_extractor 按扩展名分发)
            → RuleEngine (segments 分类 → _OP_TABLE 逐条校验)
            → errors[] → reporter.py (CSV) / annotator.py (Word 批注) / cli 修复分支
```

- **提取器字段契约**：段落是统一 dict（20+ 字段，含 `paragraph_index`，供修复/批注定位）。DOCX 提取走样式继承链（paragraph style → base_style 递归 ≤10 层 → `w:docDefaults`），中英文字体分开（`font_cn_most`/`font_en_most`）。**PDF 提取器是"尽力而为"：只取文本与页码，格式字段置 `None`**——引擎靠 `file_types` 跳过这些检查，不要把 PDF 的 `None` 当提取器缺陷修。
- **op 表驱动**：`_OP_TABLE`（`engine/rule_engine.py:528`）是 op 分发的唯一定义处，新增 op = 加一条 table 项 + 一个 `_op_x` 方法 + 测试。共 15 个 op 值：表内 12 个（`eq`/`in`/`regex`/`range`/`count_min`/`count_max`/`exists`/`sequence_consecutive`/`cross_ref_order`/`document_structure`/`page_margins`/`page_size`）+ 表外 3 个特殊分流（`before`/`after` 走 `_execute_order_rule`，`skip` 直接 return）。`cross_ref_order`/`document_structure` 是文档级 op、handler 额外接收 `segment_to_indices`。改动分发逻辑时这几条必须一起看。
- **错误结构**：`_add_error()` 产出 `type/description/location/paragraph_index/segment`，规则带 `format_action` 时额外挂 `action`/`action_value`，修复路径据此直接执行、不再回读规则文件。**结构性 op（range/count/order/exists/sequence/document_structure/page_*）不产生 action**——"校验→修复"只在可执行的等值类字段上闭合。
- **覆盖率诊断**：`RuleEngine.validate()` 顺带产出 `self.coverage`，`coverage_warnings()` 把"规则没挂上文档"的情况变成显式告警——典型场景是规则文件的 `else` 兜底段类吃光所有段落、却有一半规则一次都没执行过，用户看到少量错误就以为合规。告警是**诊断不是判决**：绝不进 `errors`、不参与修复/批注定位，也不改错误结构。CLI（`_print_coverage_warnings`）与 GUI（`_coverage_advisories`）共用 `mismatch_warnings()` 区分"错配判据"（0 覆盖 / 低覆盖）与"纯信息"（死规则、未匹配段类），后者不得把文档标成不匹配。`tests/test_rule_engine_coverage.py` 固定这条口径。
- **安全红线**：`segments[].match` 是受限表达式，由 `engine/_whitelist.py` 的 `safe_eval` 执行——先 AST 解析、节点白名单过滤（禁止 Call/Attribute/Subscript/Import/Lambda 等），**永不调用原生 `eval`**，任何越界表达式静默返回 `False`。`tests/test_match_parser_security.py` 是这条红线的对抗测试，不要为了"支持更多语法"放宽白名单。

### 排版链路：`format/`

`FormatEngine` 是**薄门面**，只组装子模块；真正的 8 步流水线在 `format/pipeline.py`：加载归一化（DOC/TXT/MD/DOCX/WPS）→ 符号归一（半角→全角、空格/零宽字符）→ 图表题注探测 → 主副标题识别 → 段落分类+字体/段落格式（`format/ops/heading.py` 的数字编号标题按点分段数推导层级 `1.`→Lv1 / `1.1`→Lv2 / `1.1.1`→Lv3，`摘  要`、`参  考  文  献` 等中间带空格的结构性标题按去空白文本识别为 Lv1） → 表格 → 页面设置（A4、页边距、页码风格）→ 保存。`format/ops/` 下每个模块是单一职责原子操作，可单独测试，改具体行为优先动 ops/ 而非门面。`format/config.py` 的 `Config` 内置 3 套预设（official/academic/legal）并递归合并用户覆盖项，允许只覆盖单字段。

### GUI 是主界面，CLI 是遗留入口 + 共享业务层

**GUI（`gui.py`）是当前唯一面向用户的界面**，CLI 是前期开发产物、已不被扩展。改功能优先加在 GUI 页面上，不要往 CLI 加新命令。

但 `cli.py` 不能删：`gui.py` 的 `GuiBackend(_cli.CLI)` **子类化** `CLI` 类复用它的业务方法（`_validate_file` / `_check_fix_core` / `_resolve_output_path` 等），**不重写逻辑**。构造时把模块级 `_cli.ui` 换成 `_UiProxy` 代理（`ui.printers` 的打印调用改走 Qt 信号回调），`super().__init__()` 结束后立即还原；后台任务用 `attach_worker_log` / `detach_worker_log` 临时替换 `sink.on_text`，任务结束必须还原，否则下次任务会复用已 `deleteLater` 的 Worker 引用。

推论：**改 `cli.py` 的共用方法会同时影响 GUI**，动之前确认 GUI 调用点（`gui.py` 里搜 `_cli.` 和 `self.gui_` 前缀的封装）。`ui/printers.py` + `ui/input.py` 只服务 CLI 的 rich 渲染与 prompt_toolkit 补全，GUI 不依赖。

### 路径解析：不要用 `cwd`

`engine/paths.py` 的 `BASE_DIR` 兼容 python 脚本 / Nuitka onefile / PyInstaller onefile 三种环境。关键陷阱：Nuitka onefile 里 `sys.executable` 指向 `%TMP%` 下解压出的子 exe（错的），必须用 `sys.argv[0]` + Windows API 双保险取原始 exe 路径。**所有相对路径都要经 `paths.py` 解析到"原始 exe/cli.py 所在目录"**，`config/` 是用户数据目录，绝不能落到临时目录。

REPL 的路径默认语义：裸文件名 / 相对路径 → 从 `input/` 读、产物落 `output/`（批注 `output/`、报告 `reports/`）；传绝对路径则完全按原路径走。新增命令需保持这一行为。

### 配置与文档

`config/schema.md` 是规则契约（`contract-v2`），写规则必须对照；`config/prompt_template.md` 是 LLM 生成规则的系统提示词，`config/schools/` 放学校专属规则。`config/api_config.yaml` 含密钥、已被 gitignore（示例为 `.example`）。规则文件至少要有 `segments` + `rules` 两个顶级键。

## 版本与口径约定（已对齐，改文档时保持一致）

- **单一版本源**：`engine/__init__.py` 的 `__version__`（当前 4.4.0）。REPL 横幅（`ui/printers.py`）与 GUI 三处版本标签（`gui.py` 经 `ENGINE_VERSION`）都从它读取，**不要在任何地方硬编码版本号**。
- **op 口径**：15 个 = v1 基础 10（`eq`/`in`/`regex`/`range`/`count_min`/`count_max`/`exists`/`before`/`after`/`skip`）+ v2 新增 5（`sequence_consecutive`/`cross_ref_order`/`document_structure`/`page_margins`/`page_size`）。`schema.md` 里「v1 的 10 个 op」指兼容基线，不是总数。
- **测试口径**：13 个测试文件、135 个 test 函数、参数化后 194 个用例。
- 根目录 `smoke_check_tmp.py` 是 gui 的手写临时冒烟脚本，非测试套件的一部分（不被 pytest 收集为正式用例）。
