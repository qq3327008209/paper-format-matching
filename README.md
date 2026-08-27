# 统一文档格式校验与排版系统

![版本](https://img.shields.io/badge/version-4.3.0-blue)
![Python](https://img.shields.io/badge/python-3.11+-green)
![License](https://img.shields.io/badge/license-MIT-yellow)
![UI](https://img.shields.io/badge/UI-prompt__toolkit%20%2B%20rich-orange)

一个**领域无关**的文档格式校验与自动排版系统。校验引擎按 YAML 规则检查格式问题，格式化引擎按预设修复——**检查 → 报告 → 修复**闭环，所有操作在交互式 REPL 中完成。

适用任何「有明确格式规范」的场景：毕业论文、公文、学术论文、法律文书、合同、标书……

## 📌 核心思想

**一次大模型智能，换取无限次零成本批量校验与排版。**

- **阶段一（一次）**：用大模型理解《格式规范细则》，生成结构化规则文件（YAML）
- **阶段二（无限次）**：本地引擎按规则批量校验与排版，零算力、零 API 费用

规则文件来源三种皆可：
1. **REPL 内用 LLM 生成**（推荐）：`/set-key` 配置 API Key 后，`/get-rules <模板.docx>` 自动提取格式 + 调用 LLM 生成 YAML，全程在 REPL 内完成
2. **网页版 AI 生成**：复制 [config/prompt_template.md](config/prompt_template.md) 中的提示词到 deepseek 网页版 / Claude / ChatGPT，附上规范文档，把输出的 YAML 保存到 `config/`
3. **直接用分享的 YAML**：把别人分享的规则文件放入 `config/` 即可

## ✨ 核心特性

| 特性 | 说明 |
|------|------|
| **交互式 REPL** | `python cli.py` 进入对话界面，输 `/` 弹命令菜单、Tab 补全、方向键历史 |
| **彩色富文本输出** | 基于 prompt_toolkit + rich，表格/进度/错误高亮 |
| **校验 + 排版一体** | 既能检查格式问题，也能按预设自动排版修复 |
| **引擎领域无关** | 10 个通用 op，换领域只需换 YAML 规则文件 |
| **规则驱动修复** | YAML 规则可带 `format_action` 字段，校验失败时自动触发修复 |
| **多格式支持** | .docx / .doc / .wps / .pdf（.doc/.wps 自动转 .docx） |
| **批量处理** | `/check-dir`、`/format-dir`、`/check-fix-dir` 目录级批量操作 |
| **三种预设** | official（公文）/ academic（论文）/ legal（法律文书） |
| **Word 批注** | `/annotate` 生成带批注的 Word 文档，定位每个错误 |
| **完全离线** | 校验与排版阶段不依赖网络，零隐私泄露 |
| **LLM 生成规则** | `/get-rules` 从 Word 模板或文本生成 YAML 规则；`/chat` 多轮对话答疑 |
| **端到端自检** | `/test` 一键跑通 校验→格式化→修复→批注 全流程，产物落 examples/，便于直接打开查看 |

## 📁 项目结构

```
paper-format-matching/
├── engine/                         # 校验引擎（领域无关）
│   ├── rule_engine.py              # 规则引擎（10 个 op + 段落分类器 + AST 沙箱）
│   ├── llm_client.py               # OpenAI 兼容 LLM 客户端
│   ├── rule_generator.py           # LLM 规则生成器
│   ├── annotator.py                # Word 批注生成
│   ├── reporter.py                 # CSV 报告生成（UTF-8-SIG + 严重度分级）
│   ├── paths.py                    # 三环境路径解析（python/exe/Nuitka）
│   └── extractors/                 # 文档提取器（docx/doc/pdf，TypedDict 字段契约）
├── format/                         # 格式化引擎
│   ├── engine.py                   # FormatEngine 薄门面（418 行）
│   ├── config.py                   # 预设配置（official/academic/legal）
│   ├── pipeline.py                 # 处理流水线
│   ├── ops/                        # 原子操作 + 拆分出的子模块
│   │   ├── font.py                 # 字体设置
│   │   ├── paragraph.py            # 段落对齐/缩进/行距
│   │   ├── heading.py              # 标题识别
│   │   ├── punctuation.py          # 中文标点
│   │   ├── space.py                # 空白处理
│   │   ├── attachment.py           # 附件标识
│   │   ├── table.py                # 表格格式化
│   │   ├── text_loader.py          # ⭐ 文本读取/Markdown 清理
│   │   ├── symbol_normalizer.py    # ⭐ 中文标点/省略号/括号/引号
│   │   └── paragraph_formatter.py  # ⭐ 段落分类/字体/对齐/标题检测
│   └── converters/                 # .doc/.wps → .docx 转换（COM + LibreOffice 兜底）
├── config/                         # YAML 规则文件、Schema 契约、提示词模板
│   ├── rules_lunwen.yaml           # 毕业论文规则
│   ├── rules_official.yaml         # 党政机关公文规则
│   ├── rules_academic.yaml         # 学术论文规则
│   ├── schema.md                   # 规则文件 Schema 契约（必读）
│   ├── prompt_template.md          # AI 生成规则的提示词模板
│   └── api_config.yaml.example     # LLM API 配置示例（复制为 api_config.yaml 使用）
├── examples/                       # ⭐ /test 自检产物缓存
│   ├── sample_for_test.docx        # 预置样本（含故意错误，用于自检演示）
│   ├── sample_formatted.docx       # /test 跑出：格式化产物
│   ├── sample_fixed.docx           # /test 跑出：修复产物
│   └── sample_annotated.docx       # /test 跑出：批注产物
├── tests/                          # 测试（127 个）
│   ├── test_rule_engine_*.py       # 规则引擎单元测试
│   ├── test_punctuation.py         # 标点规范化测试
│   ├── test_heading_regex_dedup.py # 正则常量同源测试
│   └── test_match_parser_security.py # YAML 表达式注入对抗测试
├── ui.py                           # UI 层：prompt_toolkit 补全 + rich 渲染
├── cli.py                          # 唯一入口：交互式 REPL
└── requirements.txt
```

### v4.3 重构亮点

- **FormatEngine 拆分**：原 1363 行大类拆为 418 行薄门面 + 3 个职责模块（text_loader/symbol_normalizer/paragraph_formatter），公共 API 不变
- **校验→修复语义闭合**：error dict 携带 `action`/`action_value`，`range/count/order/exists` 等结构性 op 不触发修复
- **AST 沙箱**：MatchParser 用 AST 白名单节点遍历替换 eval 黑名单，杜绝 YAML 注入
- **缺失字段分级**：Reporter/UI 把 missing 类问题标记为「警告」而非「错误」
- **提取器字段契约**：`ParagraphData` TypedDict 统一段落字段

## 🚀 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 启动交互式 REPL

```bash
python cli.py
```

进入对话界面后，输入 `/help` 查看所有命令：

```
╭───── paper-format-matching ─────╮
│ 文档格式校验与排版系统          │
│ 输入 /help 查看命令，/quit 退出 │
╰───────────── v4.3 ──────────────╯
> /help
```

输入 `/` 立即弹出命令菜单，方向键选择、Tab 补全。

### 3. 工作目录（input / output / examples）

首次启动会自动在 exe / 项目根同级创建工作目录。`examples/` 仓库自带。

```
paper-format-matching/          ← exe 或 cli.py 所在目录
├── config/                     ← YAML 规则文件、API 配置
├── examples/                   ← /test 自检产物缓存（自带预置样本）
│   ├── sample_for_test.docx    # 预置种子（含故意错误）
│   ├── sample_formatted.docx   # /test 跑出
│   ├── sample_fixed.docx       # /test 跑出
│   └── sample_annotated.docx   # /test 跑出
├── input/                      ← 把待处理文档丢到这里
├── output/                     ← 格式化 / 修复 / 批注产物
├── reports/                    ← 校验报告 CSV
└── ...
```

| 命令 | 默认行为 |
|------|----------|
| `/check 报告.docx` | 从 `input/报告.docx` 读取，CSV 报告落 `reports/` |
| `/format 报告.docx` | 从 `input/` 读，产物落 `output/报告_formatted.docx` |
| `/check-fix 报告.docx` | 从 `input/` 读，产物落 `output/报告_fixed.docx` |
| `/annotate` | 基于上次校验结果，产物落 `output/<原名>_annotated.docx` |
| `/check-dir` `/format-dir` `/check-fix-dir` | 不带参数默认操作整个 `input/`，产物落 `output/` |
| `/test` | 跑 `examples/sample_for_test.docx` 上的校验→格式化→修复→批注，产物全部落 `examples/` |

传**绝对路径**完全按原路径走，行为不受影响——上述默认只对裸文件名/相对路径生效。

### 3. 常用命令

| 命令 | 说明 |
|------|------|
| `/check <file>` | 校验单个文件（支持 .docx/.doc/.wps/.pdf） |
| `/check-dir <dir>` | 批量校验目录 |
| `/format <file>` | 格式化单个文件（按预设） |
| `/format-dir <dir>` | 批量格式化目录 |
| `/check-fix <file>` | 校验并自动修复（仅修复带 format_action 的规则） |
| `/check-fix-dir <dir>` | 批量校验并修复 |
| `/annotate` | 基于上次校验结果生成批注文档（.docx） |
| `/get-rules <文件>` | 用 LLM 从 Word 模板或文本生成 YAML 规则 |
| `/set-key` | 设置 LLM API Key（OpenAI 兼容接口） |
| `/test-api` | 测试 LLM API 配置是否有效（发最小请求） |
| `/chat` | 进入 LLM 多轮对话（问规则怎么写、格式怎么定） |
| `/test` | 端到端自检：校验→格式化→修复→批注 全流程，产物落 examples/，不烧 token |
| `/preset <name>` | 切换预设（official/academic/legal） |
| `/config` | 查看当前配置 |
| `/save-config <file>` | 保存配置到文件 |
| `/load-config <file>` | 从文件加载配置 |
| `/rules <file>` | 加载并预览规则文件 |
| `/quit` | 退出 |

命令支持可选参数：`--preset <name>`、`--output <path>`、`--rules <path>`、`--domain <名称>`
路径含空格用双引号包裹：`/check "C:\my dir\file.docx" --rules "C:\r.yaml"`

### 用 LLM 生成规则文件

```bash
# 1. 首次使用：配置 API Key（支持 DeepSeek/OpenAI/Kimi/GLM 等 OpenAI 兼容接口）
> /set-key
设置 LLM API（OpenAI 兼容接口，支持 DeepSeek/OpenAI/Kimi/GLM 等）
当前 base_url: https://api.deepseek.com/v1
当前 model:    deepseek-chat
直接回车保留当前值，输入新值则覆盖。

API Key: sk-xxxxxxxx
Base URL [https://api.deepseek.com/v1]:
Model [deepseek-chat]:
API 配置已保存到 config/api_config.yaml

# 2. 从 Word 模板生成规则（自动提取格式信息）
> /get-rules 模板.docx --domain 毕业论文
正在从文件生成规则: 模板.docx
  模型: deepseek-chat
  提取格式信息中...
规则文件已生成: config/rules_毕业论文.yaml
可用 /check <文件> --rules config/rules_毕业论文.yaml 立即验证。

# 3. 或从纯文本规范生成
> /get-rules --text "正文用宋体小四，标题用黑体三号..." --domain 期刊投稿

# 4. 也可从 txt 文件读取规范
> /get-rules 规范说明.txt
```

> API Key 保存在 `config/api_config.yaml`（已被 .gitignore 忽略，不会上传）。配置示例见 [config/api_config.yaml.example](config/api_config.yaml.example)。

### 使用示例

```
# 把待处理的论文放进 input/
> /check 论文.docx
校验中...
Document: input/论文.docx
Errors:   3
报告已保存: reports/论文_check_20260801_120000.csv

# 排版 / 修复，产物自动落到 output/
> /format 论文.docx --preset official
排版中... (预设: 公文格式)
排版完成: output/论文_formatted.docx

> /check-fix 论文.docx
校验并修复中...
修复完成: 共应用 5 处修复，已保存到 output/论文_fixed.docx

# 批注基于最近一次校验
> /annotate
标注完成: output/论文_annotated.docx

# 批量操作：直接扫整个 input/
> /check-dir
共发现 5 个文件，开始批量校验...
[1/5] 论文1.docx - 5 处问题
[2/5] 论文2.docx - 0 处问题
[3/5] 论文3.docx - 12 处问题
合并报告已保存: reports/batch_check_20260801_120100.csv

> /format-dir --preset academic
共发现 5 个文件，开始批量排版... (预设: 学术论文)
批量排版完成。产物在 output/
```

## 📋 规则文件 Schema

规则文件是**领域知识的唯一载体**，结构见 [config/schema.md](config/schema.md)。核心两个 key：

```yaml
schema: contract-v1
domain: "毕业论文"

# 段落分类：按声明顺序匹配，给每段打标签
segments:
  - id: h1
    match: "regex('^第[一二三四五六七八九十\\d]+章')"
  - id: body
    match: "else"              # 兜底

# 格式检查：逐条执行
rules:
  - segment: h1
    field: font
    op: eq
    value: "黑体"
    msg: "一级标题字体应为黑体"
    format_action: set_font    # 可选：校验失败时自动修复
```

### 10 个通用 op

| op | 含义 | 示例 |
|----|------|------|
| `eq` | 等于（带容差） | 字号=12, tol=0.5 |
| `in` | 在列表中 | 对齐∈[justify, left] |
| `regex` | 正则匹配 | 标题格式=`^第\d+章` |
| `range` | 数值范围 | 行距∈[1.0, 1.5] |
| `count_min` | 最少数量 | 参考文献≥10条 |
| `count_max` | 最多数量 | 一级标题≤8个 |
| `exists` | 必须存在 | 必须有摘要章节 |
| `before` | 顺序：在某段前 | 摘要在正文之前 |
| `after` | 顺序：在某段后 | 参考文献在正文之后 |
| `skip` | 跳过检查 | 封面不检查 |

### format_action 修复动作

规则可携带 `format_action` 字段，校验失败时 `/check-fix` 自动触发修复：

| format_action | 修复操作 |
|---------------|----------|
| `set_font` | 设置字体 |
| `set_size` | 设置字号 |
| `set_align` | 设置对齐方式 |
| `set_line_spacing` | 设置行距 |
| `set_indent` | 设置缩进 |

## 🔄 换领域

**引擎代码一行不改**，换规则文件即可检查不同领域。内置三套规则：

- `config/rules_lunwen.yaml` — 毕业论文
- `config/rules_official.yaml` — 党政机关公文
- `config/rules_academic.yaml` — 学术论文

## 🧪 测试

```bash
# 全部跑（127 个用例，约 0.7s）
python -m pytest tests/ -q

# 单独跑某一组
python -m pytest tests/test_punctuation.py -q
python -m pytest tests/test_rule_engine_after_h1.py -q
python -m pytest tests/test_match_parser_security.py -q
```

测试覆盖：
- 规则引擎：10 个 op、before/after、count、exists、after_h1 章节作用域
- 标点规范化：省略号、括号配对、引号配对、简单标点
- 安全：YAML 表达式注入对抗（AST 沙箱拒绝 `__import__`/`eval`/`open` 等）
- 正则同源：`format.engine` 与 `format.ops.heading` 共享同一对象

## 📖 相关文档

- **[config/schema.md](config/schema.md)**：规则文件 Schema 契约（必读，写规则时必须对照，v2 支持字符单位缩进、中英文字体分开、编号连续性等）
- **[config/prompt_template.md](config/prompt_template.md)**：AI 生成规则的提示词模板
- **[config/schools/](config/schools/)**：学校专属规则文件目录（每所学校/每份规范一份 yaml，`/check <file> --rules config/schools/xxx.yaml` 即按该校规范校验）
  - `rules_湖北汽院_优秀小论文.yaml`：湖北汽车工业学院优秀毕业设计（小论文）撰写格式规范（依据附件12）
- **[examples/](examples/)**：`/test` 自检产物缓存（含预置样本 sample_for_test.docx）

## 📄 许可证

MIT License
