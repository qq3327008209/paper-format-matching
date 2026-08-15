"""项目路径解析：兼容 python 脚本 / Nuitka onefile / PyInstaller onefile 三种运行环境。

核心原则：所有相对路径基于「原始 exe / cli.py 所在目录」解析，
而不是当前工作目录（cwd），也不是 onefile 解压产生的临时目录。

为什么 Nuitka onefile 需要特殊处理：
    Nuitka onefile 由 launcher + payload 组成。启动时 launcher 把 payload
    解压到 %TMP%\\onefile_<pid>_<...>\\ 下，然后 spawn 子进程运行解压后的程序。
    在子进程（我们的代码）里：
      - sys.executable  → 临时目录下的子 exe（错误，重启就没）
      - sys.argv[0]     → launcher 原样透传的原始 exe 路径（正确）
      - sys._MEIPASS    → 不存在（PyInstaller 才有）
      - __compiled__    → 存在，可用来识别 Nuitka 环境
    PyInstaller onefile 下 sys.executable 就是原始 exe，行为一致。

因此 frozen 环境的 BASE_DIR 算法：
    1. 若存在 sys._MEIPASS  → PyInstaller  → dirname(sys.executable)
    2. 否则                 → Nuitka      → argv[0] + Windows API 双保险

注意：config/ 是用户数据目录（规则文件可编辑、api_config.yaml 可写），
必须始终放在原始 exe 旁边，不能存到临时目录。
"""

import os
import sys


def _get_windows_exe_path() -> str | None:
    """Windows 下用 kernel32 API 取当前进程的命令行并解析出 exe 路径。

    比 sys.argv[0] 更可靠：argv[0] 可能被调用者伪造，而
    GetCommandLineW + CommandLineToArgvW 反映的是 CreateProcess
    时传入的真实 lpApplicationName/lpCommandLine。

    Returns:
        绝对路径字符串，或在非 Windows / API 失败时返回 None。
    """
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        shell32 = ctypes.WinDLL("shell32", use_last_error=True)

        kernel32.GetCommandLineW.restype = wintypes.LPCWSTR

        CommandLineToArgvW = shell32.CommandLineToArgvW
        CommandLineToArgvW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
        CommandLineToArgvW.restype = ctypes.POINTER(wintypes.LPWSTR)

        cmdline = kernel32.GetCommandLineW()
        argc = ctypes.c_int(0)
        argv_ptrs = CommandLineToArgvW(cmdline, ctypes.byref(argc))
        if not argv_ptrs or argc.value < 1:
            return None
        try:
            exe_path = argv_ptrs[0]
        finally:
            # CommandLineToArgvW 返回的指针数组需要用 LocalFree 释放
            kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
            kernel32.LocalFree.restype = wintypes.HLOCAL
            kernel32.LocalFree(argv_ptrs)
        if exe_path:
            return os.path.abspath(exe_path)
    except Exception:
        return None
    return None


def _compute_base_dir() -> str:
    """计算项目根目录（config/ 所在位置）。"""
    frozen = getattr(sys, "frozen", False)

    if frozen and hasattr(sys, "_MEIPASS"):
        # PyInstaller onefile / onedir：sys.executable 就是原始 exe
        return os.path.dirname(os.path.abspath(sys.executable))

    if frozen:
        # Nuitka（含 onefile 和 onedir）：优先 Windows API，其次 sys.argv[0]
        exe_via_api = _get_windows_exe_path()
        if exe_via_api:
            return os.path.dirname(exe_via_api)
        argv0 = sys.argv[0] if sys.argv else None
        if argv0:
            # argv[0] 可能是相对路径，转绝对路径
            return os.path.dirname(os.path.abspath(argv0))
        # 极端兜底：退回到 sys.executable（对 Nuitka onefile 是临时目录，
        # 但总比让程序崩溃好）
        return os.path.dirname(os.path.abspath(sys.executable))

    # python 脚本模式：paths.py 位于 engine/ 下，往上一层 = 项目根
    #   engine/paths.py  -> dirname(engine/) -> 项目根
    # 这里原本写了两层（dirname(dirname(__file__))），但 paths.py 直接在
    # engine 下，dirname(engine/paths.py) 就是 engine，再 dirname 一次就是项目根。
    # 没错，保持两层。
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# BASE_DIR 仍然是模块级常量，因为 _compute_base_dir 只依赖 sys/os 的导入
# 级属性（frozen、argv、executable、os.name），这些在 import 时就已稳定。
BASE_DIR = _compute_base_dir()


def resolve(*parts: str) -> str:
    """将相对路径片段拼到 BASE_DIR 下，返回绝对路径。

    例：
        resolve("config", "api_config.yaml")
        -> "C:/.../paper-format-matching/config/api_config.yaml"
    """
    return os.path.join(BASE_DIR, *parts)


def ensure_parent(path: str) -> str:
    """确保 path 的父目录存在，不存在则创建。返回 path 本身。"""
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    return path
