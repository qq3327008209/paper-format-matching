"""项目路径解析：兼容 python 脚本 / Nuitka / PyInstaller 三种运行环境。

核心原则：所有相对路径基于「运行入口所在目录」解析，
而不是当前工作目录（cwd）。这样编译成 exe 后，无论从哪个目录启动、
双击还是命令行调用，都能正确定位 config/ 等用户数据目录。

三种环境下的 BASE_DIR：
- python cli.py        → cli.py 所在目录（= 项目根）
- Nuitka onefile exe   → exe 所在目录（sys.executable 的 dirname）
- PyInstaller onefile  → exe 所在目录（同上）

注意：config/ 是用户数据目录（规则文件可编辑、api_config.yaml 可写），
应始终放 exe 旁边，不打包进 onefile payload。
"""

import os
import sys


def _compute_base_dir() -> str:
    """计算项目根目录（config/ 所在位置）。"""
    if getattr(sys, "frozen", False):
        # Nuitka / PyInstaller 编译后的 exe
        # sys.executable 是 exe 真实路径，其所在目录即用户数据根
        return os.path.dirname(os.path.abspath(sys.executable))
    # python 脚本模式：paths.py 位于 engine/ 下，往上两层 = 项目根
    # （dirname(engine/) = 项目根）
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


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
