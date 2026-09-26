# 规则文件 Schema 契约 v2

> 本文档定义了"格式规则文件"的结构。规则文件是**领域知识**的唯一载体，
> 引擎（py）只认本 schema，不包含任何领域知识（如"论文""期刊""公文"等）。
> 换领域只需替换规则文件，引擎代码零改动。

## 设计原则

1. **格式检查，不涉及语义**——只检查字体/字号/对齐/行距/位置/计数/存在性/顺序等可量化属性
2. **段落分类基于结构特征**——位置 + 字号 + 文本正则 + 样式名 + 章节范围，不基于语义理解
3. **引擎领域无关**——15 个通用 op（v1 基础 10 + v2 新增 5）覆盖所有纯格式检查需求
4. **规则完全透明**——每条错误可追溯到具体规则，可读可改

## v2 新增能力（v1 基础上扩展，向后兼容）

### 新增段落字段
- `first_line_chars`：字符单位首行缩进（int，2 字符 = 200，None 表示未设置）
- `hanging_chars`：字符单位悬挂缩进
- `font_cn_most`：段内中文字符最常见的 eastAsia 字体
- `font_en_most`：段内 ASCII 字符最常见的 ascii 字体
- `line_spacing_rule`：行距规则（single/multiple/atLeast/exact）
- `runs_summary`：段内 run 级字体统计 `{font: count, ...}`
- `has_blank_before` / `has_blank_after`：前后是否有空行（bool）

### 新增 op
- `sequence_consecutive`：编号连续性校验（标题号、参考文献号、图表号）
- `cross_ref_order`：引述顺序校验（"如图N.M"必须在图N.M之前出现）
- `document_structure`：文档结构顺序校验（5 大部分顺序）
- `page_margins`：文档级页边距校验（±tol）
- `page_size`：文档级纸张尺寸校验（±tol）

## 规则文件结构

```yaml
schema: contract-v1            # 必填，固定值，标识 schema 版本
domain: "毕业论文"              # 可选，仅供人类识别，引擎忽略
version: "1.0"                 # 可选，规则文件版本

# 段落分类规则：引擎按顺序匹配，给每段打标签
segments:
  - id: cover                  # 段落角色 ID（任意字符串，用于 rules 引用）
    match: "index < 3 and size >= 18"   # 分类表达式
    desc: "封面"               # 可选，仅供人类阅读
  - id: h1
    match: "regex('^第[一二三四五六七八九十\\d]+章')"
  - id: body
    match: "else"              # 兜底：未匹配其他 segment 的段落
  ...

# 格式检查规则：逐条执行
rules:
  - segment: body              # 作用于哪个段落角色
    field: font                # 检查哪个字段
    op: eq                     # 操作符
    value: "宋体"              # 期望值
    tol: 0                     # 可选，容差（数值类才用）
    msg: "正文字体应为宋体"     # 可选，自定义错误说明
  - segment: h1
    field: size
    op: eq
    value: 16
    tol: 1.0
  - segment: cover
    op: skip                   # skip 不需要 field/value
  ...

# 跳过文件关键字（可选）：文件名命中则不检查
skip_files:
  - "查重报告"
  - "成绩评定表"
```

## 段落可用字段

引擎从提取器拿到的段落字典包含以下字段，rules 中的 `field` 只能取自这里：

| 字段 | 类型 | 说明 |
|---|---|---|
| `index` | int | 段落在文档中的索引（0-based） |
| `text` | str | 段落文本内容 |
| `font` | str/None | 字体名（如"宋体""Times New Roman"） |
| `size` | float/None | 字号（pt，如 12.0、16.0） |
| `bold` | bool/None | 是否加粗 |
| `italic` | bool/None | 是否斜体 |
| `align` | str/None | 对齐方式：left/center/right/justify |
| `line_spacing` | float/None | 行距（倍数，如 1.5） |
| `first_line_indent` | float/None | 首行缩进（字符数） |
| `left_indent` | float/None | 左缩进 |
| `style` | str/None | 样式名（如"Heading 1""Normal"） |
| `heading_level` | int | 标题级别（0=非标题，1=一级标题，2=二级标题...） |
| `in_table` | bool | 是否在表格内 |
| `page` | int/None | 页码（PDF 提取器输出，1-based） |
| `first_line_chars` | int/None | 首行缩进字符数（1/100 字符，2 字符=200） |
| `hanging_chars` | int/None | 悬挂缩进字符数 |
| `font_cn_most` | str/None | 段内中文字符最常见字体 |
| `font_en_most` | str/None | 段内 ASCII 字符最常见字体 |
| `line_spacing_rule` | str/None | 行距规则：single/multiple/atLeast/exact |
| `runs_summary` | dict/None | 段内 run 级字体统计 |
| `has_blank_before` | bool | 前一段是否为空行 |
| `has_blank_after` | bool | 后一段是否为空行 |

## segments 的 match 表达式语法

match 表达式是一个字符串，引擎解析后返回 bool。支持以下元素：

### 1. 字段引用
直接写字段名：`index`、`size`、`font`、`align`、`text`、`style`、`heading_level`、`in_table`、`page`

### 2. 比较运算
```
size >= 18
index < 3
font == "黑体"
align != "left"
heading_level == 1
```

### 3. 逻辑组合
```
index < 3 and size >= 18
text =~ '^第' or text =~ '^\\d+\\.'
not in_table
```

### 4. 正则匹配
```
text =~ '^第[一二三四五六七八九十\\d]+章'
text =~ '^\\[\\d+\\]'       # 参考文献条目
```

注意：YAML 中的反斜杠需转义（`\\d` 而不是 `\d`）。

### 5. 章节范围
```
after_h1('摘要') until next h1       # 摘要章节
after_h1('参考文献') until next h1    # 参考文献章节
after_h1('致谢') until end            # 致谢到文档末尾
```

### 6. 兜底
```
else                          # 未匹配其他 segment 的段落
else and not in_table         # 兜底且不在表格内
```

### 7. 优先级
segments 按声明顺序匹配，**第一个匹配成功的 segment 生效**。
因此 `else` 必须放在最后。

## rules 条目可选字段

每条 rule 除 `segment`/`field`/`op`/`value`/`tol`/`msg` 外，还支持以下可选字段：

- `file_types`（可选）：限定该规则仅在指定文件类型上生效，取值为 `[docx, doc, pdf]` 的子集。常用于把字体/字号/样式检查限定在 docx/doc 上（PDF 字体识别不可靠、样式信息缺失）。
- `format_action`（可选）：校验失败时触发的修复动作，可选值 `set_font`/`set_size`/`set_align`/`set_line_spacing`/`set_indent`，对应 `format.ops` 原子操作。引擎在校验失败后可据此字段生成修复指令，自动将段落格式调整为规则期望值。

带 `format_action` 的 rule 示例：

```yaml
- segment: body
  field: font
  op: eq
  value: "仿宋_GB2312"
  msg: "正文字体应为仿宋_GB2312"
  file_types: [docx, doc]
  format_action: set_font     # 校验失败时调用 format.ops 设置字体为 value
```

> **修复支持矩阵**：`format_action` 仅对按段落逐条检查的 op 生效（`eq` / `in` / `regex`）。
> 结构类 op（`range` / `count_min` / `count_max` / `exists` / `before` / `after`）无法按段落自动修复，即使写了 `format_action` 也会被引擎忽略。
>
> **`in` op 的修复值**：`value` 是允许值列表（如 `["仿宋_GB2312", "仿宋"]`），修复时取**列表第一个值**作为目标。建议把首选字体放在列表首位。

## rules 的 op 完整清单（15 个，v1=10 + v2 新增 5）

### 1. `eq` —— 等于（带容差）
```yaml
- segment: body
  field: size
  op: eq
  value: 12
  tol: 0.5          # 可选，默认 0
  msg: "正文字号应为12pt"
```
- 数值类：`abs(actual - value) <= tol` 即通过
- 字符串类：`actual == value` 即通过（tol 被忽略）
- 字体类：支持别名匹配（如"宋体"== "SimSun"== "宋体_GB2312"）

### 2. `in` —— 在列表中
```yaml
- segment: body
  field: align
  op: in
  value: [justify, left]    # 任一匹配即通过
  msg: "正文对齐应为两端或左对齐"
```

### 3. `regex` —— 正则匹配
```yaml
- segment: h1
  field: text
  op: regex
  value: "^第[一二三四五六七八九十\\d]+章"
  msg: "一级标题格式应为'第X章'"
```

### 4. `range` —— 数值范围
```yaml
- segment: body
  field: line_spacing
  op: range
  value: [1.0, 1.5]     # [最小, 最大]，闭区间
  msg: "正文行距应在1.0-1.5倍之间"
```

### 5. `count_min` —— 最少数量
```yaml
- segment: reference
  op: count_min
  value: 10
  msg: "参考文献不足10条"
```
- 统计该 segment 的段落总数
- 不需要 field

### 6. `count_max` —— 最多数量
```yaml
- segment: h1
  op: count_max
  value: 8
  msg: "一级标题超过8个"
```

### 7. `exists` —— 必须存在
```yaml
- segment: abstract
  op: exists
  msg: "缺少摘要章节"
```
- 该 segment 至少有 1 段即通过
- 不需要 field

### 8. `before` —— 顺序检查（在某段落角色之前）
```yaml
- segment: abstract
  op: before
  target: body
  msg: "摘要应在正文之前"
```
- 该 segment 的第一段索引 < target 的第一段索引即通过

### 9. `after` —— 顺序检查（在某段落角色之后）
```yaml
- segment: reference
  op: after
  target: body
  msg: "参考文献应在正文之后"
```

### 10. `skip` —— 跳过检查
```yaml
- segment: cover
  op: skip
```
- 标记该 segment 不做任何格式检查
- 不需要 field/value/msg

### 11. `sequence_consecutive` —— 编号连续性
```yaml
- segment: h1
  op: sequence_consecutive
  pattern: "^(\\d+)\\s"        # regex 提取编号组，第一个 () 取编号
  start: 1                     # 起始值，默认 1
  unique: true                 # 是否要求唯一，默认 true
  parent_segment: h1           # 可选：嵌套模式，要求子编号的父级等于最近父段编号
  msg: "一级标题编号不连续"
```
- 支持 `[1] → [2] → [3]` 连续、`1 → 2 → 3` 连续、`1.1 → 1.2 → 1.3` 嵌套
- 缺号报"缺少编号 N"，重复报"编号 N 重复"

### 12. `cross_ref_order` —— 引述顺序
```yaml
- op: cross_ref_order
  ref_segment: body            # 扫描此 segment 的"如图N.M"引用
  caption_segment: figure_caption  # 对应的 caption segment
  pattern: "如(图|表)\\s*(\\d+)[\\.\\-](\\d+)"  # 引用 regex
  msg: "图/表出现在引述之前"
```

### 13. `document_structure` —— 文档结构顺序
```yaml
- op: document_structure
  segments: [title_part, abstract_cn, keyword_cn, abstract_en, keyword_en, body, reference_title, reference_item]
  msg: "文档结构顺序错误"
```
- 缺项报"缺少 XXX 部分"，顺序错报"XXX 应在 YYY 之前"

### 14. `page_margins` —— 页边距
```yaml
- op: page_margins
  value: {left: 3.0, top: 2.0, bottom: 2.0, right: 2.0}  # cm
  tol: 0.1                     # cm，默认 0.1
  section: 0                   # 可选，section 索引，默认 0
  msg: "页边距不符合规范"
```

### 15. `page_size` —— 纸张尺寸
```yaml
- op: page_size
  value: {width: 21.0, height: 29.7}  # cm，A4
  tol: 0.1
  msg: "纸张尺寸应为 A4"
```

## 字体别名匹配

为避免"宋体"vs"SimSun"vs"宋体_GB2312"的误报，`op: eq` 检查字体时自动应用别名表：

```python
FONT_ALIASES = {
    "宋体": ["宋体", "SimSun", "宋体_GB2312", "SimSun-ExtB"],
    "黑体": ["黑体", "SimHei", "黑体_GB2312", "STHeiti"],
    "楷体": ["楷体", "KaiTi", "楷体_GB2312", "STKaiti"],
    "仿宋": ["仿宋", "FangSong", "仿宋_GB2312", "STFangsong"],
    "Times New Roman": ["Times New Roman", "TimesNewRomanPSMT", "TimesNewRomanPS-BoldMT"],
    "Arial": ["Arial"],
    "Calibri": ["Calibri", "Calibri-Bold"],
}
```
规则文件中 `value: "宋体"` 会匹配上述所有别名。

## skip_files 示例

```yaml
skip_files:
  - "查重报告"        # 文件名包含此关键字则跳过
  - "成绩评定表"
  - "答辩PPT"
  - "答辩课件"
  - "答辩报告"
  - "英文原文"
```

## 完整规则文件示例 v2（毕业论文，演示新字段和新 op）

> 此示例基于 contract-v2，演示 v2 新增字段（`first_line_chars`/`font_cn_most`/`font_en_most`/`line_spacing_rule` 等）
> 与新增 op（`sequence_consecutive`/`cross_ref_order`/`document_structure`/`page_margins`/`page_size`）的用法。
> v1 规则文件可继续使用，参见下方"v1 兼容示例"小节。

```yaml
schema: contract-v2
domain: "毕业论文"
version: "2.0"

segments:
  - id: cover
    match: "index < 5 and size >= 18"
    desc: "封面"
  - id: title_part
    match: "index < 2 and size >= 18"
    desc: "标题部分"
  - id: abstract_cn
    match: "regex('^摘\\s*要[:：]')"
    desc: "中文摘要"
  - id: keyword_cn
    match: "regex('^关键词[:：]')"
    desc: "中文关键词"
  - id: abstract_en
    match: "regex('^Abstract[:：]')"
    desc: "英文摘要"
  - id: keyword_en
    match: "regex('^Keywords[:：]')"
    desc: "英文关键词"
  - id: h1
    match: "regex('^第[一二三四五六七八九十\\d]+章')"
    desc: "一级标题"
  - id: h2
    match: "regex('^\\d+\\.\\d+\\s')"
    desc: "二级标题"
  - id: figure_caption
    match: "regex('^图\\s*\\d+[\\.\\-]\\d+')"
    desc: "图题"
  - id: reference_title
    match: "regex('^参考文献$')"
    desc: "参考文献标题"
  - id: reference_item
    match: "regex('^\\[\\d+\\]')"
    desc: "参考文献条目"
  - id: body
    match: "else and not in_table"
    desc: "正文"

rules:
  # 封面不检查
  - segment: cover
    op: skip

  # 一级标题：黑体三号(16pt)
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
    msg: "一级标题字号应为16pt(三号)"

  # 二级标题：黑体小三(15pt)
  - segment: h2
    field: font
    op: eq
    value: "黑体"
    msg: "二级标题字体应为黑体"
  - segment: h2
    field: size
    op: eq
    value: 15
    tol: 1.0
    msg: "二级标题字号应为15pt(小三)"

  # 正文：宋体小四(12pt)、两端对齐、1.5倍行距
  # v2 新增字段：font_cn_most / font_en_most / line_spacing_rule / first_line_chars
  - segment: body
    field: font_cn_most
    op: eq
    value: "宋体"
    msg: "正文中文应为宋体"
  - segment: body
    field: font_en_most
    op: eq
    value: "Times New Roman"
    msg: "正文西文应为 Times New Roman"
  - segment: body
    field: size
    op: eq
    value: 12
    tol: 0.5
    msg: "正文字号应为12pt(小四)"
  - segment: body
    field: align
    op: in
    value: [justify, left]
    msg: "正文对齐应为两端对齐或左对齐"
  - segment: body
    field: line_spacing_rule
    op: in
    value: [multiple]
    msg: "正文行距规则应为 multiple"
  - segment: body
    field: line_spacing
    op: eq
    value: 1.5
    tol: 0.3
    msg: "正文行距应为1.5倍"
  - segment: body
    field: first_line_chars
    op: eq
    value: 200                  # 2 字符
    tol: 0
    msg: "正文首行缩进应为2字符"
  - segment: body
    field: has_blank_after
    op: eq
    value: false
    msg: "正文段内不应有空行"

  # 图题：宋体五号(10.5pt) 居中
  - segment: figure_caption
    field: font
    op: eq
    value: "宋体"
    msg: "图题字体应为宋体"
  - segment: figure_caption
    field: size
    op: eq
    value: 10.5
    tol: 0.5
    msg: "图题字号应为五号(10.5pt)"
  - segment: figure_caption
    field: align
    op: eq
    value: "center"
    msg: "图题应居中"

  # 文档结构顺序校验
  - op: document_structure
    segments: [title_part, abstract_cn, keyword_cn, abstract_en, keyword_en, body, reference_title, reference_item]
    msg: "文档结构顺序错误"

  # 一级标题编号连续性
  - segment: h1
    op: sequence_consecutive
    pattern: "^第(\\d+)章"
    start: 1
    unique: true
    msg: "一级标题编号不连续"

  # 参考文献条目编号连续性
  - segment: reference_item
    op: sequence_consecutive
    pattern: "^\\[(\\d+)\\]"
    start: 1
    unique: true
    msg: "参考文献编号不连续"

  # 图表引述顺序：正文中"如图N.M"必须出现在对应图题之前
  - op: cross_ref_order
    ref_segment: body
    caption_segment: figure_caption
    pattern: "如(图|表)\\s*(\\d+)[\\.\\-](\\d+)"
    msg: "图/表出现在引述之前"

  # 文档级页边距（A4，2.5cm/2.5cm/2.5cm/2.5cm 容差 0.1）
  - op: page_margins
    value: {left: 2.5, top: 2.5, bottom: 2.5, right: 2.5}
    tol: 0.1
    section: 0
    msg: "页边距应为上下左右各 2.5cm"

  # 文档级纸张尺寸 A4
  - op: page_size
    value: {width: 21.0, height: 29.7}
    tol: 0.1
    msg: "纸张尺寸应为 A4"

  # 章节存在性与顺序（v1 兼容 op）
  - segment: abstract_cn
    op: exists
    msg: "缺少中文摘要"
  - segment: reference_item
    op: count_min
    value: 10
    msg: "参考文献不足10条"
  - segment: abstract_cn
    op: before
    target: body
    msg: "摘要应在正文之前"
  - segment: reference_item
    op: after
    target: body
    msg: "参考文献应在正文之后"

skip_files:
  - "查重报告"
  - "成绩评定表"
  - "答辩PPT"
  - "答辩课件"
  - "答辩报告"
  - "英文原文"
```

## v1 兼容示例（毕业论文，contract-v1 原版，作为对比）

> 下方为原 contract-v1 规则文件，未使用任何 v2 新字段与新 op。
> 引擎加载时按 `schema: contract-v1` 识别，新字段/op 在此规则中不可用但不会报错。

```yaml
schema: contract-v1
domain: "毕业论文"
version: "1.0"

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
    match: "else and not in_table"
    desc: "正文"

rules:
  # 封面不检查
  - segment: cover
    op: skip

  # 一级标题：黑体三号(16pt)
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
    msg: "一级标题字号应为16pt(三号)"

  # 二级标题：黑体小三(15pt)
  - segment: h2
    field: font
    op: eq
    value: "黑体"
    msg: "二级标题字体应为黑体"
  - segment: h2
    field: size
    op: eq
    value: 15
    tol: 1.0
    msg: "二级标题字号应为15pt(小三)"

  # 正文：宋体小四(12pt)、两端对齐、1.5倍行距
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
    msg: "正文字号应为12pt(小四)"
  - segment: body
    field: align
    op: in
    value: [justify, left]
    msg: "正文对齐应为两端对齐或左对齐"
  - segment: body
    field: line_spacing
    op: eq
    value: 1.5
    tol: 0.3
    msg: "正文行距应为1.5倍"

  # 章节结构检查
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
  - segment: abstract
    op: before
    target: body
    msg: "摘要应在正文之前"
  - segment: reference
    op: after
    target: body
    msg: "参考文献应在正文之后"

skip_files:
  - "查重报告"
  - "成绩评定表"
  - "答辩PPT"
  - "答辩课件"
  - "答辩报告"
  - "英文原文"
```

## 换领域的示例

同样的引擎，换一份规则文件即可检查期刊投稿格式：

```yaml
schema: contract-v1
domain: "期刊投稿"

segments:
  - id: title
    match: "index == 0"
  - id: author
    match: "index == 1"
  - id: abstract
    match: "regex('^摘要[:：]')"
  - id: keywords
    match: "regex('^关键词[:：]')"
  - id: h1
    match: "regex('^[1-9]\\d?[\\s\\.·]')"
  - id: body
    match: "else"

rules:
  - segment: title
    field: size
    op: eq
    value: 18
    tol: 1.0
    msg: "标题应为18pt"
  - segment: abstract
    field: size
    op: eq
    value: 9
    tol: 0.5
    msg: "摘要应为9pt"
  ...
```

**引擎代码一行没改**，只是规则文件不同。

## 引擎实现要求

引擎（py）需要实现：

1. **规则文件加载与校验**
   - 加载 yaml，校验 schema 字段
   - 必须有 `segments` 和 `rules` 两个顶级 key

2. **段落分类器**
   - 解析 match 表达式（支持比较/逻辑/正则/章节范围/兜底）
   - 按顺序匹配 segments，给每段打标签

3. **规则执行器**
   - 实现 10 个 op
   - 支持 tol 容差
   - 支持字体别名匹配
   - 收集错误（带 paragraph_index 和 segment_id）

4. **错误输出**
   - 每条错误包含：segment_id、field、op、expected、actual、msg、paragraph_index
   - 用于 CSV 报告和批注生成

## 版本管理

- `schema: contract-v1` 是当前版本
- 未来如需扩展 op 或字段，升级到 contract-v2
- 引擎通过 `schema` 字段判断如何解析

## 向后兼容性

引擎加载 yaml 规则文件时，按 `schema` 字段判断契约版本，并据此决定可用字段与 op 范围：

- **未声明 `schema` 字段**：按 `contract-v1` 处理。仅 v1 的 10 个 op 与原段落字段可用；v2 新增字段（`first_line_chars`/`hanging_chars`/`font_cn_most`/`font_en_most`/`line_spacing_rule`/`runs_summary`/`has_blank_before`/`has_blank_after`）和 v2 新增 op（`sequence_consecutive`/`cross_ref_order`/`document_structure`/`page_margins`/`page_size`）在该规则中不可用，但引擎**不会报错**，仅按 v1 语义解析。
- **声明 `schema: contract-v1`**：与未声明等价，按 v1 处理。已存在的 v1 规则文件可零改动继续运行。
- **声明 `schema: contract-v2`**：可使用全部 v1 字段/op 加上全部 v2 新增字段与新 op。引擎在 v2 模式下遇到未知字段/op 时才会报错。

兼容性要点：

1. **v1 规则文件无需改动即可在 v2 引擎上运行**——v2 引擎同时支持 v1 与 v2 两套契约。
2. **v1 规则文件中即使出现 v2 新字段/op 也不会报错**，只是这些字段/op 在 v1 模式下被忽略，不参与校验。
3. **v2 规则文件不能在仅支持 v1 的旧引擎上运行**——旧引擎不识别 `contract-v2`，且不实现 v2 新增 op。
4. **从 v1 升级到 v2 的步骤**：将 `schema: contract-v1` 改为 `schema: contract-v2`，按需引入 v2 新字段与新 op，原有 v1 字段/op 全部保留并照常生效。
