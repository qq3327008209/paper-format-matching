"""Field contract for paragraph data passed between extractors and the rule engine.

The rule engine only understands the fields declared here; any field a rule
references via ``field: ...`` must be present in :class:`ParagraphData`.  When
a field is ``None`` the engine emits a ``missing``-type error rather than
silently treating the paragraph as compliant.
"""

from typing import Optional, TypedDict


class ParagraphData(TypedDict, total=False):
    """One paragraph as seen by the rule engine.

    ``total=False`` means every field is optional -- extractors may omit fields
    they cannot populate, and the rule engine treats ``None`` / missing values
    as "could not read" rather than "compliant".

    Attributes:
        index: 0-based position in the document's paragraph list.
        paragraph_index: Stable paragraph identifier (normally == ``index``).
        text: Raw paragraph text.
        style: Paragraph style name (e.g. ``"Heading 1"``, ``"Normal"``).
        font: Chinese (eastAsia) font name, e.g. ``"宋体"`` / ``"黑体"``.
        font_en: Western (ascii) font name, e.g. ``"Times New Roman"``.
        size: Font size in points.
        bold: Whether the run is bold.
        italic: Whether the run is italic.
        align: Alignment -- one of ``left`` / ``center`` / ``right`` / ``justify``.
        alignment: Alias for ``align`` (backwards compatible).
        line_spacing: Line spacing (multiplier, or fixed pt value).
        first_line_indent: First-line indent (cm).
        left_indent: Left indent (cm).
        right_indent: Right indent (cm).
        space_before: Spacing before paragraph (pt).
        space_after: Spacing after paragraph (pt).
        heading_level: Outline level (1-9); 0 means not a heading.
        in_table: Whether the paragraph lives inside a table cell.
        page: Page number (1-based; only populated by the PDF extractor).
    """

    index: int
    paragraph_index: int
    text: str
    style: Optional[str]
    font: Optional[str]
    font_en: Optional[str]
    size: Optional[float]
    bold: Optional[bool]
    italic: Optional[bool]
    align: Optional[str]
    alignment: Optional[str]
    line_spacing: Optional[float]
    first_line_indent: Optional[float]
    left_indent: Optional[float]
    right_indent: Optional[float]
    space_before: Optional[float]
    space_after: Optional[float]
    heading_level: int
    in_table: bool
    page: Optional[int]
