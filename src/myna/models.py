"""模型档位预设。

配置里 `[asr] model = "turbo"` 填档位名即可，自动解析为 faster-whisper
完整模型名（HF repo id）。档位表外的名字原样透传——等于支持任意
HuggingFace 模型名，自由度不丢。

档位按本机实测定位（RTX 4060 8G，中文音频）：GPU 上 large-v3 与 medium
同为 0.7s 转写，没必要为省事将就精度；turbo 更快但精度略降；CPU 上
small 最快、large 不现实。
"""

from __future__ import annotations

# 档位名 → faster-whisper 完整模型名（HF repo id）
PRESETS: dict[str, str] = {
    # Systran 没有出 turbo 的 CTranslate2 版本（曾误写成
    # Systran/faster-whisper-large-v3-turbo，那个仓库根本不存在，下载会 401）。
    # deepdml 这个是社区通行的转换版，faster-whisper 文档用的也是它。
    "turbo": "deepdml/faster-whisper-large-v3-turbo-ct2",
    "large-v3": "Systran/faster-whisper-large-v3",
    "large-v2": "Systran/faster-whisper-large-v2",
    "medium": "Systran/faster-whisper-medium",
    "small": "Systran/faster-whisper-small",
    "base": "Systran/faster-whisper-base",
    "tiny": "Systran/faster-whisper-tiny",
}

# faster-whisper 认识的其余短名（不在档位表里，但配置它也能用）
_EXTRA_SIZES = {
    "tiny.en",
    "base.en",
    "small.en",
    "medium.en",
    "large",
    "large-v1",
    "distil-small.en",
    "distil-medium.en",
    "distil-large-v2",
    "distil-large-v3",
}


# 各档位的大致下载体积，用于下载前告知用户。数字是 HF 仓库实际占用，
# 已下载的会以磁盘实测值覆盖，这里只用于「还没下载」时给个预期。
APPROX_SIZES: dict[str, str] = {
    "turbo": "1.6G",
    "large-v3": "2.9G",
    "large-v2": "2.9G",
    "medium": "1.5G",
    "small": "464M",
    "base": "145M",
    "tiny": "75M",
}


def cache_root() -> "Path":
    """HuggingFace 缓存根目录（本机是指向 /data 的符号链接）。"""
    from pathlib import Path

    import os

    env = os.environ.get("HF_HUB_CACHE") or os.environ.get("HF_HOME")
    if env:
        p = Path(env)
        return p / "hub" if p.name != "hub" else p
    return Path.home() / ".cache" / "huggingface" / "hub"


def cache_dir(name: str) -> "Path":
    """某个模型在缓存里的目录，形如 models--Systran--faster-whisper-turbo。"""
    repo = resolve_model(name)
    return cache_root() / ("models--" + repo.replace("/", "--"))


def is_downloaded(name: str) -> bool:
    """是否已完整下载。

    只看目录存在是不够的——中断的下载会留下空壳目录（只有 refs/blobs 而
    没有真正的权重）。以 snapshots 下存在 model.bin 为准。
    """
    d = cache_dir(name)
    snap = d / "snapshots"
    if not snap.is_dir():
        return False
    return any(snap.glob("*/model.bin"))


def disk_size(name: str) -> str | None:
    """已下载模型在磁盘上的实际大小，未下载返回 None。"""
    d = cache_dir(name)
    if not d.is_dir():
        return None
    total = 0
    for f in d.rglob("*"):
        try:
            if f.is_file() and not f.is_symlink():
                total += f.stat().st_size
        except OSError:
            continue
    if total == 0:
        return None
    for unit in ("B", "K", "M", "G"):
        if total < 1024 or unit == "G":
            return f"{total:.0f}{unit}" if unit in "BK" else f"{total:.1f}{unit}"
        total /= 1024
    return None


def describe(name: str) -> dict:
    """一个档位的完整说明，供下载对话框与 CLI 展示。"""
    return {
        "preset": name,
        "repo": resolve_model(name),
        "downloaded": is_downloaded(name),
        "size": disk_size(name) or APPROX_SIZES.get(name, "未知"),
        "path": str(cache_dir(name)),
    }


def resolve_model(name: str) -> str:
    """档位名 → 完整 HF 模型名；未知名字原样返回（透传任意 repo id）。"""
    return PRESETS.get(name, name)


def known(name: str) -> bool:
    """这个名字是否在档位表、faster-whisper 标准短名、或完整 repo id 里。"""
    return name in PRESETS or name in _EXTRA_SIZES or name.startswith(
        "Systran/faster-whisper-"
    )


def presets_help() -> str:
    """档位清单，用于错误提示与 doctor。"""
    return " | ".join(PRESETS)
