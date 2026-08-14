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


def test_parse_key_unknown():
    with pytest.raises(ValueError):
        inject.parse_key("ctrl+喵")


def test_inject_empty_text():
    r = inject.inject("", InjectConfig())
    assert not r.ok and not r.on_clipboard


def test_inject_keeps_text_on_clipboard_when_paste_fails(monkeypatch):
    """铁律：按键失败也绝不能丢失识别结果。"""
    monkeypatch.setattr(inject, "clipboard_get", lambda: "旧内容")
    written = []
    monkeypatch.setattr(inject, "clipboard_set", lambda t: (written.append(t), True)[1])
    monkeypatch.setattr(inject, "press", lambda k: False)
    monkeypatch.setattr(inject, "active_app", lambda: None)

    r = inject.inject("识别出来的话", InjectConfig())
    assert not r.ok
    assert r.on_clipboard
    # 剪贴板里留的必须是识别结果，不能被恢复成旧内容
    assert written == ["识别出来的话"]


def test_inject_restores_clipboard_on_success(monkeypatch):
    monkeypatch.setattr(inject, "clipboard_get", lambda: "旧内容")
    written = []
    monkeypatch.setattr(inject, "clipboard_set", lambda t: (written.append(t), True)[1])
    monkeypatch.setattr(inject, "press", lambda k: True)
    monkeypatch.setattr(inject, "active_app", lambda: None)
    monkeypatch.setattr(inject.time, "sleep", lambda s: None)

    r = inject.inject("你好", InjectConfig())
    assert r.ok
    assert written == ["你好", "旧内容"]


def test_paste_key_by_app(monkeypatch):
    monkeypatch.setattr(inject, "clipboard_get", lambda: None)
    monkeypatch.setattr(inject, "clipboard_set", lambda t: True)
    monkeypatch.setattr(inject, "active_app", lambda: "org.gnome.Console")
    used = []
    monkeypatch.setattr(inject, "press", lambda k: (used.append(k), True)[1])

    cfg = InjectConfig(restore_clipboard=False,
                       paste_key_by_app={"org.gnome.Console": "ctrl+shift+v"})
    assert inject.inject("ls", cfg).ok
    assert used == ["ctrl+shift+v"]
