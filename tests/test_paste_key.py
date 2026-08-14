"""粘贴键切换与持久化。

背景：终端粘贴是 Ctrl+Shift+V，普通输入框是 Ctrl+V，而 Wayland 下探测不到
焦点窗口是谁，自动分辨做不到。于是做成用户显式切换 —— 那就必须保证切换真的
写进了配置，且不会写坏用户已有的配置文件。
"""

import pytest

from myna import config as config_mod
from myna import daemon as daemon_mod
from myna.config import Config
from myna.daemon import Daemon, State


# ---------- 配置持久化 ----------


def test_creates_file_when_missing(tmp_path):
    p = tmp_path / "config.toml"
    config_mod.save_paste_key("ctrl+shift+v", p)
    assert config_mod.load(p).inject.paste_key == "ctrl+shift+v"


def test_replaces_existing_value(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('[inject]\npaste_key = "ctrl+v"\nrestore_clipboard = true\n',
                 encoding="utf-8")
    config_mod.save_paste_key("ctrl+shift+v", p)
    cfg = config_mod.load(p)
    assert cfg.inject.paste_key == "ctrl+shift+v"
    assert cfg.inject.restore_clipboard is True  # 同段其他键不能被殃及


def test_preserves_comments_and_other_sections(tmp_path):
    """用户的注释比我们的整洁值钱，不能重新序列化整个文件。"""
    p = tmp_path / "config.toml"
    p.write_text(
        '# 我的配置\n[asr]\nmodel = "medium"  # 手写的注释\n\n'
        '[inject]\npaste_key = "ctrl+v"\n\n[hotwords]\n"三步" = "散步"\n',
        encoding="utf-8")
    config_mod.save_paste_key("ctrl+shift+v", p)
    text = p.read_text(encoding="utf-8")
    assert "# 我的配置" in text
    assert "# 手写的注释" in text
    cfg = config_mod.load(p)
    assert cfg.asr.model == "medium"
    assert cfg.hotwords == {"三步": "散步"}
    assert cfg.inject.paste_key == "ctrl+shift+v"


def test_does_not_touch_paste_key_by_app_subtable(tmp_path):
    """paste_key 这个名字在子表里也出现，跨段乱改会写坏配置。"""
    p = tmp_path / "config.toml"
    p.write_text(
        '[inject]\npaste_key = "ctrl+v"\n\n'
        '[inject.paste_key_by_app]\n"org.gnome.Console" = "ctrl+shift+v"\n',
        encoding="utf-8")
    config_mod.save_paste_key("ctrl+shift+v", p)
    cfg = config_mod.load(p)
    assert cfg.inject.paste_key == "ctrl+shift+v"
    assert cfg.inject.paste_key_by_app == {"org.gnome.Console": "ctrl+shift+v"}


def test_adds_section_when_absent(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('[asr]\nmodel = "small"\n', encoding="utf-8")
    config_mod.save_paste_key("ctrl+shift+v", p)
    cfg = config_mod.load(p)
    assert cfg.inject.paste_key == "ctrl+shift+v"
    assert cfg.asr.model == "small"


# ---------- daemon 侧 ----------


@pytest.fixture
def d(monkeypatch, tmp_path):
    monkeypatch.setattr(daemon_mod.notify, "notify", lambda *a, **k: None)
    monkeypatch.setattr(daemon_mod.notify, "error", lambda *a, **k: None)
    monkeypatch.setattr(config_mod, "CONFIG_PATH", tmp_path / "config.toml")
    return Daemon(Config())


def test_set_paste_key_applies_and_persists(d, tmp_path):
    resp = d.set_paste_key("ctrl+shift+v")
    assert resp["ok"] and resp["persisted"]
    assert d.cfg.inject.paste_key == "ctrl+shift+v"
    assert config_mod.load(tmp_path / "config.toml").inject.paste_key == "ctrl+shift+v"


def test_rejects_invalid_key_before_writing(d, tmp_path):
    """非法键绝不能写进配置文件。"""
    resp = d.set_paste_key("ctrl+喵")
    assert not resp["ok"]
    assert d.cfg.inject.paste_key == "ctrl+v"  # 保持原值
    assert not (tmp_path / "config.toml").exists()


def test_survives_write_failure(d, monkeypatch):
    """写配置失败也要让本次生效——用户按了菜单就该有反应。"""
    def boom(key, path=None):
        raise OSError("磁盘满了")

    monkeypatch.setattr(config_mod, "save_paste_key", boom)
    resp = d.set_paste_key("ctrl+shift+v")
    assert resp["ok"] and resp["persisted"] is False
    assert d.cfg.inject.paste_key == "ctrl+shift+v"


def test_dispatch_paste_key(d):
    assert d.dispatch({"cmd": "paste_key", "key": "ctrl+shift+v"})["ok"]
    assert d.cfg.inject.paste_key == "ctrl+shift+v"


def test_status_reports_paste_key(d):
    assert d.status()["paste_key"] == "ctrl+v"


def test_refreshes_tray(d):
    calls = []
    d.on_state_change = lambda s: calls.append(s)
    d.set_paste_key("ctrl+shift+v")
    assert calls == [State.IDLE]
