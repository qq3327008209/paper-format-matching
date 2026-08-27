"""Processing pipeline: orchestrates formatting operations in sequence.

Each step calls a focused sub-module on ``FormatEngine`` directly (no facade
methods on the engine). The pipeline order:

1. Convert source file to .docx (DocumentLoader)
2. Normalize punctuation (SymbolNormalizer)
3. Detect and format figure/table captions
4. Find and format main title & subtitle (ParagraphFormatter)
5. Iterate paragraphs: classify -> apply font/indent/alignment (ParagraphFormatter)
6. Format tables (TableOp)
7. Apply page setup (PageSetupOp)
8. Save output
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from docx.text.paragraph import Paragraph

if TYPE_CHECKING:
    from .engine import FormatEngine

logger = logging.getLogger(__name__)


class Pipeline:
    """Orchestrate the sequence of formatting operations."""

    def __init__(self, engine: "FormatEngine") -> None:
        self.engine = engine
        self._formatter = engine._paragraph_formatter
        self._symbol_normalizer = engine._symbol_normalizer
        self._table_op = engine._table_op
        self._loader = engine._loader
        self._page_setup = engine._page_setup

    def run(self, input_path: str, output_path: str) -> str:
        """Execute the full formatting pipeline.

        Args:
            input_path: Source document path (any supported format).
            output_path: Destination .docx path.

        Returns:
            The actual output path.

        Raises:
            RuntimeError: If conversion fails or document is empty.
        """
        doc = self._loader.load(input_path)
        is_from_txt = self._loader.is_text_input(input_path)

        if self.engine.config.get("normalize_punctuation", False):
            self._symbol_normalizer.normalize_document_symbols(doc)

        all_blocks = list(self._formatter._iter_block_items(doc))
        processed: set[int] = set()
        apply_color = not is_from_txt

        if not is_from_txt:
            self._detect_captions(doc, all_blocks, processed, apply_color)

        title_indices, subtitle_indices = self._formatter.find_title_paragraphs(
            doc, is_from_txt,
        )
        for idx in title_indices:
            processed.add(idx)
        for idx in subtitle_indices:
            processed.add(idx)

        self._format_paragraphs(
            doc, all_blocks, processed, title_indices, subtitle_indices,
            is_from_txt, apply_color,
        )

        self._table_op.format_all(doc, apply_color=apply_color)
        self._page_setup.apply(doc, is_from_txt=is_from_txt)

        logger.info("Saving formatted document: %s", output_path)
        doc.save(output_path)
        return output_path

    def _detect_captions(
        self, doc, all_blocks, processed: set, apply_color: bool,
    ) -> None:
        """Detect and format figure/table caption paragraphs."""
        from docx.enum.text import WD_ALIGN_PARAGRAPH

        cfg = self.engine.config
        for idx, block in enumerate(all_blocks):
            is_pic = _block_has_drawing(block)
            is_table = type(block).__name__ == "Table"
            if not (is_pic or is_table):
                continue
            for direction in (-1, 1):
                caption_found = False
                for i in range(idx + direction, -1 if direction == -1 else len(all_blocks), direction):
                    if i in processed:
                        continue
                    candidate = all_blocks[i]
                    if not hasattr(candidate, "text") or not candidate.text.strip():
                        break
                    if (hasattr(candidate, "alignment")
                            and candidate.alignment == WD_ALIGN_PARAGRAPH.CENTER
                            and (candidate.text.strip().startswith("图")
                                 or candidate.text.strip().startswith("表"))):
                        det = "figure" if candidate.text.strip().startswith("图") else "table"
                        font_key = f"{det}_caption_font"
                        size_key = f"{det}_caption_size"
                        f = cfg.get(font_key, cfg.get("body_font", "仿宋_GB2312"))
                        s = cfg.get(size_key, cfg.get("body_size", 12))
                        for run in candidate.runs:
                            self._formatter._set_run_font(run, f, s, set_color=apply_color)
                        processed.add(i)
                        caption_found = True
                        break
                if caption_found:
                    break

    def _format_paragraphs(
        self, doc, all_blocks, processed,
        title_indices, subtitle_indices,
        is_from_txt, apply_color,
    ) -> None:
        """Main paragraph formatting loop."""
        from format.ops.paragraph import ParagraphOp
        from format.ops.font import FontOp

        font_op = FontOp(self.engine.config)
        para_op = ParagraphOp(self.engine.config)
        block_idx = 0
        while block_idx < len(all_blocks):
            if block_idx in processed:
                block_idx += 1
                continue

            block = all_blocks[block_idx]
            if not hasattr(block, "text"):
                block_idx += 1
                continue
            if not block.text.strip():
                block_idx += 1
                continue
            if type(block).__name__ == "Table":
                block_idx += 1
                continue

            self._formatter.apply_para_format(
                block, font_op, para_op, is_from_txt, apply_color,
            )
            block_idx += 1


def _block_has_drawing(block) -> bool:
    """Return True when a paragraph block carries a drawing/picture."""
    from format.ops.symbol_normalizer import has_drawing_or_pict

    if isinstance(block, Paragraph):
        return has_drawing_or_pict(block)
    return False
