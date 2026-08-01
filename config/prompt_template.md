# LLM 生成规则文件的 Prompt 模板

> 使用方法：
> 1. 复制下面的 Prompt 到 deepseek 网页版 / Claude / ChatGPT
> 2. 在 `{{模板文本}}` 占位符处粘贴你的《格式规范细则》原文（或 Word 文档的文字内容）
> 3. LLM 输出的 YAML 保存到 `config/rules_XXX.yaml`（如 `config/rules_lunwen.yaml`）
> 4. 启动 REPL：`python cli.py`，用 `/check student.docx --rules config/rules_XXX.yaml` 验证规则
>
> 规则文件的完整 Schema 契约见同目录的 [schema.md](schema.md)。

---

## Prompt（复制以下内容）

你是格式规范转换器。请把下面的人类可读格式规范，转换为结构化的规则文件（YAML）。

### 规则文件 Schema 契约

规则文件必须包含两个顶级 key：`segments`（段落分类规则）和 `rules`（格式检查规则）。

```yaml
schema: contract-v1
domain: "文档类型名称"  # 仅供人类识别

# 段落分类规则：按声明顺序匹配，第一个匹配成功的 segment 生效
segments:
  - id: cover                    # 段落角色 ID（任意字符串）
    match: "index < 3 and size >= 18"   # 分类表达式
    desc: "封面"                 # 可选，仅供人类阅读
  - id: h1
    match: "regex('^第[一二三四五六七八九十\\d]+章')"
  - id: body
    match: "else"                # 兜底：未匹配其他 segment 的段落

# 格式检查规则：逐条执行
rules:
  - segment: body                # 作用于哪个段落角色
    field: font                  # 检查哪个字段
    op: eq                       # 操作符
    value: "宋体"                # 期望值
    tol: 0                       # 可选，容差
    msg: "正文字体应为宋体"       # 错误说明
  - segment: cover
    op: skip                     # 跳过检查
```

### 段落可用字段

引擎从文档提取的段落字段，`field` 只能取自这里：

| 字段 | 类型 | 说明 |
|---|---|---|
| `index` | int | 段落索引（0-based） |
| `text` | str | 段落文本 |
| `font` | str | 字体名（宋体/黑体/Times New Roman 等） |
| `size` | float | 字号（pt） |
| `bold` | bool | 是否加粗 |
| `italic` | bool | 是否斜体 |
| `align` | str | 对齐：left/center/right/justify |
| `line_spacing` | float | 行距（倍数） |
| `first_line_indent` | float | 首行缩进（字符数） |
| `style` | str | 样式名（Heading 1/Normal 等） |
| `heading_level` | int | 标题级别（0=非标题，1=一级标题） |

### match 表达式语法

```
# 比较运算
size >= 18
index < 3
font == "黑体"
heading_level == 1

# 逻辑组合
index < 3 and size >= 18
text =~ '^第' or text =~ '^\\d+\\.'

# 正则匹配（两种写法等价）
regex('^第[一二三四五六七八九十\\d]+章')
text =~ '^第[一二三四五六七八九十\\d]+章'

# 章节范围（after_h1 自动定位一级标题）
after_h1('摘要') until next h1       # 摘要章节
after_h1('参考文献') until next h1    # 参考文献章节

# 兜底
else                          # 未匹配其他 segment 的段落
else and not in_table         # 兜底且不在表格内
```

注意：YAML 中反斜杠需转义（`\\d` 而非 `\d`）。

### op 完整清单（10 个）

| op | 含义 | 必填字段 | value 类型 |
|---|---|---|---|
| `eq` | 等于（带容差） | field | 字体/字号/字符串 |
| `in` | 在列表中 | field | 列表 |
| `regex` | 正则匹配 | field | 正则字符串 |
| `range` | 数值范围 | field | [min, max] |
| `count_min` | 最少数量 | 无 | 整数 |
| `count_max` | 最多数量 | 无 | 整数 |
| `exists` | 必须存在 | 无 | 无 |
| `before` | 顺序：在某 segment 之前 | 无（用 target） | target: segment_id |
| `after` | 顺序：在某 segment 之后 | 无（用 target） | target: segment_id |
| `skip` | 跳过检查 | 无 | 无 |

### 字体别名

引擎自动识别别名（无需在规则中列举）：
- 宋体 = SimSun = 宋体_GB2312
- 黑体 = SimHei = 黑体_GB2312
- 楷体 = KaiTi = 楷体_GB2312
- 仿宋 = FangSong = 仿宋_GB2312

### 完整示例（毕业论文）

```yaml
schema: contract-v1
domain: "毕业论文"

segments:
  - id: cover
    match: "index < 5 and size >= 18"
    desc: "封面"
  - id: h1
    match: "regex('^第[一二三四五六七八九十\\d]+章')"
    desc: "一级标题"
  - id: h2
    match: "regex('^\\d+\\.\\d+\\s')"
    desc: "二级标题"
  - id: abstract
    match: "after_h1('摘要') until next h1"
    desc: "摘要章节"
  - id: reference
    match: "after_h1('参考文献') until next h1"
    desc: "参考文献章节"
  - id: body
    match: "else"
    desc: "正文"

rules:
  - segment: cover
    op: skip
  - segment: h1
    field: font
    op: eq
    value: "黑体"
    msg: "一级标题字体应为黑体"
  - segment: h1
    field: size
    op: eq
    value: 16
    tol: 1.0
    msg: "一级标题字号应为16pt"
  - segment: body
    field: font
    op: eq
    value: "宋体"
    msg: "正文字体应为宋体"
  - segment: body
    field: size
    op: eq
    value: 12
    tol: 0.5
    msg: "正文字号应为12pt"
  - segment: body
    field: align
    op: in
    value: [justify, left]
    msg: "正文对齐应为两端对齐或左对齐"
  - segment: abstract
    op: exists
    msg: "缺少摘要章节"
  - segment: reference
    op: exists
    msg: "缺少参考文献章节"
  - segment: reference
    op: count_min
    value: 10
    msg: "参考文献不足10条"
```

### 你的任务

请分析下面的模板文本，识别其中包含的所有格式要求，生成符合上述 schema 的规则文件。

要求：
1. **仔细识别模板中的所有格式规定**——字体、字号、对齐、行距、缩进、章节结构等
2. **基于模板的实际格式推断**——模板文本中每个段落的格式信息已标注在 `[字号|字体|对齐|行距]` 中
3. **区分段落角色**——封面、各级标题、正文、参考文献等，用 segments 分类
4. **skip 不需要检查的段落**——如封面、目录等
5. **字体用中文名**——如"宋体""黑体"（引擎自动识别别名）
6. **字号用磅值**——如 12（小四）、16（三号）、18（小二）、22（二号）、36（初号）
7. **容差合理**——字号容差 0.5-1.0pt，行距容差 0.3
8. **只检查格式，不检查内容**——不涉及语义、措辞、逻辑
9. **输出纯 YAML**——不要加 markdown 代码块标记，不要解释，直接输出 yaml 内容

### 模板文本

{{模板文本}}

### 请输出规则文件（纯 YAML）

---
