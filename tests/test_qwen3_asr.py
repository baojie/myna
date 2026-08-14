"""Qwen3-ASR（ONNX）后端的档位注册、缓存判断与管线单元测试。

不加载真实模型、不依赖 onnxruntime/librosa/tokenizers（本机网络不稳暂未装），
用假 onnx session 和假 tokenizer 测纯逻辑。真实速度/准确率待模型下载后实测。
"""

import sys
import types
from pathlib import Path

import pytest

from myna import asr as asr_mod
from myna import models as models_mod
from myna.config import Config


# ---------- 档位与模型识别 ----------


def test_qwen3_preset_registered():
    assert models_mod.PRESETS["qwen3"] == "Daumee/Qwen3-ASR-0.6B-ONNX-CPU"
    assert models_mod.APPROX_SIZES["qwen3"] == "2.5G"


def test_is_qwen3():
    assert models_mod.is_qwen3("qwen3")
    assert models_mod.is_qwen3("Daumee/Qwen3-ASR-0.6B-ONNX-CPU")
    assert not models_mod.is_qwen3("medium")
    assert not models_mod.is_qwen3("Systran/faster-whisper-medium")


# ---------- 缓存判断（ONNX 目录结构） ----------


def test_is_downloaded_qwen3(tmp_path, monkeypatch):
    monkeypatch.setattr(models_mod, "cache_dir", lambda n: tmp_path)
    assert not models_mod.is_downloaded("qwen3")
    snap = tmp_path / "snapshots" / "rev1"
    (snap / "onnx_models").mkdir(parents=True)
    assert not models_mod.is_downloaded("qwen3")  # 空壳目录不算
    (snap / "onnx_models" / "decoder_step.int8.onnx").touch()
    assert models_mod.is_downloaded("qwen3")


def test_is_downloaded_whisper_unchanged(tmp_path, monkeypatch):
    monkeypatch.setattr(models_mod, "cache_dir", lambda n: tmp_path)
    snap = tmp_path / "snapshots" / "rev1"
    snap.mkdir(parents=True)
    assert not models_mod.is_downloaded("medium")
    (snap / "model.bin").touch()
    assert models_mod.is_downloaded("medium")


# ---------- asr 路由 ----------


def test_load_named_routes_qwen3(monkeypatch):
    calls = []
    monkeypatch.setattr(asr_mod, "is_qwen3", lambda n: True)
    monkeypatch.setattr(asr_mod.Transcriber, "_load_qwen3",
                        lambda self, n: calls.append(n) or "qwen3-loaded")
    t = asr_mod.Transcriber(Config())
    assert t._load_named("qwen3") == "qwen3-loaded"
    assert calls == ["qwen3"]


def test_load_qwen3_downloads_when_missing(monkeypatch, tmp_path):
    import myna.qwen3_asr as q3

    dl = []
    snap = tmp_path / "snap"
    (snap / "onnx_models").mkdir(parents=True)
    (snap / "onnx_models" / "encoder_conv.onnx").touch()
    monkeypatch.setattr(models_mod, "is_downloaded", lambda n: False)
    monkeypatch.setattr(models_mod, "download", lambda n, **k: dl.append(n))
    monkeypatch.setattr(models_mod, "snapshot_dir", lambda n: snap)

    class FakeQ3:
        def __init__(self, onnx_dir, **kw):
            self.onnx_dir = onnx_dir

    monkeypatch.setattr(q3, "Qwen3Asr", FakeQ3)

    t = asr_mod.Transcriber(Config())
    loaded = t._load_qwen3("qwen3")
    assert dl == ["qwen3"]
    assert loaded.device == "cpu"
    assert loaded.model.onnx_dir == snap / "onnx_models"


def test_load_qwen3_uses_cache_when_downloaded(monkeypatch, tmp_path):
    import myna.qwen3_asr as q3

    dl = []
    snap = tmp_path / "snap"
    (snap / "onnx_models").mkdir(parents=True)
    monkeypatch.setattr(models_mod, "is_downloaded", lambda n: True)
    monkeypatch.setattr(models_mod, "download", lambda n, **k: dl.append(n))
    monkeypatch.setattr(models_mod, "snapshot_dir", lambda n: snap)

    class FakeQ3:
        def __init__(self, onnx_dir, **kw):
            self.onnx_dir = onnx_dir

    monkeypatch.setattr(q3, "Qwen3Asr", FakeQ3)

    t = asr_mod.Transcriber(Config())
    loaded = t._load_qwen3("qwen3")
    assert dl == []
    assert loaded.model.onnx_dir == snap / "onnx_models"


# ---------- qwen3_asr 纯逻辑 ----------


class _FakeTok:
    def encode(self, text: str) -> list:
        return [ord(c) for c in text]

    def decode(self, ids: list) -> str:
        return "".join(chr(i) for i in ids)


def _bare():
    """绕过 __init__（不碰 onnxruntime/librosa），只挂假 tokenizer。"""
    from myna.qwen3_asr import Qwen3Asr

    a = Qwen3Asr.__new__(Qwen3Asr)
    a.tokenizer = _FakeTok()
    a.language = None
    return a


def test_prompt_build_structure():
    from myna.qwen3_asr import (AUDIO_END_ID, AUDIO_PAD_ID, AUDIO_START_ID,
                                IM_END_ID, IM_START_ID, NEWLINE_ID)

    a = _bare()
    ids = a._build_prompt_ids(5)
    assert ids[0] == IM_START_ID  # <|im_start|> 开头
    assert ids.count(AUDIO_PAD_ID) == 5  # 音频占位恰好 num_audio_tokens 个
    assert ids.index(AUDIO_START_ID) < ids.index(AUDIO_PAD_ID) < ids.index(AUDIO_END_ID)
    assert NEWLINE_ID in ids and IM_END_ID in ids

    # 语言提示只在配置了 language 时出现
    a.language = "zh"
    ids_zh = a._build_prompt_ids(5)
    assert "language" in _FakeTok().decode(ids_zh)


def test_parse_splits_language_and_text():
    a = _bare()
    r = a._parse([1], "language zh<asr_text>你好世界")
    assert r["language"] == "zh"
    assert r["text"] == "你好世界"

    r2 = a._parse([1], "直接是文本")
    assert r2["language"] == ""
    assert r2["text"] == "直接是文本"


# ---------- 依赖与完整性报错 ----------


def test_qwen3_asr_reports_missing_deps(monkeypatch, tmp_path):
    from myna.qwen3_asr import Qwen3Asr

    monkeypatch.setattr("myna.qwen3_asr._import_ok", lambda name: False)
    with pytest.raises(RuntimeError, match="onnxruntime"):
        Qwen3Asr(tmp_path)


def test_qwen3_asr_reports_incomplete_files(monkeypatch, tmp_path):
    """依赖都在但模型文件不全，得说人话而不是撞 ONNX 的 IndexError。"""
    from myna.qwen3_asr import Qwen3Asr

    monkeypatch.setattr("myna.qwen3_asr._import_ok", lambda name: True)
    # 伪装 onnxruntime，让流程走到文件检查那一步
    fake = types.ModuleType("onnxruntime")
    fake.SessionOptions = type("SessionOptions", (), {})
    fake.GraphOptimizationLevel = type("G", (), {"ORT_ENABLE_ALL": 1})
    fake.InferenceSession = type("InferenceSession", (), {"__init__": lambda *a, **k: None})
    monkeypatch.setitem(sys.modules, "onnxruntime", fake)
    with pytest.raises(RuntimeError, match="不完整"):
        Qwen3Asr(tmp_path)
