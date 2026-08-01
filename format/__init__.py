"""Format package: unified formatting engine for Word documents."""

from .engine import FormatEngine
from .config import PRESETS, DEFAULT_CONFIG, Config

__all__ = ["FormatEngine", "PRESETS", "DEFAULT_CONFIG", "Config"]
