"""顶栏状态图标（AppIndicator / KStatusNotifierItem）。

GTK 是**可选依赖**：装了就有图标，没装 daemon 照常工作，只是没图标可看。
语音输入是个没有窗口的常驻程序，图标是它唯一的「我还活着、我现在在干嘛」的出口。

线程模型：GTK 必须独占主线程跑 Gtk.main()，所以 daemon 的 socket 循环挪到
后台线程。状态回调来自 socket/转写线程，一律用 GLib.idle_add 转交主线程——
跨线程直接碰 GTK 会随机崩。
"""

from __future__ import annotations

import logging

from . import models as models_mod

log = logging.getLogger("myna")

# GTK 是可选依赖，只能延迟导入；Tray.__init__ 成功后这两个名字才有值
Gtk = None
GLib = None

# 用系统主题里的 symbolic 图标，顶栏会自动按主题渲染成单色，
# 不必自带图标文件，也就不会在深浅色主题下瞎掉。
ICONS = {
    "idle": "audio-input-microphone-symbolic",
    "recording": "media-record-symbolic",
    "transcribing": "content-loading-symbolic",
    "error": "dialog-warning-symbolic",
}

# 粘贴方式。终端用 Ctrl+Shift+V，普通输入框用 Ctrl+V，而 Wayland 下探测不到
# 焦点窗口是谁（GNOME Introspect 拒绝访问），自动分辨做不到，只能让用户切。
PASTE_KEYS = [
    ("ctrl+v", "Ctrl+V（普通输入框、浏览器、编辑器）"),
    ("ctrl+shift+v", "Ctrl+Shift+V（终端）"),
]

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
        from gi.repository import GLib as _GLib
        from gi.repository import Gtk as _Gtk

        # 导入 Gtk 就已经动了 locale，必须马上拨回来，否则音频解码会炸
        _fix_locale_after_gtk()

        # 提到模块全局，好让本类所有方法都能直接写 Gtk。
        # 否则每个方法都得记着补一句 `Gtk = self.Gtk`，漏一个就是 NameError，
        # 而托盘初始化失败是被兜底吞掉的——症状只是「图标没了」，很难联想到这里。
        global Gtk, GLib
        Gtk, GLib = _Gtk, _GLib

        self.Gtk, self.GLib = _Gtk, _GLib
        self.daemon = daemon

        # 构建和刷新 radio 菜单时 set_active() 会触发 activate 信号，
        # 那不是用户点的。不屏蔽的话每次启动都会「自己点自己」——实测导致
        # 启动时白切两次模型（small→large-v3，十几秒和一次显存搬运全浪费）
        self._suppress = 0


        self.indicator = AppIndicator.Indicator.new(
            "myna", ICONS["idle"], AppIndicator.IndicatorCategory.APPLICATION_STATUS)
        self.indicator.set_status(AppIndicator.IndicatorStatus.ACTIVE)
        self.indicator.set_title("myna 八哥语音输入")

        self.status_item = Gtk.MenuItem(label="待机")
        self.status_item.set_sensitive(False)
        self.toggle_item = Gtk.MenuItem(label="开始录音")
        self.cancel_item = Gtk.MenuItem(label="放弃本次录音")
        self.copy_item = Gtk.MenuItem(label="复制上次识别结果")
        self.about_item = Gtk.MenuItem(label="关于 myna")
        self.quit_item = Gtk.MenuItem(label="退出 myna")

        self.toggle_item.connect("activate", self._on_toggle)
        self.cancel_item.connect("activate", self._on_cancel)
        self.copy_item.connect("activate", self._on_copy)
        self.about_item.connect("activate", self._on_about)
        self.quit_item.connect("activate", self._on_quit)

        # 「识别模型」子菜单：radio 列出档位，当前模型打勾；切换中整组禁用
        self.model_label = Gtk.MenuItem(label="识别模型：-")
        self.model_label.set_sensitive(False)
        self.model_items: dict[str, Gtk.RadioMenuItem] = {}
        self._build_model_menu()
        self.model_item = Gtk.MenuItem(label="识别模型")
        self.model_item.set_submenu(self.model_menu)

        # 「粘贴方式」子菜单。Wayland 下探测不到焦点窗口是谁，自动分辨终端与
        # 普通输入框做不到，所以给用户一个明确的一键切换
        self.paste_items: dict[str, Gtk.RadioMenuItem] = {}
        self._build_paste_menu()
        self.paste_item = Gtk.MenuItem(label="粘贴方式")
        self.paste_item.set_submenu(self.paste_menu)

        menu = Gtk.Menu()
        for item in (self.status_item, Gtk.SeparatorMenuItem(),
                     self.toggle_item, self.cancel_item, Gtk.SeparatorMenuItem(),
                     self.copy_item, self.model_item, self.paste_item,
                     Gtk.SeparatorMenuItem(),
                     self.about_item, self.quit_item):
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
        self._refresh_model()
        self._refresh_paste()
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

    def _build_model_menu(self) -> None:
        self._suppress += 1
        try:
            Gtk = self.Gtk  # GTK 只在 __init__ 的局部作用域导入，方法里必须走 self
            sub = Gtk.Menu()
            group: list = []  # pygobject 的 radio group 要传列表，不能传单个 widget
            for name in models_mod.PRESETS:
                item = Gtk.RadioMenuItem.new_with_label(group, self._model_label(name))
                group.append(item)
                item.connect("activate", self._on_model_activate, name)
                self.model_items[name] = item
                sub.append(item)
            sub.show_all()
            self.model_menu = sub
        finally:
            self._suppress -= 1

    def _build_paste_menu(self) -> None:
        self._suppress += 1
        try:
            Gtk = self.Gtk  # GTK 只在 __init__ 的局部作用域导入，方法里必须走 self
            sub = Gtk.Menu()
            group: list = []  # pygobject 的 radio group 要传列表，不能传单个 widget
            for key, label in PASTE_KEYS:
                item = Gtk.RadioMenuItem.new_with_label(group, label)
                group.append(item)
                item.connect("activate", self._on_paste_activate, key)
                self.paste_items[key] = item
                sub.append(item)
            sub.show_all()
            self.paste_menu = sub
        finally:
            self._suppress -= 1

    def _on_paste_activate(self, w, key: str) -> None:
        if self._suppress:
            return
        # radio 取消选中时也会触发 activate，只处理真正被选中的那次
        if not w.get_active():
            return
        if self.daemon.cfg.inject.paste_key != key:
            self.daemon.set_paste_key(key)

    def _model_label(self, name: str) -> str:
        """菜单里就标出哪些还没下载，别等用户点了才知道要拉几个 G。"""
        d = models_mod.describe(name)
        return name if d["downloaded"] else f"{name}（需下载 {d['size']}）"

    def _confirm_download(self, name: str) -> bool:
        """未下载的档位，先把「是什么模型、多大、存哪、要联网」摆清楚再问。

        不问就下的话，用户点一下菜单就静默拉几个 G——而 README 还写着
        「全程本地，不联网」。模型体积和存储位置是用户真正关心的信息，
        尤其本机主盘已用 98%，权重全靠 /data。
        """
        d = models_mod.describe(name)
        dlg = Gtk.MessageDialog(
            transient_for=None, modal=True,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.OK_CANCEL,
            text=f"下载识别模型「{name}」？")
        dlg.format_secondary_markup(
            f"这个档位本地还没有，需要联网下载。\n\n"
            f"<b>模型</b>　{GLib.markup_escape_text(d['repo'])}\n"
            f"<b>大小</b>　约 {d['size']}\n"
            f"<b>存到</b>　<tt>{GLib.markup_escape_text(d['path'])}</tt>\n\n"
            f"下载在后台进行，完成后会自动切换并通知你。\n"
            f"期间当前模型继续可用。")
        dlg.set_title("myna 八哥 —— 下载模型")
        resp = dlg.run()
        dlg.destroy()
        return resp == Gtk.ResponseType.OK

    def _on_model_activate(self, w, name: str) -> None:
        if self._suppress:
            return
        # radio 在取消选中时也会收到 activate，只在真正选中时处理
        if not w.get_active():
            return

        # 没下载过的档位，先弹框说清是什么模型、多大、存哪，用户点头才下
        if not models_mod.is_downloaded(name):
            if not self._confirm_download(name):
                self._refresh_model()  # 用户取消，把勾选拨回当前模型
                return
            d = models_mod.describe(name)
            from . import notify as notify_mod

            notify_mod.notify(f"⬇️ 正在下载 {name}（约 {d['size']}）……")

        self.daemon.switch_model(name)

    def _refresh_model(self) -> None:
        self._suppress += 1
        try:
            st = self.daemon.status()
            cur = st.get("model")
            switching = bool(st.get("switching"))
            self.model_label.set_label(f"当前：{cur or '—'}")
            for name, item in self.model_items.items():
                item.set_sensitive(not switching)
                item.set_label(self._model_label(name))
                if not switching:
                    item.set_active(models_mod.resolve_model(name) == cur)
        finally:
            self._suppress -= 1

    def _refresh_paste(self) -> None:
        self._suppress += 1
        try:
            cur = self.daemon.cfg.inject.paste_key
            for key, item in self.paste_items.items():
                if item.get_active() != (key == cur):
                    item.set_active(key == cur)
        finally:
            self._suppress -= 1

    def _on_about(self, _w) -> None:
        from importlib.metadata import PackageNotFoundError, version

        try:
            ver = version("myna")
        except PackageNotFoundError:
            ver = "dev"
        dlg = self.Gtk.AboutDialog()
        dlg.set_program_name("myna 八哥")
        dlg.set_version(ver)
        dlg.set_comments(
            "Linux 桌面语音输入法：快捷键唤醒，文字直落焦点窗口。全程本地。")
        dlg.set_copyright("© 2026 baojie")
        dlg.set_license_type(self.Gtk.License.MIT_X11)
        dlg.set_website("https://github.com/baojie/myna")
        dlg.set_website_label("GitHub")
        dlg.connect("response", lambda d, _r: d.destroy())
        dlg.show()

    def _on_quit(self, _w) -> None:
        self.daemon.shutdown()
        self.Gtk.main_quit()

    # ---------- 主循环 ----------

    def run(self) -> None:
        self._apply(self.daemon.state.value)
        self.Gtk.main()

    def quit(self) -> None:
        self.GLib.idle_add(self.Gtk.main_quit)
