"""OpenAI-compatible LLM API client for rule generation.

Works with any OpenAI-compatible endpoint: DeepSeek, OpenAI, Kimi, GLM, etc.
"""

import os
import yaml

from engine.paths import resolve

DEFAULT_CONFIG_PATH = resolve("config", "api_config.yaml")
DEFAULT_MODEL = "deepseek-chat"
DEFAULT_BASE_URL = "https://api.deepseek.com/v1"


class LLMClient:
    """OpenAI-compatible API client.

    Config file (config/api_config.yaml) structure:
        api_key: "sk-xxxx"
        base_url: "https://api.deepseek.com/v1"  # optional
        model: "deepseek-chat"                    # optional
    """

    def __init__(self, config_path=None):
        self.config_path = config_path or DEFAULT_CONFIG_PATH
        self.api_key = None
        self.base_url = DEFAULT_BASE_URL
        self.model = DEFAULT_MODEL
        self._client = None
        self._load_config()

    def _load_config(self):
        """Load API config from YAML file. Does NOT raise if file missing."""
        if not os.path.exists(self.config_path):
            return
        try:
            with open(self.config_path, "r", encoding="utf-8") as fh:
                data = yaml.safe_load(fh) or {}
            if data.get("api_key"):
                self.api_key = data["api_key"]
            if data.get("base_url"):
                self.base_url = data["base_url"]
            if data.get("model"):
                self.model = data["model"]
        except Exception:
            pass

    def set_config(self, api_key, base_url=None, model=None):
        """Update config and save to file."""
        self.api_key = api_key
        if base_url is not None:
            self.base_url = base_url
        if model is not None:
            self.model = model
        config_dir = os.path.dirname(self.config_path)
        if config_dir:
            os.makedirs(config_dir, exist_ok=True)
        data = {
            "api_key": self.api_key,
            "base_url": self.base_url,
            "model": self.model,
        }
        with open(self.config_path, "w", encoding="utf-8") as fh:
            yaml.safe_dump(data, fh, allow_unicode=True, default_flow_style=False)
        self._client = None

    def is_configured(self):
        """Return True if api_key is set."""
        return bool(self.api_key)

    def chat(self, system_prompt, user_prompt, temperature=0.3):
        """Call the LLM API and return the response text.

        Uses the ``openai`` Python SDK with a custom base_url so it works
        with any OpenAI-compatible endpoint (DeepSeek, OpenAI, Kimi, GLM, …).

        Returns:
            The assistant's message content as a string.

        Raises:
            RuntimeError: if not configured or the API call fails.
        """
        if not self.is_configured():
            raise RuntimeError(
                "LLM client not configured. Call set_config() first."
            )
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError(
                "openai package not installed. Run: pip install openai"
            ) from exc
        if self._client is None:
            self._client = OpenAI(
                api_key=self.api_key, base_url=self.base_url
            )
        try:
            response = self._client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=temperature,
            )
            return response.choices[0].message.content
        except Exception as exc:
            raise RuntimeError(f"LLM API call failed: {exc}") from exc

    def chat_messages(self, messages, temperature=0.5):
        """多轮对话：传入完整 messages 列表，返回助手回复文本。

        Args:
            messages: OpenAI 消息格式列表，如
                [{"role": "system", "content": "..."},
                 {"role": "user", "content": "..."},
                 {"role": "assistant", "content": "..."},
                 {"role": "user", "content": "..."}]
            temperature: 采样温度。

        Returns:
            助手回复的文本内容。

        Raises:
            RuntimeError: 未配置或 API 调用失败。
        """
        if not self.is_configured():
            raise RuntimeError(
                "LLM client not configured. Call set_config() first."
            )
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError(
                "openai package not installed. Run: pip install openai"
            ) from exc
        if self._client is None:
            self._client = OpenAI(
                api_key=self.api_key, base_url=self.base_url
            )
        try:
            response = self._client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=temperature,
            )
            return response.choices[0].message.content
        except Exception as exc:
            raise RuntimeError(f"LLM API call failed: {exc}") from exc

    def test_connection(self):
        """发一个最小请求测试 API 配置是否有效。

        Returns:
            (ok: bool, message: str) —— ok 为 True 时 message 是模型回复，
            ok 为 False 时 message 是错误说明。
        """
        if not self.is_configured():
            return False, "未配置 API Key，请先用 /set-key 设置。"
        try:
            reply = self.chat(
                system_prompt="You are a connection test assistant.",
                user_prompt="请回复：ok",
                temperature=0.0,
            )
            return True, f"连接成功。模型 {self.model} 回复: {reply.strip()[:80]}"
        except Exception as exc:
            return False, f"连接失败: {exc}"
