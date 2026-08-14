"""安装：systemd user unit + GNOME 快捷键；以及依赖自检 doctor。

现有 whisper-dictate 方案最后断在这一环——GNOME 里那条自定义快捷键
name 和 command 都对，binding 却是空字符串，等于没绑。这里必须真正绑上并回读确认。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from .config import CONFIG_PATH, socket_path

SCHEMA = "org.gnome.settings-daemon.plugins.media-keys"
CUSTOM_SCHEMA = f"{SCHEMA}.custom-keybinding"
PREFIX = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/"
KEY_NAME = "myna 语音输入"
DEFAULT_BINDING = "<Super>d"

UNIT_PATH = Path.home() / ".config" / "systemd" / "user" / "myna.service"

UNIT = """[Unit]
Description=myna 八哥语音输入守护进程
After=graphical-session.target
PartOf=graphical-session.target

[Service]
Type=simple
ExecStart={exe} daemon
Restart=on-failure
RestartSec=3

[Install]
WantedBy=graphical-session.target
"""


def _gsettings(*args: str) -> str:
    r = subprocess.run(["gsettings", *args], capture_output=True, text=True, timeout=10)
    if r.returncode != 0:
        raise RuntimeError(f"gsettings {' '.join(args)} 失败：{r.stderr.strip()}")
    return r.stdout.strip()


def _parse_list(raw: str) -> list[str]:
    raw = raw.strip()
    if raw in ("@as []", "[]", ""):
        return []
    return [p.strip().strip("'\"") for p in raw.strip("[]").split(",") if p.strip()]


def _myna_exe() -> str:
    """快捷键要在没有 shell 环境的情况下执行，必须用绝对路径。"""
    exe = shutil.which("myna")
    if exe:
        return exe
    return f"{sys.executable} -m myna"


def install_shortcut(binding: str = DEFAULT_BINDING) -> str:
    """写入 GNOME 自定义快捷键，幂等：已有同名条目就地更新，不重复追加。"""
    existing = _parse_list(_gsettings("get", SCHEMA, "custom-keybindings"))

    slot = None
    for path in existing:
        try:
            name = _gsettings("get", f"{CUSTOM_SCHEMA}:{path}", "name").strip("'\"")
        except RuntimeError:
            continue
        if name == KEY_NAME:
            slot = path
            break

    if slot is None:
        used = {p for p in existing}
        i = 0
        while f"{PREFIX}custom{i}/" in used:
            i += 1
        slot = f"{PREFIX}custom{i}/"
        existing.append(slot)
        value = "[" + ", ".join(f"'{p}'" for p in existing) + "]"
        _gsettings("set", SCHEMA, "custom-keybindings", value)

    target = f"{CUSTOM_SCHEMA}:{slot}"
    _gsettings("set", target, "name", KEY_NAME)
    _gsettings("set", target, "command", f"{_myna_exe()} toggle")
    _gsettings("set", target, "binding", binding)

    # 回读确认——空 binding 正是历史上出问题的地方，必须验证真的写进去了
    got = _gsettings("get", target, "binding").strip("'\"")
    if got != binding:
        raise RuntimeError(f"快捷键写入后回读不符：期望 {binding}，实际 {got!r}")
    return slot


def remove_shortcut() -> bool:
    existing = _parse_list(_gsettings("get", SCHEMA, "custom-keybindings"))
    kept = []
    removed = False
    for path in existing:
        try:
            name = _gsettings("get", f"{CUSTOM_SCHEMA}:{path}", "name").strip("'\"")
        except RuntimeError:
            kept.append(path)
            continue
        if name == KEY_NAME:
            removed = True
        else:
            kept.append(path)
    value = "[" + ", ".join(f"'{p}'" for p in kept) + "]" if kept else "@as []"
    _gsettings("set", SCHEMA, "custom-keybindings", value)
    return removed


def install_service() -> Path:
    UNIT_PATH.parent.mkdir(parents=True, exist_ok=True)
    UNIT_PATH.write_text(UNIT.format(exe=_myna_exe()), encoding="utf-8")
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=False, timeout=30)
    subprocess.run(["systemctl", "--user", "enable", "--now", "myna.service"],
                   check=False, timeout=30)
    return UNIT_PATH


def remove_service() -> None:
    subprocess.run(["systemctl", "--user", "disable", "--now", "myna.service"],
                   check=False, timeout=30)
    UNIT_PATH.unlink(missing_ok=True)
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=False, timeout=30)


def doctor() -> list[tuple[bool, str, str]]:
    """返回 (是否通过, 项目, 说明/修复建议)。"""
    out: list[tuple[bool, str, str]] = []

    for prog, hint in [
        ("ffmpeg", "录音必需：sudo apt install ffmpeg"),
        ("ydotool", "Wayland 下模拟按键必需：sudo apt install ydotool"),
        ("wl-copy", "Wayland 剪贴板：sudo apt install wl-clipboard"),
        ("notify-send", "桌面通知：sudo apt install libnotify-bin"),
    ]:
        ok = shutil.which(prog) is not None
        out.append((ok, prog, "已安装" if ok else hint))

    for prog in ("xdotool", "xclip"):
        ok = shutil.which(prog) is not None
        out.append((True, f"{prog}（可选兜底）", "已安装" if ok else "未安装，仅影响 X11 兜底"))

    sock = Path(f"/run/user/{os.getuid()}/.ydotool_socket")
    out.append((sock.exists(), "ydotoold socket",
                str(sock) if sock.exists()
                else "ydotoold 没在跑：systemctl --user enable --now ydotool"))

    try:
        import faster_whisper  # noqa: F401
        out.append((True, "faster-whisper", "已安装"))
    except Exception:
        out.append((False, "faster-whisper", "pip install faster-whisper"))

    from .asr import cuda_available
    from .config import load as load_config

    cfg = load_config()
    gpu = cuda_available()
    out.append((True, "CUDA",
                f"可用，将用 {cfg.asr.model} / float16"
                if gpu else f"不可用，将降级 {cfg.asr.fallback_model} / CPU（精度下降）"))

    try:
        import opencc  # noqa: F401
        out.append((True, "opencc（可选）", "已安装，繁转简可用"))
    except Exception:
        out.append((True, "opencc（可选）", "未安装，繁转简将跳过：pip install opencc-python-reimplemented"))

    hf = Path.home() / ".cache" / "huggingface"
    where = f" -> {os.readlink(hf)}" if hf.is_symlink() else ""
    out.append((hf.exists(), "模型缓存", f"{hf}{where}" if hf.exists() else "尚未下载模型，首次运行会自动下载"))

    out.append((True, "配置文件", str(CONFIG_PATH) if CONFIG_PATH.exists()
                else f"{CONFIG_PATH}（不存在，使用默认值）"))

    from .client import ping

    alive = ping()
    out.append((alive, "守护进程",
                f"运行中，socket {socket_path()}"
                if alive else "未运行：systemctl --user start myna"))

    try:
        existing = _parse_list(_gsettings("get", SCHEMA, "custom-keybindings"))
        found = ""
        for path in existing:
            try:
                if _gsettings("get", f"{CUSTOM_SCHEMA}:{path}", "name").strip("'\"") == KEY_NAME:
                    found = _gsettings("get", f"{CUSTOM_SCHEMA}:{path}", "binding").strip("'\"")
                    break
            except RuntimeError:
                continue
        out.append((bool(found), "快捷键",
                    f"已绑定 {found}" if found else "未绑定（或 binding 为空）：myna install"))
    except Exception as e:
        out.append((False, "快捷键", f"读取失败：{e}"))

    return out
