#!/usr/bin/env python3
"""DocFormat Engine · PySide6 图形化界面（视觉原型 ui/演示.html 已移除）。

架构：
    - 所有业务逻辑复用 cli.py 的 CLI 类（_validate_file / _check_fix_core /
      annotate_document / FormatEngine 等完全一致的底层）。
    - UI 结构参考 ui演示.html：顶部深色标题栏 + 左侧 6 项导航栏 + 右侧 QStackedWidget
      切换 6 个功能页 + 底部状态栏（引擎版本/当前规则/时钟/内存）。
    - 6 个功能页：校验(Validate) / 排版修复(Format) / 批注(Annotate) /
      报告(Reports) / 规则生成(Rules) / 设置(Settings)。
    - 耗时操作用 QThread Worker 执行，通过信号把进度、结果、日志回传主线程。
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import sys
import time
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable

from PySide6.QtCore import (
    QAbstractTableModel,
    QByteArray,
    QModelIndex,
    QObject,
    QPoint,
    QSize,
    Qt,
    QThread,
    QTimer,
    QUrl,
    Signal,
)
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QStatusBar,
    QStackedWidget,
    QStyle,
    QTableWidget,
    QTableWidgetItem,
    QTabBar,
    QTabWidget,
    QTextEdit,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from engine import __version__ as ENGINE_VERSION, Reporter, annotate_document
from engine.rule_engine import mismatch_warnings
from engine.paths import BASE_DIR, ensure_parent, resolve
from format import Config, FormatEngine

# ---------------------------------------------------------------------------
# 业务后端：子类化 cli.py 的 CLI 类，并重写 "print" 通道，使输出流向 GUI。
# ---------------------------------------------------------------------------
import cli as _cli


SUPPORTED_EXTS = _cli.SUPPORTED_EXTS


def _coverage_advisories(warnings):
    """Yield ``(log tag, message)`` pairs for rule-coverage advisories.

    与 CLI 的 ``_print_coverage_warnings`` 同一口径：构成错配判据的告警标
    ``[规则不匹配]``，死规则 / 未匹配段类等纯信息标 ``[规则覆盖]``——后者不能
    被当成"文档不合规"的证据。
    """
    mismatches = set(mismatch_warnings(warnings or []))
    for msg in warnings or []:
        yield ("规则不匹配" if msg in mismatches else "规则覆盖", msg)


class DropZoneFrame(QFrame):
    """支持点击和拖拽的 DropZone。

    QFrame 本身没有 clicked 信号，也不接收 drop 事件；这里通过重写
    mousePressEvent / dragEnterEvent / dropEvent 实现 UI 文案承诺的
    "拖拽文档至此，或点击浏览添加文件" 行为。
    """
    files_dropped = Signal(list)  # list[str]
    clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setCursor(Qt.PointingHandCursor)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        if not event.mimeData().hasUrls():
            event.ignore()
            return
        paths: list[str] = []
        for url in event.mimeData().urls():
            p = url.toLocalFile()
            if p and p.lower().endswith(SUPPORTED_EXTS):
                paths.append(p)
        if paths:
            self.files_dropped.emit(paths)
            event.acceptProposedAction()
        else:
            event.ignore()


class _GuiUiSink:
    """把 cli 层的 ui.print_* 全部转发到 GUI 日志区。"""

    def __init__(self, on_text: Callable[[str, str], None]):
        # on_text(message, level) level ∈ good/info/warn/debug
        self.on_text = on_text

    def print_info(self, msg):
        self.on_text(msg, "info")

    def print_good(self, msg):
        self.on_text(msg, "good")

    def print_warn(self, msg):
        self.on_text(msg, "warn")

    def print_step(self, msg):
        self.on_text(msg, "info")

    def print_check_result(self, errors, path, domain):
        e_count = sum(1 for e in errors if e.get("severity") == "error")
        w_count = sum(1 for e in errors if e.get("severity") == "warning")
        self.on_text(
            f"[{domain or '规则'}] 校验完成: 错误 {e_count}, 警告 {w_count} (文件: {os.path.basename(path)})",
            "good" if e_count == 0 else ("warn" if w_count and not e_count else "info"),
        )
        # 把前 10 条打入日志
        for i, e in enumerate(errors[:10]):
            self.on_text(
                f"  #{i+1}  [{e.get('severity','?')}] {e.get('description','')}  @ {e.get('location','')}",
                "debug",
            )
        if len(errors) > 10:
            self.on_text(f"  其余 {len(errors)-10} 条省略，详见右侧错误表格。", "debug")

    def print_batch_progress(self, idx, total, name, status, detail):
        icon = {"ok": "✔", "warn": "⚠", "fail": "✖", "skip": "↷"}.get(status, "·")
        self.on_text(f"[{idx}/{total}] {icon} {name}: {detail}", "info")

    def print_batch_summary(self, count, total_errors, combined_path):
        self.on_text(
            f"批量处理完成：{count} 份文档，合计 {total_errors} 处问题。汇总报告: {combined_path}",
            "good",
        )

    def print_banner(self):
        pass


class GuiBackend(_cli.CLI):
    """CLI 的 GUI 版本：把终端输出改走回调，不打印。"""

    def __init__(self, on_log: Callable[[str, str], None]):
        original_ui = _cli.ui
        sink = _GuiUiSink(on_log)

        class _UiProxy:
            def __getattr__(self, item):
                if hasattr(sink, item):
                    return getattr(sink, item)
                return getattr(original_ui, item)

        _cli.ui = _UiProxy()
        try:
            super().__init__()
        finally:
            _cli.ui = original_ui
        self.sink = sink
        # 保存默认 on_text，以便后台任务结束后还原，避免多线程并发改写
        self._default_on_text = sink.on_text

    # ---- Worker 日志通道 attach / detach ----
    # Worker 在后台线程运行前调用 attach_worker_log 临时把 sink.on_text
    # 改成自己的 emit；任务结束后必须调用 detach_worker_log 还原，
    # 否则下一次任务可能复用旧的（已 deleteLater 的）Worker 引用。
    def attach_worker_log(self, on_text):
        self._default_on_text = self.sink.on_text
        self.sink.on_text = on_text

    def detach_worker_log(self):
        self.sink.on_text = self._default_on_text

    # ---- 开放给 GUI 调用的封装 ----
    def gui_validate(self, file_path, rules_path):
        return self._validate_file(file_path, rules_path)

    def gui_format(self, file_path, out_path, preset_name, overrides=None):
        cfg = Config(preset_name, overrides=overrides or {})
        FormatEngine(cfg).run(file_path, out_path)
        return out_path

    def gui_check_fix(self, file_path, rules_path, preset_name, overrides=None):
        cfg = Config(preset_name, overrides=overrides or {})
        return self._check_fix_core(file_path, rules_path, cfg)

    def gui_annotate(self):
        if not self.last_errors or not self.last_file:
            raise RuntimeError("请先执行校验，再插入批注。")
        stem = os.path.splitext(os.path.basename(self.last_file))[0]
        out_path = _cli._resolve_output_path(f"{stem}_annotated.docx")
        ensure_parent(out_path)
        ok = annotate_document(self.last_file, out_path, self.last_errors)
        # annotator.LAST_ERROR 携带真实失败原因，避免 GUI 一律显示"可能是 .doc/.pdf"
        return ok, out_path, _cli._annotator.LAST_ERROR

    def gui_get_rules(self, domain, text, template_path):
        # 复用 cmd_get_rules 底层逻辑，但这里直接从对象方法拿
        import engine.rule_generator as rg
        gen = rg.RuleGenerator()
        if template_path:
            rules = gen.from_template(template_path, domain=domain or "自定义领域")
        elif text:
            rules = gen.from_spec(text, domain=domain or "自定义领域")
        else:
            raise RuntimeError("请输入规范文本或上传 Word 模板。")
        return rules


# ---------------------------------------------------------------------------
# QThread Worker：用于把耗时操作放到后台线程，避免 UI 冻结
# ---------------------------------------------------------------------------
@dataclass
class WorkerJob:
    kind: str  # "validate" / "format" / "check-fix" / "annotate" / "get-rules"
    kwargs: dict = field(default_factory=dict)


class Worker(QObject):
    progress = Signal(int, str)  # 0-100, message
    log = Signal(str, str)      # message, level
    result = Signal(str, object)  # job_kind, payload
    error = Signal(str)           # error_message

    def __init__(self, backend: GuiBackend, job: WorkerJob):
        super().__init__()
        self.backend = backend
        self.job = job
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        # 给 backend 的 sink 临时绑定到这个 Worker，这样 CLI 内部的
        # ui.print_* 会作为 log 信号回传主线程。绑定必须在主线程里完成，
        # 通过 attach/detach 配对使用，避免多线程并发改写 on_text。
        self.backend.attach_worker_log(self._emit_log)
        try:
            self._run()
        except Exception as exc:
            tb = traceback.format_exc()
            self.error.emit(f"{exc}\n{tb}")
        finally:
            # 无论成功/失败/异常都还原，避免后续任务指向已销毁的 Worker
            self.backend.detach_worker_log()

    def _emit_log(self, msg, level):
        self.log.emit(msg, level)

    def _run(self):

        if self.job.kind == "validate":
            path = self.job.kwargs["path"]
            rules = self.job.kwargs["rules"]
            self.progress.emit(10, "正在解析文档结构...")
            errors, domain, _rd, warnings = self.backend.gui_validate(path, rules)
            for tag, msg in _coverage_advisories(warnings):
                self.log.emit(f"[{tag}] {msg}", "warn")
            self.progress.emit(90, "生成校验报告...")
            # 保存 CSV
            stem = os.path.splitext(os.path.basename(path))[0]
            csv_path = Reporter().to_timestamped_path(
                resolve("reports", f"{stem}.csv"), "_check"
            )
            ensure_parent(csv_path)
            Reporter().save_csv(errors, csv_path, path, domain)
            self.progress.emit(100, "校验完成 ✓")
            self.result.emit(
                "validate",
                {"errors": errors, "domain": domain, "csv_path": csv_path,
                 "source": path, "warnings": warnings},
            )

        elif self.job.kind == "validate-dir":
            from cli import _resolve_input_dir
            dir_path = _resolve_input_dir(self.job.kwargs["dir_path"])
            rules = self.job.kwargs["rules"]
            files = self.backend._collect_files(dir_path)
            all_results = []
            total = len(files)
            for i, f in enumerate(files, 1):
                if self._stop:
                    break
                self.progress.emit(int((i / max(total, 1)) * 90),
                                   f"[{i}/{total}] 校验 {os.path.basename(f)}")
                try:
                    errors, domain, _rd, warnings = self.backend.gui_validate(f, rules)
                    all_results.append((f, errors, domain))
                    # 0 错误但规则没挂上文档时必须在日志里说清楚，否则整批
                    # 会被误读成全部合规。
                    for tag, msg in _coverage_advisories(warnings):
                        self.log.emit(
                            f"[{tag}] {os.path.basename(f)}: {msg}", "warn")
                except Exception as exc:
                    self.log.emit(f"[skip] {os.path.basename(f)}: {exc}", "warn")
            reporter = Reporter()
            combined_path = reporter.to_timestamped_path(
                resolve("reports", "batch.csv"), "_check"
            )
            ensure_parent(combined_path)
            Reporter().save_batch_csv([(f, e) for (f, e, _d) in all_results], combined_path)
            self.progress.emit(100, "批量校验完成 ✓")
            self.result.emit("validate-dir",
                             {"results": all_results, "csv_path": combined_path})

        elif self.job.kind == "format":
            path = self.job.kwargs["path"]
            out_path = self.job.kwargs["out_path"]
            preset = self.job.kwargs["preset"]
            overrides = self.job.kwargs.get("overrides", {})
            self.progress.emit(20, "加载并归一化文档...")
            self.backend.gui_format(path, out_path, preset, overrides)
            self.progress.emit(100, f"排版完成 → {out_path}")
            self.result.emit("format", {"out_path": out_path, "source": path})

        elif self.job.kind == "format-dir":
            files = self.job.kwargs["files"]
            out_dir = self.job.kwargs["out_dir"]
            preset = self.job.kwargs["preset"]
            overrides = self.job.kwargs.get("overrides", {})
            total = len(files)
            succeeded = 0
            for i, f in enumerate(files, 1):
                if self._stop:
                    self.log.emit(f"用户中断，已处理 {succeeded}/{total}", "warn")
                    break
                stem = os.path.splitext(os.path.basename(f))[0]
                out_path = os.path.join(out_dir, f"{stem}_formatted.docx")
                self.progress.emit(
                    int((i - 1) / max(total, 1) * 100),
                    f"[{i}/{total}] 排版 {os.path.basename(f)}")
                try:
                    self.backend.gui_format(f, out_path, preset, overrides)
                    succeeded += 1
                except Exception as exc:
                    self.log.emit(f"[skip] {os.path.basename(f)}: {exc}", "warn")
            self.progress.emit(100, f"批量排版完成 ✓ 共 {succeeded}/{total}")
            self.result.emit("format-dir",
                             {"out_dir": out_dir, "count": succeeded, "total": total})

        elif self.job.kind == "check-fix":
            path = self.job.kwargs["path"]
            rules = self.job.kwargs["rules"]
            preset = self.job.kwargs["preset"]
            overrides = self.job.kwargs.get("overrides", {})
            self.progress.emit(20, "校验中...")
            errors, domain, repaired, out_path = self.backend.gui_check_fix(
                path, rules, preset, overrides
            )
            for tag, msg in _coverage_advisories(self.backend.last_warnings):
                self.log.emit(f"[{tag}] {msg}", "warn")
            self.progress.emit(100,
                               f"修复完成：{repaired} 处修复 → {out_path or '（非 DOCX 跳过修复）'}")
            self.result.emit(
                "check-fix",
                {"errors": errors, "domain": domain,
                 "repaired": repaired, "out_path": out_path, "source": path},
            )

        elif self.job.kind == "annotate":
            ok, out_path, reason = self.backend.gui_annotate()
            self.progress.emit(
                100, f"批注已生成 → {out_path}" if ok else f"批注失败：{reason or '未知原因'}"
            )
            self.result.emit("annotate",
                             {"ok": ok, "out_path": out_path, "reason": reason})

        elif self.job.kind == "get-rules":
            self.progress.emit(10, "调用 LLM 生成规则...")
            rules = self.backend.gui_get_rules(
                domain=self.job.kwargs.get("domain"),
                text=self.job.kwargs.get("spec_text"),
                template_path=self.job.kwargs.get("template"),
            )
            self.progress.emit(100, "规则生成完成 ✓")
            self.result.emit("get-rules", {"yaml": rules})


# ---------------------------------------------------------------------------
# 样式表（复刻 ui演示.html 的视觉风格：深色顶栏、浅灰背景、卡片白框、圆角）
# ---------------------------------------------------------------------------
GLOBAL_QSS = """
QMainWindow, QWidget { background: #f6f8fc; color: #1e293b;
    font-family: "Segoe UI", "Microsoft YaHei", system-ui, sans-serif; font-size: 13px; }

/* 顶栏 */
#AppHeader {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 #0f172a, stop:1 #1e293b);
    color: white; min-height: 64px;
}
/* 顶栏 QLabel 显式设背景为顶栏深色，避免被全局 QWidget { background: #f6f8fc; }
   染成浅灰，在深色顶栏上显示成白色方框。transparent 在 Qt 中会被当
   未定义色显示成纯黑，所以用顶栏渐变终止色 #1e293b 融入背景 */
#AppHeader QLabel { color: white; font-size: 14px; font-weight: 600;
    background: #1e293b;
}
#AppHeader #HeaderLogo { font-size: 20px; font-weight: 700; }
#AppHeader #HeaderVersion {
    background: #334155; color: #94a3b8; padding: 2px 12px;
    border-radius: 10px; font-size: 12px; font-weight: 400;
}
#AppHeader #HeaderDot {
    background: #22c55e; border-radius: 4px; min-width: 8px; max-width: 8px;
    min-height: 8px; max-height: 8px;
}

/* 侧边栏 */
#Sidebar {
    background: #f8fafc; border-right: 1px solid #e9edf2; min-width: 220px; max-width: 220px;
}
#Sidebar QPushButton {
    background: transparent; border: none; text-align: left;
    padding: 10px 20px; margin: 2px 12px; border-radius: 10px;
    color: #475569; font-weight: 500; font-size: 14px;
}
#Sidebar QPushButton:hover { background: #eef2f6; }
#Sidebar QPushButton:checked {
    background: #e2e8f0; color: #0f172a; font-weight: 600; padding-left: 22px;
}
#Sidebar QPushButton:checked #navIcon { color: #2563eb; }
#SidebarFooter {
    color: #94a3b8; font-size: 12px; padding: 16px;
    border-top: 1px solid #e9edf2; margin-top: 12px;
}

/* 主内容容器 */
#MainContent { background: #f6f8fc; }

/* 页面标题 */
#ViewTitle {
    font-size: 22px; font-weight: 600; color: #0f172a;
}
#ViewIcon { color: #2563eb; font-size: 20px; }

/* 卡片 */
.Card {
    background: white; border: 1px solid #e9edf2; border-radius: 14px;
    padding: 20px;
}

/* 上传区：padding 与 min-height 收紧，避免 DropZone 被撑得过高，
   让校验页/排版页"从上向下紧凑排列"而不是看起来像两块平分空间 */
#DropZone {
    border: 2px dashed #d1d9e6; border-radius: 16px; padding: 20px;
    background: white; min-height: 120px;
}
#DropZone:hover { border-color: #2563eb; background: #f0f7ff; }

/* 统计卡 */
.StatCard {
    background: white; border-radius: 14px; padding: 18px 20px;
    border-left: 5px solid #2563eb;
}
.StatCard#danger { border-left-color: #dc2626; }
.StatCard#warn   { border-left-color: #eab308; }
.StatCard#success{ border-left-color: #16a34a; }
.StatCard .num { font-size: 28px; font-weight: 700; color: #0f172a; }
.StatCard .label { font-size: 13px; color: #64748b; }

/* 按钮 */
QPushButton {
    background: #f1f5f9; color: #1e293b; border: none; border-radius: 8px;
    padding: 6px 12px; font-weight: 500; min-height: 22px;
}
QPushButton:hover { background: #e2e8f0; }
QPushButton:disabled { color: #94a3b8; background: #f1f5f9; }
QPushButton.btn-primary { background: #2563eb; color: white; }
QPushButton.btn-primary:hover { background: #1d4ed8; }
QPushButton.btn-primary:disabled { background: #93c5fd; color: white; }
QPushButton.btn-success { background: #16a34a; color: white; }
QPushButton.btn-success:hover { background: #15803d; }
QPushButton.btn-success:disabled { background: #86efac; color: white; }
QPushButton.btn-warning { background: #eab308; color: #0f172a; }
QPushButton.btn-warning:hover { background: #ca8a04; }
QPushButton.btn-warning:disabled { background: #fde68a; color: #64748b; }
QPushButton.btn-danger  { background: #dc2626; color: white; }
QPushButton.btn-danger:hover { background: #b91c1c; }
QPushButton.btn-danger:disabled { background: #fca5a5; color: white; }
QPushButton.btn-outline {
    background: transparent; border: 1px solid #cbd5e1; color: #334155;
}
QPushButton.btn-outline:hover { background: #f1f5f9; border-color: #94a3b8; }
QPushButton.btn-outline:disabled { color: #cbd5e1; border-color: #e2e8f0; }
QPushButton.btn-sm { padding: 4px 10px; font-size: 12px; min-height: 18px; }

/* 表单控件 */
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit, QTextEdit {
    padding: 5px 8px; border: 1px solid #d1d9e6; border-radius: 8px;
    background: #f8fafc; selection-background-color: #2563eb;
}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus,
QPlainTextEdit:focus, QTextEdit:focus {
    outline: none; border: 2px solid #2563eb; padding: 4px 7px;
}
QGroupBox {
    font-weight: 600; border: 1px solid #e2e8f0; border-radius: 12px;
    margin-top: 12px; padding-top: 18px; background: white;
}
QGroupBox::title {
    subcontrol-origin: margin; left: 16px; padding: 0 6px;
    color: #0f172a; background: #f6f8fc;
}

/* 表格 */
QHeaderView::section {
    background: #f1f5f9; color: #1e293b; font-weight: 600;
    padding: 8px 10px; border: none; border-bottom: 1px solid #e2e8f0;
}
QTableWidget {
    background: white; gridline-color: #f1f5f9;
    border: 1px solid #e2e8f0; border-radius: 12px;
    alternate-background-color: #fafbfc;
    selection-background-color: #dbeafe; selection-color: #0f172a;
}
QTableWidget::item { padding: 6px 10px; border-bottom: 1px solid #f1f5f9; }
QTableWidget QTableCornerButton::section {
    background: #f1f5f9; border: none; border-bottom: 1px solid #e2e8f0;
}

/* 进度条 */
QProgressBar {
    background: #e9edf2; border: none; border-radius: 10px; text-align: center;
    height: 16px; color: #0f172a;
}
QProgressBar::chunk { background: #2563eb; border-radius: 10px; }

/* 日志区 */
#LogView {
    background: #0f172a; color: #e2e8f0;
    font-family: "Consolas", "Cascadia Code", "Microsoft YaHei Mono", monospace;
    border-radius: 10px; border: 1px solid #1e293b; padding: 8px;
    font-size: 12px;
}
#LogView[level="good"]  { color: #4ade80; }
#LogView[level="warn"]  { color: #fbbf24; }
#LogView[level="info"]  { color: #e2e8f0; }
#LogView[level="debug"] { color: #94a3b8; }

/* 底部状态栏 */
#AppStatus {
    background: #0f172a; border-top: 1px solid #1e293b; padding: 4px 20px;
    color: #94a3b8; font-size: 13px;
}
/* 状态栏 QLabel 用 #0f172a（与 #AppStatus 一致），不用 transparent：
   Qt 中 transparent 会与全局 QWidget { background: #f6f8fc; } 冲突，
   实际渲染成浅灰白框 */
#AppStatus QLabel { color: #94a3b8; background: #0f172a; }

/* 无边框窗口标题栏按钮 */
QPushButton.winctl {
    background: transparent; color: #cbd5e1; border: none;
    border-radius: 6px; padding: 0; font-size: 13px; min-height: 28px;
}
QPushButton.winctl:hover { background: #334155; color: white; }
QPushButton.winctl.close:hover { background: #dc2626; color: white; }

/* 标签 */
QLabel.tag { padding: 2px 10px; border-radius: 12px; font-size: 11px; font-weight: 600; }
QLabel.tag-error   { background: #fee2e2; color: #991b1b; }
QLabel.tag-warning { background: #fef3c7; color: #92400e; }
QLabel.tag-segment { background: #e2e8f0; color: #334155; }
"""


# ---------------------------------------------------------------------------
# LLM 方案管理弹窗
# ---------------------------------------------------------------------------

def _mask_key(key) -> str:
    """把 API Key 显示成 ``***abcd``，不完整的 key 全部打码。"""
    key = str(key or "").strip()
    if not key:
        return "（未填）"
    return "****" + key[-4:] if len(key) > 4 else "*" * len(key)


class _LLMTestWorker(QThread):
    """后台跑 LLM 连接测试，避免网络请求卡死界面线程。

    故意不挂到弹窗上：网络请求可能挂很久，用户在这期间关掉「LLM 设置」窗口，
    带父对象的运行中 QThread 会被 Qt 直接 abort（"Destroyed while thread is
    still running"）。改用引用池 + deleteLater，线程跑完自己清理。
    """

    done = Signal(bool, str)

    # 运行中的测试线程引用池，防止被 Python 回收
    _live = []

    def __init__(self, profile: dict):
        super().__init__(None)
        self._profile = profile
        self.profile_label = str((profile or {}).get("name") or "").strip() or "（未命名）"
        self.finished.connect(self._dispose)
        _LLMTestWorker._live.append(self)

    def run(self):
        from engine.llm_client import LLMClient
        try:
            client = LLMClient(profile=self._profile)
            ok, message = client.test_connection()
        except Exception as exc:
            ok, message = False, f"测试异常: {exc}"
        self.done.emit(ok, message)

    def _dispose(self):
        try:
            _LLMTestWorker._live.remove(self)
        except ValueError:
            pass
        self.deleteLater()


class LLMProfileDialog(QDialog):
    """LLM 方案（profile）管理弹窗：新建 / 编辑 / 删除 / 设为当前 / 测试连接。

    一个方案 = 一套 OpenAI 兼容端点（name / base_url / api_key / model）。
    用户在设置页点「LLM 设置」打开本窗口，可以并存多个方案并切换当前生效的那个，
    而不是每次覆盖同一个配置文件。

    工作副本语义：编辑框里的内容**不会立刻写盘**，必须点「保存当前」才落到
    ``config/llm_profiles.yaml``。这样用户能先填好、用「测试当前」验证连通性，
    再决定要不要保存——测试用的是编辑框里的值，不是已保存的值。

    方案改动后发出 ``changed``，设置页据此刷新「当前方案」摘要。
    """

    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("LLM 设置 — 方案管理")
        self.resize(780, 480)
        self._build()
        self._reload_list()
        self._on_select()

    # ---------- 构建界面 ----------
    def _build(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(16)

        # 左：方案列表
        left = QVBoxLayout()
        left.setSpacing(6)
        title = QLabel("已保存的方案")
        title.setStyleSheet("font-weight:600; color:#0f172a;")
        self.profile_list = QListWidget()
        self.profile_list.setSelectionMode(QListWidget.SingleSelection)
        self.profile_list.setMinimumWidth(250)
        self.profile_list.itemSelectionChanged.connect(self._on_select)
        left.addWidget(title)
        left.addWidget(self.profile_list, 1)
        root.addLayout(left, 1)

        # 右：编辑区
        right = QVBoxLayout()
        right.setSpacing(10)
        box = QGroupBox("方案详情")
        form = QFormLayout(box)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(10)
        self.ed_name = QLineEdit()
        self.ed_name.setPlaceholderText("例如：DeepSeek / 公司网关")
        self.ed_base = QLineEdit()
        self.ed_base.setPlaceholderText("https://api.deepseek.com/v1")
        self.ed_key = QLineEdit()
        self.ed_key.setEchoMode(QLineEdit.Password)
        self.ed_key.setPlaceholderText("sk-...")
        self.ed_model = QLineEdit()
        self.ed_model.setPlaceholderText("deepseek-chat")
        form.addRow("名称", self.ed_name)
        form.addRow("Base URL", self.ed_base)
        form.addRow("API Key", self.ed_key)
        form.addRow("Model", self.ed_model)
        right.addWidget(box)

        edit_row = QHBoxLayout()
        self.btn_new = QPushButton("＋ 新建方案")
        self.btn_save = QPushButton("💾 保存当前")
        self.btn_delete = QPushButton("🗑 删除所选")
        self.btn_new.clicked.connect(self._new_profile)
        self.btn_save.clicked.connect(self._save_current)
        self.btn_delete.clicked.connect(self._delete_selected)
        edit_row.addWidget(self.btn_new)
        edit_row.addWidget(self.btn_save)
        edit_row.addWidget(self.btn_delete)
        right.addLayout(edit_row)

        tip = QLabel(
            "提示：「测试当前」验证的是编辑框里正在编辑的值；「设为当前」"
            "作用于列表中选中的已保存方案。"
        )
        tip.setWordWrap(True)
        tip.setStyleSheet("color:#64748b; font-size:12px;")
        right.addWidget(tip)

        bottom = QHBoxLayout()
        self.btn_current = QPushButton("✓ 设为当前方案")
        self.btn_test = QPushButton("🔌 测试当前")
        self.btn_current.clicked.connect(self._set_current)
        self.btn_test.clicked.connect(self._test_current)
        bottom.addStretch(1)
        bottom.addWidget(self.btn_current)
        bottom.addWidget(self.btn_test)
        self.btn_cancel = QPushButton("取消")
        self.btn_ok = QPushButton("完成")
        self.btn_cancel.clicked.connect(self.reject)
        self.btn_ok.clicked.connect(self.accept)
        bottom.addWidget(self.btn_cancel)
        bottom.addWidget(self.btn_ok)
        right.addLayout(bottom)
        right.addStretch(1)

        root.addLayout(right, 1)

    # ---------- 数据 ----------
    def _fields(self) -> dict:
        """Read the four edit fields into a profile dict."""
        return {
            "name": self.ed_name.text().strip(),
            "base_url": self.ed_base.text().strip(),
            "api_key": self.ed_key.text().strip(),
            "model": self.ed_model.text().strip(),
        }

    def _fill(self, profile: dict):
        self.ed_name.setText(profile.get("name", ""))
        self.ed_base.setText(profile.get("base_url", ""))
        self.ed_key.setText(profile.get("api_key", ""))
        self.ed_model.setText(profile.get("model", ""))

    def _selected_name(self) -> str:
        """当前**选中**的方案名；没选中就返回空串。

        必须看 selectedItems() 而不是 currentItem()：setCurrentRow 只移动焦点
        不改变选中，用 currentItem 会让「新建方案」之后仍然指向上一个方案，
        结果一次保存就把旧方案覆盖掉。
        """
        items = self.profile_list.selectedItems()
        return items[0].data(Qt.UserRole) if items else ""

    def _reload_list(self, select_name: str = ""):
        """Rebuild the list from the store; restore selection by name."""
        from engine import llm_profiles
        names = llm_profiles.profile_names()
        active = llm_profiles.active_name()
        self.profile_list.blockSignals(True)
        self.profile_list.clear()
        for name in names:
            profile = llm_profiles.get_profile(name) or {}
            text = f"{'● ' if name == active else '   '}{name}"
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, name)
            item.setToolTip(f"{profile.get('base_url', '')}\nmodel: {profile.get('model', '')}")
            self.profile_list.addItem(item)
        if select_name:
            for i in range(self.profile_list.count()):
                if self.profile_list.item(i).data(Qt.UserRole) == select_name:
                    self.profile_list.item(i).setSelected(True)
                    break
        self.profile_list.blockSignals(False)
        self._on_select()

    def _on_select(self):
        """Load the selected stored profile into the edit fields."""
        name = self._selected_name()
        if not name:
            return
        from engine import llm_profiles
        profile = llm_profiles.get_profile(name)
        if profile:
            self._fill(profile)

    # ---------- 动作 ----------
    def _new_profile(self):
        from engine import llm_profiles
        base = "新方案"
        names = set(llm_profiles.profile_names())
        seq = 1
        while f"{base}{seq}" in names:
            seq += 1
        self._fill({"name": f"{base}{seq}", "base_url": "", "api_key": "", "model": ""})
        # 新建方案 = 不基于任何已保存方案，必须连焦点项一起清掉
        self.profile_list.clearSelection()
        self.profile_list.setCurrentRow(-1)
        self.ed_name.setFocus()
        self.ed_name.selectAll()

    def _save_current(self):
        """Upsert the edit fields into the store. Returns the saved name.

        传 ``old_name`` 是为了改名场景：选中「公司网关」把名字改成「网关A」后保存，
        旧条目必须一并删掉，否则库里会同时留着两个指向同一端点的方案。
        """
        from engine import llm_profiles
        profile = self._fields()
        if not profile["name"]:
            QMessageBox.warning(self, "缺少名称", "请先给这个方案起个名字。")
            return ""
        saved = llm_profiles.upsert_profile(profile, old_name=self._selected_name())
        self._reload_list(saved["name"])
        self.changed.emit()
        return saved["name"]

    def _delete_selected(self):
        from engine import llm_profiles
        name = self._selected_name()
        if not name:
            QMessageBox.information(self, "未选择", "请先在左侧列表选中要删除的方案。")
            return
        answer = QMessageBox.question(
            self, "删除方案",
            f"确定删除方案「{name}」？\n其中的 API Key 将一并删除，此操作不可撤销。",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        llm_profiles.delete_profile(name)
        self._reload_list("")
        self._fill({"name": "", "base_url": "", "api_key": "", "model": ""})
        self.changed.emit()

    def _set_current(self):
        from engine import llm_profiles
        name = self._selected_name()
        if not name:
            QMessageBox.information(self, "未选择", "请先在左侧列表选中一个已保存的方案。")
            return
        if llm_profiles.set_active(name):
            self._reload_list(name)
            self.changed.emit()
            QMessageBox.information(self, "已切换", f"当前方案已切换为「{name}」。")
        else:
            QMessageBox.warning(self, "切换失败", f"未找到方案「{name}」。")

    def _test_current(self):
        profile = self._fields()
        if not profile["api_key"]:
            QMessageBox.warning(self, "缺少 API Key", "请先在上方填入 API Key 再测试连接。")
            return
        self.btn_test.setEnabled(False)
        self.btn_test.setText("测试中…")
        worker = _LLMTestWorker(profile)
        worker.done.connect(self._on_test_done)
        worker.start()

    def _on_test_done(self, ok: bool, message: str):
        """测试完成回调。

        请求还没回来时用户可能已经关掉了本窗口；C++ 侧销毁后访问控件会抛
        RuntimeError，这里必须吞掉而不是让整个信号投递链报红。
        """
        try:
            self.btn_test.setEnabled(True)
            self.btn_test.setText("🔌 测试当前")
            box = QMessageBox.information if ok else QMessageBox.warning
            box(self, "连接成功" if ok else "连接失败", message)
        except RuntimeError:
            pass


# ---------------------------------------------------------------------------
# 主窗口
# ---------------------------------------------------------------------------
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        # 去掉原生 Windows 标题栏，自己适配一套无边框 UI（圆角 + 阴影靠系统）
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Window)
        self.setAttribute(Qt.WA_TranslucentBackground, False)
        self.setWindowTitle("DocFormat Engine · 文档校验与排版系统")
        self.resize(1280, 840)
        self.setMinimumSize(1080, 720)
        self.setStyleSheet(GLOBAL_QSS)
        # 用于实现窗口拖拽移动的按下起点
        self._drag_press_pos: QPoint | None = None
        self._drag_press_topleft: QPoint | None = None

        # 业务后端
        self.backend = GuiBackend(self._append_log)
        # 状态：用户选中的校验/排版目标文件列表（str 路径）
        self.target_files: list[str] = []
        # 上次校验结果
        self.last_result: dict | None = None
        # 工作线程
        self._thread: QThread | None = None
        self._worker: Worker | None = None
        self._llm_test_worker: _LLMTestWorker | None = None
        # 规则模板：默认湖北汽院小论文（与 cli 默认保持可不同）
        self.rules_path = resolve("config", "schools",
                                  "rules_湖北汽院_优秀小论文.yaml")
        if not os.path.exists(self.rules_path):
            self.rules_path = resolve("config", "rules_lunwen.yaml")

        self._build_layout()
        self._connect_signals()
        self._refresh_reports_table()
        self._refresh_status_bar()

        # 底部时钟
        self._tick_timer = QTimer(self)
        self._tick_timer.timeout.connect(self._refresh_status_bar)
        self._tick_timer.start(1000)

    # ===================== 布局构建 =====================
    def _build_layout(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_header())
        root.addWidget(self._build_body(), 1)
        root.addWidget(self._build_status_bar())

    def _build_header(self) -> QWidget:
        w = QFrame(); w.setObjectName("AppHeader")
        # 右边距留 0，让最小化/最大化/关闭按钮可以贴到窗口最右边缘，
        # 否则 contentsMargins 右 24px 会把窗口控制按钮的右半部分裁掉
        lay = QHBoxLayout(w); lay.setContentsMargins(20, 10, 0, 10)
        lay.setSpacing(0)

        left = QHBoxLayout()
        left.setSpacing(8)
        title = QLabel("WordFormat Engine"); title.setObjectName("HeaderLogo")
        ver = QLabel(f"v{ENGINE_VERSION}"); ver.setObjectName("HeaderVersion")
        left.addWidget(title); left.addSpacing(6); left.addWidget(ver); left.addStretch()

        right = QHBoxLayout()
        right.setSpacing(10)
        self.header_dot = QLabel(""); self.header_dot.setObjectName("HeaderDot")
        self.header_dot.setStyleSheet(
            "background:#22c55e; border-radius:6px;"
            "min-width:8px; max-width:8px; min-height:8px; max-height:8px;"
            "border:1px solid rgba(255,255,255,0.25);"
        )
        self.header_status = QLabel("就绪"); self.header_status.setStyleSheet(
            "color:#e2e8f0; font-weight:600; font-size:13px;"
            "background:rgba(148,163,184,0.18); padding:3px 10px;"
            "border-radius:10px;"
        )
        right.addWidget(self.header_dot); right.addWidget(self.header_status)

        # 无边框窗口自带的三个窗口控制按钮（替换原 👤 头像占位）；
        # 直接贴到窗口最右边缘，靠 header 的右 contentsMargin=0 实现，
        # 否则按钮右半部分会被裁掉
        self.win_min_btn = QPushButton("—"); self.win_min_btn.setProperty("class", "winctl")
        self.win_max_btn = QPushButton("▢"); self.win_max_btn.setProperty("class", "winctl")
        self.win_close_btn = QPushButton("✕"); self.win_close_btn.setProperty("class", "winctl close")
        for b in (self.win_min_btn, self.win_max_btn, self.win_close_btn):
            b.setCursor(Qt.PointingHandCursor)
            b.setFixedSize(40, 32)
        self.win_min_btn.clicked.connect(self.showMinimized)
        self.win_max_btn.clicked.connect(self._toggle_maximize)
        self.win_close_btn.clicked.connect(self.close)

        right.addSpacing(14)
        right.addWidget(self.win_min_btn)
        right.addWidget(self.win_max_btn)
        right.addWidget(self.win_close_btn)

        lay.addLayout(left, 1); lay.addLayout(right)
        return w

    def _toggle_maximize(self):
        if self.isMaximized():
            self.showNormal()
        else:
            # 无边框 + 全屏最大化：避免贴边被裁掉，先 normal 再 max
            self.showMaximized()

    # ---- 无边框窗口拖拽 ----
    # 左键按下顶栏空白区域时记录起点，鼠标移动时同步移动窗口；
    # 双击顶栏切换最大化/还原。其他控件（按钮、输入框）的 mousePress
    # 不会冒泡到 MainWindow，所以不会误触发拖拽。
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_press_pos = event.globalPosition().toPoint()
            self._drag_press_topleft = self.frameGeometry().topLeft()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if (self._drag_press_pos is not None
                and event.buttons() & Qt.LeftButton):
            delta = event.globalPosition().toPoint() - self._drag_press_pos
            self.move(self._drag_press_topleft + delta)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_press_pos = None
        self._drag_press_topleft = None
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._toggle_maximize()
        super().mouseDoubleClickEvent(event)

    def _build_body(self) -> QWidget:
        w = QWidget()
        lay = QHBoxLayout(w); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(0)

        lay.addWidget(self._build_sidebar())
        lay.addWidget(self._build_stack(), 1)
        return w

    def _build_sidebar(self) -> QWidget:
        w = QFrame(); w.setObjectName("Sidebar")
        lay = QVBoxLayout(w); lay.setContentsMargins(0, 16, 0, 0); lay.setSpacing(2)

        tabs = [
            ("validate",  "✔️  校验"),
            ("format",    "✨  排版修复"),
            ("annotate",  "✏️  批注"),
            ("reports",   "📑  报告"),
            ("rules",     "🤖  规则生成"),
            ("settings",  "⚙️  设置"),
        ]
        self.nav_buttons: dict[str, QPushButton] = {}
        for key, label in tabs:
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            if key == "validate":
                btn.setChecked(True)
            btn.clicked.connect(lambda _=False, k=key, b=btn: self._switch_tab(k, b))
            self.nav_buttons[key] = btn
            lay.addWidget(btn)

        lay.addStretch(1)
        footer = QLabel(f"📀 引擎 {ENGINE_VERSION}")
        footer.setObjectName("SidebarFooter")
        footer.setAlignment(Qt.AlignCenter)
        lay.addWidget(footer)
        return w

    def _build_stack(self) -> QWidget:
        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_view_validate())
        self.stack.addWidget(self._build_view_format())
        self.stack.addWidget(self._build_view_annotate())
        self.stack.addWidget(self._build_view_reports())
        self.stack.addWidget(self._build_view_rules())
        self.stack.addWidget(self._build_view_settings())
        self._load_settings()
        # 当前生效的 LLM 方案摘要（设置页那一栏）
        self._refresh_llm_summary()

        outer = QScrollArea(); outer.setWidgetResizable(True); outer.setFrameShape(QFrame.NoFrame)
        outer.setObjectName("MainContent")
        outer.setWidget(self.stack)
        # 不使用 ScrollArea 包 QStackedWidget 太麻烦——直接用包装 Widget
        host = QWidget()
        hl = QVBoxLayout(host); hl.setContentsMargins(24, 20, 24, 20); hl.addWidget(self.stack, 1)
        return host

    # ---------- 页 1：校验 ----------
    def _build_view_validate(self) -> QWidget:
        page = QWidget(); page.setObjectName("page-validate")
        lay = QVBoxLayout(page); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(18)

        # 页头
        head = self._make_view_header("✅  文档合规校验")
        self.v_rules_btn = self._make_btn("选择规则", "btn-outline")
        self.v_run_btn = self._make_btn("执行校验", "btn-primary")
        self.v_export_btn = self._make_btn("导出报告", "btn-success"); self.v_export_btn.setEnabled(False)
        self.v_fix_btn = self._make_btn("一键修复", "btn-warning"); self.v_fix_btn.setEnabled(False)
        self.v_annotate_btn = self._make_btn("批注", "btn-outline"); self.v_annotate_btn.setEnabled(False)
        self.v_batch_btn = self._make_btn("批量校验", "btn-outline")
        head["actions"].addWidget(self.v_rules_btn)
        head["actions"].addWidget(self.v_run_btn)
        head["actions"].addWidget(self.v_export_btn)
        head["actions"].addSpacing(16)
        head["actions"].addWidget(self.v_annotate_btn)
        head["actions"].addWidget(self.v_fix_btn)
        head["actions"].addWidget(self.v_batch_btn)
        lay.addWidget(head["widget"])

        # 上传区
        dz = DropZoneFrame(); dz.setObjectName("DropZone")
        dz.clicked.connect(self._pick_files)
        dz.files_dropped.connect(self._add_files)
        dz_lay = QVBoxLayout(dz); dz_lay.setAlignment(Qt.AlignCenter)
        ico = QLabel("☁️")
        ico.setAlignment(Qt.AlignCenter)
        ico.setStyleSheet("font-size: 52px; color: #94a3b8;")
        txt = QLabel("拖拽文档至此，或点击浏览添加文件")
        txt.setAlignment(Qt.AlignCenter)
        txt.setStyleSheet("font-size:16px; font-weight:500; color:#334155;")
        hint = QLabel("支持 .docx / .doc / .wps / .pdf  (最大 50MB)")
        hint.setAlignment(Qt.AlignCenter)
        hint.setStyleSheet("font-size:13px; color:#94a3b8;")
        pick_btn = self._make_btn("📂 选择文件", "btn-outline")
        pick_btn.setMaximumWidth(160)
        self.v_file_tags = QHBoxLayout(); self.v_file_tags.setSpacing(6)
        self.v_file_tags.addStretch(1)
        btn_row = QHBoxLayout(); btn_row.setAlignment(Qt.AlignCenter); btn_row.addWidget(pick_btn)
        dz_lay.addWidget(ico); dz_lay.addSpacing(6); dz_lay.addWidget(txt); dz_lay.addWidget(hint)
        dz_lay.addSpacing(10); dz_lay.addLayout(btn_row); dz_lay.addSpacing(8); dz_lay.addLayout(self.v_file_tags)
        pick_btn.clicked.connect(self._pick_files)
        lay.addWidget(dz)

        # 进度条
        self.v_progress_wrap = QFrame(); self.v_progress_wrap.setStyleSheet(".QFrame{background:white;"
            "border:1px solid #e9edf2; border-radius:10px; padding:14px;}")
        pl = QVBoxLayout(self.v_progress_wrap); pl.setContentsMargins(4,2,4,2)
        self.v_progress = QProgressBar(); self.v_progress.setValue(0)
        self.v_progress_msg = QLabel("准备中...")
        self.v_progress_msg.setStyleSheet("color:#64748b; font-size:13px;")
        pl.addWidget(self.v_progress); pl.addWidget(self.v_progress_msg)
        self.v_progress_wrap.hide()
        lay.addWidget(self.v_progress_wrap)

        # 4 张统计卡
        self.v_stats_wrap = QWidget()
        gl = QGridLayout(self.v_stats_wrap); gl.setContentsMargins(0,0,0,0); gl.setSpacing(14)
        labels = [
            ("总段落", "info",    "v_stat_total"),
            ("错误",   "danger",  "v_stat_errors"),
            ("警告",   "warn",    "v_stat_warnings"),
            ("可修复", "success", "v_stat_fixable"),
        ]
        self.v_stat_cards = {}
        for i, (name, cls, attr) in enumerate(labels):
            card, num_lbl, _ = self._make_stat_card(name, cls)
            setattr(self, attr, num_lbl)
            gl.addWidget(card, 0, i)
            self.v_stat_cards[attr] = num_lbl
        self.v_stats_wrap.hide()
        lay.addWidget(self.v_stats_wrap)

        # 错误表格
        self.v_error_wrap = QWidget()
        twl = QVBoxLayout(self.v_error_wrap); twl.setContentsMargins(0,0,0,0); twl.setSpacing(10)
        top = QHBoxLayout()
        self.v_error_title = QLabel("问题明细 (0)")
        self.v_error_title.setStyleSheet("font-weight:600; color:#0f172a;")
        self.v_filter = QLineEdit(); self.v_filter.setPlaceholderText("按描述筛选...")
        self.v_filter.setFixedWidth(220)
        self.v_filter_s = QComboBox()
        self.v_filter_s.addItem("全部严重性", "")
        self.v_filter_s.addItem("仅错误", "error")
        self.v_filter_s.addItem("仅警告", "warning")
        top.addWidget(self.v_error_title); top.addStretch(1)
        top.addWidget(self.v_filter); top.addWidget(self.v_filter_s)
        self.v_error_table = QTableWidget(0, 8)
        self.v_error_table.setHorizontalHeaderLabels(
            ["#", "严重性", "类型", "描述", "位置", "分类", "修复动作", "操作"])
        self.v_error_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeToContents)
        self.v_error_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.v_error_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.v_error_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.v_error_table.setAlternatingRowColors(True)
        self.v_error_table.verticalHeader().setVisible(False)
        self.v_error_table.setWordWrap(True)
        self.v_error_table.verticalHeader().setDefaultSectionSize(36)
        self.v_error_table.itemSelectionChanged.connect(self._on_error_row_selected)
        twl.addLayout(top); twl.addWidget(self.v_error_table, 1)
        self.v_error_wrap.hide()
        # v_error_wrap 不再独占剩余空间（stretch=0）：让它按内容自然高度
        # 显示，配合末尾 addStretch(1) 实现从上向下紧凑排列。
        lay.addWidget(self.v_error_wrap, 0)

        # 日志区已统一迁移到设置页：用户要求"✓ 校验完成..."等
        # 日志只出现在设置页，校验页和排版页不再显示日志框。
        # 末尾 addStretch(1)：把剩余空间推到底部，让所有可见元素
        # （页头/上传区/进度条/统计卡/错误表）从顶部紧凑向下排列。
        lay.addStretch(1)
        return page

    # ---------- 页 2：排版修复 ----------
    def _build_view_format(self) -> QWidget:
        page = QWidget(); page.setObjectName("page-format")
        lay = QVBoxLayout(page); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(18)

        head = self._make_view_header("✨  智能排版修复")
        self.f_run_btn = self._make_btn("▶ 开始排版", "btn-primary")
        self.f_pick_btn = self._make_btn("📂 选择文件", "btn-outline")
        self.f_dir_btn = self._make_btn("📁 批量排版目录", "btn-outline")
        head["actions"].addWidget(self.f_pick_btn)
        head["actions"].addWidget(self.f_dir_btn)
        head["actions"].addWidget(self.f_run_btn)
        lay.addWidget(head["widget"])

        cfg = QGroupBox("排版参数配置")
        cfg_l = QGridLayout(cfg); cfg_l.setHorizontalSpacing(24); cfg_l.setVerticalSpacing(14)
        def field(row, col, label, widget):
            l = QLabel(label)
            l.setStyleSheet("font-size:13px; font-weight:500; color:#334155;")
            cfg_l.addWidget(l, row, col * 2)
            cfg_l.addWidget(widget, row, col * 2 + 1)

        self.f_preset = QComboBox(); self.f_preset.addItems(["official (党政公文)",
            "academic (学术论文)", "legal (法律文书)"]); self.f_preset.setCurrentIndex(1)
        self.f_output = QLineEdit(_resolve_output_path_str())
        self.f_output.setPlaceholderText("输出目录")
        self.f_font_cn = QLineEdit("宋体")
        self.f_font_en = QLineEdit("Times New Roman")
        self.f_size = QDoubleSpinBox(); self.f_size.setRange(6, 36); self.f_size.setSingleStep(0.5); self.f_size.setValue(12)
        self.f_linespacing = QDoubleSpinBox(); self.f_linespacing.setRange(0.5, 5); self.f_linespacing.setSingleStep(0.1); self.f_linespacing.setValue(1.5)
        self.f_indent = QDoubleSpinBox(); self.f_indent.setRange(0, 8); self.f_indent.setSingleStep(0.5); self.f_indent.setValue(2)
        self.f_align = QComboBox(); self.f_align.addItems(["左对齐", "居中", "右对齐", "两端对齐"])
        self.f_align.setCurrentIndex(3)
        self.f_space_before = QDoubleSpinBox(); self.f_space_before.setRange(0, 72); self.f_space_before.setSingleStep(2); self.f_space_before.setValue(0)
        self.f_space_after = QDoubleSpinBox(); self.f_space_after.setRange(0, 72); self.f_space_after.setSingleStep(2); self.f_space_after.setValue(0)
        self.f_mt = QDoubleSpinBox(); self.f_mt.setRange(0, 10); self.f_mt.setDecimals(2); self.f_mt.setSingleStep(0.1); self.f_mt.setValue(2.54)
        self.f_mb = QDoubleSpinBox(); self.f_mb.setRange(0, 10); self.f_mb.setDecimals(2); self.f_mb.setSingleStep(0.1); self.f_mb.setValue(2.54)
        self.f_ml = QDoubleSpinBox(); self.f_ml.setRange(0, 10); self.f_ml.setDecimals(2); self.f_ml.setSingleStep(0.1); self.f_ml.setValue(3.17)
        self.f_mr = QDoubleSpinBox(); self.f_mr.setRange(0, 10); self.f_mr.setDecimals(2); self.f_mr.setSingleStep(0.1); self.f_mr.setValue(3.17)

        self.f_preset.currentIndexChanged.connect(self._apply_format_preset_defaults)
        self._apply_format_preset_defaults(1)

        r = 0
        field(r, 0, "预设模板", self.f_preset); field(r, 1, "输出目录", self.f_output); r += 1
        field(r, 0, "中文字体", self.f_font_cn); field(r, 1, "英文字体", self.f_font_en); r += 1
        field(r, 0, "正文字号 (pt)", self.f_size); field(r, 1, "行距 (倍)", self.f_linespacing); r += 1
        field(r, 0, "首行缩进 (字符)", self.f_indent); field(r, 1, "对齐方式", self.f_align); r += 1
        field(r, 0, "段前间距 (pt)", self.f_space_before); field(r, 1, "段后间距 (pt)", self.f_space_after); r += 1
        field(r, 0, "页边距 上/下 (cm)", self._row_widget(self.f_mt, self.f_mb))
        field(r, 1, "页边距 左/右 (cm)", self._row_widget(self.f_ml, self.f_mr))
        lay.addWidget(cfg)

        self.f_result = QLabel(""); self.f_result.hide()
        self.f_result.setStyleSheet(
            "padding: 16px; background: #f0fdf4; color: #166534;"
            "border: 1px solid #bbf7d0; border-radius: 12px;")
        lay.addWidget(self.f_result)

        # 进度条（日志已迁移到设置页，此处仅保留进度条）
        self.f_progress = QProgressBar(); self.f_progress.setValue(0); self.f_progress.hide()
        lay.addWidget(self.f_progress)
        # 末尾 addStretch(1)：把剩余空间推到底部，让页头/参数配置/结果/
        # 进度条从顶部紧凑向下排列，不再被 QVBoxLayout 默认留在中间。
        lay.addStretch(1)
        return page

    # ---------- 页 3：批注 ----------
    def _build_view_annotate(self) -> QWidget:
        page = QWidget(); page.setObjectName("page-annotate")
        lay = QVBoxLayout(page); lay.setContentsMargins(0,0,0,0); lay.setSpacing(18)
        head = self._make_view_header("✏️  批注管理")
        self.a_refresh = self._make_btn("刷新", "btn-primary")
        self.a_run = self._make_btn("插入批注", "btn-warning")
        head["actions"].addWidget(self.a_run); head["actions"].addWidget(self.a_refresh)
        lay.addWidget(head["widget"])

        self.a_table = QTableWidget(0, 4)
        self.a_table.setHorizontalHeaderLabels(["文档", "生成日期", "批注数量", "操作"])
        self.a_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        # 操作列用 Fixed + setColumnWidth 强制固定列宽：ResizeToContents
        # 依赖 sizeHint 计算列宽，对 setCellWidget 的 cell 在某些 Qt 版本
        # 下计算不准导致"打开目录"按钮被裁成一半。Fixed 模式直接用
        # setColumnWidth 强制 120px，完全不依赖 sizeHint，按钮必能完整显示。
        self.a_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Fixed)
        self.a_table.setColumnWidth(3, 120)
        self.a_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.a_table.verticalHeader().setVisible(False)
        # 设置行高 44px：btn-sm 按钮 padding 4×2 + font 12 + min-height 18 ≈ 40px，
        # QTableWidget 默认行高约 30px 不够，会导致"打开目录"按钮垂直方向被裁成一半。
        self.a_table.verticalHeader().setDefaultSectionSize(44)
        lay.addWidget(self.a_table, 1)
        return page

    # ---------- 页 4：报告 ----------
    def _build_view_reports(self) -> QWidget:
        page = QWidget(); page.setObjectName("page-reports")
        lay = QVBoxLayout(page); lay.setContentsMargins(0,0,0,0); lay.setSpacing(18)
        head = self._make_view_header("📑  历史报告")
        self.r_refresh = self._make_btn("刷新", "btn-primary")
        self.r_batch = self._make_btn("批量报告", "btn-success")
        head["actions"].addWidget(self.r_batch); head["actions"].addWidget(self.r_refresh)
        lay.addWidget(head["widget"])

        self.r_table = QTableWidget(0, 4)
        self.r_table.setHorizontalHeaderLabels(["报告名称", "生成时间", "大小 (KB)", "操作"])
        self.r_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        # 操作列用 Fixed + setColumnWidth 强制固定 180px：ResizeToContents
        # 依赖 sizeHint 计算列宽，对 setCellWidget 的 cell 在某些 Qt 版本
        # 下计算不准导致按钮被裁。Fixed 模式完全不依赖 sizeHint。
        self.r_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Fixed)
        self.r_table.setColumnWidth(3, 180)
        self.r_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.r_table.verticalHeader().setVisible(False)
        # 设置行高 44px：btn-sm 按钮 padding 4×2 + font 12 + min-height 18 ≈ 40px，
        # QTableWidget 默认行高约 30px 不够，会导致按钮垂直方向被裁成一半。
        self.r_table.verticalHeader().setDefaultSectionSize(44)
        lay.addWidget(self.r_table, 1)
        return page

    # ---------- 页 5：规则生成 ----------
    def _build_view_rules(self) -> QWidget:
        page = QWidget(); page.setObjectName("page-rules")
        lay = QVBoxLayout(page); lay.setContentsMargins(0,0,0,0); lay.setSpacing(18)
        head = self._make_view_header("🤖  LLM 规则自动生成")
        self.rl_run = self._make_btn("🧪 生成规则", "btn-primary")
        head["actions"].addWidget(self.rl_run)
        lay.addWidget(head["widget"])

        gb = QGroupBox("规则生成输入")
        gl = QVBoxLayout(gb); gl.setSpacing(10)
        gl.addWidget(QLabel("📝 规范文本（粘贴学校或单位的格式规范描述文字）"))
        self.rl_spec = QPlainTextEdit(); self.rl_spec.setPlaceholderText(
            "例如：正文使用宋体小四，行距固定28磅；一级标题黑体三号居中；图表标题黑体五号……")
        self.rl_spec.setMinimumHeight(100)
        gl.addWidget(self.rl_spec)
        row = QHBoxLayout()
        self.rl_template_btn = self._make_btn("上传 Word 模板", "btn-outline")
        self.rl_template_label = QLabel("（未选择）")
        self.rl_template_label.setStyleSheet("color:#64748b; font-size:13px;")
        self.rl_domain = QLineEdit(); self.rl_domain.setPlaceholderText("领域名称，例如：毕业论文")
        self.rl_domain.setFixedWidth(220)
        domain_wrap = QHBoxLayout(); domain_wrap.addWidget(QLabel("🏷  领域："))
        domain_wrap.addWidget(self.rl_domain)
        row.addWidget(self.rl_template_btn); row.addWidget(self.rl_template_label); row.addStretch(1)
        row.addLayout(domain_wrap)
        gl.addLayout(row)
        lay.addWidget(gb)

        # 输出 YAML 区
        out = QFrame(); out.setProperty("class", "Card"); out.setStyleSheet(
            "QFrame{background:white; border:1px solid #e2e8f0; border-radius:12px; padding:14px;}")
        o_l = QVBoxLayout(out)
        r1 = QHBoxLayout()
        self.rl_out_title = QLabel("生成的 YAML 规则")
        self.rl_out_title.setStyleSheet("font-weight:600; color:#0f172a;")
        self.rl_save_btn = self._make_btn("保存规则", "btn-success"); self.rl_save_btn.setEnabled(False)
        self.rl_copy_btn = self._make_btn("复制", "btn-outline"); self.rl_copy_btn.setEnabled(False)
        r1.addWidget(self.rl_out_title); r1.addStretch(1)
        r1.addWidget(self.rl_copy_btn); r1.addWidget(self.rl_save_btn)
        self.rl_out = QPlainTextEdit(); self.rl_out.setReadOnly(True)
        self.rl_out.setPlaceholderText("生成的 YAML 将显示在这里……")
        self.rl_out.setStyleSheet("background:#f8fafc; border-radius:8px; font-family:Consolas;")
        self.rl_out.setMinimumHeight(240)
        o_l.addLayout(r1); o_l.addWidget(self.rl_out)
        lay.addWidget(out, 1)
        return page

    # ---------- 页 6：设置 ----------
    def _build_view_settings(self) -> QWidget:
        page = QWidget(); page.setObjectName("page-settings")
        lay = QVBoxLayout(page); lay.setContentsMargins(0,0,0,0); lay.setSpacing(18)
        head = self._make_view_header("⚙️  系统设置")
        self.s_save = self._make_btn("保存设置", "btn-primary")
        self.s_test = self._make_btn("测试 API", "btn-outline")
        head["actions"].addWidget(self.s_save); head["actions"].addWidget(self.s_test)
        lay.addWidget(head["widget"])

        gb = QGroupBox("偏好")
        gl = QGridLayout(gb); gl.setHorizontalSpacing(24); gl.setVerticalSpacing(14)

        # LLM 走方案管理弹窗，不再在这里直接暴露一个 Key 输入框——
        # 多个方案并存、可切换，比覆盖同一个字段更符合真实使用（多厂商/多团队）。
        self.s_llm_summary = QLabel()
        self.s_llm_summary.setStyleSheet("color:#334155; font-size:13px;")
        self.s_llm_btn = self._make_btn("⚙️ LLM 设置…", "btn-outline")
        llm_field = QWidget()
        llm_field_lay = QHBoxLayout(llm_field)
        llm_field_lay.setContentsMargins(0, 0, 0, 0)
        llm_field_lay.setSpacing(8)
        llm_field_lay.addWidget(self.s_llm_summary, 1)
        llm_field_lay.addWidget(self.s_llm_btn)
        self.s_default_rule = QComboBox()
        for p in sorted(self._list_rules_files()):
            self.s_default_rule.addItem(p)
        # 选中当前
        idx = self.s_default_rule.findText(os.path.relpath(self.rules_path, resolve("config")))
        if idx >= 0:
            self.s_default_rule.setCurrentIndex(idx)
        self.s_out_dir = QLineEdit(resolve("output"))
        self.s_rep_dir = QLineEdit(resolve("reports"))
        self.s_auto_anno = QCheckBox("完成校验后自动插入 Word 批注")
        self.s_run_test = self._make_btn("运行 /test 端到端演示", "btn-warning")

        def f(row, col, label, w):
            l = QLabel(label); l.setStyleSheet("font-weight:500; color:#334155;")
            gl.addWidget(l, row, col * 2); gl.addWidget(w, row, col * 2 + 1)

        r = 0
        f(r, 0, "LLM 设置", llm_field); f(r, 1, "默认规则文件", self.s_default_rule); r += 1
        f(r, 0, "输出目录", self.s_out_dir); f(r, 1, "报告目录", self.s_rep_dir); r += 1
        gl.addWidget(self.s_auto_anno, r, 0, 1, 2); r += 1
        gl.addWidget(self.s_run_test, r, 0, 1, 2)
        lay.addWidget(gb)

        # 统一日志区：校验/批注/排版等所有操作的日志统一在此显示，
        # 校验页和排版页不再有日志框（用户要求"只出现在设置里"）。
        log_label = QLabel("运行日志"); log_label.setStyleSheet(
            "font-weight:600; color:#0f172a; margin-top:4px;")
        lay.addWidget(log_label)
        self.s_log = QPlainTextEdit(); self.s_log.setObjectName("LogView")
        self.s_log.setReadOnly(True); self.s_log.setMinimumHeight(180)
        lay.addWidget(self.s_log, 1)
        return page

    def _load_settings(self) -> bool:
        """读取 gui_settings.json，恢复上次的偏好。

        历史上这里只有「保存」没有「读取」：用户在设置页填好点保存，重启之后
        又全部回到默认值，等于白填。读取侧一律宽容——文件缺失、字段缺失、
        保存的路径已经不存在，都静默回退到默认，不让设置页起不来。

        必须在 ``_build_stack()`` 之后调用：下拉框和输入框都在建页时创建。
        """
        p = resolve("gui_settings.json")
        if not os.path.exists(p):
            return False
        try:
            with open(p, "r", encoding="utf-8") as fh:
                data = json.load(fh) or {}
        except Exception as exc:
            self._append_log(f"设置文件读取失败，已用默认值：{exc}", "warn")
            return False
        if not isinstance(data, dict):
            return False

        rules_rel = data.get("rules_path")
        if isinstance(rules_rel, str) and rules_rel.strip():
            rules_abs = resolve(rules_rel)
            if os.path.isfile(rules_abs):
                self.rules_path = rules_abs
                self._append_log(f"已恢复规则文件：{rules_rel}", "good")
            else:
                self._append_log(
                    f"上次保存的规则文件已不存在，沿用默认：{rules_abs}", "warn")
        # 按落定的 rules_path 重新选中下拉框（建页时选的是默认值）
        if self.s_default_rule.count():
            idx = self.s_default_rule.findText(
                os.path.relpath(self.rules_path, resolve("config")))
            if idx >= 0:
                self.s_default_rule.setCurrentIndex(idx)

        for widget, key in ((self.s_out_dir, "output_dir"),
                            (self.s_rep_dir, "reports_dir")):
            val = data.get(key)
            if isinstance(val, str) and val.strip():
                widget.setText(val.strip())

        if isinstance(data.get("auto_annotate"), bool):
            self.s_auto_anno.setChecked(bool(data["auto_annotate"]))

        self._append_log(f"已恢复上次设置 ← {p}", "good")
        return True

    # ---------- 底部状态栏 ----------
    def _build_status_bar(self) -> QWidget:
        w = QFrame(); w.setObjectName("AppStatus")
        lay = QHBoxLayout(w); lay.setContentsMargins(24, 6, 24, 6)
        # 状态栏 QLabel 显式设背景为状态栏深色，避免被全局 QWidget 染成浅灰白框
        self.sb_left = QLabel()
        self.sb_left.setStyleSheet("color:#94a3b8; background:#0f172a;")
        self.sb_right = QLabel()
        self.sb_right.setStyleSheet("color:#94a3b8; background:#0f172a;")
        lay.addWidget(self.sb_left, 1); lay.addWidget(self.sb_right)
        return w

    # ===================== 通用子组件 =====================
    def _make_view_header(self, title: str):
        w = QWidget()
        lay = QHBoxLayout(w); lay.setContentsMargins(0, 0, 0, 0)
        t = QLabel(title); t.setObjectName("ViewTitle")
        t.setStyleSheet("font-size:22px; font-weight:600; color:#0f172a;")
        actions = QHBoxLayout(); actions.setSpacing(8)
        lay.addWidget(t, 1); lay.addLayout(actions)
        return {"widget": w, "actions": actions}

    def _make_btn(self, text: str, kind: str) -> QPushButton:
        b = QPushButton(text)
        # 用 setProperty("class", kind) 配合 QSS 类选择器（.btn-primary）
        # 而不是 setObjectName —— QSS ID 选择器对带连字符的 objectName
        # 在某些 Qt 版本下不生效，会导致所有彩色按钮退化成默认灰底。
        # class 属性支持复合值（"btn-sm btn-primary"），可被多个 .x 选择器命中
        # 注意：setProperty 后不要手动 unpolish/polish——实测会破坏样式应用，
        # 让按钮退化成默认 QPushButton 浅灰底。Qt 会自动在 setStyleSheet 已
        # 设过的父窗口里 polish 子控件，自然匹配类选择器即可。
        b.setProperty("class", kind)
        b.setCursor(Qt.PointingHandCursor)
        return b

    def _row_widget(self, a, b):
        w = QWidget(); l = QHBoxLayout(w); l.setContentsMargins(0,0,0,0); l.setSpacing(6)
        l.addWidget(a, 1); l.addWidget(b, 1)
        return w

    def _make_stat_card(self, label: str, kind: str):
        c = QFrame(); c.setProperty("class", "StatCard"); c.setObjectName(kind)
        c.setStyleSheet(
            f"QFrame {{ background:white; border-radius:14px; padding:18px 20px;"
            f"border-left:5px solid "
            f"{'#dc2626' if kind == 'danger' else '#eab308' if kind == 'warn' else '#16a34a' if kind == 'success' else '#2563eb'};"
            f"}}")
        l = QVBoxLayout(c); l.setContentsMargins(8, 6, 8, 6); l.setSpacing(4)
        num = QLabel("0"); num.setStyleSheet("font-size:28px; font-weight:700; color:#0f172a;")
        lbl = QLabel(label); lbl.setStyleSheet("font-size:13px; color:#64748b;")
        l.addWidget(num); l.addWidget(lbl)
        return c, num, lbl

    # ===================== 事件/信号 =====================
    def _connect_signals(self):
        # 校验页按钮
        self.v_rules_btn.clicked.connect(self._pick_rules_file)
        self.v_run_btn.clicked.connect(lambda: self._start_job(
            WorkerJob("validate", self._current_validate_job_kwargs())))
        self.v_batch_btn.clicked.connect(self._start_validate_dir)
        self.v_export_btn.clicked.connect(self._export_current_csv)
        self.v_fix_btn.clicked.connect(self._start_check_fix)
        self.v_annotate_btn.clicked.connect(lambda: self._start_job(WorkerJob("annotate", {})))
        self.v_filter.textChanged.connect(self._render_error_table)
        self.v_filter_s.currentIndexChanged.connect(self._render_error_table)

        # 排版页
        self.f_pick_btn.clicked.connect(self._pick_files)
        self.f_dir_btn.clicked.connect(self._start_format_dir)
        self.f_run_btn.clicked.connect(self._start_format)

        # 批注 / 报告 / 规则 / 设置
        self.a_refresh.clicked.connect(self._refresh_annotations_table)
        self.a_run.clicked.connect(lambda: self._start_job(WorkerJob("annotate", {})))
        self.r_refresh.clicked.connect(self._refresh_reports_table)
        self.r_batch.clicked.connect(self._start_validate_dir)
        self.rl_run.clicked.connect(self._start_get_rules)
        self.rl_template_btn.clicked.connect(self._pick_template_file)
        self.rl_save_btn.clicked.connect(self._save_generated_rule)
        self.rl_copy_btn.clicked.connect(self._copy_generated_rule)
        self.s_save.clicked.connect(self._save_settings)
        self.s_test.clicked.connect(self._test_api)
        self.s_run_test.clicked.connect(self._run_end_to_end_demo)
        self.s_llm_btn.clicked.connect(self._open_llm_settings)

    # ---------- 视图切换 ----------
    def _switch_tab(self, key: str, clicked_btn: QPushButton):
        for k, b in self.nav_buttons.items():
            b.setChecked(k == key)
        idx_map = {"validate": 0, "format": 1, "annotate": 2, "reports": 3, "rules": 4, "settings": 5}
        self.stack.setCurrentIndex(idx_map.get(key, 0))

    # ===================== 工作目录 / 输入文件 =====================
    def _pick_files(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "选择文档", resolve("input"),
            "支持的文档 (*.docx *.doc *.wps *.pdf);;所有文件 (*.*)")
        if not paths:
            return
        self._add_files(paths)

    def _add_files(self, paths: list[str]):
        """供 DropZone 的 dropEvent 和 _pick_files 共用的添加入口。"""
        for p in paths:
            if p and p not in self.target_files:
                self.target_files.append(p)
        self._render_file_tags()

    def _pick_rules_file(self):
        start = self.rules_path or resolve("config")
        path, _ = QFileDialog.getOpenFileName(self, "选择规则文件", start,
                                               "YAML 规则 (*.yaml *.yml);;所有文件 (*.*)")
        if path:
            self.rules_path = path
            self._append_log(f"切换规则 → {os.path.relpath(path, BASE_DIR)}", "good")

    def _pick_template_file(self):
        start = resolve("input")
        path, _ = QFileDialog.getOpenFileName(self, "选择 Word 模板", start,
                                               "Word 文档 (*.docx *.doc)")
        if path:
            self.rl_template_label.setText(os.path.basename(path))
            self._rl_template_path = path

    def _render_file_tags(self):
        # 清理
        while self.v_file_tags.count() > 1:
            item = self.v_file_tags.takeAt(0)
            w = item.widget()
            if w: w.deleteLater()
        for f in self.target_files:
            btn = QPushButton(os.path.basename(f))
            btn.setStyleSheet("background:#eef2f6; border:1px solid #d1d9e6;"
                              "border-radius:16px; padding:4px 14px;"
                              "font-size:13px; color:#334155;")
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda _=False, x=f: self._remove_file(x))
            self.v_file_tags.insertWidget(self.v_file_tags.count() - 1, btn)

    def _remove_file(self, path):
        if path in self.target_files:
            self.target_files.remove(path)
            self._render_file_tags()

    def _current_validate_job_kwargs(self) -> dict:
        if not self.target_files:
            raise RuntimeError("请先添加要校验的文档（拖入或点击 选择文件）。")
        return {"path": self.target_files[0], "rules": self.rules_path}

    # ===================== Worker 启动 =====================
    def _start_job(self, job: WorkerJob):
        if self._thread is not None and self._thread.isRunning():
            self._append_log("已有任务在运行，请等待其完成。", "warn")
            return
        self.header_status.setText("运行中…")
        self.header_dot.setStyleSheet(
            "background:#eab308; border-radius:4px; min-width:8px; min-height:8px;")
        self._thread = QThread(self)
        self._worker = Worker(self.backend, job)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._on_progress)
        self._worker.log.connect(self._append_log)
        self._worker.result.connect(self._on_job_done)
        self._worker.error.connect(self._on_job_error)
        self._worker.result.connect(self._thread.quit)
        self._worker.error.connect(self._thread.quit)
        self._thread.finished.connect(self._cleanup_worker)
        self._thread.start()

    def _cleanup_worker(self):
        if self._worker:
            self._worker.deleteLater()
            self._worker = None
        if self._thread:
            self._thread.deleteLater()
            self._thread = None
        # 任务结束后隐藏进度条，避免界面残留旧进度
        self.v_progress_wrap.hide()
        self.f_progress.hide()
        self.header_status.setText("就绪")
        self.header_dot.setStyleSheet(
            "background:#22c55e; border-radius:6px;"
            "min-width:8px; max-width:8px; min-height:8px; max-height:8px;"
            "border:1px solid rgba(255,255,255,0.25);")

    def _on_progress(self, pct: int, msg: str):
        # 路由到当前页的进度控件；非校验/排版页时把进度信息合并到日志，不调
        # statusBar().showMessage（那会重建系统状态栏并挤变形主界面）
        if self.stack.currentIndex() == 0:  # validate
            self.v_progress_wrap.show(); self.v_progress.setValue(max(0, min(100, pct)))
            self.v_progress_msg.setText(msg)
        elif self.stack.currentIndex() == 1:  # format
            self.f_progress.show(); self.f_progress.setValue(max(0, min(100, pct)))
            self.f_progress.setFormat(f"{msg} %p%")
        else:
            self._append_log(msg, "info")

    def _on_job_error(self, msg: str):
        self._append_log(f"❌ 出错：{msg}", "warn")
        QMessageBox.critical(self, "任务失败", msg)

    def _on_job_done(self, kind: str, payload: Any):
        if kind == "validate":
            self._after_validate(payload)
        elif kind == "validate-dir":
            self._after_validate_dir(payload)
        elif kind == "format":
            self._after_format(payload)
        elif kind == "format-dir":
            self._after_format_dir(payload)
        elif kind == "check-fix":
            self._after_check_fix(payload)
        elif kind == "annotate":
            self._after_annotate(payload)
        elif kind == "get-rules":
            self._after_get_rules(payload)

    # ===================== 校验：结果展示 =====================
    def _start_validate_dir(self):
        path = QFileDialog.getExistingDirectory(self, "选择待批量校验的目录", resolve("input"))
        if not path:
            return
        self._start_job(WorkerJob("validate-dir",
            {"dir_path": path, "rules": self.rules_path}))

    @staticmethod
    def _error_counts(errors: list[dict]) -> tuple[int, int, int]:
        warns = sum(1 for e in errors if e.get("type") == "missing")
        errs = len(errors) - warns
        fixable = sum(1 for e in errors if e.get("action"))
        return errs, warns, fixable

    def _after_validate(self, p: dict):
        self.last_result = p
        errors: list[dict] = p["errors"]
        paragraphs = 0
        try:
            ext = _cli.get_extractor(p["source"])  # 返回的是实例（不是类）
            data = ext.extract(p["source"])
            paragraphs = len(data.get("paragraphs", []))
        except Exception:
            paragraphs = 0
        errs, warns, fixable = self._error_counts(errors)
        self.v_stats_wrap.show()
        self.v_stat_total.setText(str(paragraphs))
        self.v_stat_errors.setText(str(errs))
        self.v_stat_warnings.setText(str(warns))
        self.v_stat_fixable.setText(str(fixable))
        self._render_error_table()
        self.v_error_wrap.show()
        self.v_export_btn.setEnabled(bool(p.get("csv_path")))
        self.v_fix_btn.setEnabled(fixable > 0)
        self.v_annotate_btn.setEnabled(bool(errors))
        self._refresh_reports_table()
        self._append_log(f"✓ 校验完成，报告：{p.get('csv_path')}", "good")

    def _after_validate_dir(self, p: dict):
        self._append_log(f"✓ 批量校验完成，汇总：{p['csv_path']}", "good")
        self._refresh_reports_table()

    def _render_error_table(self):
        if not self.last_result:
            self._filtered_errors = []
            return
        errors: list[dict] = list(self.last_result.get("errors", []))
        filt_txt = self.v_filter.text().strip().lower()
        sev = self.v_filter_s.currentData() or ""
        if filt_txt:
            errors = [e for e in errors if filt_txt in str(e.get("description", "")).lower()]
        if sev == "error":
            errors = [e for e in errors if e.get("type") != "missing"]
        elif sev == "warning":
            errors = [e for e in errors if e.get("type") == "missing"]
        # 缓存过滤后的错误列表，供"定位"按钮按行号准确取条目
        self._filtered_errors = errors
        self.v_error_title.setText(f"问题明细 ({len(errors)})")
        self.v_error_table.setRowCount(len(errors))
        for i, e in enumerate(errors):
            self.v_error_table.setItem(i, 0, QTableWidgetItem(str(i + 1)))
            is_warn = e.get("type") == "missing"
            s_txt = "警告" if is_warn else "错误"
            s_cls = "tag-warning" if is_warn else "tag-error"
            item = QTableWidgetItem(s_txt); item.setTextAlignment(Qt.AlignCenter)
            # 彩色 tag：用原生 QBrush/QLabel 混合方式
            item.setForeground(QBrush_color(s_cls == "tag-error", s_cls == "tag-warning"))
            self.v_error_table.setItem(i, 1, item)
            self.v_error_table.setItem(i, 2, QTableWidgetItem(e.get("type", "")))
            self.v_error_table.setItem(i, 3, QTableWidgetItem(e.get("description", "")))
            self.v_error_table.setItem(i, 4, QTableWidgetItem(e.get("location", "")))
            self.v_error_table.setItem(i, 5, QTableWidgetItem(e.get("segment", "")))
            self.v_error_table.setItem(i, 6, QTableWidgetItem(e.get("action", "") or "—"))
            op_btn = self._make_btn("定位", "btn-sm btn-primary")
            # setFixedWidth 让按钮 sizeHint 同步设为 64，配合操作列 Fixed 80px
            # 双保险确保"定位"按钮水平方向完整显示。
            op_btn.setFixedWidth(64)
            op_btn.clicked.connect(lambda _=False, row=i: self._locate_error(row))
            self.v_error_table.setCellWidget(i, 7, op_btn)

    def _on_error_row_selected(self):
        pass

    def _locate_error(self, row: int):
        # 从过滤后的缓存取条目，避免筛选后行号错位指向原始列表
        filtered = getattr(self, "_filtered_errors", None) or []
        if not filtered or row >= len(filtered):
            return
        e = filtered[row]
        QMessageBox.information(self, f"错误 #{row+1}",
            f"严重性：{e.get('severity')}\n"
            f"类型：{e.get('type')}\n"
            f"位置：{e.get('location')}\n"
            f"段落索引：{e.get('paragraph_index')}\n\n"
            f"描述：{e.get('description')}\n\n"
            f"修复动作：{e.get('action') or '（无可直接执行动作）'}")

    def _export_current_csv(self):
        if not self.last_result:
            return
        csv_path = self.last_result.get("csv_path")
        if not csv_path or not os.path.exists(csv_path):
            return
        dest, _ = QFileDialog.getSaveFileName(self, "另存 CSV 报告",
            os.path.basename(csv_path), "CSV 文件 (*.csv)")
        if dest:
            import shutil
            shutil.copy2(csv_path, dest)
            self._append_log(f"✓ 已导出到 {dest}", "good")

    # ===================== 校验修复（一键修复） =====================
    def _start_check_fix(self):
        if not self.target_files:
            QMessageBox.warning(self, "请先选择文件", "没有待修复的目标文档。")
            return
        overrides = self._collect_format_overrides()
        preset = _current_preset_name(self.f_preset.currentText())
        self._start_job(WorkerJob("check-fix", {
            "path": self.target_files[0],
            "rules": self.rules_path,
            "preset": preset,
            "overrides": overrides,
        }))

    def _after_check_fix(self, p: dict):
        self._after_validate({"errors": p["errors"], "domain": p["domain"],
                              "csv_path": None, "source": p["source"]})
        out = p.get("out_path") or "（非 DOCX 文件未执行修复）"
        self.f_result.setText(
            f"✅ 修复完成：共应用 {p['repaired']} 处修复 → {out}")
        self.f_result.show()

    # ===================== 排版修复 =====================
    def _current_preset_name(self) -> str:
        return _current_preset_name(self.f_preset.currentText())

    def _apply_format_preset_defaults(self, idx: int):
        """切换预设时，把表单控件填为该预设的参考值。"""
        name = self.f_preset.currentText().split()[0]
        cfg = Config(name)
        m = cfg.merged
        if self.f_font_cn: self.f_font_cn.setText(m.get("body_font_cn") or m.get("body_font", "宋体"))
        if self.f_font_en: self.f_font_en.setText(m.get("body_font_en") or m.get("body_font", "Times New Roman"))
        if self.f_size: self.f_size.setValue(float(m.get("body_size", 12)))
        # 行距
        ls = m.get("line_spacing") or 1.5
        try:
            v = float(ls); self.f_linespacing.setValue(v if 0.5 <= v <= 5 else 1.5)
        except (TypeError, ValueError):
            self.f_linespacing.setValue(1.5)
        ind = m.get("first_line_indent_chars"); self.f_indent.setValue(float(ind or 0))
        align_map = {"left": 0, "center": 1, "right": 2, "justify": 3}
        self.f_align.setCurrentIndex(align_map.get(m.get("alignment", "justify"), 3))
        self.f_space_before.setValue(float(m.get("space_before_pt") or 0))
        self.f_space_after.setValue(float(m.get("space_after_pt") or 0))
        self.f_mt.setValue(float(m.get("margin_top_cm") or 2.54))
        self.f_mb.setValue(float(m.get("margin_bottom_cm") or 2.54))
        self.f_ml.setValue(float(m.get("margin_left_cm") or 3.17))
        self.f_mr.setValue(float(m.get("margin_right_cm") or 3.17))

    def _collect_format_overrides(self) -> dict:
        align_map = {0: "left", 1: "center", 2: "right", 3: "justify"}
        return {
            "body_font_cn": self.f_font_cn.text().strip() or None,
            "body_font_en": self.f_font_en.text().strip() or None,
            "body_size": self.f_size.value(),
            "line_spacing": self.f_linespacing.value(),
            "first_line_indent_chars": self.f_indent.value(),
            "alignment": align_map[self.f_align.currentIndex()],
            "space_before_pt": self.f_space_before.value(),
            "space_after_pt": self.f_space_after.value(),
            "margin_top_cm": self.f_mt.value(),
            "margin_bottom_cm": self.f_mb.value(),
            "margin_left_cm": self.f_ml.value(),
            "margin_right_cm": self.f_mr.value(),
        }

    def _start_format(self):
        if not self.target_files:
            QMessageBox.warning(self, "请先选择文件", "请先添加要排版的文档。")
            return
        src = self.target_files[0]
        stem = os.path.splitext(os.path.basename(src))[0]
        out_dir = self.f_output.text().strip() or _resolve_output_path_str()
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, f"{stem}_formatted.docx")
        preset = self._current_preset_name()
        overrides = self._collect_format_overrides()
        self._start_job(WorkerJob("format", {"path": src, "out_path": out_path,
                                              "preset": preset, "overrides": overrides}))

    def _start_format_dir(self):
        src_dir = QFileDialog.getExistingDirectory(self, "选择批量排版目录", resolve("input"))
        if not src_dir:
            return
        files = GuiBackend._collect_files(src_dir)
        if not files:
            QMessageBox.warning(self, "没有可排版的文档",
                f"目录中未找到扩展名在 {SUPPORTED_EXTS} 中的文件。")
            return
        out_dir = self.f_output.text().strip() or _resolve_output_path_str()
        os.makedirs(out_dir, exist_ok=True)
        preset = self._current_preset_name()
        overrides = self._collect_format_overrides()
        self._append_log(f"[批量排版] 目录 {src_dir}，共 {len(files)} 个文件", "info")
        self._start_job(WorkerJob("format-dir", {
            "files": files, "out_dir": out_dir,
            "preset": preset, "overrides": overrides,
        }))

    def _after_format(self, p: dict):
        self.f_result.setText(f"✅ 排版完成 → {p['out_path']}")
        self.f_result.show()

    def _after_format_dir(self, p: dict):
        self.f_result.setText(
            f"✅ 批量排版完成：{p['count']}/{p['total']} 份成功 → {p['out_dir']}")
        self.f_result.show()

    # ===================== 批注 / 报告 =====================
    def _after_annotate(self, p: dict):
        ok, out = p["ok"], p["out_path"]
        reason = p.get("reason", "")
        if ok:
            self._append_log(f"✓ 批注文档已生成：{out}", "good")
        elif reason:
            self._append_log(f"批注失败：{reason}", "warn")
        else:
            self._append_log(f"批注失败：{out}", "warn")
        self._refresh_annotations_table()

    def _refresh_annotations_table(self):
        rows = []
        out_dir = _resolve_output_path_str()
        if os.path.isdir(out_dir):
            for fn in sorted(os.listdir(out_dir)):
                if "_annotated.docx" in fn:
                    fp = os.path.join(out_dir, fn)
                    mtime = _dt.datetime.fromtimestamp(os.path.getmtime(fp)).strftime("%Y-%m-%d %H:%M")
                    rows.append((fn, mtime, "（需重新计算）"))
        # 简化：估算批注数量的地方比较重，这里写 "—"
        self.a_table.setRowCount(len(rows))
        for i, (fn, mt, n) in enumerate(rows):
            self.a_table.setItem(i, 0, QTableWidgetItem(fn))
            self.a_table.setItem(i, 1, QTableWidgetItem(mt))
            self.a_table.setItem(i, 2, QTableWidgetItem("—"))
            cell = QWidget(); lay = QHBoxLayout(cell); lay.setContentsMargins(4,2,4,2); lay.setSpacing(6)
            o_btn = self._make_btn("打开目录", "btn-sm btn-outline")
            o_btn.clicked.connect(lambda _=False, p=fp: self._reveal_in_explorer(p))
            lay.addWidget(o_btn); lay.addStretch(1)
            self.a_table.setCellWidget(i, 3, cell)

    def _refresh_reports_table(self):
        rows = []
        reports_dir = resolve("reports")
        if os.path.isdir(reports_dir):
            for fn in sorted(os.listdir(reports_dir)):
                if fn.endswith(".csv"):
                    fp = os.path.join(reports_dir, fn)
                    mt = _dt.datetime.fromtimestamp(os.path.getmtime(fp)).strftime("%Y-%m-%d %H:%M")
                    sz = f"{os.path.getsize(fp) / 1024:.1f}"
                    rows.append((fn, mt, sz, fp))
        self.r_table.setRowCount(len(rows))
        for i, (fn, mt, sz, fp) in enumerate(rows):
            self.r_table.setItem(i, 0, QTableWidgetItem(fn))
            self.r_table.setItem(i, 1, QTableWidgetItem(mt))
            self.r_table.setItem(i, 2, QTableWidgetItem(sz))
            cell = QWidget(); lay = QHBoxLayout(cell); lay.setContentsMargins(4,2,4,2); lay.setSpacing(6)
            # cell.setMinimumWidth 双保险：ResizeToContents 用 sizeHint 算列宽，
            # 对 setCellWidget 的 cell 在某些 Qt 版本下 sizeHint 计算不准，
            # 显式给 cell 设最小宽度确保列宽足够，按钮不再被裁成一半。
            cell.setMinimumWidth(160)
            open_btn = self._make_btn("打开", "btn-sm btn-success")
            open_btn.setFixedWidth(72)
            open_btn.clicked.connect(lambda _=False, p=fp: self._open_with_system(p))
            del_btn = self._make_btn("删除", "btn-sm btn-danger")
            del_btn.setFixedWidth(72)
            del_btn.clicked.connect(lambda _=False, p=fp, r=i: self._delete_report(p, r))
            # 不再加 addStretch(1)：stretch 会让 cell 的 sizePolicy 变成
            # MinimumExpanding，干扰 ResizeToContents 的列宽计算，且会把
            # 按钮挤到最左导致裁切。按钮靠左自然排列即可。
            lay.addWidget(open_btn); lay.addWidget(del_btn)
            self.r_table.setCellWidget(i, 3, cell)

    def _delete_report(self, path: str, row: int):
        if QMessageBox.question(self, "确认", f"删除 {os.path.basename(path)}？") == QMessageBox.Yes:
            try:
                os.remove(path)
                self._refresh_reports_table()
            except OSError as exc:
                QMessageBox.warning(self, "删除失败", str(exc))

    # ===================== LLM 规则生成 =====================
    _rl_template_path: str | None = None

    def _start_get_rules(self):
        spec_text = self.rl_spec.toPlainText().strip()
        tmpl = getattr(self, "_rl_template_path", None)
        if not spec_text and not tmpl:
            QMessageBox.warning(self, "缺少输入", "请粘贴规范文字或上传 Word 模板。")
            return
        self._start_job(WorkerJob("get-rules", {
            "domain": self.rl_domain.text().strip() or "自定义领域",
            "spec_text": spec_text or None,
            "template": tmpl,
        }))

    def _after_get_rules(self, p: dict):
        self.rl_out.setPlainText(p["yaml"])
        self.rl_save_btn.setEnabled(True)
        self.rl_copy_btn.setEnabled(True)
        self._append_log("✓ 规则已生成，点击『保存到规则目录』即可成为一套可用规则文件。", "good")

    def _save_generated_rule(self):
        domain = self.rl_domain.text().strip() or "自定义"
        default = f"rules_{domain}.yaml"
        start = resolve("config", default)
        path, _ = QFileDialog.getSaveFileName(self, "保存 YAML 规则", start,
                                              "YAML 规则 (*.yaml)")
        if not path:
            return
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(self.rl_out.toPlainText())
        self._append_log(f"✓ 规则已保存：{path}", "good")

    def _copy_generated_rule(self):
        QApplication.clipboard().setText(self.rl_out.toPlainText())
        self._append_log("✓ 已复制 YAML 规则文本到剪贴板。", "info")

    # ===================== 设置页 =====================
    #: config/ 下非规则文件，不能出现在"默认规则文件"下拉框里。
    _NON_RULE_CONFIG_FILES = {"api_config.yaml", "llm_profiles.yaml", "prompt_template.md"}

    def _list_rules_files(self) -> list[str]:
        config_dir = resolve("config")
        out = []
        for root, _dirs, files in os.walk(config_dir):
            for fn in files:
                if not fn.endswith((".yaml", ".yml")):
                    continue
                if fn in self._NON_RULE_CONFIG_FILES or "template" in fn:
                    continue
                rel = os.path.relpath(os.path.join(root, fn), config_dir)
                out.append(rel)
        return out

    def _save_settings(self):
        # LLM 凭据不写进 gui_settings.json：那是普通用户数据文件，
        # 历史上把明文 api_key 存在这里、而该文件未被 gitignore，容易误提交。
        # LLM 凭据只存放在 config/llm_profiles.yaml（已 gitignore）。
        data = {
            "rules_path": os.path.join("config", self.s_default_rule.currentText()),
            "output_dir": self.s_out_dir.text().strip(),
            "reports_dir": self.s_rep_dir.text().strip(),
            "auto_annotate": self.s_auto_anno.isChecked(),
        }
        p = resolve("gui_settings.json")
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        # 即时应用
        self.rules_path = resolve(data["rules_path"])
        self._append_log(f"✓ 设置已保存 → {p}", "good")

    def _refresh_llm_summary(self):
        """刷新设置页的「当前方案」摘要标签。"""
        from engine import llm_profiles
        profile = llm_profiles.active_profile()
        if profile and profile["api_key"]:
            model = profile["model"] or "（未填 model）"
            self.s_llm_summary.setText(
                f"当前方案：{profile['name']}  ·  {model}  ·  Key {_mask_key(profile['api_key'])}"
            )
        elif profile:
            self.s_llm_summary.setText(f"当前方案：{profile['name']}  ·  未填 API Key")
        else:
            self.s_llm_summary.setText("尚未配置 LLM 方案")

    def _open_llm_settings(self):
        """打开 LLM 方案管理弹窗；关闭后刷新摘要。"""
        dlg = LLMProfileDialog(self)
        dlg.changed.connect(self._refresh_llm_summary)
        dlg.exec_()
        self._refresh_llm_summary()

    def _test_api(self):
        """测试**当前生效**的方案（不是编辑中的草稿），走后台线程不卡界面。"""
        from engine import llm_profiles
        profile = llm_profiles.active_profile()
        if not profile or not profile["api_key"]:
            QMessageBox.warning(self, "未配置", "请先在「LLM 设置」里新建一个带 API Key 的方案。")
            return
        self._llm_test_worker = _LLMTestWorker(dict(profile))
        self._llm_test_worker.done.connect(self._on_llm_test_done)
        self._llm_test_worker.start()

    def _on_llm_test_done(self, ok: bool, message: str):
        """主界面「测试连接」的回调。窗口销毁后访问控件会抛 RuntimeError。"""
        try:
            worker = self._llm_test_worker
            label = worker.profile_label if worker else "（未知）"
            self._append_log(("✓ " if ok else "✗ ") + f"方案「{label}」：{message}",
                             "good" if ok else "warn")
            if not ok:
                QMessageBox.warning(self, "连接失败", message)
        except RuntimeError:
            pass

    def _run_end_to_end_demo(self):
        """对应 cli.py 的 /test：examples/sample_for_test.docx → check → format → check-fix → annotate。"""
        sample = resolve("examples", "sample_for_test.docx")
        if not os.path.exists(sample):
            QMessageBox.warning(self, "样例缺失", "examples/sample_for_test.docx 不存在。")
            return
        self.target_files = [sample]
        self._render_file_tags()
        self._start_job(WorkerJob("check-fix", {
            "path": sample,
            "rules": self.rules_path,
            "preset": self._current_preset_name(),
            "overrides": self._collect_format_overrides(),
        }))

    # ===================== 辅助：工具函数 =====================
    def _append_log(self, msg: str, level: str = "info"):
        # 日志统一显示在设置页的 s_log；校验页/排版页的日志框已移除。
        # 用 getattr 容错：如果用户从未切到设置页导致 s_log 未创建，
        # 跳过日志写入而不是抛 AttributeError。
        from PySide6.QtGui import QTextCursor
        color_map = {"good": "#4ade80", "warn": "#fbbf24", "error": "#fca5a5",
                     "info": "#e2e8f0", "debug": "#94a3b8"}
        color = color_map.get(level, color_map["info"])
        s_log = getattr(self, "s_log", None)
        if s_log is None:
            return
        s_log.appendHtml(f'<span style="color:{color}">{msg}</span>')
        c = s_log.textCursor(); c.movePosition(QTextCursor.End)
        s_log.setTextCursor(c)

    def _refresh_status_bar(self):
        try:
            import psutil
            mem = psutil.Process(os.getpid()).memory_info().rss / 1024 / 1024
            mem_str = f"内存 {mem:.0f} MB"
        except Exception:
            mem_str = ""
        now = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        rule_rel = os.path.relpath(self.rules_path, BASE_DIR) \
            if self.rules_path.startswith(BASE_DIR + os.sep) else self.rules_path
        self.sb_left.setText(f"📀 引擎 {ENGINE_VERSION} · 规则: {rule_rel}")
        self.sb_right.setText(f"🕒 {now}" + (f"   💾 {mem_str}" if mem_str else ""))

    def _open_with_system(self, path: str):
        QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.abspath(path)))

    def _reveal_in_explorer(self, path: str):
        QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(os.path.abspath(path))))

    def closeEvent(self, event):
        if self._thread and self._thread.isRunning():
            self._thread.requestInterruption()
            if self._worker:
                self._worker.stop()
            self._thread.quit()
            self._thread.wait(2000)
        super().closeEvent(event)


# ---------------------------------------------------------------------------
# 小工具：给错误严重性上色
# ---------------------------------------------------------------------------
def QBrush_color(is_red: bool, is_yellow: bool) -> QColor:
    if is_red:
        return QColor("#991b1b")
    if is_yellow:
        return QColor("#92400e")
    return QColor("#1e293b")


def _current_preset_name(text: str) -> str:
    return text.split()[0]


def _resolve_output_path_str() -> str:
    return resolve("output")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main() -> int:
    # 启动前创建 input/ output/ reports/ 工作目录，与 CLI 对齐
    for name in ("input", "output", "reports"):
        os.makedirs(resolve(name), exist_ok=True)
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
