"""语音识别：faster-whisper 封装，含设备探测与降级。

实测（RTX 4060 8G，3.5s 中文音频）：
    large-v3 / cuda float16   加载 12.7s   转写 0.7s
    medium   / cuda float16   加载  9.7s   转写 0.7s
    small    / cpu int8       加载  1.7s   转写 1.6s，且把「散步」听成「三步」

所以：GPU 上直接用 large-v3（和 medium 一样快，没理由将就精度）；
加载慢转写快 —— 模型必须常驻，这正是 daemon 存在的理由。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .config import Config


def cuda_available() -> bool:
    try:
        import ctranslate2  # type: ignore

        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False


@dataclass
class Loaded:
    model: object
    name: str
    device: str
    compute_type: str
    degraded: bool  # 是否因 GPU 不可用/加载失败而降级


class Transcriber:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.loaded: Loaded | None = None

    def _plan(self) -> list[tuple[str, str, str]]:
        """返回 (模型, 设备, 计算类型) 的尝试顺序。"""
        a = self.cfg.asr
        gpu = (a.model, "cuda", "float16")
        cpu = (a.fallback_model, "cpu", "int8")
        if a.device == "cuda":
            return [gpu]
        if a.device == "cpu":
            return [(a.model, "cpu", "int8"), cpu]
        # auto
        return [gpu, cpu] if cuda_available() else [cpu]

    def load(self) -> Loaded:
        """加载模型。GPU 失败自动降级到 CPU，降级必须让用户看得见（由调用方通知）。"""
        if self.loaded is not None:
            return self.loaded

        from faster_whisper import WhisperModel  # 延迟导入，客户端命令不必付这个代价

        attempts = self._plan()
        errors = []
        for i, (name, device, compute_type) in enumerate(attempts):
            try:
                model = WhisperModel(name, device=device, compute_type=compute_type)
                self.loaded = Loaded(
                    model=model, name=name, device=device,
                    compute_type=compute_type, degraded=(i > 0 or device == "cpu"),
                )
                return self.loaded
            except Exception as e:  # 显存不足、缺 cuDNN、模型下载失败……
                errors.append(f"{name}/{device}: {type(e).__name__}: {e}")
        raise RuntimeError("模型加载全部失败：\n" + "\n".join(errors))

    def transcribe(self, wav: Path) -> str:
        a = self.cfg.asr
        loaded = self.load()
        segments, _info = loaded.model.transcribe(  # type: ignore[attr-defined]
            str(wav),
            language=a.language or None,
            beam_size=a.beam_size,
            initial_prompt=a.initial_prompt or None,
            vad_filter=a.vad_filter,
        )
        # segments 是生成器，这里才真正开始算
        return "".join(seg.text for seg in segments).strip()
