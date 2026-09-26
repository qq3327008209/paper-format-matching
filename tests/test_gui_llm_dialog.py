"""GUI 侧 LLM 方案弹窗与设置持久化的端到端测试（offscreen，不发真实网络请求）。

这些用例覆盖的是引擎单元测试覆盖不到的部分：QListWidget 的选中语义、
「新建/改名/设为当前/删除」的操作流、设置页的读写往返、以及后台测试线程
的生命周期（用户可能在请求未返回时就关掉窗口）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 必须在导入 PySide6 之前定，否则平台插件已经按系统默认加载了。
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

import engine.paths as paths
from engine import llm_profiles
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app
    app.processEvents()


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    (tmp_path / "config").mkdir()
    monkeypatch.setattr(paths, "BASE_DIR", str(tmp_path))
    llm_profiles.clear_store()
    yield
    llm_profiles.clear_store()


@pytest.fixture
def no_popups(monkeypatch):
    """静音 QMessageBox，测试只看操作流不弹窗。"""
    from PySide6.QtWidgets import QMessageBox
    for method, result in (("warning", QMessageBox.Ok),
                           ("information", QMessageBox.Ok),
                           ("critical", QMessageBox.Ok),
                           ("question", QMessageBox.Yes)):
        monkeypatch.setattr(QMessageBox, method, staticmethod(lambda *a, **k: result))


def _silent_connect(monkeypatch):
    """把连接测试换成立刻返回失败，避免用例真的去打网络。"""
    from engine import llm_client
    monkeypatch.setattr(llm_client.LLMClient, "test_connection",
                        lambda self: (False, "测试桩：未发请求"))


@pytest.fixture
def dialog(qapp, no_popups):
    import gui
    dlg = gui.LLMProfileDialog(None)
    yield dlg
    dlg.deleteLater()
    qapp.processEvents()


@pytest.fixture
def window(qapp, no_popups):
    import gui
    win = gui.MainWindow()
    yield win
    win.deleteLater()
    qapp.processEvents()


def _add(dialog, name, key=None):
    """在弹窗里以「新增」的方式存一个方案。

    必须先清掉选中：保存时 ``old_name`` 取自选中项，不清的话第二次保存会
    把第一条**改名**成第二条，测试里的两条方案其实只剩一条。
    """
    dialog.profile_list.clearSelection()
    dialog.profile_list.setCurrentRow(-1)
    dialog._fill({"name": name, "base_url": "u", "api_key": key or name, "model": "m"})
    return dialog._save_current()


class _DestroyedWidget:
    """模拟 C++ 侧已销毁的 QWidget：任何属性访问都抛 RuntimeError。"""

    def __getattribute__(self, name):
        raise RuntimeError("Internal C++ object already deleted.")


# ---------------------------------------------------------------------------
# 弹窗操作流
# ---------------------------------------------------------------------------

class TestDialogFlow:
    def test_empty_name_is_rejected(self, dialog):
        assert dialog._save_current() == ""
        assert llm_profiles.profile_names() == []

    def test_new_profile_does_not_overwrite_the_previous_one(self, dialog):
        """「新建方案」之后保存必须新增条目，不能覆盖上一条。

        真实缺陷回归：setCurrentRow 只移动焦点不改变选中，用 currentItem()
        判断「工作副本基于哪条已保存方案」会让新建变成覆盖。
        """
        assert _add(dialog, "DeepSeek", key="sk-1") == "DeepSeek"

        dialog._new_profile()
        assert dialog._selected_name() == ""
        assert dialog._fields()["api_key"] == ""
        dialog.ed_name.setText("公司网关")
        dialog.ed_base.setText("u2")
        dialog.ed_key.setText("sk-2")
        dialog.ed_model.setText("m2")
        assert dialog._save_current() == "公司网关"

        assert llm_profiles.profile_names() == ["DeepSeek", "公司网关"]
        assert llm_profiles.get_profile("DeepSeek")["api_key"] == "sk-1"

    def test_list_shows_active_marker_and_tooltip(self, dialog):
        _add(dialog, "A", key="1")
        _add(dialog, "B", key="2")
        dialog._reload_list()

        lst = dialog.profile_list
        assert lst.count() == 2
        assert lst.item(0).text().startswith("●")
        assert not lst.item(1).text().startswith("●")
        assert [lst.item(i).data(Qt.UserRole) for i in range(2)] == ["A", "B"]
        assert "u" in lst.item(1).toolTip() and "m" in lst.item(1).toolTip()

    def test_selecting_a_row_refills_the_fields(self, dialog):
        _add(dialog, "A", key="sk-a")
        dialog.profile_list.item(0).setSelected(True)
        assert dialog._selected_name() == "A"
        assert dialog._fields() == {"name": "A", "base_url": "u",
                                    "api_key": "sk-a", "model": "m"}

    def test_renaming_replaces_in_place(self, dialog):
        _add(dialog, "A")
        _add(dialog, "B")
        dialog._reload_list()
        dialog.profile_list.item(1).setSelected(True)
        dialog.ed_name.setText("B2")
        assert dialog._save_current() == "B2"
        assert llm_profiles.profile_names() == ["A", "B2"]

    def test_set_current_switches_active(self, dialog):
        _add(dialog, "A")
        _add(dialog, "B")
        dialog._reload_list()
        dialog.profile_list.item(1).setSelected(True)
        dialog._set_current()
        assert llm_profiles.active_name() == "B"

    def test_set_current_without_selection_is_refused(self, dialog):
        _add(dialog, "A")
        dialog._reload_list()
        dialog.profile_list.clearSelection()
        assert dialog._set_current() is None
        assert llm_profiles.active_name() == "A"

    def test_delete_falls_back_to_a_remaining_profile(self, dialog):
        _add(dialog, "A")
        _add(dialog, "B")
        dialog._reload_list()
        dialog.profile_list.item(1).setSelected(True)
        dialog._delete_selected()
        assert llm_profiles.profile_names() == ["A"]
        assert llm_profiles.active_name() == "A"


# ---------------------------------------------------------------------------
# 设置页
# ---------------------------------------------------------------------------

class TestSettingsPage:
    def test_summary_masks_the_key(self, window):
        llm_profiles.upsert_profile(
            {"name": "网关", "base_url": "u", "api_key": "sk-abcdef1234", "model": "m"})
        window._refresh_llm_summary()
        text = window.s_llm_summary.text()
        assert "网关" in text and "****1234" in text
        assert "sk-abcdef1234" not in text, "明文 key 不得出现在界面"

    def test_summary_without_any_profile(self, window):
        window._refresh_llm_summary()
        assert "尚未配置" in window.s_llm_summary.text()

    def test_saved_settings_survive_a_restart(self, qapp):
        """「保存设置」历史上只有写没有读，重启后全部回到默认值。"""
        import gui
        win = gui.MainWindow()
        win.s_out_dir.setText("E:/自定义输出")
        win.s_rep_dir.setText("E:/自定义报告")
        win.s_auto_anno.setChecked(True)
        win._save_settings()

        win2 = gui.MainWindow()
        assert win2.s_out_dir.text() == "E:/自定义输出"
        assert win2.s_rep_dir.text() == "E:/自定义报告"
        assert win2.s_auto_anno.isChecked() is True
        for w in (win, win2):
            w.deleteLater()
        qapp.processEvents()

    def test_saved_api_key_never_lands_in_settings_file(self, window):
        window._save_settings()
        import json
        data = json.load(open(paths.resolve("gui_settings.json"), encoding="utf-8"))
        assert "api_key" not in data
        assert "base_url" not in data

    def test_corrupted_settings_file_does_not_break_startup(self, qapp):
        with open(paths.resolve("gui_settings.json"), "w", encoding="utf-8") as fh:
            fh.write("{{{ not json")
        import gui
        win = gui.MainWindow()
        win.deleteLater()
        qapp.processEvents()

    def test_missing_saved_rules_file_falls_back_silently(self, qapp):
        import json
        with open(paths.resolve("gui_settings.json"), "w", encoding="utf-8") as fh:
            json.dump({"rules_path": "config/不存在的规则.yaml"}, fh, ensure_ascii=False)
        import gui
        win = gui.MainWindow()
        win.deleteLater()
        qapp.processEvents()


# ---------------------------------------------------------------------------
# 规则文件列表
# ---------------------------------------------------------------------------

class TestRulesFileList:
    def test_llm_config_files_are_not_offered_as_rules(self, window, tmp_path):
        cfg = tmp_path / "config"
        for name in ("api_config.yaml", "llm_profiles.yaml",
                     "rules_lunwen.yaml", "prompt_template.md"):
            (cfg / name).write_text("x: 1\n", encoding="utf-8")
        names = [os.path.basename(p) for p in window._list_rules_files()]
        assert "rules_lunwen.yaml" in names
        for bad in ("api_config.yaml", "llm_profiles.yaml", "prompt_template.md"):
            assert bad not in names, names


# ---------------------------------------------------------------------------
# 后台测试线程
# ---------------------------------------------------------------------------

class TestTestWorker:
    def test_worker_is_not_parented_to_the_dialog(self, dialog, qapp, monkeypatch):
        import gui
        _silent_connect(monkeypatch)
        worker = gui._LLMTestWorker({"name": "x", "base_url": "u",
                                     "api_key": "k", "model": "m"})
        assert worker.parent() is None, "挂父对象会让关窗口时 Qt 直接 abort"
        assert gui._LLMTestWorker._live.count(worker) == 1
        worker.start()
        worker.wait(30000)
        qapp.processEvents()
        assert gui._LLMTestWorker._live.count(worker) == 0, "跑完应自清理"

    def test_worker_reports_failure_without_raising(self, qapp, monkeypatch):
        import gui
        _silent_connect(monkeypatch)
        payload = []
        worker = gui._LLMTestWorker({"name": "x", "base_url": "u",
                                     "api_key": "k", "model": "m"})
        worker.done.connect(lambda ok, msg: payload.append((ok, msg)))
        worker.start()
        worker.wait(30000)
        qapp.processEvents()
        assert payload == [(False, "测试桩：未发请求")]

    def test_callback_after_the_dialog_is_closed(self, qapp, monkeypatch, no_popups):
        """请求还没回来用户就关掉了弹窗，回调不能炸。"""
        import gui
        _silent_connect(monkeypatch)
        dlg = gui.LLMProfileDialog(None)
        worker = gui._LLMTestWorker({"name": "x", "base_url": "u",
                                     "api_key": "k", "model": "m"})
        worker.done.connect(dlg._on_test_done)
        worker.start()
        dlg.close()
        qapp.processEvents()
        worker.wait(30000)
        qapp.processEvents()
        assert gui._LLMTestWorker._live.count(worker) == 0

    def test_callback_tolerates_a_destroyed_widget(self):
        """回调被调用时控件已随窗口销毁：访问抛 RuntimeError，必须吞掉。"""
        import gui
        gui.LLMProfileDialog._on_test_done(_DestroyedWidget(), False, "连接失败")

    def test_main_window_callback_tolerates_a_destroyed_widget(self):
        import gui
        gui.MainWindow._on_llm_test_done(_DestroyedWidget(), False, "连接失败")
