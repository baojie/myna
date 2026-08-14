"""识别结果后处理：简繁转换 → 热词替换 → 清理。纯逻辑，可单测。"""

from __future__ import annotations

import re

from .config import Config

_converter = None
_converter_tried = False


def _to_simplified(text: str) -> str:
    """繁转简。opencc 装了就用，没装就原样返回——不因为缺个可选依赖就报错。"""
    global _converter, _converter_tried
    if not _converter_tried:
        _converter_tried = True
        try:
            import opencc  # type: ignore

            _converter = opencc.OpenCC("t2s")
        except Exception:
            _converter = None
    if _converter is None:
        return text
    try:
        return _converter.convert(text)
    except Exception:
        return text


def apply_hotwords(text: str, hotwords: dict[str, str]) -> str:
    """用户词表替换，解决人名、术语反复听错。长词优先，避免短词先替换吃掉长词。"""
    for wrong in sorted(hotwords, key=len, reverse=True):
        if wrong:
            text = text.replace(wrong, hotwords[wrong])
    return text


def clean(text: str) -> str:
    text = text.strip()
    # whisper 偶尔在整句外面套一对引号
    if len(text) >= 2 and text[0] in "\"“'" and text[-1] in "\"”'":
        text = text[1:-1].strip()
    # 段间换行压成一行，输入框里不需要它们
    text = re.sub(r"\s*\n\s*", "", text)
    return text.strip()


def process(text: str, cfg: Config) -> str:
    if cfg.postprocess.to_simplified:
        text = _to_simplified(text)
    if cfg.hotwords:
        text = apply_hotwords(text, cfg.hotwords)
    if cfg.postprocess.strip:
        text = clean(text)
    return text
