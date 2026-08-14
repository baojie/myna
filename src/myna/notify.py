"""桌面通知。录音/识别用同一个通知位更新，不在屏幕上堆一摞。"""

from __future__ import annotations

import shutil
import subprocess

APP = "myna 八哥"
_last_id: str | None = None


def notify(message: str, *, replace: bool = True, urgency: str = "normal") -> None:
    """发一条通知。通知失败绝不影响主流程。"""
    global _last_id
    if not shutil.which("notify-send"):
        return

    cmd = ["notify-send", "-a", APP, "-u", urgency, "-t", "4000", "-p"]
    if replace and _last_id:
        cmd += ["-r", _last_id]
    cmd += [APP, message]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=3, text=True)
        out = r.stdout.strip()
        if out.isdigit():
            _last_id = out
    except Exception:
        pass


def recording() -> None:
    notify("🎙 录音中…… 再按一次快捷键结束")


def transcribing() -> None:
    notify("⏳ 识别中……")


def result(text: str) -> None:
    shown = text if len(text) <= 40 else text[:40] + "……"
    notify(f"✅ {shown}")


def error(message: str) -> None:
    notify(f"❌ {message}", urgency="critical")
