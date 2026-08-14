"""顶栏状态图标（AppIndicator / KStatusNotifierItem）。

GTK 是**可选依赖**：装了就有图标，没装 daemon 照常工作，只是没图标可看。
语音输入是个没有窗口的常驻程序，图标是它唯一的「我还活着、我现在在干嘛」的出口。

线程模型：GTK 必须独占主线程跑 Gtk.main()，所以 daemon 的 socket 循环挪到
后台线程。状态回调来自 socket/转写线程，一律用 GLib.idle_add 转交主线程——
跨线程直接碰 GTK 会随机崩。
"""

from __future__ import annotations

import logging

log = logging.getLogger("myna")

# 用系统主题里的 symbolic 图标，顶栏会自动按主题渲染成单色，
# 不必自带图标文件，也就不会在深浅色主题下瞎掉。
ICONS = {
    "idle": "audio-input-microphone-symbolic",
    "recording": "media-record-symbolic",
    "transcribing": "content-loading-symbolic",
    "error": "dialog-warning-symbolic",
}

LABELS = {
    "idle": "待机",
    "recording": "正在录音…… 再按快捷键结束",
    "transcribing": "识别中……",
}


def _fix_locale_after_gtk() -> None:
    """把 GTK 改掉的 locale 拨回来。

    GTK 初始化会 setlocale(LC_ALL, "")，于是 C 库的 strerror 开始返回**本地化**
    字符串。PyAV 的错误处理路径拿 strerror 的结果时用 ascii 解码，遇到中文直接
    UnicodeDecodeError——而它本来在处理的只是一个良性的 EOF 信号。后果是：
    只要托盘开着，音频解码必炸，转写全军覆没。

    这是 PyAV 的 bug，我们只能规避：把 LC_MESSAGES 钉回 C，让 strerror 返回
    纯 ASCII 英文。顺带把 LC_NUMERIC 也钉回 C——GTK 程序处理数字时的惯例做法，
    免得某些 locale 下小数点变成逗号。
    界面文字走的是 LC_CTYPE/LANG，不受影响，菜单仍是中文。
    """
    import locale

    for category in ("LC_MESSAGES", "LC_NUMERIC"):
        cat = getattr(locale, category, None)
        if cat is None:
            continue
        try:
            locale.setlocale(cat, "C")
        except Exception:
            log.debug("重置 %s 失败", category, exc_info=True)


def available() -> bool:
    try:
        import gi

        gi.require_version("Gtk", "3.0")
        try:
            gi.require_version("AyatanaAppIndicator3", "0.1")
        except ValueError:
            gi.require_version("AppIndicator3", "0.1")
        return True
    except Exception:
        return False


class Tray:
    """把 daemon 包成一个顶栏图标。daemon 不知道托盘存在，只暴露状态回调。"""

    def __init__(self, daemon) -> None:
        import gi

        gi.require_version("Gtk", "3.0")
        try:
            gi.require_version("AyatanaAppIndicator3", "0.1")
            from gi.repository import AyatanaAppIndicator3 as AppIndicator
        except ValueError:
            gi.require_version("AppIndicator3", "0.1")
            from gi.repository import AppIndicator3 as AppIndicator
        from gi.repository import GLib, Gtk

        # 导入 Gtk 就已经动了 locale，必须马上拨回来，否则音频解码会炸
        _fix_locale_after_gtk()

        self.Gtk, self.GLib = Gtk, Gtk and GLib
        self.daemon = daemon

        self.indicator = AppIndicator.Indicator.new(
            "myna", ICONS["idle"], AppIndicator.IndicatorCategory.APPLICATION_STATUS)
        self.indicator.set_status(AppIndicator.IndicatorStatus.ACTIVE)
        self.indicator.set_title("myna 八哥语音输入")

        self.status_item = Gtk.MenuItem(label="待机")
        self.status_item.set_sensitive(False)
        self.toggle_item = Gtk.MenuItem(label="开始录音")
        self.cancel_item = Gtk.MenuItem(label="放弃本次录音")
        self.copy_item = Gtk.MenuItem(label="复制上次识别结果")
        self.quit_item = Gtk.MenuItem(label="退出 myna")

        self.toggle_item.connect("activate", self._on_toggle)
        self.cancel_item.connect("activate", self._on_cancel)
        self.copy_item.connect("activate", self._on_copy)
        self.quit_item.connect("activate", self._on_quit)

        menu = Gtk.Menu()
        for item in (self.status_item, Gtk.SeparatorMenuItem(),
                     self.toggle_item, self.cancel_item, Gtk.SeparatorMenuItem(),
                     self.copy_item, Gtk.SeparatorMenuItem(), self.quit_item):
            menu.append(item)
        menu.show_all()
        self.indicator.set_menu(menu)

        self.cancel_item.set_sensitive(False)
        self.copy_item.set_sensitive(False)
        daemon.on_state_change = self.on_state_change

    # ---------- daemon -> 图标 ----------

    def on_state_change(self, state) -> None:
        """从任意线程调用；只做一次 idle_add，绝不阻塞 daemon 的锁。"""
        self.GLib.idle_add(self._apply, state.value)

    def _apply(self, state: str) -> bool:
        self.indicator.set_icon_full(ICONS.get(state, ICONS["idle"]), state)
        self.status_item.set_label(LABELS.get(state, state))
        self.toggle_item.set_label("停止并识别" if state == "recording" else "开始录音")
        self.toggle_item.set_sensitive(state != "transcribing")
        self.cancel_item.set_sensitive(state == "recording")
        self.copy_item.set_sensitive(bool(self.daemon._last_text))
        # 录音时把状态写到图标旁边，扫一眼就知道它在听
        self.indicator.set_label("● 录音中" if state == "recording" else "", "myna")
        return False  # 一次性回调

    # ---------- 菜单 ----------

    def _on_toggle(self, _w) -> None:
        self.daemon.toggle()

    def _on_cancel(self, _w) -> None:
        self.daemon.cancel()

    def _on_copy(self, _w) -> None:
        from .inject import clipboard_set

        if self.daemon._last_text:
            clipboard_set(self.daemon._last_text)

    def _on_quit(self, _w) -> None:
        self.daemon.shutdown()
        self.Gtk.main_quit()

    # ---------- 主循环 ----------

    def run(self) -> None:
        self._apply(self.daemon.state.value)
        self.Gtk.main()

    def quit(self) -> None:
        self.GLib.idle_add(self.Gtk.main_quit)
