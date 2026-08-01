# 统一文档格式校验与格式化系统 - 重写方案

## Context

当前仓库有三个独立项目，各自承担不同职责：
- **主项目 (paper-format-matching)**：领域无关的 YAML 规则校验引擎（只读检查，不修改文档）
- **docformat-gui-main**：专注党政机关公文的格式化 GUI 工具（5287 行单文件，视觉优秀但架构耦合）
- **Word-Formatter-Pro-main**：通用格式化工具（模块化架构 core/gui/cli/config 分离）

**目标**：以主项目的 YAML 规则校验引擎为核心，以 Word-Formatter-Pro 的模块化架构为基础，整合两个 formatter 的最佳实践，**重写一个统一的 CLI + 引擎系统**。排除 GUI。

**核心思路**：校验引擎发现格式问题 → 格式化引擎按预设/规则修正 → 检查-报告-修复闭环。

---

## 目标目录结构

```
paper-format-matching/
├── engine/                     # 校验引擎（来自主项目改造）
│   ├── __init__.py
│   ├── rule_engine.py          # RuleEngine + MatchParser + FONT_ALIASES + normalize_font()
│   ├── extractors/
│   │   ├── __init__.py
│   │   ├── docx.py             # DocxExtractor
│   │   ├── doc.py              # DocExtractor
│   │   └── pdf.py              # PdfExtractor
│   ├── annotator.py            # Word 批注生成
│   └── reporter.py             # CSV 报告生成 + 格式诊断
│
├── format/                     # 格式化引擎（从两个 formatter 整合）
│   ├── __init__.py
│   ├── engine.py               # FormatEngine 主类（核心处理逻辑）
│   ├── config.py               # 统一配置 + 默认值 + PRESETS
│   ├── pipeline.py             # 处理流水线编排
│   ├── ops/                    # 原子格式化操作
│   │   ├── __init__.py
│   │   ├── font.py             # 字体/字号设置
│   │   ├── paragraph.py        # 段落格式（缩进/行距/对齐）
│   │   ├── heading.py          # 标题识别与分级
│   │   ├── attachment.py       # 附件格式化
│   │   ├── punctuation.py      # 标点修复
│   │   ├── space.py            # 空格/空行处理
│   │   └── table.py            # 表格格式化
│   └── converters/             # 旧格式转换
│       ├── __init__.py
│       └── office.py           # LibreOffice 兜底转换
│
├── cli.py                      # 交互式 CLI（REPL 对话界面）
├── tests/                      # 测试
│   ├── test_rule_engine.py
│   ├── test_format_engine.py
│   └── test_cli.py
├── config/                     # YAML 规则文件和配置
│   ├── rules_official.yaml     # 党政机关公文规则
│   └── rules_academic.yaml     # 学术论文规则
└── requirements.txt
```

---

## 文件迁移映射

| 源文件 | 目标位置 | 动作 |
|--------|---------|------|
| `checker/rules.py` (RuleEngine, MatchParser, FONT_ALIASES, normalize_font) | `engine/rule_engine.py` | 剥离 ThesisValidator，保留引擎核心 |
| `checker/extractors/docx_extractor.py` | `engine/extractors/docx.py` | 直接迁移 |
| `checker/extractors/doc_extractor.py` | `engine/extractors/doc.py` | 直接迁移 |
| `checker/extractors/pdf_extractor.py` | `engine/extractors/pdf.py` | 直接迁移 |
| `checker/annotator.py` | `engine/annotator.py` | 直接迁移，调整 import 路径 |
| `checker/main.py` 中 CSV 报告 + 文件遍历合并逻辑 | `engine/reporter.py` | 剥离为独立模块 |
| `docformat-gui/scripts/punctuation.py` | `format/ops/punctuation.py` | 标点标准化 |
| `docformat-gui/scripts/formatter.py` (PRESETS, detect_para_type) | `format/config.py` + `format/pipeline.py` | 预设配置 + 段落分类逻辑 |
| `docformat-gui/scripts/analyzer.py` | `engine/reporter.py` | 作为诊断报告的一部分 |
| `WFP/wfp_core.py` (WordProcessor) | `format/engine.py` | 核心处理逻辑 |
| `WFP/wfp_core.py` (WPSAppManager, SofficeConverter) | `format/converters/office.py` | COM 管理 + LibreOffice 转换 |
| `WFP/wfp_core.py` (标题/附件/表格识别) | `format/ops/heading.py` 等 | 拆分到原子操作 |
| `WFP/wfp_config.py` | `format/config.py` | 默认配置 |

---

## 核心设计

### 1. 交互式 CLI（REPL 对话界面）

运行 `python cli.py` 后进入交互式对话界面，像 Claude Code 那样，输入 `/help` 查看命令：

```
文档格式工具 v1.0
输入 /help 查看可用命令

> /help

可用命令:
  /help                    显示帮助信息
  /config                  查看/修改当前配置
  /preset <name>           切换预设 (official/academic/legal)
  /check <file>            校验单个文件
  /check-dir <dir>         校验目录下的所有文档
  /format <file>           格式化单个文件
  /format-dir <dir>        格式化目录下的所有文档
  /check-fix <file>        校验 + 自动修复
  /annotate                生成批注文档（配合上次校验结果）
  /save-config <file>      保存当前配置到文件
  /load-config <file>      从文件加载配置
  /quit                    退出

> /check ./docs/report.docx
校验中...
[1/1] report.docx
  ✅ 未检测到格式问题，论文符合要求
报告已保存至: ./reports/report.csv

> /format ./docs/report.docx --preset official
排版中...
  - 标点修复: 3 处已修复
  - 段落格式: 已应用
  - 页面设置: 已应用
输出文件: ./docs/report_formatted.docx

> /check-dir ./batch/
校验中...
[1/5] student1.docx - 检测到 5 处问题
[2/5] student2.docx - 未检测到格式问题
[3/5] student3.docx - 检测到 12 处问题
报告已保存至: ./reports/report_batch.csv

> /quit
再见！
```

命令解析规则：
- 以 `/` 开头调用内置命令处理器（`/help`、`/check`、`/format` 等）
- 不以 `/` 开头的行：如果包含文件路径，默认为最近操作的命令（如 `/check` 或 `/format`）
- 支持 `--preset`、`--output`、`--rules` 等可选参数

### 2. 统一配置系统 (`format/config.py`)

合并两个 formatter 的配置优点：

```python
DEFAULT_CONFIG = {
    'page': {
        'top': 3.7, 'bottom': 3.5, 'left': 2.8, 'right': 2.6,
        'paper_size': 'A4', 'force_a4': False,
    },
    'fonts': {
        'title': {'cn': '方正小标宋简体', 'en': 'Times New Roman', 'size': 22},
        'h1':   {'cn': '黑体',         'en': 'Times New Roman', 'size': 16},
        'h2':   {'cn': '楷体_GB2312',  'en': 'Times New Roman', 'size': 16},
        'body': {'cn': '仿宋_GB2312',  'en': 'Times New Roman', 'size': 16},
    },
    'paragraph': {
        'line_spacing': 28,           # 磅
        'first_line_indent': 2,       # 字符数
        'space_handling': 'remove_all',
    },
    'features': {
        'set_outline': True,
        'enable_attachment_formatting': True,
        'enable_table_formatting': False,
        'enable_punctuation_fix': True,
    },
}

PRESETS = {
    'official': {...},   # 党政机关公文
    'academic': {...},   # 学术论文
    'legal': {...},      # 法律文书
}
```

### 3. YAML 规则驱动校验和格式化

校验规则 YAML 的 `rules` 段默认只用于校验。新增 `format_action` 可选字段，指定发现不匹配时的自动修复动作：

```yaml
# 仅校验
rules:
  - segment: body
    field: font
    op: eq
    value: "仿宋_GB2312"

# 校验 + 修复
rules:
  - segment: body
    field: font
    op: eq
    value: "仿宋_GB2312"
    format_action: set_font  # 发现不匹配时自动修复
```

---

## 关键文件设计

### `engine/rule_engine.py`
- 保留 `MatchParser`（match 表达式解析器）、`RuleEngine`（规则执行器）、`FONT_ALIASES`、`normalize_font()`
- **移除** `ThesisValidator`（领域依赖）
- `MatchParser` 的 after_h1 章节范围、安全 eval 是整个系统的核心

### `format/engine.py`
- `FormatEngine` 核心类，从 WFP 的 `WordProcessor` 重构
- 融合 docformat-gui 的段落分类逻辑（detect_para_type 风格）
- 调用 `pipeline.py` 编排的原子操作（punctuation → heading → format → space → table）

### `format/pipeline.py`
- 处理流水线：读取文档 → 段落分类 → 标点修复 → 标题分级 → 格式应用 → 空格处理 → 表格处理 → 保存

### `format/ops/punctuation.py`
- 从 docformat-gui 的 `punctuation.py` 迁移
- 保护 URL/邮箱/ISO 标准号/时间，引号配对，中英文标点统一

### `cli.py`
- 交互式 REPL 入口：运行后进入 `while True` 循环，`input('> ')` 接受命令
- 以 `/` 开头调用命令处理器字典 `{"/help": cmd_help, "/check": cmd_check, ...}`
- 底层调用 `engine.rule_engine.RuleEngine` 和 `format.engine.FormatEngine`
- 同时提供非交互批量模式：`python cli.py --check-dir ./docs`（直接运行不进入 REPL）

---

## 关键决策

| 决策 | 选择 | 理由 |
|------|------|------|
| 以哪个 formatter 为基础 | Word-Formatter-Pro | 模块化架构更适合维护，已有 CLI 复用路径 |
| YAML 规则是否驱动格式化 | 是，加 `format_action` 字段 | 规则文件一次编写，校验和修复共用 |
| 是否保留 PDF 格式化 | 不保留 | PDF 是不可变格式，两个 formatter 都不支持 |
| CLI 交互方式 | 交互式 REPL + `/` 子命令 | 用户体验好，支持对话式操作 |

---

## 实施步骤

1. **Phase 1**：引擎层改造
   - 迁移 `checker/rules.py` → `engine/rule_engine.py`（剥离 ThesisValidator）
   - 迁移 `checker/extractors/` → `engine/extractors/`
   - 迁移 `checker/annotator.py` → `engine/annotator.py`
   - 从 `checker/main.py` 剥离 CSV 报告生成 → `engine/reporter.py`

2. **Phase 2**：格式化引擎构建
   - 从 WFP 提取核心逻辑 → `format/engine.py` + `format/converters/office.py`
   - 从 docformat-gui 提取标点修复 → `format/ops/punctuation.py`
   - 从 docformat-gui 提取预设配置 → `format/config.py`
   - 从 WFP 提取标题/附件/表格识别 → `format/ops/` 对应模块
   - 构建 `format/pipeline.py` 流水线

3. **Phase 3**：CLI 整合
   - 编写交互式 REPL 界面 → `cli.py`
   - 支持 `/help`、`/check`、`/format`、`/config` 等子命令
   - 同时提供 `--check-dir`、`--format-dir` 非交互批量模式

4. **Phase 4**：测试
   - 编写 `tests/test_rule_engine.py`、`tests/test_format_engine.py`
   - 运行验证
