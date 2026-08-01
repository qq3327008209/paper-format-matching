"""Unified configuration for the formatting engine.

Provides defaults, built-in presets (official/academic/legal), and a ``Config``
helper for merging user overrides.
"""

from __future__ import annotations

import copy
import logging
import sys
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Default (official document) preset
# ---------------------------------------------------------------------------

DEFAULT_OFFICIAL_CONFIG: dict[str, Any] = {
    "name": "公文格式",
    "deep_clean": False,
    "normalize_punctuation": True,
    "remove_blank_lines": True,
    "blank_line_mode": "delete_single",
    "set_outline": True,
    "page_number": True,
    "page_number_font": "宋体",
    "page_number_size": 14,
    "page_number_style": "dash",
    "page_number_position": "outside",
    "page_number_offset_mm": 10,
    "replace_existing_page_number": True,
    "page": {"top": 3.7, "bottom": 3.5, "left": 2.8, "right": 2.6},
    "margin_top": 3.7,
    "margin_bottom": 3.5,
    "margin_left": 2.8,
    "margin_right": 2.6,
    "footer_distance": 1.5,
    # Title
    "title": {
        "font_cn": "方正小标宋简体",
        "font_en": "Times New Roman",
        "size": 22,
        "bold": False,
        "align": "center",
        "indent": 0,
        "line_spacing": 28,
        "space_before": 0,
        "space_after": 0,
    },
    "title_font": "方正小标宋简体",
    "title_size": 22,
    "title_line_spacing": 28,
    # Subtitle
    "subtitle": {
        "font_cn": "楷体",
        "font_en": "Times New Roman",
        "size": 16,
        "bold": False,
        "align": "center",
        "indent": 0,
        "line_spacing": 28,
    },
    "subtitle_font": "楷体",
    "subtitle_size": 16,
    "subtitle_line_spacing": 28,
    # Heading 1
    "heading1": {
        "font_cn": "黑体",
        "font_en": "Times New Roman",
        "size": 16,
        "bold": False,
        "align": "left",
        "indent": 32,
        "line_spacing": 28,
    },
    "h1_font": "黑体",
    "h1_size": 16,
    # Heading 2
    "heading2": {
        "font_cn": "楷体_GB2312",
        "font_en": "Times New Roman",
        "size": 16,
        "bold": False,
        "align": "left",
        "indent": 32,
        "line_spacing": 28,
    },
    "h2_font": "楷体_GB2312",
    "h2_size": 16,
    # Heading 3
    "heading3": {
        "font_cn": "仿宋_GB2312",
        "font_en": "Times New Roman",
        "size": 16,
        "bold": True,
        "align": "left",
        "indent": 32,
        "line_spacing": 28,
    },
    "h3_font": "仿宋_GB2312",
    "h3_size": 16,
    # Heading 4
    "heading4": {
        "font_cn": "仿宋_GB2312",
        "font_en": "Times New Roman",
        "size": 16,
        "bold": False,
        "align": "left",
        "indent": 32,
        "line_spacing": 28,
    },
    "h4_font": "仿宋_GB2312",
    "h4_size": 16,
    # Body
    "body": {
        "font_cn": "仿宋_GB2312",
        "font_en": "Times New Roman",
        "size": 16,
        "bold": False,
        "align": "justify",
        "indent": 32,
        "line_spacing": 28,
    },
    "body_font": "仿宋_GB2312",
    "body_size": 16,
    "body_font_en": "Times New Roman",
    "line_spacing": 28,
    "left_indent_cm": 2.8,
    "right_indent_cm": 2.6,
    # Signature / date
    "signature_font": "仿宋_GB2312",
    "signature_size": 16,
    "date_font": "仿宋_GB2312",
    "date_size": 16,
    # Attachment
    "attachment_font": "仿宋_GB2312",
    "attachment_size": 16,
    "enable_attachment_formatting": True,
    # Tables
    "table_font": "仿宋_GB2312",
    "table_header_font": "仿宋_GB2312",
    "table_size": 12,
    "table_line_spacing": 22,
    "table_row_height_cm": 0.7,
    "table_border_size_pt": 0.5,
    "table_width_percent": 100,
    "table_col_min_pct": 8,
    "table_col_max_pct": 45,
    "table_auto_col_width": True,
    "table_header_bold": True,
    "table_smart_align": False,
    "table_unified_borders": True,
    "enable_table_formatting": True,
    # Font aliases (from main project)
    "font_aliases": {
        "宋体": ["宋体", "SimSun", "SimSun-ExtB", "simsun"],
        "黑体": ["黑体", "SimHei", "SimHei-ExtB", "simhei"],
        "楷体": ["楷体", "KaiTi", "KaiTi_GB2312", "STKaiti"],
        "仿宋": ["仿宋", "FangSong", "FangSong_GB2312", "STFangsong"],
        "微软雅黑": ["微软雅黑", "Microsoft YaHei"],
        "Times New Roman": ["Times New Roman", "TimesNewRoman", "times"],
        "Arial": ["Arial", "arial"],
        "Calibri": ["Calibri", "calibri"],
    },
}


# ---------------------------------------------------------------------------
# Academic preset
# ---------------------------------------------------------------------------

ACADEMIC_CONFIG: dict[str, Any] = {
    **DEFAULT_OFFICIAL_CONFIG,
    "name": "学术论文格式",
    "page": {"top": 2.5, "bottom": 2.5, "left": 2.5, "right": 2.5},
    "title_font": "黑体", "title_size": 18,
    "h1_font": "黑体", "h1_size": 15,
    "h2_font": "黑体", "h2_size": 14,
    "body_font": "宋体", "body_size": 12,
    "body_font_en": "Times New Roman",
    "line_spacing": None,
    "attachment_font": "宋体", "attachment_size": 12,
    "signature_font": "宋体", "signature_size": 12,
    "date_font": "宋体", "date_size": 12,
    "table_font": "宋体",
    "table_size": 12,
}

# ---------------------------------------------------------------------------
# Legal preset
# ---------------------------------------------------------------------------

LEGAL_CONFIG: dict[str, Any] = {
    **DEFAULT_OFFICIAL_CONFIG,
    "name": "法律文书格式",
    "page": {"top": 3.0, "bottom": 2.5, "left": 3.0, "right": 2.5},
    "title_font": "宋体", "title_size": 22,
    "h1_font": "黑体", "h1_size": 14,
    "h2_font": "黑体", "h2_size": 14,
    "body_font": "宋体", "body_size": 14,
    "line_spacing": None,
    "attachment_font": "宋体", "attachment_size": 14,
    "signature_font": "宋体", "signature_size": 14,
    "date_font": "宋体", "date_size": 14,
    "table_font": "宋体",
}

# ---------------------------------------------------------------------------
# PRESETS registry
# ---------------------------------------------------------------------------

PRESETS: dict[str, dict[str, Any]] = {
    "official": DEFAULT_OFFICIAL_CONFIG,
    "academic": ACADEMIC_CONFIG,
    "legal": LEGAL_CONFIG,
}

# Convenience reference
DEFAULT_CONFIG: dict[str, Any] = DEFAULT_OFFICIAL_CONFIG


def get_preset(name: str) -> dict[str, Any]:
    """Return a deep copy of a named preset, or the official preset."""
    preset = PRESETS.get(name)
    if preset is None:
        logger.warning("Preset '%s' not found, using 'official'", name)
        preset = PRESETS["official"]
    return copy.deepcopy(preset)


def merge_config(base: dict[str, Any], overrides: Optional[dict]) -> dict[str, Any]:
    """Merge user overrides into a base config dict.

    Top-level keys are replaced; nested dicts are merged recursively.

    Args:
        base: Base configuration dict (e.g. a preset).
        overrides: User-supplied overrides (may be ``None``).

    Returns:
        A new merged dict.
    """
    if not overrides:
        return copy.deepcopy(base)
    result = copy.deepcopy(base)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merge_config(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


# ---------------------------------------------------------------------------
# Config helper class
# ---------------------------------------------------------------------------


class Config:
    """Configuration manager for the format engine.

    Supports built-in presets, user overrides, and platform-specific
    font resolution.
    """

    def __init__(
        self,
        preset_name: str = "official",
        overrides: Optional[dict[str, Any]] = None,
    ) -> None:
        """
        Args:
            preset_name: One of the keys in ``PRESETS``.
            overrides: Optional dict of config overrides.
        """
        self._base = get_preset(preset_name)
        self._merged = merge_config(self._base, overrides)

    def get(self, key: str, default: Any = None) -> Any:
        """Get a configuration value."""
        return self._merged.get(key, default)

    def get_nested(self, *keys: str, default: Any = None) -> Any:
        """Get a nested config value by key path.

        Example: ``config.get_nested('title', 'font_cn')``
        """
        obj = self._merged
        for k in keys:
            if isinstance(obj, dict):
                obj = obj.get(k)
            else:
                return default
            if obj is None:
                return default
        return obj

    @property
    def merged(self) -> dict[str, Any]:
        """Return the fully merged config dict."""
        return self._merged

    @property
    def preset_name(self) -> str:
        return self._base.get("name", "unknown")
