"""录音：ffmpeg 从 PulseAudio 抓 16kHz 单声道 wav。参数沿用本机已验证可行的一套。"""

from __future__ import annotations

import contextlib
import itertools
import os
import signal
import subprocess
import time
import wave
from pathlib import Path

from .config import runtime_dir

_counter = itertools.count()


def _session_env() -> dict[str, str]:
    """
    补全会话环境变量。

    daemon 由 systemd --user 拉起时环境本来是全的，但历史上快捷键守护进程启动的
    子进程缺这几个变量会静默失败——手动在终端跑一切正常，非常难查。兜底代价为零。
    """
    env = os.environ.copy()
    uid = os.getuid()
    env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{uid}")
    env.setdefault("PULSE_SERVER", f"unix:/run/user/{uid}/pulse/native")
    env.setdefault("WAYLAND_DISPLAY", "wayland-0")
    env.setdefault("DISPLAY", ":0")
    return env


def wav_duration(path: Path) -> float:
    """读 wav 时长；文件损坏或不可读时返回 0。"""
    try:
        with contextlib.closing(wave.open(str(path), "rb")) as w:
            rate = w.getframerate()
            return w.getnframes() / rate if rate else 0.0
    except Exception:
        return 0.0


class Recorder:
    """一次只录一路。线程安全由调用方（daemon 的锁）保证。"""

    def __init__(self) -> None:
        self._proc: subprocess.Popen | None = None
        self._path: Path | None = None
        self._started_at: float = 0.0

    @property
    def recording(self) -> bool:
        return self._proc is not None

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self._started_at if self._proc else 0.0

    def start(self) -> Path:
        if self._proc is not None:
            raise RuntimeError("已经在录音")
        path = runtime_dir() / f"rec-{next(_counter)}.wav"
        self._proc = subprocess.Popen(
            ["ffmpeg", "-loglevel", "error", "-f", "pulse", "-i", "default",
             "-ar", "16000", "-ac", "1", "-y", str(path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            env=_session_env(),
        )
        self._path = path
        self._started_at = time.monotonic()
        return path

    def stop(self) -> Path | None:
        """停止并返回 wav 路径。用 SIGTERM 让 ffmpeg 写完文件头，SIGKILL 会得到坏文件。"""
        proc, path = self._proc, self._path
        self._proc = self._path = None
        if proc is None:
            return None
        try:
            proc.send_signal(signal.SIGTERM)
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2)
        except Exception:
            pass
        return path if path and path.exists() else None

    def cancel(self) -> None:
        """放弃本次录音并删掉文件。"""
        path = self.stop()
        if path:
            path.unlink(missing_ok=True)
