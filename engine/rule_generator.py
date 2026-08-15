"""Rule file generator: extracts document format, calls LLM, produces YAML rules."""

import os
import re
import logging

import yaml

from engine.llm_client import LLMClient
from engine.paths import resolve

logger = logging.getLogger(__name__)


def _prompt_template_path() -> str:
    """返回 prompt_template.md 的绝对路径（惰性求值）。"""
    return resolve("config", "prompt_template.md")


class RuleGenerator:
    """Generates YAML rule files from documents using LLM.

    Workflow:
    1. Extract format info from a Word template (.docx/.doc) OR read text file
    2. Build a prompt using config/prompt_template.md
    3. Call LLM via LLMClient
    4. Parse and save the YAML output
    """

    def __init__(self, llm_client: LLMClient):
        """Args: llm_client -- an LLMClient instance."""
        self.llm = llm_client

    def generate_from_docx(self, docx_path, output_path=None, domain_name=None):
        """Generate rules from a Word template document.

        Steps:
        1. Use engine.extractors.get_extractor to extract paragraphs with format info
        2. Format each paragraph as: [index] text [size|font|align|line_spacing]
        3. Build user_prompt with this formatted text
        4. Call LLM with system_prompt (from prompt_template.md) + user_prompt
        5. Parse YAML from response
        6. Save to output_path (default: config/rules_<domain>.yaml)

        Returns: output_path
        """
        from engine.extractors import get_extractor

        data = get_extractor(docx_path).extract(docx_path)
        paragraphs = data.get("paragraphs", [])

        user_prompt = self._format_paragraphs_for_llm(paragraphs)
        if not user_prompt.strip():
            raise ValueError(f"No non-empty paragraphs found in: {docx_path}")

        system_prompt = self._load_prompt_template()

        response = self.llm.chat(system_prompt, user_prompt)

        yaml_text = self._extract_yaml_from_response(response)
        parsed = yaml.safe_load(yaml_text)
        if not isinstance(parsed, dict) or "segments" not in parsed or "rules" not in parsed:
            raise ValueError(
                "Generated YAML missing required top-level keys 'segments' and 'rules'"
            )

        if output_path is None:
            output_path = self._default_output_path(domain_name)

        output_dir = os.path.dirname(output_path)
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as fh:
            fh.write(yaml_text)
        logger.info("Rule file saved to %s", output_path)
        return output_path

    def generate_from_text(self, text, output_path=None, domain_name=None):
        """Generate rules from a plain-text format specification.

        Same as generate_from_docx but uses the given text directly as user_prompt.
        """
        system_prompt = self._load_prompt_template()

        response = self.llm.chat(system_prompt, text)

        yaml_text = self._extract_yaml_from_response(response)
        parsed = yaml.safe_load(yaml_text)
        if not isinstance(parsed, dict) or "segments" not in parsed or "rules" not in parsed:
            raise ValueError(
                "Generated YAML missing required top-level keys 'segments' and 'rules'"
            )

        if output_path is None:
            output_path = self._default_output_path(domain_name)

        output_dir = os.path.dirname(output_path)
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as fh:
            fh.write(yaml_text)
        logger.info("Rule file saved to %s", output_path)
        return output_path

    def generate_from_file(self, file_path, output_path=None, domain_name=None):
        """Auto-detect: if .docx/.doc -> generate_from_docx, if .txt/.md -> read & generate_from_text."""
        lower = file_path.lower()
        if lower.endswith((".docx", ".doc")):
            return self.generate_from_docx(file_path, output_path, domain_name)
        if lower.endswith((".txt", ".md")):
            with open(file_path, "r", encoding="utf-8") as fh:
                text = fh.read()
            return self.generate_from_text(text, output_path, domain_name)
        raise ValueError(f"Unsupported file format: {file_path}")

    @staticmethod
    def _format_paragraphs_for_llm(paragraphs, max_paras=100):
        """Format extracted paragraphs into a readable text block for the LLM.

        Output format:
        段落 0: 测试标题
          [字号: 18.0pt | 字体: 黑体 | 对齐: center | 行距: 1.5 | 样式: Heading 1]
        段落 1: 这是正文内容。
          [字号: 12.0pt | 字体: 宋体 | 对齐: justify | 行距: 1.5]
        ...

        Skip paragraphs with empty text.
        Limit to max_paras paragraphs.
        """
        lines = []
        count = 0
        for para in paragraphs:
            text = para.get("text") or ""
            if not text.strip():
                continue
            if count >= max_paras:
                break
            index = para.get("index", count)
            size = para.get("size")
            font = para.get("font")
            align = para.get("align") or para.get("alignment")
            line_spacing = para.get("line_spacing")
            style = para.get("style")

            parts = []
            if size is not None:
                parts.append(f"字号: {size}pt")
            if font:
                parts.append(f"字体: {font}")
            if align:
                parts.append(f"对齐: {align}")
            if line_spacing is not None:
                parts.append(f"行距: {line_spacing}")
            if style:
                parts.append(f"样式: {style}")

            lines.append(f"段落 {index}: {text}")
            lines.append(f"  [{' | '.join(parts)}]")
            count += 1
        return "\n".join(lines)

    @staticmethod
    def _load_prompt_template():
        """Load config/prompt_template.md and split into (system_prompt, has_placeholder).

        The template has a "{{模板文本}}" placeholder. Everything before it
        (after the "---" separator) is the system prompt.
        Returns the system prompt text.
        """
        with open(_prompt_template_path(), "r", encoding="utf-8") as fh:
            content = fh.read()
        placeholder = "{{模板文本}}"
        # The placeholder may appear in the usage header too; find it after the
        # "---" separator so we extract the actual prompt, not the header.
        sep_idx = content.find("---")
        if sep_idx == -1:
            idx = content.find(placeholder)
            if idx == -1:
                return content.strip()
            return content[:idx].strip()
        after_sep = content[sep_idx + 3:]
        idx = after_sep.find(placeholder)
        if idx == -1:
            return after_sep.strip()
        return after_sep[:idx].strip()

    @staticmethod
    def _extract_yaml_from_response(response_text):
        """Extract YAML content from LLM response.

        Handles:
        - Raw YAML (starts with "schema:" or "segments:")
        - YAML in markdown code blocks (```yaml ... ```)
        - YAML with leading/trailing explanatory text

        Returns: the YAML string
        Raises: ValueError if no YAML found
        """
        # Try ```yaml ... ``` or ``` ... ``` code block
        match = re.search(r"```(?:ya?ml)?\s*\n(.*?)```", response_text, re.DOTALL)
        if match:
            return match.group(1).strip()
        # Try raw YAML starting with schema: or segments:
        for key in ("schema:", "segments:"):
            idx = response_text.find(key)
            if idx != -1:
                yaml_text = response_text[idx:].strip()
                # Strip trailing markdown separator if present
                if yaml_text.endswith("---"):
                    yaml_text = yaml_text[:-3].strip()
                return yaml_text
        raise ValueError("No YAML content found in LLM response")

    @staticmethod
    def _default_output_path(domain_name):
        """Generate default output path: config/rules_<domain>.yaml"""
        name = domain_name or "custom"
        return resolve("config", f"rules_{name}.yaml")
