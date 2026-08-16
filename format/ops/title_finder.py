"""Title / subtitle detection for docx documents.

``TitleFinder`` locates the main title and subtitle paragraph block indices
by scanning the document's block items.  It is a focused extraction of the
title-detection logic that previously lived inside ``ParagraphFormatter``.
"""

from __future__ import annotations

from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.text.paragraph import CT_P
from docx.text.paragraph import Paragraph

from format.ops.heading import RE_TITLE_H1, RE_TITLE_H2
from format.ops.paragraph_formatter import (
    get_paragraph_alignment,
    get_paragraph_font_info,
    iter_block_items,
)


class TitleFinder:
    """Locate the main title and subtitle paragraph block indices."""

    def __init__(self, config, logger) -> None:
        """Initialise the finder.

        Args:
            config: A ``format.config.Config`` instance.
            logger: A ``logging.Logger`` used for progress messages.
        """
        self.config = config
        self._logger = logger

    def _log(self, message: str) -> None:
        self._logger.info(message)

    def find_title_paragraphs(self, doc, is_from_txt: bool, start_index: int = 0):
        """Find the main title and subtitle paragraph block indices.

        Returns:
            ``(title_indices, subtitle_indices)`` — lists of indices into the
            flat block list returned by ``iter_block_items``.
        """
        all_blocks = list(iter_block_items(doc))

        first_title_idx = -1

        if is_from_txt:
            self._log("文档源自 TXT，采用智能规则查找题目...")
            for idx in range(start_index, len(all_blocks)):
                block = all_blocks[idx]
                if isinstance(block, Paragraph) and block.text.strip():
                    text_to_check = block.text.strip()
                    if RE_TITLE_H1.match(text_to_check) or RE_TITLE_H2.match(text_to_check):
                        self._log(
                            f"  > 首个非空行 (块 {idx + 1}) 符合标题格式，"
                            "认定本文档无独立题目。"
                        )
                        return [], []
                    else:
                        self._log(f"  > 在块 {idx + 1} 发现首个非空段落，认定为题目首行。")
                        first_title_idx = idx
                        break
        else:
            self._log("正在预扫描以确定居中题目位置...")
            for idx in range(start_index, len(all_blocks)):
                block = all_blocks[idx]
                if not isinstance(block, Paragraph) or not block.text.strip():
                    continue
                para = block
                text_to_check = para.text.lstrip()
                if RE_TITLE_H1.match(text_to_check) or RE_TITLE_H2.match(text_to_check):
                    self._log("  > 发现一级/二级标题，在此之前未找到居中题目。")
                    return [], []
                if get_paragraph_alignment(para) == WD_ALIGN_PARAGRAPH.CENTER:
                    self._log(f"  > 在块 {idx + 1} 发现潜在题目首行。")
                    first_title_idx = idx
                    break

        if first_title_idx == -1:
            self._log("  > 扫描结束，未能找到题目。")
            return [], []

        first_title_para = all_blocks[first_title_idx]
        title_font, title_size = get_paragraph_font_info(first_title_para)

        title_indices = [first_title_idx]
        idx = first_title_idx + 1

        while idx < len(all_blocks):
            block = all_blocks[idx]
            if not isinstance(block, Paragraph):
                break

            para = block
            text = para.text.strip()

            if not text:
                self._log(f"  > 在块 {idx + 1} 遇到空行，标题识别结束。")
                break

            if get_paragraph_alignment(para) != WD_ALIGN_PARAGRAPH.CENTER:
                break

            para_font, para_size = get_paragraph_font_info(para)
            if para_font == title_font and para_size == title_size:
                self._log(f"  > 块 {idx + 1} 也是标题行（居中且字体字号相同）。")
                title_indices.append(idx)
                idx += 1
            else:
                break

        self._log(f"  > 共识别到 {len(title_indices)} 行标题。")

        subtitle_indices = []
        subtitle_start_idx = idx

        while subtitle_start_idx < len(all_blocks):
            block = all_blocks[subtitle_start_idx]
            if isinstance(block, Paragraph) and block.text.strip():
                break
            if isinstance(block, Paragraph):
                subtitle_start_idx += 1
            else:
                break

        if subtitle_start_idx < len(all_blocks):
            block = all_blocks[subtitle_start_idx]
            if isinstance(block, Paragraph):
                para = block
                text = para.text.strip()

                if text and get_paragraph_alignment(para) == WD_ALIGN_PARAGRAPH.CENTER:
                    para_font, para_size = get_paragraph_font_info(para)
                    if para_font != title_font or para_size != title_size:
                        self._log(
                            f"  > 在块 {subtitle_start_idx + 1} 发现副标题首行"
                            "（居中且字体字号与标题不同）。"
                        )
                        subtitle_indices.append(subtitle_start_idx)

                        subtitle_font, subtitle_size = para_font, para_size
                        idx = subtitle_start_idx + 1

                        while idx < len(all_blocks):
                            block = all_blocks[idx]
                            if not isinstance(block, Paragraph):
                                break

                            para = block
                            text = para.text.strip()

                            if not text:
                                self._log(f"  > 在块 {idx + 1} 遇到空行，副标题识别结束。")
                                break

                            if get_paragraph_alignment(para) != WD_ALIGN_PARAGRAPH.CENTER:
                                break

                            para_font, para_size = get_paragraph_font_info(para)
                            if para_font == subtitle_font and para_size == subtitle_size:
                                self._log(
                                    f"  > 块 {idx + 1} 也是副标题行（居中且字体字号相同）。"
                                )
                                subtitle_indices.append(idx)
                                idx += 1
                            else:
                                break

                        self._log(f"  > 共识别到 {len(subtitle_indices)} 行副标题。")

        return title_indices, subtitle_indices
