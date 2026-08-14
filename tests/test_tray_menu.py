"""用假 GTK 构造整个托盘，跑遍菜单构建与回调。

真实事故：新加的 `_build_paste_menu` 里直接写了 `Gtk.Menu()`，但 GTK 只在
`__init__` 的局部作用域导入，方法里得走 `self.Gtk`——于是 NameError，
Tray 初始化整个失败。而失败被 daemon 兜底吞掉，症状只是「顶栏图标没了」，
日志不翻到根本联想不到。

托盘代码基本无法用真 GTK 单测（要显示服务器、要主循环），但它恰恰又是最容易
悄悄坏掉的部分。所以这里注入假 gi 模块，把整条构造路径和所有回调走一遍。
"""

import sys
import types

import pytest

from myna.config import Config


class FakeWidget:
    def __init__(self, label=None, *a, **k):
        self._label = label
        self._active = False
        self._sensitive = True
        self._handlers = {}

    def set_label(self, v):
        self._label = v

    def get_label(self):
        return self._label

    def set_sensitive(self, v):
        self._sensitive = v

    def get_active(self):
        return self._active

    def set_active(self, v):
        self._active = v

    def connect(self, signal, cb, *args):
        self._handlers.setdefault(signal, []).append((cb, args))

    def emit(self, signal):
        for cb, args in self._handlers.get(signal, []):
            cb(self, *args)

    def set_submenu(self, m):
        self._submenu = m

    def append(self, item):
        pass

    def show_all(self):
        pass

    def show(self):
        pass

    def destroy(self):
        pass


class FakeRadio(FakeWidget):
    @classmethod
    def new_with_label(cls, group, label):
        # 真实 GTK 要求 group 是 GSList（列表）。传单个 widget 会得到
        # 「TypeError: Must be sequence」，托盘随之整个起不来。假对象必须
        # 照搬这条约束，否则测试永远发现不了这类错误。
        if not isinstance(group, (list, tuple)):
            raise TypeError("Must be sequence, not " + type(group).__name__)
        return cls(label)

    def get_group(self):
        return []


class FakeAboutDialog(FakeWidget):
    def __getattr__(self, name):
        if name.startswith("set_"):
            return lambda *a, **k: None
        raise AttributeError(name)


class FakeGtk:
    Menu = FakeWidget
    MenuItem = FakeWidget
    SeparatorMenuItem = FakeWidget
    RadioMenuItem = FakeRadio
    AboutDialog = FakeAboutDialog
    License = types.SimpleNamespace(MIT_X11="mit")

    @staticmethod
    def main():
        pass

    @staticmethod
    def main_quit():
        pass


class FakeIndicator(FakeWidget):
    @staticmethod
    def new(_id, _icon, _cat):
        return FakeIndicator()

    def set_status(self, s):
        pass

    def set_title(self, t):
        pass

    def set_menu(self, m):
        pass

    def set_icon_full(self, icon, desc):
        self.icon = icon

    def set_label(self, a, b):
        pass


@pytest.fixture
def tray(monkeypatch):
    fake_gi = types.ModuleType("gi")
    fake_gi.require_version = lambda *a, **k: None
    repo = types.ModuleType("gi.repository")
    repo.Gtk = FakeGtk
    repo.GLib = types.SimpleNamespace(idle_add=lambda fn, *a: fn(*a))
    repo.AyatanaAppIndicator3 = types.SimpleNamespace(
        Indicator=FakeIndicator,
        IndicatorCategory=types.SimpleNamespace(APPLICATION_STATUS=1),
        IndicatorStatus=types.SimpleNamespace(ACTIVE=1))
    monkeypatch.setitem(sys.modules, "gi", fake_gi)
    monkeypatch.setitem(sys.modules, "gi.repository", repo)

    from myna import tray as tray_mod
    from myna.daemon import Daemon

    d = Daemon(Config())
    return tray_mod.Tray(d), d


def test_constructs_without_error(tray):
    """整条构造路径跑通——这一条就能抓住 NameError 那类事故。"""
    t, _ = tray
    assert t.paste_items and t.model_items


def test_apply_all_states(tray):
    """状态刷新会调到 _refresh_model / _refresh_paste，一起走一遍。"""
    t, _ = tray
    for state in ("idle", "recording", "transcribing"):
        t._apply(state)


def test_paste_menu_reflects_current_key(tray):
    t, d = tray
    d.cfg.inject.paste_key = "ctrl+shift+v"
    t._refresh_paste()
    assert t.paste_items["ctrl+shift+v"].get_active()
    assert not t.paste_items["ctrl+v"].get_active()


def test_clicking_paste_item_switches(tray, monkeypatch, tmp_path):
    from myna import config as config_mod

    monkeypatch.setattr(config_mod, "CONFIG_PATH", tmp_path / "c.toml")
    t, d = tray
    item = t.paste_items["ctrl+shift+v"]
    item.set_active(True)
    item.emit("activate")
    assert d.cfg.inject.paste_key == "ctrl+shift+v"


def test_paste_item_ignores_deselect(tray):
    """radio 取消选中时也会触发 activate，那次不能当成用户的选择。"""
    t, d = tray
    before = d.cfg.inject.paste_key
    item = t.paste_items["ctrl+shift+v"]
    item.set_active(False)
    item.emit("activate")
    assert d.cfg.inject.paste_key == before


def test_menu_callbacks_do_not_crash(tray, monkeypatch):
    """每个菜单项都点一遍，确保没有漏掉的 NameError。"""
    t, d = tray
    monkeypatch.setattr("myna.inject.clipboard_set", lambda s: True)
    d._last_text = "测试文本"
    t._on_copy(None)
    t._on_about(None)
    t._on_cancel(None)


def test_startup_does_not_self_trigger(monkeypatch, tmp_path):
    """构建/刷新 radio 菜单时 set_active() 会触发 activate 信号，那不是用户点的。

    真实后果：每次启动托盘都「自己点自己」，白切两次模型
    （small→large-v3），十几秒和一次显存搬运全浪费；粘贴键也被来回改写。
    """
    fake_gi = types.ModuleType("gi")
    fake_gi.require_version = lambda *a, **k: None
    repo = types.ModuleType("gi.repository")
    repo.Gtk = FakeGtk
    repo.GLib = types.SimpleNamespace(idle_add=lambda fn, *a: fn(*a))
    repo.AyatanaAppIndicator3 = types.SimpleNamespace(
        Indicator=FakeIndicator,
        IndicatorCategory=types.SimpleNamespace(APPLICATION_STATUS=1),
        IndicatorStatus=types.SimpleNamespace(ACTIVE=1))
    monkeypatch.setitem(sys.modules, "gi", fake_gi)
    monkeypatch.setitem(sys.modules, "gi.repository", repo)

    from myna import config as config_mod
    from myna import tray as tray_mod
    from myna.daemon import Daemon

    monkeypatch.setattr(config_mod, "CONFIG_PATH", tmp_path / "c.toml")
    d = Daemon(Config())
    switched, pasted = [], []
    monkeypatch.setattr(d, "switch_model", lambda n: switched.append(n))
    monkeypatch.setattr(d, "set_paste_key", lambda k: pasted.append(k))

    t = tray_mod.Tray(d)          # 构建
    for state in ("idle", "recording", "transcribing", "idle"):
        t._apply(state)           # 反复刷新

    assert switched == [], f"启动时不该切换模型，却切了 {switched}"
    assert pasted == [], f"启动时不该改粘贴键，却改了 {pasted}"


def test_user_click_still_works_after_suppression(monkeypatch, tmp_path):
    """抑制不能把真实点击也一起吃掉。"""
    fake_gi = types.ModuleType("gi")
    fake_gi.require_version = lambda *a, **k: None
    repo = types.ModuleType("gi.repository")
    repo.Gtk = FakeGtk
    repo.GLib = types.SimpleNamespace(idle_add=lambda fn, *a: fn(*a))
    repo.AyatanaAppIndicator3 = types.SimpleNamespace(
        Indicator=FakeIndicator,
        IndicatorCategory=types.SimpleNamespace(APPLICATION_STATUS=1),
        IndicatorStatus=types.SimpleNamespace(ACTIVE=1))
    monkeypatch.setitem(sys.modules, "gi", fake_gi)
    monkeypatch.setitem(sys.modules, "gi.repository", repo)

    from myna import config as config_mod
    from myna import tray as tray_mod
    from myna.daemon import Daemon

    monkeypatch.setattr(config_mod, "CONFIG_PATH", tmp_path / "c.toml")
    d = Daemon(Config())
    t = tray_mod.Tray(d)
    t._apply("idle")

    pasted = []
    monkeypatch.setattr(d, "set_paste_key", lambda k: pasted.append(k))
    item = t.paste_items["ctrl+shift+v"]
    item.set_active(True)
    item.emit("activate")
    assert pasted == ["ctrl+shift+v"]


# ---------- 未下载档位的下载确认 ----------


def _fake_env(monkeypatch, dialog_response):
    """注入假 GTK，并让 MessageDialog.run() 返回指定结果。"""
    class FakeDialog(FakeWidget):
        last = {}

        def __init__(self, **kw):
            super().__init__()
            FakeDialog.last = dict(kw)

        def format_secondary_markup(self, text):
            FakeDialog.last["body"] = text

        def set_title(self, t):
            pass

        def run(self):
            return dialog_response

    gtk = type("G", (FakeGtk,), {
        "MessageDialog": FakeDialog,
        "MessageType": types.SimpleNamespace(QUESTION=1),
        "ButtonsType": types.SimpleNamespace(OK_CANCEL=1),
        "ResponseType": types.SimpleNamespace(OK=-5, CANCEL=-6),
    })
    fake_gi = types.ModuleType("gi")
    fake_gi.require_version = lambda *a, **k: None
    repo = types.ModuleType("gi.repository")
    repo.Gtk = gtk
    repo.GLib = types.SimpleNamespace(
        idle_add=lambda fn, *a: fn(*a),
        markup_escape_text=lambda s: s)
    repo.AyatanaAppIndicator3 = types.SimpleNamespace(
        Indicator=FakeIndicator,
        IndicatorCategory=types.SimpleNamespace(APPLICATION_STATUS=1),
        IndicatorStatus=types.SimpleNamespace(ACTIVE=1))
    monkeypatch.setitem(sys.modules, "gi", fake_gi)
    monkeypatch.setitem(sys.modules, "gi.repository", repo)
    return FakeDialog


def test_undownloaded_model_asks_first(monkeypatch):
    """点未下载的档位不能直接开下——先弹框说清是什么、多大、存哪。"""
    dlg = _fake_env(monkeypatch, -5)  # OK
    from myna import models as models_mod
    from myna import tray as tray_mod
    from myna.daemon import Daemon

    monkeypatch.setattr(models_mod, "is_downloaded", lambda n: n != "tiny")
    d = Daemon(Config())
    switched = []
    monkeypatch.setattr(d, "switch_model", lambda n, **k: switched.append(n))
    monkeypatch.setattr(tray_mod, "log", logging_stub := types.SimpleNamespace(
        debug=lambda *a, **k: None, warning=lambda *a, **k: None))
    t = tray_mod.Tray(d)

    item = t.model_items["tiny"]
    item.set_active(True)
    item.emit("activate")

    body = dlg.last.get("body", "")
    assert "Systran/faster-whisper-tiny" in body, "对话框必须写明真实模型名"
    assert "75M" in body, "对话框必须写明体积"
    assert "models--Systran--faster-whisper-tiny" in body, "对话框必须写明保存位置"
    assert switched == ["tiny"], "确认后应当真的去切换"


def test_cancel_download_does_not_switch(monkeypatch):
    dlg = _fake_env(monkeypatch, -6)  # CANCEL
    from myna import models as models_mod
    from myna import tray as tray_mod
    from myna.daemon import Daemon

    monkeypatch.setattr(models_mod, "is_downloaded", lambda n: n != "tiny")
    d = Daemon(Config())
    switched = []
    monkeypatch.setattr(d, "switch_model", lambda n, **k: switched.append(n))
    t = tray_mod.Tray(d)

    item = t.model_items["tiny"]
    item.set_active(True)
    item.emit("activate")
    assert switched == [], "用户取消了就不能下载/切换"


def test_downloaded_model_switches_without_dialog(monkeypatch):
    dlg = _fake_env(monkeypatch, -6)  # 若弹框则返回 CANCEL，切换就不会发生
    from myna import models as models_mod
    from myna import tray as tray_mod
    from myna.daemon import Daemon

    monkeypatch.setattr(models_mod, "is_downloaded", lambda n: True)
    d = Daemon(Config())
    switched = []
    monkeypatch.setattr(d, "switch_model", lambda n, **k: switched.append(n))
    t = tray_mod.Tray(d)

    item = t.model_items["medium"]
    item.set_active(True)
    item.emit("activate")
    assert switched == ["medium"], "已下载的档位不该多问一句"


def test_menu_label_marks_undownloaded(monkeypatch):
    _fake_env(monkeypatch, -5)
    from myna import models as models_mod
    from myna import tray as tray_mod
    from myna.daemon import Daemon

    monkeypatch.setattr(models_mod, "is_downloaded", lambda n: n == "large-v3")
    t = tray_mod.Tray(Daemon(Config()))
    assert t._model_label("large-v3") == "large-v3"
    assert "需下载" in t._model_label("tiny")
