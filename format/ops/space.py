"""Whitespace and blank-line operations.

Handles in-run space normalisation (copied from the legacy ``punctuation.py``
``_process_spaces_text`` logic) and whole-document blank-paragraph cleanup
(inspired by the legacy blank-line handling).
"""

from __future__ import annotations

import re


# Markers used to detect drawings / pictures embedded inside a paragraph so
# that image-only paragraphs are never treated as "blank".
_MEDIA_MARKERS = (
    "w:drawing", "w:pict", "w:object", "mc:AlternateContent", "v:shape",
)


def redistribute_text_to_runs(runs, new_full_text):
    """Redistribute ``new_full_text`` back across ``runs`` preserving formats.

    If the new text length matches the combined original length, each run
    receives a slice matching its original length (pure character replacement).
    Otherwise the whole text is placed in the first run and the rest cleared.
    """
    run_lengths = [len(run.text) for run in runs]
    total_original = sum(run_lengths)
    if len(new_full_text) == total_original:
        pos = 0
        for i, run in enumerate(runs):
            run.text = new_full_text[pos:pos + run_lengths[i]]
            pos += run_lengths[i]
    else:
        runs[0].text = new_full_text
        for run in runs[1:]:
            run.text = ""


def _process_spaces_text(text, mode):
    """Normalise spaces inside ``text`` according to ``mode``.

    Modes:
        - ``'keep_all'``: leave text untouched.
        - ``'remove_all'``: strip every half/full-width space.
        - ``'keep_en_boundary'``: collapse spaces at CJK/CJK boundaries but
          keep exactly one space at CJK ↔ ASCII boundaries.
    """
    if mode == "keep_all" or not text:
        return text
    if mode == "remove_all":
        return text.replace("\u3000", "").replace(" ", "")
    if mode == "keep_en_boundary":
        CN = r"\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff"
        EN = r"[A-Za-z0-9]"
        CN_cls = f"[{CN}]"

        # 1. Drop spaces between two CJK characters.
        text = re.sub(f"(?<={CN_cls}) +(?={CN_cls})", "", text)
        # 2. Normalise CJK <-> ASCII boundaries to exactly one space.
        text = re.sub(f"(?<={CN_cls}) *(?={EN})", " ", text)
        text = re.sub(f"(?<={EN}) *(?={CN_cls})", " ", text)
        # 3. Trim leading / trailing spaces.
        text = re.sub(r"^ +", "", text)
        text = re.sub(r" +$", "", text)
        return text
    return text


class SpaceOp:
    """Normalise spaces within paragraphs and clean blank paragraphs."""

    def __init__(self, config) -> None:
        self.config = config

    # ------------------------------------------------------------------
    # Space normalisation
    # ------------------------------------------------------------------
    def process_spaces_text(self, text, mode):
        """Module-level space normalisation exposed on the Op."""
        return _process_spaces_text(text, mode)

    def process_paragraph(self, para, mode="remove_all"):
        """Normalise spaces inside ``para`` in place.

        Returns ``True`` when the paragraph text was modified.
        """
        if mode == "keep_all":
            return False
        full_text = para.text
        if not full_text.strip():
            return False
        new_text = _process_spaces_text(full_text, mode)
        if new_text == full_text:
            return False
        runs = para.runs
        if not runs:
            return False
        redistribute_text_to_runs(runs, new_text)
        return True

    # ------------------------------------------------------------------
    # Blank-line cleanup
    # ------------------------------------------------------------------
    @staticmethod
    def _has_media(para) -> bool:
        xml = getattr(para._p, "xml", "") or ""
        return any(marker in xml for marker in _MEDIA_MARKERS)

    def _is_blank_paragraph(self, para) -> bool:
        """A paragraph counts as blank only if it has no text and no media."""
        if para.text.strip():
            return False
        return not self._has_media(para)

    @staticmethod
    def _remove_paragraph(para) -> None:
        element = para._p
        parent = element.getparent()
        if parent is not None:
            parent.remove(element)

    def remove_blank_lines(self, doc, mode="delete_single"):
        """Remove or merge blank paragraphs across the document body.

        Args:
            mode: One of
                - ``'preserve'``: do nothing.
                - ``'delete_single'``: drop isolated blank paragraphs; collapse
                  runs of multiple blanks down to one.
                - ``'keep_single'``: keep isolated blanks; collapse runs of
                  multiple blanks down to one.

        Returns:
            The number of paragraphs removed.
        """
        if mode == "preserve":
            return 0

        removed = 0
        paragraphs = list(doc.paragraphs)
        index = 0
        while index < len(paragraphs):
            para = paragraphs[index]
            if not self._is_blank_paragraph(para):
                index += 1
                continue

            # Collect the run of consecutive blank paragraphs.
            end = index
            while end < len(paragraphs) and self._is_blank_paragraph(paragraphs[end]):
                end += 1
            group = paragraphs[index:end]

            if len(group) == 1:
                if mode == "delete_single":
                    self._remove_paragraph(group[0])
                    removed += 1
                # 'keep_single' keeps a single blank paragraph.
            else:
                # Multiple consecutive blanks: always keep one, remove the rest.
                for extra in group[1:]:
                    self._remove_paragraph(extra)
                    removed += 1
            index = end
        return removed
