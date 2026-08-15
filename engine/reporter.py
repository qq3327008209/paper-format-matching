"""Reporter module: CSV report generation for rule-check results."""

import csv
import io
import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


class Reporter:
    """Generate human-readable reports from rule-engine error lists."""

    # Errors whose ``type`` indicates a field could not be read by the
    # extractor (as opposed to a genuine format violation).  These are
    # surfaced as warnings rather than errors in summaries.
    _WARNING_TYPES = {"missing"}

    @staticmethod
    def is_warning(err: dict) -> bool:
        """Return True for errors that are warnings rather than failures."""
        return err.get("type", "") in Reporter._WARNING_TYPES

    def to_csv_string(self, errors: list[dict],
                      doc_path: str = "",
                      domain: str = "") -> str:
        """Serialize error list to a CSV-formatted string.

        Args:
            errors: Output from ``RuleEngine.validate()``.
            doc_path: Source document path for the report header.
            domain: Rule domain string from the rule file.

        Returns:
            CSV content as a UTF-8 string (no BOM).
        """
        buf = io.StringIO()
        writer = csv.writer(buf, lineterminator="\n")
        # Header
        writer.writerow(["#", "Severity", "Type", "Description",
                         "Location", "Paragraph", "Segment"])
        for i, err in enumerate(errors, start=1):
            severity = "警告" if self.is_warning(err) else "错误"
            writer.writerow([
                i,
                severity,
                err.get("type", ""),
                err.get("description", ""),
                err.get("location", ""),
                err.get("paragraph_index", ""),
                err.get("segment", ""),
            ])
        return buf.getvalue()

    def save_csv(self, errors: list[dict],
                 output_path: str,
                 doc_path: str = "",
                 domain: str = "") -> str:
        """Write CSV report to disk.

        Args:
            errors: Error list.
            output_path: Destination file path (``.csv`` extension recommended).
            doc_path: Source document path.
            domain: Rule domain string.

        Returns:
            The output file path.
        """
        content = self.to_csv_string(errors, doc_path, domain)
        # 用 utf-8-sig（带 BOM）写入，Excel 默认按 GBK 读 CSV 时能正确识别中文
        with open(output_path, "w", encoding="utf-8-sig", newline="") as fh:
            fh.write(content)
        logger.info("CSV report saved: %s (%d errors)", output_path, len(errors))
        return output_path

    def save_batch_csv(self,
                       results: list[tuple[str, list[dict]]],
                       output_path: str) -> str:
        """Write a merged CSV report for multiple documents.

        Args:
            results: List of ``(file_path, errors)`` tuples, in the order they
                should appear in the report.
            output_path: Destination file path.

        Returns:
            The output file path.
        """
        buf = io.StringIO()
        writer = csv.writer(buf, lineterminator="\n")
        writer.writerow(["File", "#", "Severity", "Type", "Description",
                         "Location", "Paragraph", "Segment"])
        for f, errors in results:
            for j, err in enumerate(errors, 1):
                severity = "警告" if self.is_warning(err) else "错误"
                writer.writerow([
                    f,
                    j,
                    severity,
                    err.get("type", ""),
                    err.get("description", ""),
                    err.get("location", ""),
                    err.get("paragraph_index", ""),
                    err.get("segment", ""),
                ])
        content = buf.getvalue()
        with open(output_path, "w", encoding="utf-8-sig", newline="") as fh:
            fh.write(content)
        logger.info("Batch CSV report saved: %s", output_path)
        return output_path

    def summary_text(self, errors: list[dict],
                     doc_path: str = "",
                     domain: str = "") -> str:
        """Return a compact plain-text summary of errors.

        Args:
            errors: Error list.
            doc_path: Source document path.
            domain: Rule domain.

        Returns:
            Formatted multi-line string suitable for terminal output.
        """
        lines: list[str] = []
        lines.append(f"Document: {doc_path or '(stdin)'}")
        lines.append(f"Domain:   {domain or '(none)'}")

        warnings = [e for e in errors if self.is_warning(e)]
        failures = [e for e in errors if not self.is_warning(e)]
        lines.append(f"Errors:   {len(failures)}  |  Warnings: {len(warnings)}")
        lines.append("")

        type_counts: dict[str, int] = {}
        for err in errors:
            etype = err.get("type", "unknown")
            type_counts[etype] = type_counts.get(etype, 0) + 1
        for etype, count in sorted(type_counts.items()):
            tag = "[警告]" if etype in self._WARNING_TYPES else "[错误]"
            lines.append(f"  {tag} {etype}: {count}")
        lines.append("")

        for i, err in enumerate(errors, start=1):
            tag = "警告" if self.is_warning(err) else "错误"
            lines.append(f"[{i}] [{tag}] {err.get('description', '')}")
            loc = err.get("location", "")
            if loc:
                lines.append(f"    -> {loc}")
        return "\n".join(lines)

    def to_timestamped_path(self, base_path: str, suffix: str = "_check") -> str:
        """Create a timestamped file path from a base path.

        Example:  ``doc_check_20260801_123045.csv``

        Args:
            base_path: Base path like ``/path/to/doc`` (without extension).
            suffix: Prefix to insert before the timestamp.

        Returns:
            Path string with timestamp appended.
        """
        base, ext = _split_ext(base_path)
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        return f"{base}{suffix}_{ts}{ext}"


def _split_ext(path: str) -> tuple[str, str]:
    """Split a path into (stem, extension)."""
    if "." in path:
        idx = path.rfind(".")
        return path[:idx], path[idx:]
    return path, ""
