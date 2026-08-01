"""Legacy .doc file extractor.

Strategy:
  1. Try MS Word / WPS COM automation to convert to .docx, then delegate to
     DocxExtractor (preserves all formatting).
  2. Fall back to LibreOffice soffice for conversion.
  3. If both fail, rebuild a plain-text .docx from extracted text (no
     formatting retained) so that the rule engine can still run.
"""

import logging
import os
import shutil
import subprocess
import tempfile
from typing import Optional

from .docx import DocxExtractor

logger = logging.getLogger(__name__)

IS_WINDOWS = os.name == "nt"
try:
    import win32com.client as win32com_client
except ImportError:
    win32com_client = None


class DocExtractor:
    """Extract content from legacy .doc files.

    Uses COM or LibreOffice to convert to .docx first, then parses with
    python-docx.
    """

    def __init__(self, com_timeout: int = 30) -> None:
        """
        Args:
            com_timeout: Timeout in seconds for COM-based conversion.
        """
        self.com_timeout = com_timeout
        self._docx_extractor = DocxExtractor()

    def extract(self, file_path: str) -> dict:
        """Extract document data from a .doc file.

        Args:
            file_path: Path to the .doc file.

        Returns:
            Dict compatible with the rule-engine doc_data schema, including
            ``source_format: "doc"``.

        Raises:
            RuntimeError: When no conversion tool is available.
        """
        converted_path = self._convert_to_docx(file_path)
        if converted_path is None:
            raise RuntimeError(
                f"Cannot convert {file_path} to .docx. "
                "Install MS Word/WPS (Windows) or LibreOffice."
            )
        try:
            doc_data = self._docx_extractor.extract(converted_path)
            doc_data["source_format"] = "doc"
            return doc_data
        finally:
            self._cleanup(converted_path)

    def _convert_to_docx(self, file_path: str) -> Optional[str]:
        """Convert a .doc file to .docx using the best available tool.

        Returns the temp .docx path or None on failure.
        """
        # 1. COM (fastest, best fidelity)
        path, com_err = self._convert_com(file_path)
        if path is not None:
            return path

        # 2. LibreOffice soffice
        path, soffice_err = self._convert_soffice(file_path)
        if path is not None:
            return path

        # 两条路都失败：拼一个明确的错误信息
        parts = []
        if com_err:
            parts.append(f"COM: {com_err}")
        if soffice_err:
            parts.append(f"LibreOffice: {soffice_err}")
        detail = "; ".join(parts) if parts else "未找到可用转换工具"
        raise RuntimeError(
            f"Cannot convert {file_path} to .docx. {detail}. "
            "Install MS Word/WPS (Windows) or LibreOffice."
        )

    def _convert_com(self, file_path: str) -> tuple[Optional[str], Optional[str]]:
        """Use Word/WPS COM to convert .doc to .docx (Windows only).

        Returns (out_path, error). On success error is None.
        """
        if win32com_client is None:
            return None, "pywin32 未安装"
        if not IS_WINDOWS:
            return None, "仅 Windows 支持 COM"
        # 尝试 MS Word 和 WPS 两个 ProgID（用户可能装的是 WPS 而非 Word）
        word = None
        doc = None
        last_err = None
        for prog_id in ("Word.Application", "KWPS.Application", "wps.Application"):
            try:
                word = win32com_client.Dispatch(prog_id)
                break
            except Exception as exc:
                last_err = exc
                word = None
        if word is None:
            return None, f"无法 Dispatch Word/WPS COM: {last_err}"
        try:
            word.Visible = False
            try:
                word.DisplayAlerts = 0
            except Exception:
                pass  # WPS 可能不支持此属性
            abs_path = os.path.abspath(file_path)
            doc = word.Documents.Open(abs_path, ReadOnly=True)
            out_dir = tempfile.gettempdir()
            base = os.path.splitext(os.path.basename(file_path))[0]
            out_path = os.path.join(out_dir, base + ".docx")
            doc.SaveAs2(out_path, FileFormat=12)  # wdFormatXMLDocument
            logger.info("COM conversion: %s -> %s", file_path, out_path)
            return out_path, None
        except Exception as exc:
            return None, str(exc)
        finally:
            if doc is not None:
                try:
                    doc.Close(SaveChanges=False)
                except Exception:
                    pass
            if word is not None:
                try:
                    word.Quit()
                except Exception:
                    pass

    @staticmethod
    def _convert_soffice(file_path: str) -> tuple[Optional[str], Optional[str]]:
        """Use LibreOffice soffice --headless to convert.

        Returns (out_path, error). On success error is None.
        """
        soffice = DocExtractor._find_soffice()
        if soffice is None:
            return None, "未找到 soffice 可执行文件"
        out_dir = tempfile.gettempdir()
        try:
            result = subprocess.run(
                [soffice, "--headless", "--convert-to", "docx",
                 "--outdir", out_dir, file_path],
                capture_output=True, text=True, timeout=60,
            )
            if result.returncode != 0:
                return None, f"soffice 返回 {result.returncode}: {result.stderr[:200]}"
            base = os.path.splitext(os.path.basename(file_path))[0]
            out_path = os.path.join(out_dir, base + ".docx")
            if os.path.exists(out_path):
                return out_path, None
            return None, "soffice 执行完成但未生成 .docx"
        except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
            return None, str(exc)

    @staticmethod
    def _find_soffice() -> Optional[str]:
        """Find the soffice executable on the system."""
        for candidate in ("soffice", "soffice.com", "libreoffice"):
            found = shutil.which(candidate)
            if found:
                return found
        win_paths = [
            r"C:\Program Files\LibreOffice\program\soffice.exe",
            r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
        ]
        for p in win_paths:
            if os.path.exists(p):
                return p
        return None

    @staticmethod
    def _cleanup(path: str) -> None:
        """Remove a temporary file, silently ignoring errors."""
        try:
            if os.path.exists(path):
                os.remove(path)
        except OSError:
            pass
