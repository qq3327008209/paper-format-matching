"""LLM 方案（profile）存储与解析的测试。

覆盖三件事：
1. ``config/llm_profiles.yaml`` 的多方案读写（新建/编辑/删除/设为当前）。
2. ``config/api_config.yaml`` 这份遗留平铺配置仍能作为兜底方案被读到，
   老安装无需迁移步骤就能继续用。
3. ``LLMClient`` 的解析优先级：显式 profile → 方案库当前方案 → 遗留文件。

所有测试把 ``engine.paths.BASE_DIR`` 指到临时目录，不碰真实的 config/。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
import yaml

from engine import llm_client, llm_profiles
from engine.llm_client import LLMClient
import engine.paths as paths


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    """把配置目录指到临时目录，保证测试之间互不污染。"""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    monkeypatch.setattr(paths, "BASE_DIR", str(tmp_path))
    llm_profiles.clear_store()
    yield tmp_path
    llm_profiles.clear_store()


def _store_path():
    return llm_profiles.profile_store_path()


def _write_legacy(api_key="sk-legacy", base_url="https://api.deepseek.com/v1",
                  model="deepseek-chat"):
    data = {"api_key": api_key, "base_url": base_url, "model": model}
    with open(llm_profiles.legacy_config_path(), "w", encoding="utf-8") as fh:
        yaml.safe_dump(data, fh, allow_unicode=True)
    return data


# ---------------------------------------------------------------------------
# normalize_profile
# ---------------------------------------------------------------------------

class TestNormalizeProfile:
    def test_missing_keys_become_empty_strings(self):
        p = llm_profiles.normalize_profile({"name": "A"})
        assert p == {"name": "A", "base_url": "", "api_key": "", "model": ""}

    def test_blank_name_gets_a_placeholder(self):
        assert llm_profiles.normalize_profile({"name": "  "})["name"] == "未命名"
        assert llm_profiles.normalize_profile({})["name"] == "未命名"

    def test_values_are_stripped(self):
        p = llm_profiles.normalize_profile(
            {"name": "  DeepSeek  ", "api_key": "  sk-1  ", "base_url": " u ", "model": " m "})
        assert p["name"] == "DeepSeek"
        assert p["api_key"] == "sk-1"
        assert p["base_url"] == "u"
        assert p["model"] == "m"

    def test_non_dict_input_is_all_empty(self):
        p = llm_profiles.normalize_profile(None)
        assert p["api_key"] == ""
        assert p["name"] == "未命名"

    def test_input_is_not_mutated(self):
        raw = {"name": "A", "base_url": "b", "api_key": "k", "model": "m"}
        before = dict(raw)
        llm_profiles.normalize_profile(raw)
        assert raw == before


# ---------------------------------------------------------------------------
# 读写往返
# ---------------------------------------------------------------------------

class TestStoreRoundTrip:
    def test_empty_store_reports_nothing(self):
        assert llm_profiles.list_profiles() == []
        assert llm_profiles.profile_names() == []
        assert llm_profiles.active_name() == ""
        assert llm_profiles.active_profile() is None
        assert llm_profiles.get_profile("无") is None

    def test_upsert_then_list(self):
        llm_profiles.upsert_profile(
            {"name": "DeepSeek", "base_url": "u1", "api_key": "k1", "model": "m1"})
        assert llm_profiles.profile_names() == ["DeepSeek"]
        got = llm_profiles.get_profile("DeepSeek")
        assert got == {"name": "DeepSeek", "base_url": "u1", "api_key": "k1", "model": "m1"}

    def test_multiple_profiles_keep_file_order(self):
        for name, key in (("A", "1"), ("B", "2"), ("C", "3")):
            llm_profiles.upsert_profile(
                {"name": name, "base_url": "u", "api_key": key, "model": "m"})
        assert llm_profiles.profile_names() == ["A", "B", "C"]
        assert len(llm_profiles.list_profiles()) == 3

    def test_upsert_same_name_replaces_instead_of_duplicating(self):
        llm_profiles.upsert_profile(
            {"name": "A", "base_url": "old", "api_key": "k1", "model": "m1"})
        llm_profiles.upsert_profile(
            {"name": "A", "base_url": "new", "api_key": "k2", "model": "m2"})
        assert llm_profiles.profile_names() == ["A"]
        assert llm_profiles.get_profile("A")["api_key"] == "k2"
        assert llm_profiles.get_profile("A")["base_url"] == "new"

    def test_upsert_persists_to_disk(self):
        llm_profiles.upsert_profile(
            {"name": "A", "base_url": "u", "api_key": "k", "model": "m"})
        assert os.path.exists(_store_path())
        data = yaml.safe_load(open(_store_path(), encoding="utf-8"))
        assert data["profiles"][0]["name"] == "A"
        assert "api_key" in data["profiles"][0]

    def test_upsert_without_name_is_rejected(self):
        with pytest.raises(ValueError):
            llm_profiles.upsert_profile({"name": "  "})

    def test_corrupted_store_is_treated_as_empty(self):
        with open(_store_path(), "w", encoding="utf-8") as fh:
            fh.write("profiles: not-a-list\nactive: A\n")
        assert llm_profiles.list_profiles() == []
        assert llm_profiles.active_name() == ""

    def test_unreadable_store_is_treated_as_empty(self):
        with open(_store_path(), "w", encoding="utf-8") as fh:
            fh.write("{{{\n  : : :")
        assert llm_profiles.list_profiles() == []


# ---------------------------------------------------------------------------
# 删除
# ---------------------------------------------------------------------------

class TestDelete:
    def _two(self):
        for name in ("A", "B"):
            llm_profiles.upsert_profile(
                {"name": name, "base_url": "u", "api_key": name, "model": "m"})
        return True

    def test_delete_removes_entry(self):
        self._two()
        assert llm_profiles.delete_profile("A") is True
        assert llm_profiles.profile_names() == ["B"]

    def test_delete_missing_name_is_a_noop(self):
        self._two()
        assert llm_profiles.delete_profile("Z") is False
        assert llm_profiles.profile_names() == ["A", "B"]

    def test_deleting_active_falls_back_to_first_remaining(self):
        self._two()
        llm_profiles.set_active("B")
        llm_profiles.delete_profile("B")
        assert llm_profiles.active_name() == "A"

    def test_deleting_everything_clears_active(self):
        self._two()
        llm_profiles.delete_profile("A")
        llm_profiles.delete_profile("B")
        assert llm_profiles.active_name() == ""
        assert llm_profiles.active_profile() is None


# ---------------------------------------------------------------------------
# 当前方案选择
# ---------------------------------------------------------------------------

class TestActiveSelection:
    def test_first_upsert_becomes_active(self):
        llm_profiles.upsert_profile(
            {"name": "A", "base_url": "u", "api_key": "k", "model": "m"})
        assert llm_profiles.active_name() == "A"
        assert llm_profiles.active_profile()["name"] == "A"

    def test_set_active_switches(self):
        for name in ("A", "B"):
            llm_profiles.upsert_profile(
                {"name": name, "base_url": "u", "api_key": name, "model": "m"})
        assert llm_profiles.set_active("B") is True
        assert llm_profiles.active_name() == "B"
        assert llm_profiles.active_profile()["api_key"] == "B"

    def test_set_active_unknown_name_fails(self):
        llm_profiles.upsert_profile(
            {"name": "A", "base_url": "u", "api_key": "k", "model": "m"})
        assert llm_profiles.set_active("幽灵") is False
        assert llm_profiles.active_name() == "A"

    def test_stale_active_falls_back_to_first_profile(self):
        """方案库里 active 指向一个已不存在的名字时，落到第一个方案。"""
        for name in ("A", "B"):
            llm_profiles.upsert_profile(
                {"name": name, "base_url": "u", "api_key": name, "model": "m"})
        with open(_store_path(), "w", encoding="utf-8") as fh:
            yaml.safe_dump({"active": "幽灵",
                            "profiles": [{"name": "A", "base_url": "u", "api_key": "1",
                                          "model": "m"},
                                         {"name": "B", "base_url": "u", "api_key": "2",
                                          "model": "m"}]},
                           fh, allow_unicode=True)
        assert llm_profiles.active_name() == "A"

    def test_renaming_the_active_profile_moves_active(self):
        """改名的方案若正是当前方案，active 跟着新名字走，不留悬空引用。"""
        for name in ("A", "B"):
            llm_profiles.upsert_profile(
                {"name": name, "base_url": "u", "api_key": name, "model": "m"})
        llm_profiles.set_active("B")
        llm_profiles.upsert_profile(
            {"name": "网关B", "base_url": "u", "api_key": "2", "model": "m"},
            old_name="B")
        assert llm_profiles.active_name() == "网关B"

    def test_adding_a_profile_does_not_steal_active(self):
        """新增一个方案是「多存一个」，不该把当前连接偷偷换掉。"""
        for name in ("A", "B"):
            llm_profiles.upsert_profile(
                {"name": name, "base_url": "u", "api_key": name, "model": "m"})
        llm_profiles.set_active("A")
        llm_profiles.upsert_profile(
            {"name": "C", "base_url": "u", "api_key": "3", "model": "m"})
        assert llm_profiles.active_name() == "A"
        assert llm_profiles.active_profile()["api_key"] == "A"

    def test_rename_does_not_leave_a_duplicate_behind(self):
        """「改名」不能变成「多出一条」：旧名条目要被删掉。"""
        for name in ("A", "B"):
            llm_profiles.upsert_profile(
                {"name": name, "base_url": "u", "api_key": name, "model": "m"})
        llm_profiles.upsert_profile(
            {"name": "B-新", "base_url": "u", "api_key": "2", "model": "m"},
            old_name="B")
        assert llm_profiles.profile_names() == ["A", "B-新"]

    def test_editing_without_renaming_removes_nothing_extra(self):
        llm_profiles.upsert_profile(
            {"name": "A", "base_url": "u", "api_key": "1", "model": "m"})
        llm_profiles.upsert_profile(
            {"name": "B", "base_url": "u", "api_key": "2", "model": "m"})
        llm_profiles.set_active("A")
        llm_profiles.upsert_profile(
            {"name": "A", "base_url": "u2", "api_key": "9", "model": "m2"}, old_name="A")
        assert llm_profiles.profile_names() == ["A", "B"]
        assert llm_profiles.active_name() == "A"
        assert llm_profiles.get_profile("A")["api_key"] == "9"

    def test_editing_keeps_the_profiles_position(self):
        """就地编辑不该让条目跳到列表末尾，绑着这个列表的弹窗才不会乱跳。"""
        for name in ("A", "B", "C"):
            llm_profiles.upsert_profile(
                {"name": name, "base_url": "u", "api_key": name, "model": "m"})
        llm_profiles.upsert_profile(
            {"name": "A", "base_url": "u", "api_key": "改动", "model": "m"}, old_name="A")
        assert llm_profiles.profile_names() == ["A", "B", "C"]
        assert llm_profiles.get_profile("A")["api_key"] == "改动"

    def test_renaming_keeps_the_old_position(self):
        for name in ("A", "B", "C"):
            llm_profiles.upsert_profile(
                {"name": name, "base_url": "u", "api_key": name, "model": "m"})
        llm_profiles.upsert_profile(
            {"name": "乙", "base_url": "u", "api_key": "2", "model": "m"}, old_name="B")
        assert llm_profiles.profile_names() == ["A", "乙", "C"]


# ---------------------------------------------------------------------------
# 遗留 api_config.yaml 兜底
# ---------------------------------------------------------------------------

class TestLegacyFallback:
    def test_no_store_reads_legacy_file(self):
        _write_legacy(api_key="sk-legacy")
        profile = llm_profiles.active_profile()
        assert profile is not None
        assert profile["name"] == llm_profiles.LEGACY_PROFILE_NAME
        assert profile["api_key"] == "sk-legacy"
        assert profile["model"] == "deepseek-chat"

    def test_legacy_file_without_key_yields_none(self):
        with open(llm_profiles.legacy_config_path(), "w", encoding="utf-8") as fh:
            yaml.safe_dump({"api_key": "", "base_url": "u", "model": "m"}, fh)
        assert llm_profiles.legacy_profile() is None
        assert llm_profiles.active_profile() is None

    def test_missing_legacy_file_yields_none(self):
        assert llm_profiles.legacy_profile() is None

    def test_store_takes_precedence_over_legacy(self):
        _write_legacy(api_key="sk-legacy")
        llm_profiles.upsert_profile(
            {"name": "自定", "base_url": "u2", "api_key": "sk-new", "model": "m2"})
        profile = llm_profiles.active_profile()
        assert profile["api_key"] == "sk-new"
        assert profile["name"] == "自定"

    def test_store_write_does_not_touch_legacy_file(self):
        _write_legacy(api_key="sk-legacy")
        before = open(llm_profiles.legacy_config_path(), encoding="utf-8").read()
        llm_profiles.upsert_profile(
            {"name": "新方案", "base_url": "u", "api_key": "sk-x", "model": "m"})
        assert open(llm_profiles.legacy_config_path(), encoding="utf-8").read() == before


# ---------------------------------------------------------------------------
# LLMClient 解析
# ---------------------------------------------------------------------------

class TestLLMClientResolution:
    def test_explicit_profile_wins(self):
        _write_legacy(api_key="sk-legacy")
        client = LLMClient(profile={"name": "X", "base_url": "ux",
                                    "api_key": "sk-x", "model": "mx"})
        assert client.api_key == "sk-x"
        assert client.base_url == "ux"
        assert client.model == "mx"
        assert client.profile_name == "X"

    def test_defaults_when_nothing_is_configured(self):
        client = LLMClient()
        assert client.api_key is None
        assert client.base_url == llm_client.DEFAULT_BASE_URL
        assert client.model == llm_client.DEFAULT_MODEL
        assert client.is_configured() is False

    def test_active_store_profile_is_picked_up(self):
        llm_profiles.upsert_profile(
            {"name": "A", "base_url": "ua", "api_key": "sk-a", "model": "ma"})
        client = LLMClient()
        assert client.api_key == "sk-a"
        assert client.model == "ma"
        assert client.is_configured() is True

    def test_legacy_file_is_the_last_resort(self):
        _write_legacy(api_key="sk-legacy", model="legacy-model")
        client = LLMClient()
        assert client.api_key == "sk-legacy"
        assert client.model == "legacy-model"

    def test_set_config_writes_a_named_profile(self):
        client = LLMClient()
        client.set_config("sk-1234", "https://api.example.com/v1", "model-1")
        store = llm_profiles.list_profiles()
        assert len(store) == 1
        assert store[0]["name"] == "默认"
        assert store[0]["api_key"] == "sk-1234"
        assert llm_profiles.active_name() == "默认"

    def test_set_config_repeats_edit_the_same_profile(self):
        client = LLMClient()
        client.set_config("sk-1")
        client.set_config("sk-2")
        assert llm_profiles.profile_names() == ["默认"]
        assert llm_profiles.get_profile("默认")["api_key"] == "sk-2"

    def test_set_config_keeps_its_own_state(self):
        client = LLMClient()
        client.set_config("sk-1", "u1", "m1")
        assert (client.api_key, client.base_url, client.model) == ("sk-1", "u1", "m1")

    def test_set_config_does_not_create_the_legacy_file(self):
        client = LLMClient()
        client.set_config("sk-1")
        assert not os.path.exists(llm_profiles.legacy_config_path())

    def test_test_connection_unconfigured_mentions_the_settings_page(self):
        client = LLMClient()
        ok, message = client.test_connection()
        assert ok is False
        assert "LLM 设置" in message

    def test_profile_name_defaults_to_active_name(self):
        llm_profiles.upsert_profile(
            {"name": "网关A", "base_url": "u", "api_key": "sk-a", "model": "m"})
        client = LLMClient()
        assert client.profile_name == "网关A"
        client.set_config("sk-b")
        assert llm_profiles.profile_names() == ["网关A"]
        assert llm_profiles.get_profile("网关A")["api_key"] == "sk-b"


# ---------------------------------------------------------------------------
# 消费方
# ---------------------------------------------------------------------------

class TestConsumers:
    def test_rule_generator_accepts_no_arguments(self):
        """GUI 的「生成规则」按钮就是无参构造，不能炸。"""
        from engine.rule_generator import RuleGenerator
        gen = RuleGenerator()
        assert gen.llm is not None
        assert gen.llm.is_configured() is False

    def test_rule_generator_honours_injected_client(self):
        from engine.rule_generator import RuleGenerator
        client = LLMClient()
        gen = RuleGenerator(client)
        assert gen.llm is client
