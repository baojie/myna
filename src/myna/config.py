"""配置加载。全部字段可选，缺省即默认值。"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path

CONFIG_PATH = Path(
    os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")
) / "myna" / "config.toml"


def runtime_dir() -> Path:
    """运行时目录，优先 tmpfs，录音不落盘。"""
    base = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    d = Path(base) / "myna"
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError:
        d = Path("/tmp") / f"myna-{os.getuid()}"
        d.mkdir(parents=True, exist_ok=True)
    return d


def socket_path() -> Path:
    return runtime_dir() / "control.sock"


@dataclass
class AsrConfig:
    model: str = "large-v3"
    fallback_model: str = "small"
    device: str = "auto"  # auto | cuda | cpu
    language: str = "zh"
    # 实测：这句 prompt 同时解决了繁体输出和 small 档的误识，是必需参数而非优化
    initial_prompt: str = "以下是简体中文的句子。"
    beam_size: int = 5
    vad_filter: bool = True


@dataclass
class AudioConfig:
    max_seconds: float = 120.0
    min_seconds: float = 0.3


@dataclass
class InjectConfig:
    method: str = "clipboard"  # clipboard | type
    # 默认用 shift+insert：终端和普通输入框都认它，省掉「进终端前先切一下」。
    # 代价是终端那边粘的是 PRIMARY，所以 inject() 会把两份 selection 都写上
    paste_key: str = "shift+insert"
    restore_clipboard: bool = True
    # 按窗口类覆盖粘贴键，例如 { "org.gnome.Console" = "ctrl+shift+v" }
    paste_key_by_app: dict[str, str] = field(default_factory=dict)


@dataclass
class PostprocessConfig:
    to_simplified: bool = True
    strip: bool = True


@dataclass
class HistoryConfig:
    """识别历史存档。默认开着——攒下来的记录是日后批量纠错唯一的原料。"""

    enabled: bool = True
    # 音频默认不存：存了才能重跑新模型做 A/B，但也最占盘。要做 batch 纠错
    # 建议打开，并把 dir 指到空间富裕的盘
    save_audio: bool = False
    max_audio_mb: float = 200.0  # 音频总量上限，超了删最旧的
    dir: str = ""  # 留空即 ~/.local/share/myna


@dataclass
class TrayConfig:
    # 顶栏状态图标。缺 GTK/AppIndicator 时自动跳过，不影响 daemon 本身
    enabled: bool = True


@dataclass
class Config:
    asr: AsrConfig = field(default_factory=AsrConfig)
    audio: AudioConfig = field(default_factory=AudioConfig)
    inject: InjectConfig = field(default_factory=InjectConfig)
    postprocess: PostprocessConfig = field(default_factory=PostprocessConfig)
    tray: TrayConfig = field(default_factory=TrayConfig)
    history: HistoryConfig = field(default_factory=HistoryConfig)
    hotwords: dict[str, str] = field(default_factory=dict)


def _build(cls, data: dict):
    """只取 dataclass 认识的键，多余键忽略（配置写错不至于起不来）。"""
    known = {f.name for f in fields(cls)}
    return cls(**{k: v for k, v in data.items() if k in known})


def save_paste_key(key: str, path: Path | None = None) -> None:
    """把粘贴键写回配置文件，让托盘里的切换在重启后仍然有效。

    这里是**定点文本替换**而不是重新序列化整个 TOML：用户的注释和排版比
    我们的整洁更值钱，重写一遍会把它们全抹掉。代价是只认得 `[inject]` 段里
    顶格的 `paste_key = ...`，格式古怪时退化为追加一段——够用。
    """
    import re

    path = path or CONFIG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    line = f'paste_key = "{key}"'

    # 只在 [inject] 段内替换：paste_key 这个名字在 [inject.paste_key_by_app]
    # 的子表里也会出现，跨段乱改会写坏用户配置
    # 注意 [ \t]* 而不是 \s*：多行模式下 \s 会把行尾换行符一起吃掉，
    # 拼接时就会把 [inject] 和下一行黏成一行，写出语法坏掉的 TOML
    m = re.search(r"^\[inject\][ \t]*$", text, re.M)
    if m:
        start = m.end()  # 停在 [inject] 行尾，换行符之前
        rest = text[start:]
        next_section = re.search(r"^\[", rest, re.M)
        end = start + (next_section.start() if next_section else len(rest))
        section = text[start:end]
        if re.search(r"^[ \t]*paste_key[ \t]*=", section, re.M):
            section = re.sub(r"^[ \t]*paste_key[ \t]*=.*$", line, section,
                             count=1, flags=re.M)
        else:
            section = "\n" + line + section
        text = text[:start] + section + text[end:]
    else:
        text = (text.rstrip() + f"\n\n[inject]\n{line}\n").lstrip("\n")

    path.write_text(text, encoding="utf-8")


def load(path: Path | None = None) -> Config:
    path = path or CONFIG_PATH
    if not path.exists():
        return Config()
    with path.open("rb") as f:
        raw = tomllib.load(f)
    return Config(
        asr=_build(AsrConfig, raw.get("asr", {})),
        audio=_build(AudioConfig, raw.get("audio", {})),
        inject=_build(InjectConfig, raw.get("inject", {})),
        postprocess=_build(PostprocessConfig, raw.get("postprocess", {})),
        tray=_build(TrayConfig, raw.get("tray", {})),
        history=_build(HistoryConfig, raw.get("history", {})),
        hotwords={str(k): str(v) for k, v in raw.get("hotwords", {}).items()},
    )
