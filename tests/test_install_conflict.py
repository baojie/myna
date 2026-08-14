"""快捷键冲突检测。

真实事故：默认绑定曾是 `<Super>d`，而 GNOME 自带的「显示桌面」正占着它。
写入和回读都成功、配置看着完全正常，按下去却是在最小化窗口——这种
「配置全对、功能不工作」最难查，必须在装的时候就拦住。
"""

import pytest

from myna import install


def _fake_gsettings(recursive_output: dict, custom: dict):
    """custom: {路径: (名字, 绑定)}"""
    def run(cmd, capture_output=True, text=True, timeout=10):
        class R:
            returncode = 0
            stdout = recursive_output.get(cmd[2], "") if cmd[1] == "list-recursively" else ""
        return R()

    def gs(*args):
        if args[0] == "get" and args[1] == install.SCHEMA:
            return "[" + ", ".join(f"'{p}'" for p in custom) + "]" if custom else "@as []"
        if args[0] == "get" and args[1].startswith(install.CUSTOM_SCHEMA):
            path = args[1].split(":", 1)[1]
            name, binding = custom[path]
            return f"'{name}'" if args[2] == "name" else f"'{binding}'"
        return ""

    return run, gs


def test_detects_gnome_builtin(monkeypatch):
    run, gs = _fake_gsettings(
        {"org.gnome.desktop.wm.keybindings":
         "org.gnome.desktop.wm.keybindings show-desktop ['<Super>d']"}, {})
    monkeypatch.setattr(install.subprocess, "run", run)
    monkeypatch.setattr(install, "_gsettings", gs)

    assert any("show-desktop" in c for c in install.find_conflicts("<Super>d"))
    assert install.find_conflicts("<Super>z") == []


def test_does_not_match_longer_combo(monkeypatch):
    """<Super>d 不能误命中 <Primary><Super>d —— 那是不同的键。"""
    run, gs = _fake_gsettings(
        {"org.gnome.desktop.wm.keybindings":
         "org.gnome.desktop.wm.keybindings foo ['<Primary><Super>d']"}, {})
    monkeypatch.setattr(install.subprocess, "run", run)
    monkeypatch.setattr(install, "_gsettings", gs)

    assert install.find_conflicts("<Super>d") == []


def test_detects_other_custom_shortcut(monkeypatch):
    """自定义快捷键住在子路径 schema 里，list-recursively 扫不到，得逐个查。"""
    run, gs = _fake_gsettings({}, {"/path/custom0/": ("截图工具", "<Super>z")})
    monkeypatch.setattr(install.subprocess, "run", run)
    monkeypatch.setattr(install, "_gsettings", gs)

    conflicts = install.find_conflicts("<Super>z")
    assert conflicts and "截图工具" in conflicts[0]


def test_install_refuses_occupied_key(monkeypatch):
    monkeypatch.setattr(install, "find_conflicts",
                        lambda b: ["org.gnome.desktop.wm.keybindings show-desktop"])
    with pytest.raises(RuntimeError, match="已经被占用"):
        install.install_shortcut("<Super>d")


def test_force_overrides(monkeypatch):
    monkeypatch.setattr(install, "find_conflicts",
                        lambda b: ["org.gnome.desktop.wm.keybindings show-desktop"])
    monkeypatch.setattr(install, "_gsettings", lambda *a: "@as []")
    monkeypatch.setattr(install, "_myna_exe", lambda: "/usr/bin/myna")
    # 回读校验会拿到 "@as []"，这里只验冲突不再拦路
    with pytest.raises(RuntimeError, match="回读不符"):
        install.install_shortcut("<Super>d", force=True)


def test_ignores_self_when_rebinding_same_key(monkeypatch):
    """重复装同一个键必须幂等，不能把自己当成冲突。"""
    monkeypatch.setattr(
        install, "find_conflicts",
        lambda b: [f"{install.SCHEMA} custom-keybindings（自定义快捷键「myna 语音输入」）"])
    monkeypatch.setattr(install, "_gsettings", lambda *a: "@as []")
    monkeypatch.setattr(install, "_myna_exe", lambda: "/usr/bin/myna")
    # 自己占的那条被滤掉，于是不会抛「已经被占用」，而是走到回读校验
    with pytest.raises(RuntimeError, match="回读不符"):
        install.install_shortcut("<Super>z")


def test_default_binding_is_not_super_d():
    """<Super>d 是 GNOME 显示桌面，绝不能再做默认值。"""
    assert install.DEFAULT_BINDING != "<Super>d"
