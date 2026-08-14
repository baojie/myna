"""dock 图标的 .desktop 渲染。

真实事故：Icon= 写图标名 "myna" 而主题里查不到时，GNOME Dash 渲染这一项会抛
`TypeError: firstIcon.icon is null`，异常打断渲染，图标**整个不出现**——
而 favorite-apps 里明明有、gio 查得到、should_show 也是 True，极难查。
所以 Icon 必须是绝对路径，这里把它钉死。
"""

from myna import install


def _render(term=None):
    return install.render_desktop("/usr/bin/myna", "/home/u/.local/share/icons/myna.png", term)


def test_icon_is_absolute_path():
    icon = [ln for ln in _render().splitlines() if ln.startswith("Icon=")]
    assert icon == ["Icon=/home/u/.local/share/icons/myna.png"]
    assert icon[0].split("=", 1)[1].startswith("/")


def test_no_placeholder_left():
    for term in (None, "/usr/bin/kitty"):
        assert "@" not in _render(term).replace("@EXEC@", ""), term


def test_terminal_actions_only_when_terminal_exists():
    without = _render(None)
    assert "Actions=Cancel;\n" in without
    assert "Desktop Action Log" not in without

    with_term = _render("/usr/bin/kitty")
    assert "Actions=Cancel;Status;Restart;Log;" in with_term
    assert "Exec=/usr/bin/kitty journalctl" in with_term
    # 窗口得自己撑住：Desktop Action 分组里不能写 Terminal= 键
    assert 'read -n1 -r -p' in with_term


def test_click_toggles():
    body = _render()
    assert "Exec=/usr/bin/myna toggle" in body


def test_finds_terminal_or_none(monkeypatch):
    monkeypatch.setattr(install.shutil, "which", lambda p: None)
    assert install._find_terminal() is None
    monkeypatch.setattr(install.shutil, "which",
                        lambda p: "/usr/bin/konsole" if p == "konsole" else None)
    assert install._find_terminal() == "/usr/bin/konsole -e"
