import pytest

from myna import inject
from myna.config import InjectConfig


def test_parse_key_ctrl_v():
    # 29=LEFTCTRL, 47=V：依次按下，再逆序抬起
    assert inject.parse_key("ctrl+v") == ["29:1", "47:1", "47:0", "29:0"]


def test_parse_key_three_keys():
    assert inject.parse_key("ctrl+shift+v") == [
        "29:1", "42:1", "47:1", "47:0", "42:0", "29:0"
    ]


def test_parse_key_shift_insert():
    # 42=LEFTSHIFT, 110=INSERT：终端和输入框都认的那个通用键
    assert inject.parse_key("shift+insert") == ["42:1", "110:1", "110:0", "42:0"]


def test_parse_key_unknown():
    with pytest.raises(ValueError):
        inject.parse_key("ctrl+喵")


def test_inject_empty_text():
    r = inject.inject("", InjectConfig())
    assert not r.ok and not r.on_clipboard


def test_inject_keeps_text_on_clipboard_when_paste_fails(monkeypatch):
    """铁律：按键失败也绝不能丢失识别结果。"""
    monkeypatch.setattr(inject, "clipboard_get", lambda primary=False: "旧内容")
    written = []
    monkeypatch.setattr(inject, "clipboard_set",
                        lambda t, primary=False: (written.append(t), True)[1])
    monkeypatch.setattr(inject, "press", lambda k: False)
    monkeypatch.setattr(inject, "active_app", lambda: None)

    r = inject.inject("识别出来的话", InjectConfig(paste_key="ctrl+v"))
    assert not r.ok
    assert r.on_clipboard
    # 剪贴板里留的必须是识别结果，不能被恢复成旧内容
    assert written == ["识别出来的话"]


def test_inject_restores_clipboard_on_success(monkeypatch):
    monkeypatch.setattr(inject, "clipboard_get", lambda primary=False: "旧内容")
    written = []
    monkeypatch.setattr(inject, "clipboard_set",
                        lambda t, primary=False: (written.append(t), True)[1])
    monkeypatch.setattr(inject, "press", lambda k: True)
    monkeypatch.setattr(inject, "active_app", lambda: None)
    monkeypatch.setattr(inject.time, "sleep", lambda s: None)

    r = inject.inject("你好", InjectConfig(paste_key="ctrl+v"))
    assert r.ok
    assert written == ["你好", "旧内容"]


def _fake_selections(monkeypatch):
    """把两份 selection 各自记下来，好分别断言 CLIPBOARD 和 PRIMARY。"""
    sel = {False: "旧剪贴板", True: "旧选中区"}
    log: list[tuple[bool, str]] = []
    monkeypatch.setattr(inject, "clipboard_get", lambda primary=False: sel[primary])

    def _set(t, primary=False):
        log.append((primary, t))
        sel[primary] = t
        return True

    monkeypatch.setattr(inject, "clipboard_set", _set)
    monkeypatch.setattr(inject, "active_app", lambda: None)
    monkeypatch.setattr(inject.time, "sleep", lambda s: None)
    return log


def test_shift_insert_writes_both_selections(monkeypatch):
    """终端的 Shift+Insert 粘 PRIMARY，输入框粘 CLIPBOARD——两份都得是识别结果。"""
    log = _fake_selections(monkeypatch)
    monkeypatch.setattr(inject, "press", lambda k: True)

    r = inject.inject("你好", InjectConfig(paste_key="shift+insert",
                                          restore_clipboard=False))
    assert r.ok
    assert log == [(False, "你好"), (True, "你好")]


def test_shift_insert_restores_both_selections(monkeypatch):
    """PRIMARY 是用户的鼠标选中区，借用了就得还回去。"""
    log = _fake_selections(monkeypatch)
    monkeypatch.setattr(inject, "press", lambda k: True)

    assert inject.inject("你好", InjectConfig(paste_key="shift+insert")).ok
    assert log == [(False, "你好"), (True, "你好"),
                   (False, "旧剪贴板"), (True, "旧选中区")]


def test_ctrl_v_leaves_primary_alone(monkeypatch):
    """没用到 PRIMARY 就别碰它——多写一次就毁掉用户正在选的东西。"""
    log = _fake_selections(monkeypatch)
    monkeypatch.setattr(inject, "press", lambda k: True)

    assert inject.inject("你好", InjectConfig(paste_key="ctrl+v")).ok
    assert all(primary is False for primary, _ in log)


def test_primary_failure_still_reports_success(monkeypatch):
    """PRIMARY 写不进去不算丢结果：CLIPBOARD 里有，输入框那一路照常。"""
    monkeypatch.setattr(inject, "clipboard_get", lambda primary=False: None)
    monkeypatch.setattr(inject, "clipboard_set",
                        lambda t, primary=False: not primary)
    monkeypatch.setattr(inject, "active_app", lambda: None)
    monkeypatch.setattr(inject, "press", lambda k: True)

    r = inject.inject("你好", InjectConfig(paste_key="shift+insert"))
    assert r.ok and r.on_clipboard
    assert "PRIMARY" in r.detail  # 但要说出来，别让终端里粘错了还查不到原因


def test_paste_key_by_app(monkeypatch):
    monkeypatch.setattr(inject, "clipboard_get", lambda primary=False: None)
    monkeypatch.setattr(inject, "clipboard_set", lambda t, primary=False: True)
    monkeypatch.setattr(inject, "active_app", lambda: "org.gnome.Console")
    used = []
    monkeypatch.setattr(inject, "press", lambda k: (used.append(k), True)[1])

    cfg = InjectConfig(restore_clipboard=False,
                       paste_key_by_app={"org.gnome.Console": "ctrl+shift+v"})
    assert inject.inject("ls", cfg).ok
    assert used == ["ctrl+shift+v"]


def _fake_xdotool(monkeypatch, active: str, focus: str, cls: str = "kitty"):
    monkeypatch.setattr(inject.shutil, "which", lambda p: f"/usr/bin/{p}")
    calls = []

    class R:
        def __init__(self, out):
            self.returncode, self.stdout = 0, out

    def run(cmd, **k):
        calls.append(cmd)
        return R(f"{active}\n{focus}\n" if "getwindowfocus" in cmd else f"{cls}\n")

    monkeypatch.setattr(inject.subprocess, "run", run)
    return calls


def test_active_app_rejects_stale_x_window(monkeypatch):
    """焦点在原生 Wayland 窗口时，getactivewindow 会返回上一个 XWayland 窗口。

    拿这个过期的 wm_class 去匹配 paste_key_by_app 会按错键，比拿不到更糟，
    所以要靠 getwindowfocus 交叉验证后直接放弃。
    """
    calls = _fake_xdotool(monkeypatch, active="123", focus="1")
    assert inject.active_app() is None
    # 既然已经判定不可信，就不该再去问 wm_class
    assert not any("getwindowclassname" in c for c in calls)


def test_active_app_accepts_focused_x_window(monkeypatch):
    _fake_xdotool(monkeypatch, active="123", focus="123", cls="kitty")
    assert inject.active_app() == "kitty"
