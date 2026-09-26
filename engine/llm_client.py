"""OpenAI-compatible LLM API client for rule generation.

Works with any OpenAI-compatible endpoint: DeepSeek, OpenAI, Kimi, GLM, etc.
"""

import os
import yaml

from engine.paths import resolve

DEFAULT_MODEL = "deepseek-chat"
DEFAULT_BASE_URL = "https://api.deepseek.com/v1"


def _default_config_path() -> str:
    """返回 config/api_config.yaml 的绝对路径。

    用函数而非模块级常量，避免在 Nuitka onefile 下、sys.argv 填充之前
    的极早期导入阶段过早求值。
    """
    return resolve("config", "api_config.yaml")


class LLMClient:
    """OpenAI-compatible API client.

    Configuration resolution, in order:

    1. An explicit ``profile`` dict passed to the constructor.
    2. The active profile in ``config/llm_profiles.yaml`` (named 方案 managed
       by the GUI's LLM settings dialog).
    3. The legacy flat ``config/api_config.yaml`` file, so existing
       installations and the CLI ``/set-key`` flow keep working unchanged.
    """

    def __init__(self, config_path=None, profile=None):
        self.config_path = config_path or _default_config_path()
        self.api_key = None
        self.base_url = DEFAULT_BASE_URL
        self.model = DEFAULT_MODEL
        self.profile_name = ""
        self._client = None
        if profile is not None:
            self._apply_profile(profile)
        else:
            self._load_active()

    def _apply_profile(self, profile):
        """Adopt one profile dict (see ``engine.llm_profiles.normalize_profile``)."""
        from engine import llm_profiles
        p = llm_profiles.normalize_profile(profile)
        self.profile_name = p["name"]
        if p["api_key"]:
            self.api_key = p["api_key"]
        if p["base_url"]:
            self.base_url = p["base_url"]
        if p["model"]:
            self.model = p["model"]

    def _load_active(self):
        """Store first, legacy flat file second. Never raises."""
        from engine import llm_profiles
        profile = llm_profiles.active_profile()
        if profile:
            self._apply_profile(profile)
            return
        self._load_legacy()

    def _load_legacy(self):
        """Load the legacy flat config file. Does NOT raise if file missing."""
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

    def set_config(self, api_key, base_url=None, model=None, profile_name=None):
        """Update config and save it as an active named profile.

        ``profile_name`` defaults to the currently active name, so calling this
        repeatedly edits the same 方案 rather than piling up duplicates.
        """
        from engine import llm_profiles
        name = profile_name or self.profile_name or "默认"
        self.api_key = api_key
        if base_url is not None:
            self.base_url = base_url
        if model is not None:
            self.model = model
        self.profile_name = name
        llm_profiles.upsert_profile({
            "name": name,
            "base_url": self.base_url,
            "api_key": self.api_key,
            "model": self.model,
        })
        llm_profiles.set_active(name)
        self._client = None

    def is_configured(self):
        """Return True if api_key is set."""
        return bool(self.api_key)

    def _call_api(self, messages: list[dict], temperature: float) -> str:
        """Internal: send ``messages`` to the configured OpenAI-compatible API.

        Lazily instantiates the ``openai.OpenAI`` client on first use and
        caches it for the lifetime of this ``LLMClient`` instance.

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
                messages=messages,
                temperature=temperature,
            )
            return response.choices[0].message.content
        except Exception as exc:
            raise RuntimeError(f"LLM API call failed: {exc}") from exc

    def chat(self, system_prompt, user_prompt, temperature=0.3):
        """Call the LLM API and return the response text.

        Uses the ``openai`` Python SDK with a custom base_url so it works
        with any OpenAI-compatible endpoint (DeepSeek, OpenAI, Kimi, GLM, …).

        Returns:
            The assistant's message content as a string.

        Raises:
            RuntimeError: if not configured or the API call fails.
        """
        return self._call_api(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature,
        )

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
        return self._call_api(messages, temperature)

    def test_connection(self):
        """发一个最小请求测试 API 配置是否有效。

        Returns:
            (ok: bool, message: str) —— ok 为 True 时 message 是模型回复，
            ok 为 False 时 message 是错误说明。
        """
        if not self.is_configured():
            return False, (
                "未配置 LLM API Key。请在「设置 → LLM 设置」里新建一个方案，"
                "或在 CLI 里用 /set-key 设置。"
            )
        try:
            reply = self.chat(
                system_prompt="You are a connection test assistant.",
                user_prompt="请回复：ok",
                temperature=0.0,
            )
            return True, f"连接成功。模型 {self.model} 回复: {reply.strip()[:80]}"
        except Exception as exc:
            return False, f"连接失败: {exc}"
