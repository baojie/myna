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
    paste_key: str = "ctrl+v"
    restore_clipboard: bool = True
    # 按窗口类覆盖粘贴键，例如 { "org.gnome.Console" = "ctrl+shift+v" }
    paste_key_by_app: dict[str, str] = field(default_factory=dict)


@dataclass
class PostprocessConfig:
    to_simplified: bool = True
    strip: bool = True


@dataclass
class Config:
    asr: AsrConfig = field(default_factory=AsrConfig)
    audio: AudioConfig = field(default_factory=AudioConfig)
    inject: InjectConfig = field(default_factory=InjectConfig)
    postprocess: PostprocessConfig = field(default_factory=PostprocessConfig)
    hotwords: dict[str, str] = field(default_factory=dict)


def _build(cls, data: dict):
    """只取 dataclass 认识的键，多余键忽略（配置写错不至于起不来）。"""
    known = {f.name for f in fields(cls)}
    return cls(**{k: v for k, v in data.items() if k in known})


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
        hotwords={str(k): str(v) for k, v in raw.get("hotwords", {}).items()},
    )
