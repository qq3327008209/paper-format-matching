# -*- coding: utf-8 -*-
"""Office 文档转换器。

将旧版 Office 文档（.doc / .wps）转换为 .docx：

- Windows 平台优先通过 win32com 调用 WPS / Word COM 自动化完成转换；
- 其他平台或 COM 不可用时，回退到 LibreOffice
  (``soffice --headless --convert-to docx``)。

转换产生的临时 .docx 文件统一存放在系统 tempfile 目录中。
本模块完全自包含，不依赖旧项目 wfp_core。
"""

import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

IS_WINDOWS = sys.platform.startswith('win')

if IS_WINDOWS:
    try:
        import win32com.client
    except ImportError:
        win32com = None
    try:
        import pythoncom
    except ImportError:
        pythoncom = None
else:
    win32com = None
    pythoncom = None


logger = logging.getLogger(__name__)

_RE_SAFE_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')


def _make_temp_docx_path(base_name):
    """在系统 tempfile 目录下生成一个唯一的 .docx 临时文件路径。"""
    safe_base_name = _RE_SAFE_FILENAME_CHARS.sub('_', base_name).strip(' ._')
    safe_base_name = (safe_base_name or 'document')[:80]
    temp_name = f"~temp_{safe_base_name}_{os.getpid()}_{uuid.uuid4().hex[:8]}.docx"
    return os.path.join(tempfile.gettempdir(), temp_name)


class SofficeConverter:
    """LibreOffice 包装器：把旧版 .doc / .wps 文件转换为 .docx。"""

    def __init__(self, soffice_path=None, timeout=120):
        self.soffice_path = soffice_path or self._find_soffice()
        self.timeout = int(timeout or 120)

    @property
    def available(self):
        return bool(self.soffice_path)

    @staticmethod
    def _find_soffice():
        for executable in ("soffice", "soffice.com", "libreoffice"):
            found = shutil.which(executable)
            if found:
                return found

        common_paths = []
        if sys.platform == "darwin":
            common_paths.extend(
                [
                    "/Applications/LibreOffice.app/Contents/MacOS/soffice",
                    "/opt/homebrew/bin/soffice",
                    "/usr/local/bin/soffice",
                ]
            )
        elif os.name == "nt":
            for base in (os.environ.get("PROGRAMFILES"), os.environ.get("PROGRAMFILES(X86)")):
                if base:
                    common_paths.extend(
                        [
                            os.path.join(base, "LibreOffice", "program", "soffice.com"),
                            os.path.join(base, "LibreOffice", "program", "soffice.exe"),
                        ]
                    )
        else:
            common_paths.extend(
                [
                    "/usr/bin/soffice",
                    "/usr/local/bin/soffice",
                    "/usr/bin/libreoffice",
                    "/usr/local/bin/libreoffice",
                    "/snap/bin/libreoffice",
                    "/opt/libreoffice/program/soffice",
                    "/opt/libreoffice*/program/soffice",
                ]
            )

        for path in common_paths:
            if "*" in path:
                for candidate in sorted(Path("/").glob(path.lstrip("/"))):
                    if candidate.is_file():
                        return str(candidate)
                continue
            if path and os.path.isfile(path):
                return path
        return None

    def convert_to_docx(self, input_path):
        """调用 soffice --headless --convert-to docx 转换文件。

        返回 ``(converted_path, work_dir)``，``work_dir`` 由调用方负责清理。
        """
        input_path = Path(input_path).expanduser().resolve()
        if not self.available:
            raise RuntimeError(
                "LibreOffice (soffice) 不可用，无法转换 "
                f"{input_path}。请安装 LibreOffice，或先将文档另存为 .docx 后再处理。"
            )

        work_dir = Path(tempfile.mkdtemp(prefix="wfp_soffice_"))
        out_dir = work_dir / "out"
        profile_dir = work_dir / "profile"
        out_dir.mkdir(parents=True, exist_ok=True)
        profile_dir.mkdir(parents=True, exist_ok=True)
        profile_uri = profile_dir.resolve().as_posix()
        if os.name == "nt":
            profile_uri = "/" + profile_uri

        cmd = [
            self.soffice_path,
            "--headless",
            "--norestore",
            f"-env:UserInstallation=file://{profile_uri}",
            "--convert-to",
            "docx",
            "--outdir",
            str(out_dir),
            str(input_path),
        ]
        logger.info("正在使用 LibreOffice 转换为 .docx: %s", input_path.name)

        creationflags = 0
        if os.name == "nt" and hasattr(subprocess, "CREATE_NO_WINDOW"):
            creationflags = subprocess.CREATE_NO_WINDOW

        try:
            proc = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=self.timeout,
                creationflags=creationflags,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            shutil.rmtree(work_dir, ignore_errors=True)
            raise RuntimeError(f"LibreOffice 转换超时 ({self.timeout}s): {input_path}") from exc
        except FileNotFoundError as exc:
            shutil.rmtree(work_dir, ignore_errors=True)
            raise RuntimeError(f"找不到 soffice 可执行文件: {self.soffice_path}") from exc
        except Exception:
            shutil.rmtree(work_dir, ignore_errors=True)
            raise

        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()
            shutil.rmtree(work_dir, ignore_errors=True)
            raise RuntimeError(
                f"LibreOffice 无法将 {input_path} 转为 .docx。"
                f"请先手动转为 .docx 后再处理。详细错误: {detail}"
            )

        generated_files = sorted(out_dir.glob("*.docx"))
        if not generated_files:
            detail = (proc.stderr or proc.stdout or "").strip()
            shutil.rmtree(work_dir, ignore_errors=True)
            raise RuntimeError(
                f"LibreOffice 未生成 {input_path} 对应的 .docx。"
                f"请先手动转为 .docx 后再处理。详细信息: {detail}"
            )

        logger.info("LibreOffice 转换完成: %s", generated_files[0].name)
        return generated_files[0], work_dir


class WPSAppManager:
    """Windows 下通过 win32com 管理 WPS / Word COM 自动化，用于把 .doc 转为 .docx。"""

    def __init__(self):
        self.com_app = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.quit()
        return False

    @staticmethod
    def _com_available():
        return IS_WINDOWS and win32com is not None

    def get_app(self):
        if not self._com_available():
            raise RuntimeError(
                "当前环境无法使用 WPS/Word COM 自动化（非 Windows 或未安装 pywin32）。"
            )

        if self.com_app is None:
            logger.info("正在启动 WPS/Word 应用（独立进程）...")
            try:
                self.com_app = win32com.client.DispatchEx('KWPS.Application')
                logger.info("已成功连接到 WPS。")
            except Exception:
                try:
                    self.com_app = win32com.client.DispatchEx('Word.Application')
                    logger.info("已成功连接到 Word。")
                except Exception as e:
                    raise RuntimeError(f"未能启动 WPS 或 Word，请确保已安装。错误: {e}")

            try:
                self.com_app.Visible = False
            except Exception:
                pass
            try:
                self.com_app.DisplayAlerts = False
            except Exception:
                pass

        return self.com_app

    def convert_to_docx(self, input_path, temp_docx_path):
        """通过 COM 打开旧版文档并另存为 .docx (FileFormat=12)。"""
        app = self.get_app()
        doc_com = None
        try:
            doc_com = app.Documents.Open(os.path.abspath(input_path), ReadOnly=1)
            doc_com.SaveAs2(os.path.abspath(temp_docx_path), FileFormat=12)
        finally:
            if doc_com is not None:
                doc_com.Close()
        logger.info("COM 文件格式转换完成。")

    def quit(self):
        if self.com_app:
            logger.info("正在关闭 WPS/Word 应用...")
            try:
                self.com_app.Quit()
            except Exception as e:
                logger.warning("关闭应用时发生异常: %s", e)
            finally:
                self.com_app = None
                logger.info("应用已关闭。")


def convert_to_docx(path) -> str:
    """将 .doc / .wps 文件转换为 .docx，返回生成的 .docx 临时文件路径。

    转换策略：

    - 输入为 .docx 时，直接拷贝到 tempfile 目录下的临时文件并返回；
    - Windows 平台优先尝试 COM（WPSAppManager）；
    - COM 不可用或转换失败、以及非 Windows 平台，回退到 SofficeConverter；
    - 转换结果统一拷贝到系统 tempfile 目录下的临时 .docx 文件并返回其路径。
    """
    file_ext = os.path.splitext(path)[1].lower()
    base_name = os.path.splitext(os.path.basename(path))[0]
    temp_docx_path = _make_temp_docx_path(base_name)

    if file_ext == '.docx':
        shutil.copy2(path, temp_docx_path)
        return temp_docx_path

    if file_ext not in ('.doc', '.wps'):
        raise ValueError(
            f"不支持的文件格式: {file_ext}（仅支持 .doc / .wps / .docx）"
        )

    if IS_WINDOWS and WPSAppManager._com_available():
        try:
            with WPSAppManager() as mgr:
                mgr.convert_to_docx(path, temp_docx_path)
            return temp_docx_path
        except Exception as exc:
            logger.warning("WPS/Word COM 转换失败，尝试使用 LibreOffice 兜底: %s", exc)

    converter = SofficeConverter()
    if not converter.available:
        raise RuntimeError(
            "当前环境无法转换旧版文档：WPS/Word COM 不可用，且未找到 LibreOffice (soffice)。"
            "请安装 LibreOffice，或先将文档另存为 .docx 后再处理。"
        )

    converted_path, work_dir = converter.convert_to_docx(path)
    try:
        shutil.copy2(converted_path, temp_docx_path)
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
    return temp_docx_path
