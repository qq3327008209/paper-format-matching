"""Rich 彩色输出：banner / kv table / 校验 / 批量进度 / 自检。

命令补全元数据 ``COMMAND_META`` 也在这里集中定义，供 ui/input.py 共用。
"""
from __future__ import annotations

import os

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text


# ------------------------------------------------------------------
# 命令元数据：命令名 -> (简述, 用法)
# 供补全菜单、/help、底部提示共用
# ------------------------------------------------------------------
COMMAND_META = {
    "/help":        ("显示帮助",            "/help"),
    "/config":      ("显示当前配置",         "/config"),
    "/preset":      ("切换预设",            "/preset <official|academic|legal>"),
    "/check":       ("校验单个文件",         "/check <file> [--rules <path>]"),
    "/check-dir":   ("批量校验目录",         "/check-dir <dir> [--rules <path>]"),
    "/format":      ("格式化单个文件",       "/format <file> [--preset <name>] [--output <path>]"),
    "/format-dir":  ("批量格式化目录",       "/format-dir <dir> [--preset <name>]"),
    "/check-fix":   ("校验并修复",           "/check-fix <file> [--rules <path>] [--preset <name>]"),
    "/check-fix-dir": ("批量校验修复",       "/check-fix-dir <dir> [--rules <path>] [--preset <name>]"),
    "/annotate":    ("标注上次校验结果",     "/annotate"),
    "/get-rules":   ("LLM 生成 YAML 规则",   "/get-rules <file> [--output <path>] [--domain <name>]"),
    "/set-key":     ("设置 LLM API Key",    "/set-key"),
    "/test-api":    ("测试 LLM API",        "/test-api"),
    "/chat":        ("LLM 多轮对话",         "/chat  (exit 退出, /clear 清空)"),
    "/test":        ("端到端自检",           "/test"),
    "/save-config": ("保存配置到 JSON",      "/save-config <file>"),
    "/load-config": ("从 JSON 加载配置",     "/load-config <file>"),
    "/quit":        ("退出",                "/quit"),
}


console = Console()


# ------------------------------------------------------------------
# 基本消息
# ------------------------------------------------------------------

def print_banner():
    """打印启动 banner。"""
    from engine import __version__

    content = Text("文档格式校验与排版系统", style="bold cyan", justify="center")
    sub = Text("输入 /help 查看命令，/quit 退出", style="dim", justify="center")
    console.print(Panel.fit(
        f"{content}\n{sub}",
        border_style="cyan",
        title="[bold]paper-format-matching[/bold]",
        subtitle=f"[dim]v{__version__}[/dim]",
    ))


def print_good(msg):
    """成功消息。"""
    console.print(f"[bold green]✓[/bold green] {msg}")


def print_bad(msg):
    """错误/失败消息。"""
    console.print(f"[bold red]✗[/bold red] {msg}")


def print_info(msg):
    """普通信息。"""
    console.print(f"[blue]ℹ[/blue] {msg}")


def print_warn(msg):
    """警告。"""
    console.print(f"[yellow]![/yellow] {msg}")


def print_error(exc):
    """异常输出。"""
    console.print(f"[bold red]错误:[/bold red] {exc}")


# ------------------------------------------------------------------
# 表格 / 列表
# ------------------------------------------------------------------

def print_kv_table(title, rows, headers=("项", "值")):
    """打印键值对表格。rows 是 list[tuple[str, str]]。"""
    table = Table(title=title, show_header=True, header_style="bold cyan", border_style="cyan")
    for h in headers:
        table.add_column(h)
    for k, v in rows:
        table.add_row(str(k), str(v))
    console.print(table)


def print_check_result(errors, file_path, domain):
    """打印校验结果摘要。"""
    if not errors:
        console.print(Panel.fit(
            f"[bold green]✓ 通过[/bold green]  {file_path}\n领域: {domain or 'N/A'}",
            border_style="green",
        ))
        return

    # Separate warnings (e.g. missing fields) from genuine format failures.
    warnings = [e for e in errors if e.get("type") == "missing"]
    failures = [e for e in errors if e.get("type") != "missing"]

    table = Table(
        title=(
            f"校验结果: {os.path.basename(file_path)}  "
            f"({len(failures)} 处问题"
            f"{f', {len(warnings)} 处警告' if warnings else ''})"
        ),
        show_header=True,
        header_style="bold red",
        border_style="red",
    )
    table.add_column("#", style="dim", width=4)
    table.add_column("严重度", width=8)
    table.add_column("段落", width=10)
    table.add_column("类型", width=12)
    table.add_column("说明")
    for i, err in enumerate(errors, 1):
        is_warn = err.get("type") == "missing"
        sev = "[yellow]警告[/yellow]" if is_warn else "[red]错误[/red]"
        table.add_row(
            str(i),
            sev,
            str(err.get("segment", "")),
            str(err.get("type", "")),
            str(err.get("description", "")),
        )
    console.print(table)


def print_batch_progress(idx, total, name, status, detail=""):
    """打印批量处理单条进度。status: ok/fail/warn/skip。"""
    style_map = {"ok": "green", "fail": "red", "warn": "yellow", "skip": "dim"}
    icon_map = {"ok": "✓", "fail": "✗", "warn": "!", "skip": "-"}
    style = style_map.get(status, "white")
    icon = icon_map.get(status, "?")
    line = f"[{idx}/{total}] {name} [{style}]{icon}[/{style}]"
    if detail:
        line += f" {detail}"
    console.print(line)


def print_batch_summary(total_files, total_errors, report_path):
    """批量处理汇总。"""
    console.print(Panel.fit(
        f"共 [bold]{total_files}[/bold] 个文件  |  "
        f"[red]{total_errors}[/red] 处问题\n"
        f"报告: [dim]{report_path}[/dim]",
        border_style="cyan",
        title="批量完成",
    ))


def print_test_step(name, ok, detail=""):
    """自检单步结果。"""
    if ok:
        console.print(f"  [green]✓[/green] {name}" + (f"  [dim]— {detail}[/dim]" if detail else ""))
    else:
        console.print(f"  [red]✗[/red] {name}" + (f"  [dim]— {detail}[/dim]" if detail else ""))


def print_test_summary(passed, total, ex_dir):
    """自检汇总。

    ``ex_dir`` 是 /test 产物落地的 examples/ 目录——保留展示路径便于用户
    直接打开产物查看。流程跑通 + 路径可见，是 /test 的核心价值。
    """
    if passed == total:
        console.print(Panel.fit(
            f"[bold green]全部通过[/bold green]  {passed}/{total}\n"
            f"产物目录: [dim]{ex_dir}[/dim]",
            border_style="green",
            title="自检完成",
        ))
    else:
        console.print(Panel.fit(
            f"[bold yellow]{passed}/{total}[/bold yellow] 项通过\n"
            f"产物目录: [dim]{ex_dir}[/dim]",
            border_style="yellow",
            title="自检完成",
        ))


def print_farewell():
    console.print("[dim]再见！[/dim]")
