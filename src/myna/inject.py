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


def clipboard_set(text: str) -> bool:
    for cmd in (["wl-copy"], ["xclip", "-selection", "clipboard"]):
        if not shutil.which(cmd[0]):
            continue
        try:
            r = subprocess.run(cmd, input=text.encode(), capture_output=True, timeout=3)
            if r.returncode == 0:
                return True
        except Exception:
            continue
    return False


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
    """尽力而为地取当前窗口类。GNOME 45+ 封了 Shell.Eval，多数情况下取不到。"""
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
        clipboard_set(saved)

    return InjectResult(True, True, f"已粘贴（{key}）")
