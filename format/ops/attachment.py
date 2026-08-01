"""Attachment paragraph operations.

Detects ``附件`` marker paragraphs and applies the attachment font / size
configured on the engine ``Config`` using :class:`FontOp`.
"""

from __future__ import annotations

import re

from format.ops.font import FontOp


# Attachment marker pattern (copied from wfp_core.py).
RE_ATTACHMENT = re.compile(r"^附件\s*(\d+|[一二三四五六七八九十百千万零]+)?\s*[:：]?$")


class AttachmentOp:
    """Detect and format ``附件`` paragraphs."""

    def __init__(self, config) -> None:
        self.config = config
        self._font_op = FontOp(config)

    def is_attachment(self, text) -> bool:
        """Return ``True`` when ``text`` is an attachment marker line."""
        if text is None:
            return False
        return RE_ATTACHMENT.match(text.strip()) is not None

    def format(self, para) -> None:
        """Apply attachment font / size (from config) to ``para``.

        Uses ``attachment_font`` and ``attachment_size`` config keys, falling
        back to body defaults. Western font defaults to ``Times New Roman``.
        """
        font_cn = self.config.get("attachment_font", self.config.get("body_font", "仿宋_GB2312"))
        size = self.config.get("attachment_size", self.config.get("body_size", 16))
        font_en = self.config.get("body_font_en", "Times New Roman")
        self._font_op.apply_to_paragraph(para, font_cn, font_en, size)
