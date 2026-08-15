"""Engine package: rule engine, paragraph extractor, annotator, and LLM client."""

__version__ = "4.2.0"

from .rule_engine import RuleEngine, MatchParser, normalize_font
from .extractors import DocxExtractor, PdfExtractor, DocExtractor, get_extractor
from .annotator import annotate_document
from .reporter import Reporter
from .llm_client import LLMClient
from .rule_generator import RuleGenerator

__all__ = [
    "RuleEngine",
    "MatchParser",
    "normalize_font",
    "DocxExtractor",
    "PdfExtractor",
    "DocExtractor",
    "get_extractor",
    "annotate_document",
    "Reporter",
    "LLMClient",
    "RuleGenerator",
]
