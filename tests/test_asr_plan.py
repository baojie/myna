"""_plan() 的档位解析与设备分支是纯逻辑，不加载模型，值得单测。"""

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
