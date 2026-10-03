# 文档格式校验与排版引擎 —— 功能结构总览

> 版本：engine 4.6.0 | 代码规模：59 个 Python 文件（含测试），非测试代码约 10,676 行 | 测试：15 个测试文件，258 个单元测试用例

---

## 一、项目定位与总体架构

本项目是一个**面向 Word/PDF 办公文档的自动化格式合规校验与智能排版一体化引擎**。系统将传统人工逐段核对字体、字号、对齐、行距、缩进、编号连续性等格式规范的工作，转换为由 YAML 规则文件驱动的自动化检查流程，并在检查完成后支持一键批量修复与 Word 批注标注。项目以"**引擎领域无关，规则承载领域知识**"为核心设计原则——引擎代码本身不包含任何"毕业论文""党政公文""学术期刊"的特定业务逻辑，切换领域只需要替换规则文件，引擎零改动。这种分层设计使得同一套校验内核可以同时服务于高校毕业论文审查、企事业单位公文排版、学术期刊投稿预审等多种场景。

从功能视角来看，系统划分为**四大功能子系统**与**一个交互入口层**，自下而上分别是：**文档特征提取子系统**（负责把异构文档格式统一转换为结构化段落数据）、**规则驱动校验子系统**（基于 YAML 规则文件执行合规检查）、**智能排版修复子系统**（按预设流水线执行文档自动格式化）、**报告输出与辅助子系统**（CSV 报告、Word 批注、LLM 规则自动生成），最上层的交互入口层通过交互式 REPL 命令行界面将四大子系统串联为完整的使用闭环。从数据流动视角看，一份用户文档的完整处理路径为：输入文档 → 提取器解析为结构化段落 → 规则引擎按 YAML 逐条校验 → 生成错误清单 → 同时可分支流向"CSV 报告导出""Word 错误批注""自动修复并保存新文档"三条输出通道。

---

## 二、文档特征提取子系统（engine.extractors）

文档特征提取子系统是引擎与真实办公文档之间的唯一桥梁，其职责是把 `.docx`、`.doc`、`.pdf` 三种异构格式的输入文档统一转换为引擎可理解的结构化段落数据模型。该子系统采用**策略模式**组织多种提取器：统一入口函数 `get_extractor(file_path)` 根据文件扩展名自动分发到对应提取器实例，上层调用者无需关心底层格式差异。每个提取器的 `extract()` 方法返回一份统一结构的字典数据，其中包含 `paragraphs` 段落列表、`tables` 表格列表、`sections` 节配置列表、`source_format` 原始格式标记等关键字段，每个段落对象包含 20 余个格式维度的特征值。

对于**DOCX 格式**（现代 Word 文档），`DocxExtractor` 借助 python-docx 库访问 OOXML 底层结构，提取深度覆盖了从简单文本到样式继承链的全部维度。在字体与字号方面，提取器不仅读取 run 级别的直接格式设置，还向上追溯段落样式的继承链（paragraph style → base_style 递归至多 10 层），最后回退到 `styles.xml` 中的 `w:docDefaults` 默认字体配置，三级机制确保即使文档作者只改了部分样式也能拿到真实生效的字体值；字体又细分为中文字体（`w:eastAsia` 槽位）与英文字体（`w:ascii` 槽位）分别提取，并按段内出现频次汇总为 `font_cn_most` 与 `font_en_most` 两个主体字体字段。在行距与缩进方面，提取器将 Word 的 6 种行距规则（单倍、1.5 倍、多倍、最小值、固定值等）映射为统一的 `line_spacing_rule` 枚举值，同时区分"厘米单位首行缩进"与"字符单位首行缩进"两种表达。对于节级别的页面配置，提取器读取每个 section 的页边距、纸张宽高、页眉页脚距离，并统一换算为厘米。此外，DOCX 提取器还支持提取表格内部段落（标记 `in_table=True`）、段落所在页码、段前后空行存在性、run 级字体汇总 `runs_summary` 等辅助字段，为后续规则引擎提供充分的判断依据。

对于**旧版 DOC 格式**（Word 97-2003），`DocExtractor` 通过调用系统上的 Office 或 WPS 应用程序接口，先将文档转换为临时 DOCX，再复用 DOCX 提取器的完整解析链路。该转换步骤对上层调用者透明，转换后的临时文件在提取完成后自动清理，避免磁盘残留。对于 **PDF 格式**，考虑到 PDF 本质是一种以页面坐标描述文字位置的"显示格式"而非"文档结构格式"，字体与字号信息的解析成本高且准确率不稳定，因此 `PdfExtractor` 采取"尽力而为"策略，仅提取文本内容与页码信息，并将字体、字号、行距等字段置为 `None`；规则引擎在后续执行时通过 `file_types` 机制自动跳过需要格式字段的检查条目，避免对 PDF 文档产生大量无意义的 `missing` 警告。

---

## 三、规则驱动校验子系统（engine.rule_engine + YAML 规则）

规则驱动校验子系统是整个项目的核心计算引擎，其设计思路是将"文档格式合规性"这一模糊的人工判断过程，形式化为一套**基于 YAML 规则文件的声明式定义 + 通用操作符解释执行**的严格流程。规则文件的 Schema 契约目前版本为 `contract-v2`，其结构分为三大块：`segments` 段落分类规则、`rules` 格式检查规则、`skip_files` 跳过文件关键字。每一份领域规则文件（如湖北汽院优秀小论文规则、党政公文规则、学术期刊规则）都是该契约的一个实例，引擎本身不包含领域知识。

### 3.1 段落分类（Segments）

段落分类阶段的目标是为文档中的每一段打上角色标签，例如"封面标题""学生信息行""一级标题 h1""正文 body""图表题注""参考文献条目"等。分类逻辑是**按声明顺序优先匹配**的：`segments` 数组中的每一条规则包含一个 `match` 匹配表达式与一个 `id` 标识符，引擎从第一段开始逐段遍历，对每个段顺次尝试所有 segment 的 `match` 表达式，命中即打上对应 `id` 标签并跳过后续 segment；最后一条 segment 通常是 `match: "else"` 的兜底 body 段，用来承接所有未被特殊分类的正文段落。

`match` 表达式本身是一个受限的 Python 语法子集字符串，支持引用段落的 20 余个字段（如 `index`、`size`、`align`、`font_cn_most`、`text`），支持比较运算（`>=`、`<`、`==`、`in`）、逻辑运算（`and`、`or`、`not`）、正则匹配函数 `regex()`、以及特殊的章节范围语法 `after_h1('标题名') until next h1`。为了防止用户在 YAML 中写入恶意表达式（如调用 `os.system`），`match` 表达式的执行并非直接走 Python 原生 `eval`，而是通过自研的**AST 白名单解释器**（见 `engine._whitelist` 模块）安全执行：先将表达式字符串解析为抽象语法树，再递归遍历整棵树并拒绝任何不在白名单内的节点类型（严格禁止 `Call`、`Attribute`、`Subscript`、`Import`、`Lambda` 等 20 余种危险节点），通过白名单检查后，再将 AST 中的所有 `Name` 变量引用替换为当前段落的实际值常量，最后以纯函数方式手动解释执行比较节点与逻辑节点。整个解释过程永不调用原生 `eval`，即使表达式中包含未经转义的危险字符串也只能返回布尔假值，从根本上杜绝了规则文件中的代码注入风险。

### 3.2 格式检查操作符（15 个通用 op：v1 基础 10 + v2 新增 5）

完成段落分类后，规则引擎逐条执行 `rules` 数组中的检查条目。每条规则的基本结构是：`segment` 指定作用于哪一类段、`field` 指定检查哪个格式字段、`op` 指定使用哪个操作符、`value` / `tol` 等参数提供比较基准、`msg` 为自定义错误说明。引擎目前实现了 12 种通用操作符，覆盖了从简单等值比较到复杂的文档级结构检查。

等值类与集合类操作符是最基础的检查手段。`eq` 操作符用于精确相等判断，支持数值型字段的容差参数 `tol`（如字号允许 ±0.5pt 的测量误差），对字体类型字段会先做别名归一化（将 SimSun/宋体_GB2312 等变体统一映射为标准"宋体"再比较）。`in` 操作符用于集合包含判断，当合规值存在多组可选情况时（如行距可以是单倍 1.0 或固定 28pt），将期望值数组传入 `value` 即可。`range` 操作符用于数值区间判断，适合检查页边距、缩进量等带边界的格式参数。`regex` 操作符则用正则表达式直接验证段落文本模式，例如要求参考文献条目的开头必须是 `[数字]` 格式。

结构完整性类操作符处理跨段落级的约束。`count_min` 与 `count_max` 检查某类段的数量区间，例如"参考文献条目至少 5 条"、"致谢标题最多出现 1 次"。标题段前后的空行分隔不通过独立 op 检查，而是读取 v2 字段 `has_blank_before` / `has_blank_after`（bool）配合 `eq` 操作符断言。`skip` 操作符显式声明某类段不需要任何格式检查（如封面行），用于显式排除已知格式自由的区域。

文档级高级操作符是本引擎区别于简单逐段循环检查的关键能力。`sequence_consecutive` 检查编号连续性，适用于一级/二级/三级标题编号、图表编号、参考文献条目标号等场景，该操作符支持按父段分组——例如三级标题 `1.1.1` 只在其父段 `1.1` 的范围内检查连续性，与 `2.1.1` 互不干扰；实现时引擎先收集所有目标段的编号，再向前查找最近的父段索引作为分组键，每个分组独立维护期望编号的递增状态，独立报告重复、跳号等异常。`cross_ref_order` 执行引述顺序校验，例如"如图 2-1 所示"这句话在正文中出现的位置，不得早于图 2-1 题注在文档中出现的位置，该检查通过滑动窗口扫描所有引述模式与所有被引述目标的相对位置，定位"先引后现"的违规。`document_structure` 校验文档五大部分（摘要→正文→参考文献→致谢→附录）的宏观顺序是否符合规范，避免作者把参考文献插在摘要前面这类结构性错误。`page_margins` 与 `page_size` 则直接读取提取器返回的节级配置，检查文档每个 section 的上下左右页边距是否符合目标值（带容差）、纸张尺寸是否为 A4。

### 3.3 错误收集与去重

所有操作符在发现违规时都通过 `_add_error()` 统一接口上报错误，每条错误记录包含错误类型 `type`（format/missing/sequence/order/structure/count 等）、描述 `description`、定位信息 `location`（页码+段号或 section 级别说明）、`paragraph_index`（对应 python-docx 的段索引，供后续修复与批注定位）、`segment`（命中的段分类标签）。对于"修复规则"，每条错误还可附带 `action` 与 `action_value`，例如 `set_font: 宋体`、`set_size: 10.5`、`set_line_spacing: 1`，为后续的自动修复模块提供直接可执行的操作指令。`missing` 类型的"字段无法读取"警告做了专门的去重与智能抑制：同一段落同一字段只报告一次避免噪音；纯中文段落的 `font_en_most` 缺失、纯英文段落的 `font_cn_most` 缺失、仅设置了厘米单位缩进但无字符单位缩进（`first_line_chars`）等场景会做文本特征识别并自动跳过，不把已知合理的 None 值当作提取器缺陷报告。

---

## 四、智能排版修复子系统（format 包）

智能排版修复子系统解决的是"文档原始格式完全混乱，需要从零开始按国家标准或学校规范重新排版"的场景，与规则校验子系统"按规则挑错、尽可能保留原有格式"的定位形成互补。该子系统以**管道模式**（Pipeline）组织多个聚焦单一职责的操作模块，按固定顺序依次对文档施加变换，最终产出一份格式合规的 Word 文件。FormatEngine 本身是一个非常薄的 Facade 类，只负责初始化各子模块实例并创建 Pipeline，真正的处理流程完全在 Pipeline 中编排。

Pipeline 的标准执行顺序分为八步。第一步由 `DocumentLoader` 负责源文件加载与格式归一化：DOC/WPS 文件委托 `office` 转换器转换为 DOCX；TXT 纯文本文件按行拆分后创建段落；Markdown 文件则先清理标题符号、列表符、加粗斜体等标记，再转换为纯段落。第二步由 `SymbolNormalizer` 执行中文符号归一化，把文档中混用的半角符号统一替换为对应的中文全角形式（例如逗号、句号、冒号、引号、破折号），同时统一处理各种空格变体与零宽字符。第三步是图表题注探测与格式化：Pipeline 遍历文档的 paragraph/table 混合块序列，对含有图片或表格的块，向上、向下各扫描若干段寻找居中对齐且以"图""表"开头的候选题注段，命中后单独应用图表题注的字体与字号配置，并加入已处理集合避免被后续正文规则覆盖。第四步由 `TitleFinder` 识别文档主标题与副标题段落。第五步进入主循环，`ParagraphClassifier` 对剩余未处理的段落逐段分类（正文/一级至四级标题/附件说明/署名日期等），再由 `FontOp` 与 `ParagraphOp` 分别施加字体和段落级格式化（字体、字号、对齐、首行缩进、行距、段前段后间距）。第六步由 `TableOp` 统一格式化所有表格：字体、行高、边框粗细、表头加粗、列宽智能分配等。第七步 `PageSetupOp` 对文档所有 section 施加页面设置：页边距、A4 纸张强制尺寸、页脚页码插入。页码支持"— 1 —"、"第 1 页"、"1 / N"三种显示风格，以及居中、左右、内外侧（奇偶页分别对齐）四种位置。第八步将处理完成的 Document 对象保存到输出路径。

该子系统的格式配置通过 `format.config` 模块统一管理，内置三套预置（Preset）：`official` 党政公文预设（方正小标宋标题 22pt、仿宋正文 16pt、A4 页边距 3.7/3.5/2.8/2.6cm）、`academic` 学术论文预设（黑体标题、宋体正文、宽松页边距）、`legal` 法律文书预设（宋体正文 14pt）。`Config` 类在构造时以字典展开方式加载预置，再通过递归合并算法应用用户传入的覆盖项；这种方式允许用户仅覆盖"正文字号"一个字段而保留其余所有默认值，避免把几十项配置重复写一遍。

---

## 五、报告输出与辅助子系统

### 5.1 错误批注（engine.annotator）

错误批注模块接受校验引擎产生的错误列表与原始 DOCX 文件路径，为每一条带 `paragraph_index` 的错误在对应 Word 段落后插入一条评论（Comment）。评论内容包含错误严重级别（错误/警告）、规则描述、修复建议等字段，作者打开批注后的文档即可在 Word 内置的批注面板中逐条浏览、定位、修改，无需对照 CSV 报告手动查找段号。该功能通过 python-docx 操作 Word 的 comments 扩展 XML 实现，对 .doc 与 .pdf 文件则会跳过并提示仅支持 DOCX。

### 5.2 CSV 报告生成（engine.reporter）

Reporter 模块将错误列表序列化为可供 Excel 打开的 CSV 文件，分为单文档报告与批量报告两种模式。单文档模式输出七列：序号、严重级别、错误类型、错误描述、定位信息、段落索引、段分类标签；批量模式在单文档的基础上额外增加"文件名列"，并将多份文档的错误连续写入同一张 CSV，便于教务处或院系统计全年级论文的合规情况。CSV 写入采用带 BOM 的 UTF-8 编码（utf-8-sig），解决了 Windows Excel 默认按 GBK 解读 UTF-8 CSV 时中文乱码的经典问题。Reporter 还提供 `to_timestamped_path()` 辅助方法，将输出文件名自动附加时间戳后缀（如 `论文名_check_20260822_014331.csv`），避免多次运行时相互覆盖。

### 5.3 LLM 规则自动生成（engine.rule_generator）

RuleGenerator 模块提供一种"低代码"方式创建规则文件：用户只需提供一份格式规范的 Word 模板文档或纯文字规范，模块会先调用提取器从模板中取出各段的格式特征（字号、字体、对齐、行距），或直接读取用户输入的规范文字，然后将格式化后的文本作为 Prompt 上下文发送给大语言模型（通过内置的 `LLMClient` 调用 DeepSeek、Kimi、GLM 等 OpenAI 兼容接口），要求 LLM 按 Prompt Template 中规定的 Schema 契约输出 YAML 规则文件。从 LLM 返回中提取 YAML 时做了三重鲁棒处理：优先匹配 markdown 代码块 ````yaml...````，其次匹配以 `schema:` 或 `segments:` 开头的原始 YAML 片段，最后尝试剥离尾部说明文字，确保即使 LLM 生成解释性文本也能正确拿到 YAML 主体。解析后会校验 YAML 至少包含 `segments` 与 `rules` 两个顶级键，不满足则抛错提示用户重试。

---

## 六、交互入口层（gui.py + cli.py）

交互入口层有两个：**GUI（`gui.py`，PySide6）是当前唯一面向用户的主界面**，6 个功能页（校验 / 排版修复 / 批注 / 报告 / 规则生成 / 设置），耗时操作用 `QThread` Worker 在后台执行并经信号回传进度；**CLI（`cli.py`）是前期开发产物，作为备用入口保留、不再扩展新命令**。两者共用同一份业务逻辑：`gui.py` 的 `GuiBackend(_cli.CLI)` 子类化 `CLI` 类复用其方法，构造时用 `_UiProxy` 把 `ui.printers` 的打印调用改走 Qt 回调，任务结束后还原。Windows 发行包同样以 `gui.py` 为打包入口（Nuitka 走 `--enable-plugin=pyside6` + `--windows-console-mode=disable`，PyInstaller 走 `--windowed`），`cli.py` 作为模块被打进产物供 GUI 复用业务层，但不会启动 REPL 主循环。

CLI 基于 prompt_toolkit 实现，程序启动后自动创建 `input/` 与 `output/` 两个工作目录，用户以 `/` 开头的命令进行操作，每一条命令都经过统一的参数解析器解析（支持双引号包裹含空格路径、`--key value`、`--key=value`、`--key` 无值等多种写法）。共注册 18 条命令（含 `/help` 与 `/quit`，业务命令 16 条）：

文档校验类命令包括 `check` 校验单个文件与 `check-dir` 批量校验目录，两者都支持 `--rules` 参数指定非默认规则文件。校验完成后在终端打印结构化的结果表格，并自动写入 CSV 报告。文档修复类命令包括 `format` 按预设排版单个文件与 `format-dir` 批量排版，支持 `--preset` 切换官方/学术/法律三套预设与 `--output` 指定输出路径；`check-fix` 与 `check-fix-dir` 则组合执行"校验+自动修复"两步流程，并把修复数量输出给用户看。`annotate` 命令基于最近一次 `check` 的结果在 Word 文档中插入评论批注。配置类命令包括 `config` 查看当前配置、`preset` 切换排版预设、`save-config` 与 `load-config` 以 JSON 格式持久化和恢复自定义配置。`get-rules` 命令是 LLM 规则生成能力的交互入口，支持 `--text "规范内容"` 从文字直接生成与 `--domain 名称` 指定产物文件名。`test` 命令执行端到端演示：在 `examples/` 目录下跑一遍 校验→格式化→修复→批注 的完整链路，生成四份产物文档供用户直观查看效果。`chat` 命令进入与 LLM 的多轮对话子界面，允许用户询问规则编写、格式规范、YAML 结构等问题。`set-key` 与 `test-api` 用于配置 LLM 的 API Key 与验证连通性。

---

## 七、规则文件体系（config 目录）

项目内置四套领域规则文件与一所学校的专属规则。`rules_lunwen.yaml` 是完整毕业论文（大论文）规则，标题采用"第X章 xxx"中文编号格式、正文小四（12pt）、固定 28pt 行距。`rules_湖北汽院_优秀小论文.yaml` 位于 `schools/` 子目录，适配湖北汽车工业学院的优秀毕业设计（小论文）规范，差异点包括：标题采用"1 xxx"阿拉伯数字编号格式、正文五号（10.5pt）、单倍行距、参考文献首行不缩进、左 3cm 其余 2cm 页边距等。`rules_official.yaml` 与 `rules_academic.yaml` 分别覆盖党政公文与学术投稿两个场景。每套规则文件都包含完整的 15 余个 segment 分类定义与 20 余条 rule 检查定义，可作为用户编写自定义规则的直接模板。

规则契约的完整设计说明见 `config/schema.md`，其中逐一列出了 v2 版本新增的段落字段（字符单位缩进、中英文字体分别提取、段前后空行检测等）、新增的 5 个操作符（sequence_consecutive/cross_ref_order/document_structure/page_margins/page_size），并以代码块形式给出规则文件的标准骨架与字段对照表。

---

## 八、测试体系（tests 目录）

项目的自动化测试采用 pytest 框架组织为 15 个测试文件共 258 个用例，核心覆盖规则引擎的关键计算能力。`test_rule_engine.py` 是基础测试，验证各个 op（eq/in/range/count_min 等）的通过与失败分支。`test_rule_engine_actions.py` 验证规则条目附带的 `action/action_value` 修复指令能否正确传递到错误对象。`test_rule_engine_after_h1.py` 专门测试 `after_h1('标题') until next h1/end` 章节范围语法的边界情况，验证标题命中、范围跨越、超出边界后自动失效等关键分支。`test_rule_engine_order.py` 验证 `cross_ref_order` 引述顺序检查与 `sequence_consecutive` 编号连续性检查，覆盖了"先引后现"异常、跳号、重复编号等错误路径。`test_rule_engine_missing.py` 验证 `first_line_chars` 回退推算、纯中文段落 font_en_most 静默抑制等 missing 警告的智能逻辑。`test_match_parser_security.py` 则是沙箱安全的红线测试，构造了 `__import__('os')`、`eval()`、属性访问、列表推导式、lambda 等十几种恶意表达式，验证全部被白名单拒绝并返回布尔假，无一例执行成功。`test_format_engine.py`、`test_punctuation.py`、`test_heading_regex_dedup.py` 覆盖格式引擎的标点归一化、标题正则去重、格式化流水线等能力。`test_cli.py` 对 CLI 命令的参数解析器做了多组合覆盖，确保路径与标志位解析的鲁棒性。`test_annotator.py` 覆盖 Word 批注写入的完整链路，重点固定一条历史致命缺陷：`engine/annotator.py` 拼 XML 标签时漏写 Clark 记法花括号导致 `word/comments.xml` 不存在的文档必崩，故用"无现成批注的 docx"作为回归夹具；另含控制台编码加固测试，保证 GBK 控制台下状态消息不会抛 `UnicodeEncodeError`。`test_heading_depth.py` 覆盖标题层级识别，固定两类历史缺陷：`RE_HEADING_H3` 曾把 `1.` / `1.1` / `1.1.1` 一律判成三级标题、层级信息完全丢失，以及 `摘  要`、`参  考  文  献` 这类中间带全角空格的结构性标题曾被当成正文套上首行缩进；现由 `numeric_heading_level()` 按点分段数推导层级，`structural_heading_kind()` 按去空白文本识别结构性标题。`test_rule_engine_coverage.py` 固定规则覆盖诊断的口径：规则文件与文档不匹配（典型是 `else` 兜底段类吃光全部段落、却有一半规则一次都没执行）时必须显式告警，且这些告警是诊断而非判决——不进 `errors`、不参与修复与批注定位、不改错误对象结构，也不得替用户判定文档合规与否。`test_llm_profiles.py` 覆盖 LLM 命名方案库：`config/llm_profiles.yaml` 的多方案读写往返、按名 upsert 的改名语义（旧条目必须一并删除、就地编辑不改变列表位置、active 指针在改名与删除后都不能悬空）、损坏文件按空库处理，以及`LLMClient` 的三级解析优先级（构造时显式 profile → 方案库当前方案 → 遗留 `api_config.yaml`）；特别固定了「写方案库不得触碰遗留文件」与「`RuleGenerator()` 无参可构造」两条回归。`test_gui_llm_dialog.py` 是唯一需要 Qt 的测试文件（`QT_QPA_PLATFORM=offscreen` + session 级 QApplication，网络请求一律用 monkeypatch 桩替换），覆盖 LLM 设置弹窗的完整操作流与两条真实缺陷回归：「新建方案后保存不得覆盖上一条」（`setCurrentRow` 只移动焦点不改变选中，用 `currentItem()` 判断工作副本来源会让新建变成覆盖）、以及「设置页保存的偏好重启后必须恢复」（历史上只有写没有读）；另含设置页摘要的密钥掩码、后台测试线程不挂父对象与自清理、以及窗口销毁后回调吞掉 RuntimeError 的健壮性用例。

---

## 九、目录结构速览

```
paper-format-matching/
├── cli.py                                  # 遗留 REPL 入口（18 条命令），业务编排层 CLI 类，被 GUI 子类化复用
├── gui.py                                  # PySide6 图形界面（主入口），子类化 cli.CLI 复用业务层（需 PySide6，已在 requirements.txt）
├── CLAUDE.md                               # Claude Code 项目指引
├── requirements.txt                        # 依赖清单（python-docx、pypdf、pytest、prompt_toolkit、yaml 等）
├── engine/                                 # 校验与报告引擎包
│   ├── __init__.py                         # 统一导出 RuleEngine / get_extractor / annotate_document 等
│   ├── rule_engine.py                      # 规则引擎核心：段落分类+15 个 op 的表驱动执行（1206 行）
│   ├── _whitelist.py                       # AST 白名单沙箱：安全执行 match 表达式，拒绝注入
│   ├── extractors/                         # 文档提取器（策略模式分发）
│   │   ├── docx.py                         # DOCX 提取：字体继承链、行距换算、20+ 字段
│   │   ├── doc.py                          # DOC 提取：通过 Office/WPS COM 转 DOCX
│   │   ├── pdf.py                          # PDF 提取：尽力而为仅取文本+页码
│   │   └── schema.py                       # 提取结果的结构定义
│   ├── annotator.py                        # Word 批注写入：把错误以评论形式插入 DOCX
│   ├── reporter.py                         # CSV 报告（单文件+批量，含 BOM 防 Excel 乱码）
│   ├── rule_generator.py                   # LLM 规则自动生成（从模板文档或规范文本生成 YAML）
│   ├── llm_client.py                       # OpenAI 兼容 HTTP LLM 客户端（支持多种国产模型）
│   └── paths.py                            # 跨打包/源码环境的路径解析
├── format/                                 # 智能排版修复引擎包
│   ├── engine.py                           # FormatEngine 薄 Facade（构造子模块并启 Pipeline）
│   ├── pipeline.py                         # 8 步排版流水线：加载→符号归一→题注→标题→段落→表格→页面→保存
│   ├── config.py                           # 3 套预置（official/academic/legal）+ 递归合并 Config 类
│   ├── document_loader.py                  # 多格式统一加载器（DOC/TXT/MD/DOCX/WPS）
│   ├── page_setup.py                       # 页边距/A4/页码（支持奇偶页多种风格）
│   ├── converters/
│   │   └── office.py                       # .doc/.wps → .docx 转换（调用系统 Office/WPS）
│   └── ops/                                # 排版操作原子模块（单一职责，可单独测试）
│       ├── paragraph_formatter.py          # 段落格式化 Facade（组合 TitleFinder + Classifier）
│       ├── paragraph_classifier.py         # 段分类并应用字体/缩进/对齐
│       ├── title_finder.py                 # 主标题与副标题识别算法
│       ├── _paragraph_helpers.py           # 共享辅助函数（字体设置、格式化标题、块迭代器）
│       ├── font.py                         # FontOp（中英文字体+字号批量设置）
│       ├── paragraph.py                    # ParagraphOp（对齐、行距、缩进）
│       ├── punctuation.py                  # 中文标点/符号归一化（~364 行）
│       ├── symbol_normalizer.py            # 全角/半角/空格/零宽字符统一
│       ├── table.py                        # 表格排版（字体、行高、边框、列宽、表头加粗）
│       ├── attachment.py                   # 附件说明段识别与格式
│       ├── space.py                        # 空格清理与规范化
│       └── text_loader.py                  # TXT/MD 文本加载与 Markdown 标记清洗
├── config/                                 # 规则文件与 Schema 契约
│   ├── schema.md                           # 规则文件格式契约 v2（设计原则+字段+op 全解）
│   ├── prompt_template.md                  # LLM 生成规则的系统 Prompt 模板
│   ├── rules_lunwen.yaml                   # 完整毕业论文（大论文）规则
│   ├── rules_official.yaml                 # 党政公文规则
│   ├── rules_academic.yaml                 # 学术期刊投稿规则
│   ├── api_config.yaml.example             # 遗留单配置模板（仍作兜底读取）
│   ├── llm_profiles.yaml                   # LLM 命名方案库（gitignore，GUI 方案管理弹窗）
│   └── gui_settings.json                 # GUI 偏好（gitignore，不含密钥）
│   └── schools/
│       └── rules_湖北汽院_优秀小论文.yaml    # 湖北汽院专属：优秀小论文格式规则
├── tests/                                  # pytest 单元测试（15 文件 / 258 用例）
│   ├── fixtures/sample.docx                # 测试预置样本
│   ├── test_rule_engine.py                 # 基础 op 测试
│   ├── test_rule_engine_actions.py         # 修复动作传递测试
│   ├── test_rule_engine_after_h1.py        # 章节范围语法测试
│   ├── test_rule_engine_order.py           # 编号连续性+引述顺序测试
│   ├── test_rule_engine_missing.py         # missing 字段抑制逻辑测试
│   ├── test_match_parser_security.py       # AST 沙箱安全红线测试（恶意表达式拒绝）
│   ├── test_format_engine.py               # 格式化流水线测试
│   ├── test_punctuation.py                 # 标点/符号归一化测试
│   ├── test_heading_regex_dedup.py         # 标题正则去重测试
│   ├── test_cli.py                         # CLI 参数解析测试
│   ├── test_annotator.py                   # Word 批注写入回归测试（含 GBK 控制台编码加固）
│   ├── test_heading_depth.py               # 数字编号标题层级 + 结构性标题识别测试
│   └── test_rule_engine_coverage.py      # 规则覆盖诊断测试（诊断不等于判决）
├── ui/                                     # REPL 界面辅助
│   ├── printers.py                         # 终端彩色输出（表格、步骤、欢迎横幅）
│   └── input.py                            # prompt_toolkit 输入会话构建
├── examples/                               # /test 端到端演示产物缓存区
│   └── sample_for_test.docx                # 预置演示文档
├── input/                                  # 工作目录：用户待处理文档
├── output/                                 # 工作目录：格式化/修复产物
└── reports/                                # 工作目录：CSV 检查报告
```
