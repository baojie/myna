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


def test_pipeline_archives_raw_and_final(d, monkeypatch, tmp_path):
    """存档里 raw 必须是后处理**之前**的——否则日后没法判断错是谁造成的。"""
    from myna import history

    d.cfg.history.dir = str(tmp_path / "store")
    wav = tmp_path / "a.wav"
    wav.write_bytes(b"x" * 4096)
    monkeypatch.setattr(daemon_mod, "wav_duration", lambda p: 2.0)
    monkeypatch.setattr(d.transcriber, "transcribe", lambda p: " 去公园三步 ")
    d.cfg.hotwords = {"三步": "散步"}
    monkeypatch.setattr(daemon_mod.inject_mod, "inject",
                        lambda t, c: daemon_mod.inject_mod.InjectResult(True, True, "ok"))

    d._pipeline(wav)
    rows = history.read_recent(10, d.cfg)
    assert len(rows) == 1
    assert rows[0]["raw"] == " 去公园三步 "
    assert rows[0]["text"] == "去公园散步"
    assert rows[0]["injected"] == "ok"


def test_pipeline_archive_failure_does_not_break_injection(d, monkeypatch, tmp_path):
    """存档挂了也得把字打出来——它是附加功能，不是前置条件。"""
    wav = tmp_path / "a.wav"
    wav.write_bytes(b"x" * 4096)
    monkeypatch.setattr(daemon_mod, "wav_duration", lambda p: 2.0)
    monkeypatch.setattr(d.transcriber, "transcribe", lambda p: "你好")
    monkeypatch.setattr(daemon_mod.history, "record",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("盘满了")))
    injected = []
    monkeypatch.setattr(daemon_mod.inject_mod, "inject",
                        lambda t, c: injected.append(t) or
                        daemon_mod.inject_mod.InjectResult(True, True, "ok"))

    d._pipeline(wav)
    assert injected == ["你好"]


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


# ---------- 托盘退出不该拖垮 daemon ----------
#
# 真实事故：点了托盘菜单里某一项，整个服务就没了，而且再也不自己起来。
# 原因是 run() 里 `try: tray.run() finally: d.shutdown()`——GTK 主循环无论
# 因为什么返回（GTK 内部致命错误、AppIndicator 出问题），daemon 都跟着关，
# 而退出码是 0，systemd 的 Restart=on-failure 也不来救。


class FakeTray:
    """托盘替身：run() 立刻返回，模拟 GTK 主循环意外结束。"""

    def __init__(self, daemon, user_quit=False):
        self.user_quit = user_quit
        self.ran = False

    def run(self):
        self.ran = True

    def quit(self):
        pass


@pytest.fixture
def run_with_tray(monkeypatch, tmp_path):
    """把 run() 的重活（加载模型、真开 socket）全打桩，只留托盘那段收尾逻辑。"""
    import threading

    def _go(user_quit: bool, serve_blocks: bool = False):
        monkeypatch.setattr(daemon_mod.notify, "notify", lambda *a, **k: None)
        # 别撞上机器上真在跑的那个 daemon——它会让 run() 直接返回 1
        monkeypatch.setattr(daemon_mod, "socket_path",
                            lambda: tmp_path / "control.sock")
        monkeypatch.setattr(Daemon, "preload", lambda self: None)

        served = threading.Event()
        stopped = []

        def fake_serve(self):
            served.set()
            if serve_blocks:
                self._stop.wait(5)  # 模拟一直服务着，直到被 shutdown

        monkeypatch.setattr(Daemon, "serve", fake_serve)

        real_shutdown = Daemon.shutdown

        def spy_shutdown(self):
            stopped.append(True)
            real_shutdown(self)

        monkeypatch.setattr(Daemon, "shutdown", spy_shutdown)

        tray = FakeTray(None, user_quit=user_quit)
        import myna.tray as tray_mod

        monkeypatch.setattr(tray_mod, "available", lambda: True)
        monkeypatch.setattr(tray_mod, "Tray", lambda d: tray)

        cfg = Config()
        cfg.tray.enabled = True
        rc = daemon_mod.run(cfg)
        return rc, tray, served.is_set(), bool(stopped)

    return _go


def test_tray_loss_keeps_daemon_serving(run_with_tray):
    """托盘自己没了，语音输入必须继续——它是配角，不是开关。"""
    rc, tray, served, was_shutdown = run_with_tray(user_quit=False)
    assert rc == 0 and tray.ran and served
    assert not was_shutdown, "托盘意外退出不该关掉 daemon"


def test_user_quit_stops_daemon(run_with_tray):
    """点了「退出 myna」就得真的退出，否则退出菜单形同虚设。"""
    rc, tray, served, was_shutdown = run_with_tray(user_quit=True)
    assert rc == 0 and served
    assert was_shutdown
