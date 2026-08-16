"""Paragraph classification and formatting -- thin facade.

After the round-2 refactor this module is a thin facade that composes the
focused sub-modules below and exposes the original ``ParagraphFormatter``
API so existing callers (``format.pipeline``, tests) keep working unchanged.

Real logic lives in:

- :class:`format.ops.title_finder.TitleFinder` -- title / subtitle detection
- :class:`format.ops.paragraph_classifier.ParagraphClassifier` -- per-paragraph
  classification and formatting
- :mod:`format.ops._paragraph_helpers` -- shared helpers used by both
"""

from __future__ import annotations

from format.ops._paragraph_helpers import (
    apply_font_to_runs,
    apply_text_indent_and_align,
    format_heading,
    get_paragraph_alignment,
    get_paragraph_font_info,
    iter_block_items,
    reset_pagination_properties,
    set_run_font,
    strip_leading_whitespace,
)
from format.ops.paragraph_classifier import ParagraphClassifier
from format.ops.title_finder import TitleFinder


# Re-export the helpers as module-level names so existing ``ParagraphFormatter``
# methods can delegate to them without re-importing at call sites.
__all__ = [
    "ParagraphFormatter",
    "apply_font_to_runs",
    "apply_text_indent_and_align",
    "format_heading",
    "get_paragraph_alignment",
    "get_paragraph_font_info",
    "iter_block_items",
    "reset_pagination_properties",
    "set_run_font",
    "strip_leading_whitespace",
]


class ParagraphFormatter:
    """Thin facade over ``TitleFinder`` and ``ParagraphClassifier``.

    Refactored from the paragraph-formatting portion of the legacy
    ``FormatEngine``.  Each instance is constructed with a
    ``format.config.Config`` and a ``logging.Logger``.
    """

    def __init__(self, config, logger) -> None:
        """Initialise the formatter.

        Args:
            config: A ``format.config.Config`` instance.
            logger: A ``logging.Logger`` used for progress messages.
        """
        self.config = config
        self._title_finder = TitleFinder(config, logger)
        self._classifier = ParagraphClassifier(config, logger)

    # ------------------------------------------------------------------
    # Public API (delegates)
    # ------------------------------------------------------------------
    def find_title_paragraphs(self, doc, is_from_txt: bool, start_index: int = 0):
        """Find the main title and subtitle paragraph block indices."""
        return self._title_finder.find_title_paragraphs(doc, is_from_txt, start_index)

    def apply_para_format(
        self,
        block,
        font_op=None,
        para_op=None,
        is_from_txt: bool = False,
        apply_color: bool = False,
    ) -> None:
        """Classify and format a single paragraph block."""
        self._classifier.apply_para_format(
            block,
            font_op=font_op,
            para_op=para_op,
            is_from_txt=is_from_txt,
            apply_color=apply_color,
        )

    # ------------------------------------------------------------------
    # Backward-compatible helpers (thin wrappers around _paragraph_helpers)
    # ------------------------------------------------------------------
    @staticmethod
    def _iter_block_items(parent):
        """Yield ``Paragraph`` and ``Table`` objects in document order."""
        return iter_block_items(parent)

    def _set_run_font(self, run, font_name, size_pt, set_color=False):
        """Set western + east-asian font, size, and optionally color on a run."""
        set_run_font(run, font_name, size_pt, self._classifier._font_op, self.config, set_color=set_color)

    def _apply_font_to_runs(self, para, font_name, size_pt, set_color=False):
        """Apply the configured font / size to every run in ``para``."""
        apply_font_to_runs(para, font_name, size_pt, self._classifier._font_op, self.config, set_color=set_color)

    def _get_paragraph_font_info(self, para):
        """Return ``(font_name, font_size_pt)`` of the first non-empty run."""
        return get_paragraph_font_info(para)

    @staticmethod
    def _get_paragraph_alignment(para):
        return get_paragraph_alignment(para)

    def _strip_leading_whitespace(self, para) -> None:
        strip_leading_whitespace(para, self._classifier._logger)

    @staticmethod
    def _reset_pagination_properties(para) -> None:
        reset_pagination_properties(para)

    def _get_outline_level(self, para):
        from format.ops._paragraph_helpers import get_outline_level
        return get_outline_level(para)

    def _set_outline_level(self, para, level):
        from format.ops._paragraph_helpers import set_outline_level
        return set_outline_level(para, level, self.config, self._classifier._logger)

    def _format_heading(self, para, level) -> None:
        format_heading(para, level, self.config, self._classifier._logger)

    def _apply_text_indent_and_align(self, para) -> None:
        apply_text_indent_and_align(para, self.config)
