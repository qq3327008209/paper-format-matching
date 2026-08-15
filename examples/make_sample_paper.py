# -*- coding: utf-8 -*-
"""生成示例论文 docx：配合 examples/rules_example.yaml 使用。

运行方式：
    python examples/make_sample_paper.py

生成文件：
    examples/sample_paper.docx

该文档故意包含若干格式错误，用于演示校验引擎的检出能力：
  - 一级标题字体错误（宋体，应为黑体）
  - 正文字号错误（11pt，应为12pt）
  - 正文对齐错误（居中，应为两端对齐）
  - 三级标题字体错误（宋体，应为仿宋）
  - 缺少摘要（exists 规则会报）
"""
import os

from docx import Document
from docx.shared import Pt, Cm
from docx.enum.text import WD_ALIGN_PARAGRAPH


def set_run(para, text, font_name, size_pt, bold=False):
    """向段落添加一个 run，设置字体、字号、粗体。"""
    run = para.add_run(text)
    run.font.name = font_name
    run.font.size = Pt(size_pt)
    run.bold = bold
    return run


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    out = os.path.join(here, "sample_paper.docx")

    doc = Document()

    # ---- 页面设置：页边距 2.54 cm ----
    for section in doc.sections:
        section.top_margin = Cm(2.54)
        section.bottom_margin = Cm(2.54)
        section.left_margin = Cm(3.17)
        section.right_margin = Cm(3.17)

    # ---- 标题（index=0）：黑体 16pt 居中 ✅ ----
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    set_run(p, "示例论文", "黑体", 16)

    # ---- 摘要（index=1）：楷体 12pt ✅ ----
    p = doc.add_paragraph()
    set_run(p, "摘要：本文演示论文格式校验引擎的使用方法。", "楷体", 12)

    # ---- 一级标题：黑体 16pt ----
    # ❌ 故意用宋体，校验应检出
    p = doc.add_paragraph()
    set_run(p, "一、引言", "宋体", 16)

    # ---- 正文：仿宋 12pt 两端对齐 ----
    # ❌ 故意用11pt + 居中，校验应检出字号和对齐问题
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    set_run(p, "这是引言的正文段落。按照规则，正文应使用仿宋小四（12pt）两端对齐。"
               "本段故意设置为11pt居中，用于演示校验引擎的检出能力。"
               "此外，本文还包含省略号……以及引号“测试引号”的规范化处理。", "仿宋", 11)

    # ---- 二级标题：楷体 12pt ✅ ----
    p = doc.add_paragraph()
    set_run(p, "（一）研究背景", "楷体", 12)

    # ---- 正文 ✅ ----
    p = doc.add_paragraph()
    p.paragraph_format.first_line_indent = Cm(0.74)
    set_run(p, "研究背景是论文的重要组成部分。正文应使用仿宋小四（12pt），两端对齐，首行缩进两字符。", "仿宋", 12)

    # ---- 三级标题：仿宋 12pt ----
    # ❌ 故意用宋体，校验应检出
    p = doc.add_paragraph()
    set_run(p, "1. 数据来源", "宋体", 12)

    # ---- 正文 ✅ ----
    p = doc.add_paragraph()
    p.paragraph_format.first_line_indent = Cm(0.74)
    set_run(p, "本文数据来源于公开资料，包括统计年鉴、政府公报和学术论文。所有数据均经过交叉验证，确保可靠性。", "仿宋", 12)

    # ---- 一级标题：黑体 16pt ✅ ----
    p = doc.add_paragraph()
    set_run(p, "二、结论", "黑体", 16)

    # ---- 正文 ✅ ----
    p = doc.add_paragraph()
    p.paragraph_format.first_line_indent = Cm(0.74)
    set_run(p, "综上所述，本文通过示例演示了文档格式校验与排版系统的核心功能，包括规则驱动的格式检查、自动修复和批量处理能力。", "仿宋", 12)

    doc.save(out)
    print(f"CREATED {out}")
    print("提示：运行以下命令校验该文档：")
    print(f"  /check-fix examples/sample_paper.docx --rules examples/rules_example.yaml")


if __name__ == "__main__":
    main()
