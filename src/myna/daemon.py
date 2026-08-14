"""守护进程：常驻预加载模型，监听 Unix socket，跑 IDLE→RECORDING→TRANSCRIBING 状态机。

分成 daemon + 瘦客户端是因为两个约束撞在一起：快捷键必须立刻返回（否则桌面卡），
模型加载要 10~13s（所以不能每次现加载）。只有常驻这一种解法。
"""

from __future__ import annotations

import json
import logging
import os
import socket
import threading
import time
from enum import Enum
from pathlib import Path

from . import inject as inject_mod
from . import notify, postprocess
from .asr import Transcriber
from .audio import Recorder, wav_duration
from .config import Config, socket_path

log = logging.getLogger("myna")


class State(str, Enum):
    IDLE = "idle"
    RECORDING = "recording"
    TRANSCRIBING = "transcribing"


class Daemon:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.state = State.IDLE
        self.recorder = Recorder()
        self.transcriber = Transcriber(cfg)
        self.lock = threading.Lock()
        self._stop = threading.Event()
        self._server: socket.socket | None = None
        self._last_text = ""

    # ---------- 生命周期 ----------

    def preload(self) -> None:
        """启动时就把模型读进显存，用户第一次按键不该等十几秒。"""
        try:
            t = time.monotonic()
            loaded = self.transcriber.load()
            log.info("模型已加载：%s / %s / %s（%.1fs）",
                     loaded.name, loaded.device, loaded.compute_type, time.monotonic() - t)
            if loaded.degraded:
                # 降级必须可见，否则用户会以为精度天生就这样
                notify.notify(
                    f"⚠️ 已降级到 {loaded.name} / {loaded.device}，识别精度会下降")
        except Exception as e:
            log.error("模型加载失败：%s", e)
            notify.error(f"模型加载失败：{e}")

    def serve(self) -> None:
        path = socket_path()
        path.unlink(missing_ok=True)
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(str(path))
        os.chmod(path, 0o600)  # 仅本用户
        srv.listen(8)
        srv.settimeout(0.5)
        self._server = srv
        log.info("监听 %s", path)

        threading.Thread(target=self._watchdog, daemon=True).start()

        try:
            while not self._stop.is_set():
                try:
                    conn, _ = srv.accept()
                except socket.timeout:
                    continue
                except OSError:
                    break
                threading.Thread(target=self._handle, args=(conn,), daemon=True).start()
        finally:
            srv.close()
            path.unlink(missing_ok=True)
            log.info("已退出")

    def shutdown(self) -> None:
        self._stop.set()
        with self.lock:
            if self.state is State.RECORDING:
                self.recorder.cancel()

    def _watchdog(self) -> None:
        """录太久自动收尾，防止用户忘了关还在录。"""
        while not self._stop.wait(1.0):
            with self.lock:
                over = (self.state is State.RECORDING
                        and self.recorder.elapsed > self.cfg.audio.max_seconds)
            if over:
                log.info("录音超过 %.0fs，自动停止", self.cfg.audio.max_seconds)
                notify.notify("⏱ 录音超时，自动结束")
                self.stop()

    # ---------- 协议 ----------

    def _handle(self, conn: socket.socket) -> None:
        try:
            conn.settimeout(5)
            data = conn.recv(65536).decode("utf-8", "replace").strip()
            try:
                req = json.loads(data) if data else {}
            except json.JSONDecodeError:
                req = {}
            resp = self.dispatch(req)
            conn.sendall((json.dumps(resp, ensure_ascii=False) + "\n").encode())
        except Exception as e:
            log.debug("连接处理出错：%s", e)
        finally:
            conn.close()

    def dispatch(self, req: dict) -> dict:
        cmd = req.get("cmd", "")
        if cmd == "toggle":
            return self.toggle()
        if cmd == "start":
            return self.start()
        if cmd == "stop":
            return self.stop()
        if cmd == "cancel":
            return self.cancel()
        if cmd == "status":
            return self.status()
        if cmd == "ping":
            return {"ok": True, "state": self.state.value}
        return {"ok": False, "error": f"未知命令：{cmd}"}

    # ---------- 动作 ----------

    def status(self) -> dict:
        loaded = self.transcriber.loaded
        return {
            "ok": True,
            "state": self.state.value,
            "model": loaded.name if loaded else None,
            "device": loaded.device if loaded else None,
            "compute_type": loaded.compute_type if loaded else None,
            "degraded": loaded.degraded if loaded else None,
            "elapsed": round(self.recorder.elapsed, 1),
            "last_text": self._last_text,
            "socket": str(socket_path()),
        }

    def toggle(self) -> dict:
        with self.lock:
            state = self.state
        if state is State.RECORDING:
            return self.stop()
        if state is State.TRANSCRIBING:
            # 不排队：正在识别时按键直接忽略，免得状态机变复杂而用户也没得到什么
            notify.notify("⏳ 还在识别上一段，请稍候")
            return {"ok": False, "state": state.value, "error": "正在识别"}
        return self.start()

    def start(self) -> dict:
        with self.lock:
            if self.state is not State.IDLE:
                return {"ok": False, "state": self.state.value, "error": "非空闲状态"}
            try:
                self.recorder.start()
            except Exception as e:
                notify.error(f"启动录音失败：{e}")
                return {"ok": False, "state": self.state.value, "error": str(e)}
            self.state = State.RECORDING
        notify.recording()
        return {"ok": True, "state": State.RECORDING.value}

    def stop(self) -> dict:
        with self.lock:
            if self.state is not State.RECORDING:
                return {"ok": False, "state": self.state.value, "error": "当前没有在录音"}
            wav = self.recorder.stop()
            self.state = State.TRANSCRIBING
        # 转写放到后台线程，客户端（快捷键）立刻返回
        threading.Thread(target=self._finish, args=(wav,), daemon=True).start()
        return {"ok": True, "state": State.TRANSCRIBING.value}

    def cancel(self) -> dict:
        with self.lock:
            if self.state is not State.RECORDING:
                return {"ok": False, "state": self.state.value, "error": "当前没有在录音"}
            self.recorder.cancel()
            self.state = State.IDLE
        notify.notify("🚫 已放弃本次录音")
        return {"ok": True, "state": State.IDLE.value}

    # ---------- 转写流水线 ----------

    def _finish(self, wav: Path | None) -> None:
        try:
            self._pipeline(wav)
        except Exception as e:
            log.exception("转写流程出错")
            notify.error(str(e))
        finally:
            if wav:
                wav.unlink(missing_ok=True)
            with self.lock:
                self.state = State.IDLE

    def _pipeline(self, wav: Path | None) -> None:
        if wav is None or not wav.exists():
            notify.error("没有录到音频")
            return

        duration = wav_duration(wav)
        # 太短判为误触，静默丢弃（不打扰用户）
        if duration < self.cfg.audio.min_seconds or wav.stat().st_size < 1024:
            log.info("音频过短（%.2fs），按误触丢弃", duration)
            notify.notify("🚫 太短，已忽略")
            return

        notify.transcribing()
        t = time.monotonic()
        raw = self.transcriber.transcribe(wav)
        text = postprocess.process(raw, self.cfg)
        log.info("识别 %.1fs 音频用时 %.1fs：%r", duration, time.monotonic() - t, text)

        if not text:
            notify.error("未识别到语音")
            return

        self._last_text = text
        result = inject_mod.inject(text, self.cfg.inject)
        if result.ok:
            notify.result(text)
        elif result.on_clipboard:
            notify.notify(f"📋 {text[:30]}…… 已复制，请手动粘贴")
        else:
            notify.error(result.detail)
        log.info("注入：%s", result.detail)


def run(cfg: Config) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    lock_path = socket_path()
    if lock_path.exists():
        # 可能是上次没清干净的残留，探一下活
        from .client import ping

        if ping():
            log.error("已经有一个 myna daemon 在运行")
            return 1
        lock_path.unlink(missing_ok=True)

    d = Daemon(cfg)
    d.preload()

    import signal as _signal

    def _bye(_sig, _frm):
        d.shutdown()

    _signal.signal(_signal.SIGTERM, _bye)
    _signal.signal(_signal.SIGINT, _bye)

    d.serve()
    return 0
