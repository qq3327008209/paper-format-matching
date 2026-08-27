# -*- coding: utf-8 -*-
"""``FormatEngine`` 薄 facade：持有子模块并暴露 ``run()``。

重构说明：

历史版本里 ``FormatEngine`` 是 ~400 行的"什么都有"大杂烩，30+ 个 ``_xxx``
透传方法把请求转发给 :mod:`format.ops` 的子模块。本轮把它砍到只剩
构造和 ``run()``，所有真实逻辑下沉到：

- :class:`format.document_loader.DocumentLoader` — 文件加载、空白行归一化
- :class:`format.page_setup.PageSetupOp` — 页边距、A4、页码
- :class:`format.ops.symbol_normalizer.SymbolNormalizer` — 中文符号归一化
- :class:`format.ops.paragraph_formatter.ParagraphFormatter` — 段落分类与排版
- :class:`format.ops.table.TableOp` — 表格排版

:class:`format.pipeline.Pipeline` 直接拿这些子模块，不再走 ``engine._xxx`` 透传。
"""

import logging

from format.document_loader import DocumentLoader
from format.ops.paragraph_formatter import ParagraphFormatter
from format.ops.symbol_normalizer import SymbolNormalizer
from format.ops.table import TableOp
from format.page_setup import PageSetupOp

logger = logging.getLogger(__name__)


class FormatEngine:
    """Format a document end-to-end via :class:`format.pipeline.Pipeline`.

    The engine is a thin facade: it owns the focused sub-modules listed above
    and exposes a single ``run(input_path, output_path)`` entry point.
    """

    def __init__(self, config) -> None:
        """Initialise the engine with a :class:`format.config.Config` instance."""
        self.config = config
        self._symbol_normalizer = SymbolNormalizer()
        self._paragraph_formatter = ParagraphFormatter(config, logger)
        self._table_op = TableOp(config)
        self._loader = DocumentLoader(config)
        self._page_setup = PageSetupOp(config)

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------
    def run(self, input_path: str, output_path: str) -> str:
        """Run the full formatting pipeline.

        ``Pipeline`` is imported lazily to avoid a circular import
        (``pipeline`` imports ``FormatEngine`` for type-checking).
        """
        from .pipeline import Pipeline
        return Pipeline(self).run(input_path, output_path)
