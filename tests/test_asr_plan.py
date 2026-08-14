"""_plan()/switch() 的档位解析与设备分支是纯逻辑，不加载模型，值得单测。"""

import sys
import types

import pytest

from myna import config as config_mod
from myna.asr import Transcriber, cuda_available


def _plan(model: str, fallback: str = "small", device: str = "auto"):
    cfg = config_mod.Config()
    cfg.asr.model = model
    cfg.asr.fallback_model = fallback
    cfg.asr.device = device
    return Transcriber(cfg)._plan()


def test_plan_resolves_preset_names():
    # turbo 档位名在 _plan 里已被解析为完整 HF 模型名
    plan = _plan("turbo", device="cuda")
    assert plan == [("Systran/faster-whisper-large-v3-turbo", "cuda", "float16")]


def test_plan_passthrough_custom_repo_id():
    plan = _plan("Systran/faster-whisper-medium", device="cuda")
    assert plan == [("Systran/faster-whisper-medium", "cuda", "float16")]


def test_plan_cpu_has_fallback():
    plan = _plan("large-v3", "small", device="cpu")
    assert plan == [
        ("Systran/faster-whisper-large-v3", "cpu", "int8"),
        ("Systran/faster-whisper-small", "cpu", "int8"),
    ]


@pytest.mark.parametrize("gpu", [True, False])
def test_plan_auto_picks_device(monkeypatch, gpu):
    monkeypatch.setattr("myna.asr.cuda_available", lambda: gpu)
    plan = _plan("medium", "small", device="auto")
    if gpu:
        assert plan[0] == ("Systran/faster-whisper-medium", "cuda", "float16")
        assert plan[1] == ("Systran/faster-whisper-small", "cpu", "int8")
    else:
        assert plan == [("Systran/faster-whisper-small", "cpu", "int8")]


# ---------- 热切换 switch() ----------


class _FakeLoaded:
    def __init__(self, name: str):
        self.name = name


def test_switch_replaces_loaded_after_success(monkeypatch):
    t = Transcriber(config_mod.Config())
    t.loaded = _FakeLoaded("old")
    monkeypatch.setattr(t, "_load_named", lambda name: _FakeLoaded(name))
    got = t.switch("medium")
    assert got.name == "medium"
    assert t.loaded is got


def test_switch_rolls_back_on_failure(monkeypatch):
    """切换失败必须保留旧模型，识别能力不中断。"""
    t = Transcriber(config_mod.Config())
    old = _FakeLoaded("old")
    t.loaded = old

    def boom(name):
        raise RuntimeError("显存不足")

    monkeypatch.setattr(t, "_load_named", boom)
    with pytest.raises(RuntimeError):
        t.switch("medium")
    assert t.loaded is old


def test_load_named_does_not_touch_loaded(monkeypatch):
    """加载新模型期间 self.loaded 不动，正在转写的线程拿得到完整旧模型。"""
    fake_mod = types.ModuleType("faster_whisper")
    fake_mod.WhisperModel = lambda *a, **k: object()
    monkeypatch.setitem(sys.modules, "faster_whisper", fake_mod)

    cfg = config_mod.Config()
    cfg.asr.device = "cuda"  # 固定走 cuda 分支，不依赖本机 GPU 探测
    t = Transcriber(cfg)
    old = _FakeLoaded("old")
    t.loaded = old

    got = t._load_named("medium")
    assert t.loaded is old  # 关键：加载过程中旧模型仍是当前模型
    assert got.name == "Systran/faster-whisper-medium"
