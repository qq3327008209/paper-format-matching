"""REPL 输入侧：基于 prompt_toolkit 的命令补全 + 历史 + 多行读取。"""
from __future__ import annotations

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.history import InMemoryHistory

from .printers import COMMAND_META


def make_session():
    """创建带命令补全和历史的 PromptSession。

    特性：
    - 输入 / 开头时弹出命令菜单（WordCompleter 的 meta_dict 提供描述）
    - 上下方向键浏览历史
    - Ctrl+C / Ctrl+D 退出
    """
    commands = sorted(COMMAND_META.keys())
    completer = WordCompleter(
        words=commands,
        meta_dict={k: v[0] for k, v in COMMAND_META.items()},
        sentence=True,
    )
    history = InMemoryHistory()
    return PromptSession(
        completer=completer,
        history=history,
        complete_while_typing=True,
        prompt_continuation="  ",
    )


def read_input(session, prompt_text="> "):
    """读一行输入。返回 (line, quit_flag)。

    quit_flag 为 True 时表示用户按了 Ctrl+C/D 或 EOF。
    """
    try:
        line = session.prompt(prompt_text).strip()
        return line, False
    except (EOFError, KeyboardInterrupt):
        return "", True
