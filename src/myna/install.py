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
# 不要用 <Super>d：GNOME 自带的「显示桌面」就占着它，绑上去按了只会最小化窗口。
# <Super>z 实测空闲，且左手单手可及。
DEFAULT_BINDING = "<Super>z"

# 扫这些 schema 找冲突。GNOME 的快捷键散落在好几个 schema 里，只看一个会漏。
KEYBINDING_SCHEMAS = (
    "org.gnome.desktop.wm.keybindings",
    "org.gnome.shell.keybindings",
    "org.gnome.settings-daemon.plugins.media-keys",
    "org.gnome.mutter.keybindings",
    "org.gnome.mutter.wayland.keybindings",
)


def find_conflicts(binding: str) -> list[str]:
    """找出还有谁占着这个键，返回 "schema 项名" 的列表。

    绑一个已被占用的键，症状是「按了没反应」或者「按了在干别的事」，
    而配置看起来完全正常——很难查。装之前就该拦住。
    """
    hits = []
    for schema in KEYBINDING_SCHEMAS:
        try:
            r = subprocess.run(["gsettings", "list-recursively", schema],
                               capture_output=True, text=True, timeout=10)
        except Exception:
            continue
        if r.returncode != 0:
            continue
        for line in r.stdout.splitlines():
            parts = line.split(None, 2)
            if len(parts) < 3:
                continue
            sch, key, value = parts
            # 值形如 ['<Super>d', ...]，要精确匹配整个键位而非子串，
            # 否则 <Super>d 会误命中 <Primary><Super>d
            if f"'{binding}'" in value:
                hits.append(f"{sch} {key}")

    # 自定义快捷键各自住在带路径的子 schema 里，list-recursively 扫不到，
    # 得顺着列表逐个查——否则两个自定义键撞在一起完全发现不了
    try:
        for path in _parse_list(_gsettings("get", SCHEMA, "custom-keybindings")):
            target = f"{CUSTOM_SCHEMA}:{path}"
            try:
                if _gsettings("get", target, "binding").strip("'\"") != binding:
                    continue
                name = _gsettings("get", target, "name").strip("'\"")
            except RuntimeError:
                continue
            hits.append(f"{SCHEMA} custom-keybindings（自定义快捷键「{name}」）")
    except RuntimeError:
        pass

    return hits

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


def install_shortcut(binding: str = DEFAULT_BINDING, *, force: bool = False) -> str:
    """写入 GNOME 自定义快捷键，幂等：已有同名条目就地更新，不重复追加。

    绑之前先查冲突：绑一个已被占用的键，写入和回读都会成功，但按下去是别人
    响应——这种「配置看着全对，功能就是不工作」最难查。宁可在这里拦住。
    """
    conflicts = [c for c in find_conflicts(binding)
                 if not c.startswith(f"{SCHEMA} custom-keybindings")]
    if conflicts and not force:
        raise RuntimeError(
            f"{binding} 已经被占用：\n  " + "\n  ".join(conflicts)
            + "\n\n换一个键（myna install --key '<Super>z'），"
              "或先在「设置 → 键盘」里解绑；确实要覆盖用 --force。")

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
