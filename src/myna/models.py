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
    "turbo": "Systran/faster-whisper-large-v3-turbo",
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
