"""状态机测试。录音、识别、注入、通知全部打桩，只验状态流转与边界判断。"""

import pytest

from myna import daemon as daemon_mod
from myna.config import Config
from myna.daemon import Daemon, State


class FakeRecorder:
    def __init__(self):
        self.recording = False
        self.elapsed = 0.0
        self.cancelled = False
        self.path = "/tmp/fake.wav"

    def start(self):
        self.recording = True
        return self.path

    def stop(self):
        self.recording = False
        return self.path

    def cancel(self):
        self.recording = False
        self.cancelled = True


@pytest.fixture
def d(monkeypatch):
    # 通知、注入都不该在测试里真的发生
    monkeypatch.setattr(daemon_mod.notify, "notify", lambda *a, **k: None)
    for name in ("recording", "transcribing", "error"):
        monkeypatch.setattr(daemon_mod.notify, name, lambda *a, **k: None)
    monkeypatch.setattr(daemon_mod.notify, "result", lambda t: None)
    dae = Daemon(Config())
    dae.recorder = FakeRecorder()
    return dae


def test_toggle_starts_then_stops(d, monkeypatch):
    finished = []
    monkeypatch.setattr(d, "_finish", lambda wav: finished.append(wav))
    # stop() 会把 _finish 丢进线程，这里直接同步调用便于断言
    monkeypatch.setattr(daemon_mod.threading, "Thread",
                        lambda target, args=(), daemon=None: type(
                            "T", (), {"start": lambda self: target(*args)})())

    assert d.toggle()["state"] == State.RECORDING.value
    assert d.state is State.RECORDING

    assert d.toggle()["state"] == State.TRANSCRIBING.value
    assert finished == ["/tmp/fake.wav"]


def test_start_twice_rejected(d):
    d.start()
    resp = d.start()
    assert not resp["ok"]
    assert d.state is State.RECORDING


def test_toggle_during_transcribing_is_ignored(d):
    """识别期间按键不排队，直接忽略——排队会让状态机复杂而用户并没得到什么。"""
    d.state = State.TRANSCRIBING
    resp = d.toggle()
    assert not resp["ok"]
    assert d.state is State.TRANSCRIBING


def test_stop_when_idle_rejected(d):
    resp = d.stop()
    assert not resp["ok"]
    assert d.state is State.IDLE


def test_cancel_discards_recording(d):
    d.start()
    resp = d.cancel()
    assert resp["ok"]
    assert d.state is State.IDLE
    assert d.recorder.cancelled


def test_cancel_when_idle_rejected(d):
    assert not d.cancel()["ok"]


def test_pipeline_skips_too_short_audio(d, monkeypatch, tmp_path):
    """太短判为误触，静默丢弃，绝不送去识别。"""
    wav = tmp_path / "a.wav"
    wav.write_bytes(b"x" * 4096)
    monkeypatch.setattr(daemon_mod, "wav_duration", lambda p: 0.1)
    called = []
    monkeypatch.setattr(d.transcriber, "transcribe", lambda p: called.append(p) or "x")

    d._pipeline(wav)
    assert called == []


def test_pipeline_injects_processed_text(d, monkeypatch, tmp_path):
    wav = tmp_path / "a.wav"
    wav.write_bytes(b"x" * 4096)
    monkeypatch.setattr(daemon_mod, "wav_duration", lambda p: 2.0)
    monkeypatch.setattr(d.transcriber, "transcribe", lambda p: " 去公园三步 ")
    d.cfg.hotwords = {"三步": "散步"}
    injected = []
    monkeypatch.setattr(daemon_mod.inject_mod, "inject",
                        lambda t, c: injected.append(t) or
                        daemon_mod.inject_mod.InjectResult(True, True, "ok"))

    d._pipeline(wav)
    assert injected == ["去公园散步"]
    assert d._last_text == "去公园散步"


def test_pipeline_no_inject_when_empty_result(d, monkeypatch, tmp_path):
    wav = tmp_path / "a.wav"
    wav.write_bytes(b"x" * 4096)
    monkeypatch.setattr(daemon_mod, "wav_duration", lambda p: 2.0)
    monkeypatch.setattr(d.transcriber, "transcribe", lambda p: "   ")
    injected = []
    monkeypatch.setattr(daemon_mod.inject_mod, "inject",
                        lambda t, c: injected.append(t))

    d._pipeline(wav)
    assert injected == []


def test_finish_always_returns_to_idle(d, monkeypatch, tmp_path):
    """转写炸了也必须回到 IDLE，否则整个输入法就卡死了。"""
    wav = tmp_path / "a.wav"
    wav.write_bytes(b"x" * 4096)
    d.state = State.TRANSCRIBING
    monkeypatch.setattr(daemon_mod, "wav_duration", lambda p: 2.0)

    def boom(p):
        raise RuntimeError("模型炸了")

    monkeypatch.setattr(d.transcriber, "transcribe", boom)
    d._finish(wav)
    assert d.state is State.IDLE
    assert not wav.exists()  # 音频不留在 tmpfs 里


def test_status_shape(d):
    s = d.status()
    assert s["ok"] and s["state"] == "idle" and "socket" in s


def test_unknown_command(d):
    assert not d.dispatch({"cmd": "飞起来"})["ok"]


# ---------- 模型热切换 switch_model ----------


class _FakeLoaded:
    def __init__(self, name: str):
        self.name = name


def _sync_thread(target, args=(), daemon=None):
    return type("T", (), {"start": lambda self: target(*args)})()


def test_switch_model_idle_starts_background_switch(d, monkeypatch):
    switched = []
    monkeypatch.setattr(d.transcriber, "switch",
                        lambda name: switched.append(name) or _FakeLoaded(name))
    monkeypatch.setattr(daemon_mod.threading, "Thread", _sync_thread)
    resp = d.switch_model("medium")
    assert resp["ok"] and resp["switching"]
    assert switched == ["medium"]
    assert d._switching is False  # 同步线程跑完已复位


def test_switch_model_rejected_while_recording(d):
    d.start()
    resp = d.switch_model("medium")
    assert not resp["ok"]
    assert "空闲" in resp["error"]
    assert d.state is State.RECORDING


def test_switch_model_rejects_reentry(d):
    d._switching = True
    resp = d.switch_model("medium")
    assert not resp["ok"]
    assert "切换" in resp["error"]


def test_switch_model_failure_notifies_and_resets(d, monkeypatch):
    notified = []
    monkeypatch.setattr(daemon_mod.notify, "error", lambda m: notified.append(m))

    def boom(name):
        raise RuntimeError("显存不足")

    monkeypatch.setattr(d.transcriber, "switch", boom)
    monkeypatch.setattr(daemon_mod.threading, "Thread", _sync_thread)
    resp = d.switch_model("turbo")
    assert resp["ok"]
    assert notified  # 失败被明确通知
    assert d._switching is False


def test_switch_model_refreshes_tray(d, monkeypatch):
    """切换完成/失败都触发状态回调，托盘借此刷新模型勾选。"""
    calls = []
    d.on_state_change = lambda s: calls.append(s)
    monkeypatch.setattr(d.transcriber, "switch", lambda name: _FakeLoaded(name))
    monkeypatch.setattr(daemon_mod.threading, "Thread", _sync_thread)
    d.switch_model("medium")
    assert calls  # 完成后调用了状态回调


def test_switch_command_dispatch(d, monkeypatch):
    monkeypatch.setattr(daemon_mod.threading, "Thread", _sync_thread)
    monkeypatch.setattr(d.transcriber, "switch", lambda name: _FakeLoaded(name))
    resp = d.dispatch({"cmd": "switch", "model": "small"})
    assert resp["ok"]
