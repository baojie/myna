"""把文本送进当前焦点窗口。

Wayland 下没有 XTEST，逐字符 type 中文既慢又不可靠，所以走
「写剪贴板 + 模拟 Ctrl+V」，这是本机已验证可行的路子。

铁律：**任何情况下都不能丢失识别结果**。所有注入手段都失败时，文本仍留在
剪贴板里，并明确告诉用户手动粘贴。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from dataclasses import dataclass

from .config import InjectConfig

# Linux input-event-codes，ydotool 用的是这套键码而不是键名
KEYCODES = {
    "ctrl": 29, "control": 29, "leftctrl": 29,
    "shift": 42, "leftshift": 42,
    "alt": 56, "leftalt": 56,
    "super": 125, "meta": 125, "win": 125,
    "v": 47, "insert": 110,
}


@dataclass
class InjectResult:
    ok: bool          # 文本是否真的进了输入框
    on_clipboard: bool  # 文本是否至少进了剪贴板
    detail: str


def _ydotool_env() -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("YDOTOOL_SOCKET", f"/run/user/{os.getuid()}/.ydotool_socket")
    return env


def parse_key(spec: str) -> list[str]:
    """'ctrl+shift+v' -> ydotool 参数：依次按下，再逆序抬起。"""
    names = [p.strip().lower() for p in spec.split("+") if p.strip()]
    codes = []
    for n in names:
        if n not in KEYCODES:
            raise ValueError(f"不认识的按键：{n}")
        codes.append(KEYCODES[n])
    return [f"{c}:1" for c in codes] + [f"{c}:0" for c in reversed(codes)]


def clipboard_get() -> str | None:
    for cmd in (["wl-paste", "-n"], ["xclip", "-selection", "clipboard", "-o"]):
        if not shutil.which(cmd[0]):
            continue
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=3)
            if r.returncode == 0:
                return r.stdout.decode("utf-8", "replace")
        except Exception:
            continue
    return None


# wl-copy / xclip 写入后会常驻，这里留着句柄以便回收，避免僵尸进程堆积
_holders: list[subprocess.Popen] = []


def _reap() -> None:
    for p in _holders[:]:
        if p.poll() is not None:
            _holders.remove(p)


def clipboard_set(text: str) -> bool:
    """写系统剪贴板。

    **wl-copy 和 xclip 都不会退出**——Wayland 与 X11 的剪贴板都要求源进程存活
    来提供内容，这是协议决定的，不是 bug。所以绝不能用 `subprocess.run(timeout=)`
    等它结束：那必然超时，而内容其实早就写进去了。

    （这里踩过一次：原先用 run(timeout=3)，每次都判定「写剪贴板失败」而直接
    return，于是粘贴那一步压根没执行。文本躺在剪贴板里，用户什么也没看到。）

    改成：起进程、喂完 stdin 就撒手，然后**读回校验**——剪贴板是「识别结果绝不
    丢失」这条铁律的最后一道防线，值得多花几十毫秒确认它真的写进去了。
    """
    _reap()
    for cmd in (["wl-copy"], ["xclip", "-selection", "clipboard"]):
        if not shutil.which(cmd[0]):
            continue
        try:
            p = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                                 stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
            p.stdin.write(text.encode())
            p.stdin.close()
            _holders.append(p)
        except Exception:
            continue
        # 给它一点时间抓住 selection，再读回确认
        for _ in range(12):
            time.sleep(0.05)
            got = clipboard_get()
            if got is not None and got.strip() == text.strip():
                return True
    return False


def clipboard_clear() -> None:
    """清空剪贴板。恢复空剪贴板时用——wl-copy 不接受空输入。"""
    if shutil.which("wl-copy"):
        try:
            subprocess.run(["wl-copy", "--clear"], capture_output=True, timeout=3)
        except Exception:
            pass


def press(key_spec: str) -> bool:
    """模拟按键。先 ydotool（Wayland），再 xdotool（XWayland 兜底）。"""
    if shutil.which("ydotool"):
        try:
            r = subprocess.run(["ydotool", "key", *parse_key(key_spec)],
                               capture_output=True, timeout=3, env=_ydotool_env())
            if r.returncode == 0:
                return True
        except Exception:
            pass
    if shutil.which("xdotool"):
        try:
            r = subprocess.run(
                ["xdotool", "key", "--clearmodifiers", key_spec.lower()],
                capture_output=True, timeout=3)
            if r.returncode == 0:
                return True
        except Exception:
            pass
    return False


def active_app() -> str | None:
    """尽力而为地取当前窗口类。

    实测在 GNOME + Wayland 上**取不到**：GNOME Shell 的 Introspect 接口返回
    AccessDenied，xdotool 也拿不到原生 Wayland 窗口。这是 Wayland 的安全模型，
    不是配置问题——客户端无权知道别的窗口是谁。

    于是 `paste_key_by_app` 在 Wayland 上基本是死配置，只在 XWayland 应用上
    偶尔生效。终端这类粘贴键不同的场景，只能靠用户显式配 `paste_key`。
    """
    if shutil.which("xdotool"):
        try:
            r = subprocess.run(
                ["xdotool", "getactivewindow", "getwindowclassname"],
                capture_output=True, timeout=2, text=True)
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout.strip()
        except Exception:
            pass
    return None


def type_text(text: str) -> bool:
    """逐字符输入。对中文不可靠，仅作为 method="type" 时的显式选择。"""
    if not shutil.which("ydotool"):
        return False
    try:
        r = subprocess.run(["ydotool", "type", "--", text],
                           capture_output=True, timeout=15, env=_ydotool_env())
        return r.returncode == 0
    except Exception:
        return False


def inject(text: str, cfg: InjectConfig) -> InjectResult:
    if not text:
        return InjectResult(False, False, "空文本")

    if cfg.method == "type":
        if type_text(text):
            return InjectResult(True, False, "逐字符输入")
        # 退回剪贴板路线，别让用户白说一句
    saved = clipboard_get() if cfg.restore_clipboard else None

    if not clipboard_set(text):
        return InjectResult(False, False, "写剪贴板失败（wl-copy / xclip 都不可用）")

    key = cfg.paste_key
    app = active_app()
    if app and app in cfg.paste_key_by_app:
        key = cfg.paste_key_by_app[app]

    if not press(key):
        # 不恢复剪贴板：文本留在里面，用户还能手动粘
        return InjectResult(False, True, "模拟按键失败，文本已复制，请手动粘贴")

    if saved is not None:
        # 等粘贴动作真正读完剪贴板再恢复，否则会粘到旧内容
        time.sleep(0.3)
        if saved.strip():
            clipboard_set(saved)
        else:
            clipboard_clear()  # 原本就是空的，wl-copy 不接受空输入

    return InjectResult(True, True, f"已粘贴（{key}）")
