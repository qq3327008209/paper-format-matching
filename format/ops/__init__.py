"""Atomic formatting operations.

Each sub-module exports one ``*Op`` class that operates on python-docx
objects.  Re-export them here so callers can write
``from format.ops import FontOp`` instead of reaching into sub-modules.

The higher-level orchestration classes (``ParagraphFormatter``,
``SymbolNormalizer``, ``TextLoader``) are also re-exported here for
convenience; they are the refactored pieces of the legacy ``FormatEngine``.
"""

from .attachment import AttachmentOp
from .font import FontOp
from .heading import HeadingOp
from .paragraph import ParagraphOp
from .paragraph_formatter import ParagraphFormatter
from .punctuation import PunctuationOp
from .space import SpaceOp
from .symbol_normalizer import SymbolNormalizer
from .table import TableOp
from .text_loader import TextLoader

__all__ = [
    "AttachmentOp",
    "FontOp",
    "HeadingOp",
    "ParagraphOp",
    "ParagraphFormatter",
    "PunctuationOp",
    "SpaceOp",
    "SymbolNormalizer",
    "TableOp",
    "TextLoader",
]
