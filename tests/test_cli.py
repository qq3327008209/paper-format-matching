import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from cli import CLI

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_parse_args_simple():
    assert CLI._parse_args("file.docx") == ("file.docx", {})


def test_parse_args_with_flags():
    path, flags = CLI._parse_args("file.docx --preset official --output out.docx")
    assert path == "file.docx"
    assert flags == {"preset": "official", "output": "out.docx"}


def test_parse_args_path_with_spaces():
    path, flags = CLI._parse_args("path with spaces.docx --rules r.yaml")
    assert path == "path with spaces.docx"
    assert flags == {"rules": "r.yaml"}


def test_cli_help():
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    result = subprocess.run(
        [sys.executable, "cli.py"],
        input="/help\n/quit\n",
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=PROJECT_ROOT,
        env=env,
    )
    assert "/check-dir" in result.stdout
    assert "/format-dir" in result.stdout
    assert "/check-fix-dir" in result.stdout


def test_cli_no_argparse():
    cli_path = os.path.join(PROJECT_ROOT, "cli.py")
    with open(cli_path, "r", encoding="utf-8") as fh:
        source = fh.read()
    assert "argparse" not in source
    assert "--check-dir" not in source.replace("/check-dir", "")
