#!/usr/bin/env python3
"""Interactive REPL CLI for document format checking and formatting.

Running ``python cli.py`` enters an interactive ``>`` prompt loop.  All
operations -- including batch directory operations -- happen INSIDE the REPL
via ``/`` commands.  There is no non-interactive command-line mode.

Only imports from the new project modules ``engine`` and ``format`` (plus the
standard library and ``python-docx``) are used.
"""

import json
import logging
import os
import sys

from engine import get_extractor, RuleEngine, Reporter, annotate_document
from engine.paths import resolve, ensure_parent, BASE_DIR
from format import FormatEngine, Config, PRESETS
from prompt_toolkit.formatted_text import HTML
import ui

logger = logging.getLogger(__name__)

# Supported document extensions for batch operations.
SUPPORTED_EXTS = (".docx", ".doc", ".wps", ".pdf")


class CLI:
    """Interactive command-line interface for document format tooling."""

    def __init__(self):
        self.config = Config("official")
        self.rules_path = resolve("config", "rules_lunwen.yaml")
        self.last_errors = []
        self.last_file = None
        self.session = None  # 延迟到 run() 创建，避免非交互环境报错
        self.commands = {
            "/help": self.cmd_help,
            "/config": self.cmd_config,
            "/preset": self.cmd_preset,
            "/check": self.cmd_check,
            "/check-dir": self.cmd_check_dir,
            "/format": self.cmd_format,
            "/format-dir": self.cmd_format_dir,
            "/check-fix": self.cmd_check_fix,
            "/check-fix-dir": self.cmd_check_fix_dir,
            "/annotate": self.cmd_annotate,
            "/get-rules": self.cmd_get_rules,
            "/set-key": self.cmd_set_key,
            "/test-api": self.cmd_test_api,
            "/test": self.cmd_test,
            "/chat": self.cmd_chat,
            "/save-config": self.cmd_save_config,
            "/load-config": self.cmd_load_config,
            "/quit": self.cmd_quit,
        }

    # ------------------------------------------------------------------
    # REPL loop
    # ------------------------------------------------------------------
    def run(self):
        """Print the welcome banner and enter the read-eval-print loop.

        非交互环境（subprocess 管道、重定向）下退化为普通 input()，
        避免 prompt_toolkit 在无 TTY 时抛 NoConsoleScreenBufferError。
        """
        logging.basicConfig(level=logging.INFO, format="%(message)s")
        interactive = sys.stdin.isatty() and sys.stdout.isatty()
        if interactive:
            self.session = ui.make_session()
        ui.print_banner()
        while True:
            if interactive:
                line, quit_flag = ui.read_input(self.session, HTML("<ansicyan><b>&gt;</b></ansicyan> "))
            else:
                try:
                    line = input("> ").strip()
                    quit_flag = False
                except (EOFError, KeyboardInterrupt):
                    ui.print_farewell()
                    break
            if quit_flag:
                ui.print_farewell()
                break
            if not line:
                continue
            parts = line.split(None, 1)
            cmd = parts[0]
            args = parts[1] if len(parts) > 1 else ""
            handler = self.commands.get(cmd)
            if handler is None:
                ui.print_bad(f"未知命令: {cmd}。输入 /help 查看可用命令。")
                continue
            try:
                handler(args)
            except Exception as exc:
                ui.print_error(exc)
            if cmd == "/quit":
                break

    # ------------------------------------------------------------------
    # Argument parsing
    # ------------------------------------------------------------------
    @staticmethod
    def _parse_args(args_str):
        """Parse a command's argument string into (path, flags).

        支持：
        - 双引号包裹含空格的路径：``"C:\\my dir\\file.docx"``
        - ``--key value``（value 可含空格，合并到下一个 ``--`` 之前）
        - ``--key="value"``
        - ``--key``（无值时为 True）

        非 flag token 拼成主路径。
        """
        import re
        # tokenizer 优先级：
        # 1. 双引号包裹整体
        # 2. --key="value" 整体（等号后双引号）
        # 3. --key=value 整体（等号后无空格直到下一个空白或其他 --）
        # 4. 普通非空白序列
        raw_tokens = re.findall(
            r'"[^"]*"'                            # 双引号整体
            r'|--[^\s=]+="[^"]*"'                 # --key="value"
            r'|--[^\s=]+=\S+'                     # --key=value
            r'|\S+',                              # 普通非空白
            args_str
        )
        tokens = [t[1:-1] if t.startswith('"') and t.endswith('"') else t
                  for t in raw_tokens]
        path_parts: list[str] = []
        flags = {}
        i = 0
        while i < len(tokens):
            tok = tokens[i]
            if tok.startswith("--"):
                key = tok[2:]
                # --key="value" 或 --key=value 形式
                if "=" in key:
                    k, _, v = key.partition("=")
                    # 去掉值两端可能残留的引号
                    if v.startswith('"') and v.endswith('"') and len(v) >= 2:
                        v = v[1:-1]
                    value_parts = [v] if v else []
                    i += 1
                    # 对于无引号的 --key=value 且 value 被空格截断的情况，继续合并
                    while i < len(tokens) and not tokens[i].startswith("--"):
                        value_parts.append(tokens[i])
                        i += 1
                    flags[k] = " ".join(value_parts)
                    continue
                # --key value 形式：连续合并非 -- token
                value_parts: list[str] = []
                i += 1
                while i < len(tokens) and not tokens[i].startswith("--"):
                    value_parts.append(tokens[i])
                    i += 1
                if value_parts:
                    flags[key] = " ".join(value_parts)
                else:
                    flags[key] = True
            else:
                path_parts.append(tok)
                i += 1
        path = " ".join(path_parts) if path_parts else None
        return path, flags

    @staticmethod
    def _collect_files(dir_path):
        """Walk a directory and return supported document files (sorted)."""
        files = []
        for root, _dirs, fnames in os.walk(dir_path):
            for name in fnames:
                if name.lower().endswith(SUPPORTED_EXTS):
                    files.append(os.path.join(root, name))
        return sorted(files)

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------
    def _validate_file(self, file_path, rules_path):
        """Validate one file. Returns (errors, domain, rules_data)."""
        extractor = get_extractor(file_path)
        doc_data = extractor.extract(file_path)
        engine = RuleEngine(doc_data, rules_path=rules_path)
        errors = engine.validate()
        domain = engine.rules_data.get("domain", "")
        return errors, domain, engine.rules_data

    @staticmethod
    def _segment_font_size(segment, config):
        """Return (font_cn, size) appropriate for a segment from config."""
        font_key = f"{segment}_font"
        size_key = f"{segment}_size"
        defaults = {
            "title": ("方正小标宋简体", 22),
            "h1": ("黑体", 16),
            "h2": ("楷体_GB2312", 16),
            "h3": ("仿宋_GB2312", 16),
            "h4": ("仿宋_GB2312", 16),
            "body": ("仿宋_GB2312", 16),
        }
        default_font, default_size = defaults.get(segment, ("仿宋_GB2312", 16))
        return config.get(font_key, default_font), config.get(size_key, default_size)

    def _apply_format_action(self, para, action, value, segment, config):
        """Apply a single repair action to a paragraph."""
        from format.ops.font import FontOp
        from format.ops.paragraph import ParagraphOp

        font_op = FontOp(config)
        para_op = ParagraphOp(config)
        en_font = config.get("body_font_en", "Times New Roman")

        if action == "set_font":
            font_cn = value
            _font, seg_size = self._segment_font_size(segment, config)
            font_op.apply_to_paragraph(para, font_cn, en_font, seg_size)
        elif action == "set_size":
            try:
                size = float(value)
            except (TypeError, ValueError):
                return
            font_cn, _size = self._segment_font_size(segment, config)
            font_op.apply_to_paragraph(para, font_cn, en_font, size)
        elif action == "set_align":
            para_op.set_align(para, value)
        elif action == "set_line_spacing":
            try:
                spacing = float(value)
            except (TypeError, ValueError):
                return
            para_op.set_line_spacing(para, spacing)
        elif action == "set_indent":
            para_op.set_indent(para, value)

    def _check_fix_core(self, file_path, rules_path, config):
        """Validate then repair a single file.

        Returns (errors, domain, repaired_count, out_path).
        ``out_path`` is None when the file cannot be repaired in place.
        """
        errors, domain, rules_data = self._validate_file(file_path, rules_path)
        self.last_errors = errors
        self.last_file = file_path

        if not file_path.lower().endswith(".docx"):
            return errors, domain, 0, None

        from docx import Document

        doc = Document(file_path)
        paragraphs = doc.paragraphs

        # 修复决策直接来自 validate() 返回的 error dict（action / action_value
        # 字段由规则引擎在 _execute_rule 中写入）。不再重新遍历 rules 聚合
        # seg_actions——那会让修复路径与校验路径脱节，且对 in/range 等 op 的
        # value 语义不匹配。
        repaired = 0
        seen: set[tuple] = set()
        for err in errors:
            idx = err.get("paragraph_index")
            action = err.get("action")
            if idx is None or action is None:
                continue
            value = err.get("action_value")
            seg = err.get("segment")
            key = (idx, action)
            if key in seen:
                continue
            seen.add(key)
            if 0 <= idx < len(paragraphs):
                self._apply_format_action(
                    paragraphs[idx], action, value, seg, config
                )
                repaired += 1

        stem = os.path.splitext(file_path)[0]
        out_path = f"{stem}_fixed.docx"
        doc.save(out_path)
        return errors, domain, repaired, out_path

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------
    def cmd_help(self, args):
        """显示所有可用命令。"""
        ui.print_kv_table(
            "可用命令",
            [(cmd, f"{meta[1]}  [dim]— {meta[0]}[/dim]") for cmd, meta in ui.COMMAND_META.items()],
            headers=("命令", "用法"),
        )

    def cmd_config(self, args):
        """显示当前配置。"""
        cfg = self.config
        ui.print_kv_table("当前配置", [
            ("当前预设", cfg.preset_name),
            ("规则文件", self.rules_path),
            ("正文字体", f"{cfg.get('body_font', 'N/A')}  {cfg.get('body_size', 'N/A')}pt"),
            ("标题字体", f"{cfg.get('title_font', 'N/A')}  {cfg.get('title_size', 'N/A')}pt"),
            ("一级标题", f"{cfg.get('h1_font', 'N/A')}  {cfg.get('h1_size', 'N/A')}pt"),
            ("二级标题", f"{cfg.get('h2_font', 'N/A')}  {cfg.get('h2_size', 'N/A')}pt"),
            ("行距", f"{cfg.get('line_spacing', 'N/A')}pt"),
            ("页边距(cm)", f"上{cfg.get('margin_top', 'N/A')} 下{cfg.get('margin_bottom', 'N/A')} "
                          f"左{cfg.get('margin_left', 'N/A')} 右{cfg.get('margin_right', 'N/A')}"),
        ])

    def cmd_preset(self, args):
        """切换预设。"""
        name, _flags = self._parse_args(args)
        if not name:
            ui.print_warn("用法: /preset <official|academic|legal>")
            return
        if name not in PRESETS:
            ui.print_bad(f"未知预设: {name}。可选: {', '.join(PRESETS.keys())}")
            return
        self.config = Config(name)
        ui.print_good(f"已切换到预设: {self.config.preset_name}")

    def cmd_check(self, args):
        """校验单个文件。"""
        path, flags = self._parse_args(args)
        if not path:
            ui.print_warn("用法: /check <文件路径> [--rules <规则文件>]")
            return
        rules_path = flags.get("rules", self.rules_path)
        ui.print_info("校验中...")
        errors, domain, _rules_data = self._validate_file(path, rules_path)
        self.last_errors = errors
        self.last_file = path
        ui.print_check_result(errors, path, domain)
        reporter = Reporter()
        stem = os.path.splitext(os.path.basename(path))[0]
        csv_path = reporter.to_timestamped_path(
            resolve("reports", f"{stem}.csv"), "_check"
        )
        ensure_parent(csv_path)
        reporter.save_csv(errors, csv_path, path, domain)
        ui.print_info(f"报告已保存: {csv_path}")

    def cmd_check_dir(self, args):
        """批量校验目录。"""
        dir_path, flags = self._parse_args(args)
        if not dir_path:
            ui.print_warn("用法: /check-dir <目录> [--rules <规则文件>]")
            return
        rules_path = flags.get("rules", self.rules_path)
        files = self._collect_files(dir_path)
        if not files:
            ui.print_warn("未找到支持的文档文件。")
            return
        ui.print_info(f"共发现 {len(files)} 个文件，开始批量校验...")
        reporter = Reporter()
        all_results = []
        total_errors = 0
        for i, f in enumerate(files, 1):
            try:
                errors, domain, _rd = self._validate_file(f, rules_path)
                all_results.append((f, errors))
                total_errors += len(errors)
                self.last_errors = errors
                self.last_file = f
                ui.print_batch_progress(i, len(files), os.path.basename(f),
                                        "warn" if errors else "ok",
                                        f"{len(errors)} 处问题" if errors else "通过")
            except Exception as exc:
                ui.print_batch_progress(i, len(files), os.path.basename(f), "fail", f"校验失败: {exc}")
        combined_path = reporter.to_timestamped_path(
            resolve("reports", "batch.csv"), "_check"
        )
        ensure_parent(combined_path)
        reporter.save_batch_csv(all_results, combined_path)
        ui.print_batch_summary(len(files), total_errors, combined_path)

    def cmd_format(self, args):
        """排版单个文件。"""
        path, flags = self._parse_args(args)
        if not path:
            ui.print_warn("用法: /format <文件路径> [--preset <预设>] [--output <输出路径>]")
            return
        preset_name = flags.get("preset")
        config = Config(preset_name) if preset_name else self.config
        out_path = flags.get("output")
        if not out_path:
            stem = os.path.splitext(path)[0]
            out_path = f"{stem}_formatted.docx"
        ui.print_info(f"排版中... (预设: {config.preset_name})")
        engine = FormatEngine(config)
        engine.run(path, out_path)
        ui.print_good(f"排版完成: {out_path}")

    def cmd_format_dir(self, args):
        """批量排版目录。"""
        dir_path, flags = self._parse_args(args)
        if not dir_path:
            ui.print_warn("用法: /format-dir <目录> [--preset <预设>]")
            return
        preset_name = flags.get("preset")
        config = Config(preset_name) if preset_name else self.config
        files = self._collect_files(dir_path)
        if not files:
            ui.print_warn("未找到支持的文档文件。")
            return
        ui.print_info(f"共发现 {len(files)} 个文件，开始批量排版... (预设: {config.preset_name})")
        for i, f in enumerate(files, 1):
            try:
                stem = os.path.splitext(f)[0]
                out_path = f"{stem}_formatted.docx"
                engine = FormatEngine(config)
                engine.run(f, out_path)
                ui.print_batch_progress(i, len(files), os.path.basename(f), "ok", "done")
            except Exception as exc:
                ui.print_batch_progress(i, len(files), os.path.basename(f), "fail", f"排版失败: {exc}")
        ui.print_good("批量排版完成。")

    def cmd_check_fix(self, args):
        """校验并修复单个文件。"""
        path, flags = self._parse_args(args)
        if not path:
            ui.print_warn("用法: /check-fix <文件路径> [--rules <规则文件>] [--preset <预设>]")
            return
        rules_path = flags.get("rules", self.rules_path)
        preset_name = flags.get("preset")
        config = Config(preset_name) if preset_name else self.config
        ui.print_info("校验并修复中...")
        errors, domain, repaired, out_path = self._check_fix_core(
            path, rules_path, config
        )
        ui.print_check_result(errors, path, domain)
        if out_path:
            ui.print_good(f"修复完成: 共应用 {repaired} 处修复，已保存到 {out_path}")
        else:
            ui.print_warn("注意: 仅 .docx 文件支持修复，已跳过修复步骤。")

    def cmd_check_fix_dir(self, args):
        """批量校验并修复目录。"""
        dir_path, flags = self._parse_args(args)
        if not dir_path:
            ui.print_warn("用法: /check-fix-dir <目录> [--rules <规则文件>] [--preset <预设>]")
            return
        rules_path = flags.get("rules", self.rules_path)
        preset_name = flags.get("preset")
        config = Config(preset_name) if preset_name else self.config
        files = self._collect_files(dir_path)
        if not files:
            ui.print_warn("未找到支持的文档文件。")
            return
        ui.print_info(f"共发现 {len(files)} 个文件，开始批量校验修复...")
        for i, f in enumerate(files, 1):
            try:
                _errors, _domain, repaired, out_path = self._check_fix_core(
                    f, rules_path, config
                )
                if out_path:
                    ui.print_batch_progress(i, len(files), os.path.basename(f), "ok",
                                            f"修复完成 ({repaired} 处)")
                else:
                    ui.print_batch_progress(i, len(files), os.path.basename(f), "skip", "非 .docx")
            except Exception as exc:
                ui.print_batch_progress(i, len(files), os.path.basename(f), "fail", f"修复失败: {exc}")
        ui.print_good("批量校验修复完成。")

    def cmd_annotate(self, args):
        """标注上次校验结果。"""
        if not self.last_errors or not self.last_file:
            ui.print_warn("没有可标注的校验结果，请先运行 /check。")
            return
        stem = os.path.splitext(os.path.basename(self.last_file))[0]
        out_path = resolve("annotated", f"{stem}_annotated.docx")
        ensure_parent(out_path)
        ui.print_info("标注中...")
        ok = annotate_document(self.last_file, out_path, self.last_errors)
        if ok:
            ui.print_good(f"标注完成: {out_path}")
        else:
            ui.print_bad("标注失败: 仅支持 .docx 文件。")

    def cmd_test_api(self, args):
        """测试 LLM API 配置是否有效（发一个最小请求）。"""
        from engine.llm_client import LLMClient

        client = LLMClient()
        if not client.is_configured():
            ui.print_warn("尚未配置 LLM API Key。请先用 /set-key 设置。")
            return
        ui.print_info(f"测试 API 连接中... (base_url: {client.base_url}, model: {client.model})")
        ok, message = client.test_connection()
        if ok:
            ui.print_good(message)
        else:
            ui.print_bad(message)

    def cmd_test(self, args):
        """端到端自检：生成样例文档，跑 校验→格式化→修复→批注 全流程。"""
        import tempfile

        from docx import Document

        ui.print_info("端到端自检开始（不调用 LLM，不消耗 token）")
        tmp_dir = tempfile.mkdtemp(prefix="pfm_test_")
        results = []

        def step(name, ok, detail=""):
            results.append(ok)
            ui.print_test_step(name, ok, detail)

        # 1. 生成样例 .docx
        sample_path = os.path.join(tmp_dir, "sample.docx")
        try:
            doc = Document()
            doc.add_paragraph("测试标题", style="Title")
            doc.add_paragraph("第一章 引言", style="Heading 1")
            doc.add_paragraph("这是正文段落，用于测试校验功能。")
            doc.add_paragraph("第二章 方法", style="Heading 1")
            doc.add_paragraph("正文内容继续。")
            doc.save(sample_path)
            step("生成样例文档", True, sample_path)
        except Exception as exc:
            step("生成样例文档", False, str(exc))
            ui.print_bad("样例文档生成失败，终止自检。")
            return

        # 2. 校验 /check
        rules_path = self.rules_path
        if not os.path.exists(rules_path):
            # 兜底：找 config 下任意 rules_*.yaml
            import glob
            candidates = sorted(glob.glob(resolve("config", "rules_*.yaml")))
            if candidates:
                rules_path = candidates[0]
        try:
            errors, domain, _rd = self._validate_file(sample_path, rules_path)
            step("校验 /check", True, f"{len(errors)} 处问题, 领域={domain or 'N/A'}")
        except Exception as exc:
            step("校验 /check", False, str(exc))

        # 3. 格式化 /format
        formatted_path = os.path.join(tmp_dir, "sample_formatted.docx")
        try:
            engine = FormatEngine(self.config)
            engine.run(sample_path, formatted_path)
            ok_fmt = os.path.exists(formatted_path)
            step("格式化 /format", ok_fmt, formatted_path if ok_fmt else "未生成产物")
        except Exception as exc:
            step("格式化 /format", False, str(exc))

        # 4. 校验并修复 /check-fix
        try:
            _errors, _domain, repaired, out_path = self._check_fix_core(
                sample_path, rules_path, self.config
            )
            ok_fix = out_path is not None and os.path.exists(out_path)
            step("校验修复 /check-fix", ok_fix,
                 f"修复 {repaired} 处" if ok_fix else "未生成产物")
        except Exception as exc:
            step("校验修复 /check-fix", False, str(exc))

        # 5. 批注 /annotate（基于上次校验结果）
        try:
            if self.last_errors and self.last_file:
                anno_path = os.path.join(tmp_dir, "sample_annotated.docx")
                ok_anno = annotate_document(self.last_file, anno_path, self.last_errors)
                step("批注 /annotate", ok_anno, anno_path if ok_anno else "仅支持 .docx")
            else:
                step("批注 /annotate", False, "无上次校验结果")
        except Exception as exc:
            step("批注 /annotate", False, str(exc))

        ui.print_test_summary(sum(results), len(results), tmp_dir)

    def cmd_chat(self, args):
        """进入与 LLM 的多轮对话子界面。输入 exit 退出。"""
        from engine.llm_client import LLMClient

        client = LLMClient()
        if not client.is_configured():
            ui.print_warn("尚未配置 LLM API Key。请先用 /set-key 设置。")
            return
        ui.print_info(f"进入 LLM 对话模式 (model: {client.model})  exit 退出, /clear 清空")
        ui.console.print("[dim]" + "-" * 56 + "[/dim]")

        system_prompt = (
            "你是一个文档格式校验与排版助手。用户会询问规则编写、格式规范、"
            "YAML 规则文件结构等问题。请用中文简洁回答。"
        )
        messages = [{"role": "system", "content": system_prompt}]

        while True:
            try:
                user_input = input("你> ").strip()
            except (EOFError, KeyboardInterrupt):
                ui.print_info("退出对话。")
                break
            if not user_input:
                continue
            if user_input.lower() in ("exit", "quit", "退出"):
                ui.print_info("退出对话。")
                break
            if user_input.lower() == "/clear":
                messages = [{"role": "system", "content": system_prompt}]
                ui.print_info("[上下文已清空]")
                continue

            messages.append({"role": "user", "content": user_input})
            ui.console.print("[bold green]AI>[/bold green] ", end="")
            try:
                reply = client.chat_messages(messages)
                ui.console.print(reply)
                messages.append({"role": "assistant", "content": reply})
            except Exception as exc:
                ui.console.print()
                ui.print_bad(f"对话失败: {exc}")
                # 移除刚才加入的用户消息，避免坏消息留在上下文里
                if messages and messages[-1]["role"] == "user":
                    messages.pop()

    def cmd_save_config(self, args):
        """保存当前配置到 JSON 文件。"""
        path, _flags = self._parse_args(args)
        if not path:
            ui.print_warn("用法: /save-config <文件路径>")
            return
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self.config.merged, fh, ensure_ascii=False, indent=2)
        ui.print_good(f"配置已保存: {path}")

    def cmd_load_config(self, args):
        """从 JSON 文件加载配置。"""
        path, _flags = self._parse_args(args)
        if not path:
            ui.print_warn("用法: /load-config <文件路径>")
            return
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        self.config = Config("official", overrides=data)
        ui.print_good(f"配置已加载: {path}")

    def cmd_set_key(self, args):
        """设置 LLM API Key（OpenAI 兼容接口）。"""
        from engine.llm_client import LLMClient

        client = LLMClient()
        ui.print_info("设置 LLM API（OpenAI 兼容接口，支持 DeepSeek/OpenAI/Kimi/GLM 等）")
        ui.console.print(f"  当前 base_url: [dim]{client.base_url}[/dim]")
        ui.console.print(f"  当前 model:    [dim]{client.model}[/dim]")
        ui.console.print("  直接回车保留当前值，输入新值则覆盖。")

        try:
            api_key = input("API Key: ").strip()
            if not api_key and client.is_configured():
                api_key = client.api_key
            if not api_key:
                ui.print_warn("未提供 API Key，取消设置。")
                return

            base_url = input(f"Base URL [{client.base_url}]: ").strip()
            if not base_url:
                base_url = client.base_url

            model = input(f"Model [{client.model}]: ").strip()
            if not model:
                model = client.model
        except (EOFError, KeyboardInterrupt):
            ui.print_warn("取消设置。")
            return

        client.set_config(api_key, base_url, model)
        ui.print_good(f"API 配置已保存到 {client.config_path}")
        ui.console.print(f"  base_url: [dim]{client.base_url}[/dim]")
        ui.console.print(f"  model:    [dim]{client.model}[/dim]")
        masked = '*' * (len(api_key) - 4) + api_key[-4:] if len(api_key) >= 4 else '****'
        ui.console.print(f"  api_key:  [dim]{masked}[/dim]")

    def cmd_get_rules(self, args):
        """用 LLM 从模板文件或文本生成 YAML 规则文件。"""
        path, flags = self._parse_args(args)

        if not path and "--text" not in args:
            ui.print_warn("用法:")
            ui.console.print("  /get-rules <模板文件>          从 Word/txt 文件生成规则")
            ui.console.print('  /get-rules --text "规范文本"   直接输入文本生成规则')
            ui.console.print("  可选: --output <path>  指定输出路径")
            ui.console.print("  可选: --domain <名称>  指定领域名称（如 毕业论文）")
            return

        from engine.llm_client import LLMClient
        from engine.rule_generator import RuleGenerator

        client = LLMClient()
        if not client.is_configured():
            ui.print_warn("尚未配置 LLM API Key。请先用 /set-key 设置。")
            ui.console.print("支持任何 OpenAI 兼容接口（DeepSeek/OpenAI/Kimi/GLM 等）。")
            return

        output_path = flags.get("output")
        domain_name = flags.get("domain")
        generator = RuleGenerator(client)

        try:
            if path:
                if not os.path.exists(path):
                    ui.print_bad(f"文件不存在: {path}")
                    return
                ui.print_info(f"正在从文件生成规则: {path}")
                ui.console.print(f"  模型: [dim]{client.model}[/dim]")
                ui.console.print("  提取格式信息中...")
                saved_path = generator.generate_from_file(
                    path, output_path=output_path, domain_name=domain_name,
                )
            else:
                text_start = args.find('"')
                text_end = args.rfind('"')
                if text_start == -1 or text_end == text_start:
                    ui.print_warn('请用引号包裹文本: /get-rules --text "规范内容..."')
                    return
                text = args[text_start + 1:text_end]
                if not text.strip():
                    ui.print_warn("文本为空。")
                    return
                ui.print_info(f"正在从文本生成规则（{len(text)} 字符）...")
                ui.console.print(f"  模型: [dim]{client.model}[/dim]")
                saved_path = generator.generate_from_text(
                    text, output_path=output_path, domain_name=domain_name,
                )

            ui.print_good(f"规则文件已生成: {saved_path}")
            ui.console.print(f"  可用 /check <文件> --rules {saved_path} 立即验证。")
        except Exception as exc:
            ui.print_bad(f"生成规则失败: {exc}")

    def cmd_quit(self, args):
        """退出。"""
        ui.print_farewell()


def main():
    CLI().run()


if __name__ == "__main__":
    main()
