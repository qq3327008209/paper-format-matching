"""UI 子包：输出 (rich) 与输入 (prompt_toolkit) 的薄封装。"""
from .printers import (
    COMMAND_META,
    console,
    print_bad,
    print_banner,
    print_batch_progress,
    print_batch_summary,
    print_check_result,
    print_error,
    print_farewell,
    print_good,
    print_info,
    print_kv_table,
    print_test_step,
    print_test_summary,
    print_warn,
)
from .input import make_session, read_input

__all__ = [
    "COMMAND_META",
    "console",
    "make_session",
    "read_input",
    "print_bad",
    "print_banner",
    "print_batch_progress",
    "print_batch_summary",
    "print_check_result",
    "print_error",
    "print_farewell",
    "print_good",
    "print_info",
    "print_kv_table",
    "print_test_step",
    "print_test_summary",
    "print_warn",
]
