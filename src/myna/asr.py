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
from .models import known, presets_help, resolve_model


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
        """返回 (模型, 设备, 计算类型) 的尝试顺序。模型档位名在此解析为完整名。"""
        return self._attempts_for(self.cfg.asr.model)

    def _attempts_for(self, model: str) -> list[tuple[str, str, str]]:
        a = self.cfg.asr
        gpu = (resolve_model(model), "cuda", "float16")
        cpu = (resolve_model(a.fallback_model), "cpu", "int8")
        if a.device == "cuda":
            return [gpu]
        if a.device == "cpu":
            return [(resolve_model(model), "cpu", "int8"), cpu]
        # auto
        return [gpu, cpu] if cuda_available() else [cpu]

    def _hint(self, name: str) -> str:
        if known(name):
            return ""
        return (f"\n\n模型名不认识？可用档位：{presets_help()}；"
                "或直接写任意 HuggingFace 模型名（如 Systran/faster-whisper-medium）")

    def _load_named(self, name: str) -> Loaded:
        """按指定模型名加载并返回 Loaded，**不写 self.loaded**——热切换的前提。

        先把所有候选按「只用本地缓存」试一遍，全都不行才允许联网下载。
        模型已缓存时就不该为了校验版本去连 HuggingFace——那既拖慢启动，
        也让「全程本地」这句话不成立。
        """
        from faster_whisper import WhisperModel  # 延迟导入，客户端命令不必付这个代价

        attempts = self._attempts_for(name)
        errors = []
        for local_only in (True, False):
            for i, (m, device, compute_type) in enumerate(attempts):
                try:
                    model = WhisperModel(m, device=device,
                                         compute_type=compute_type,
                                         local_files_only=local_only)
                except Exception as e:  # 未缓存、显存不足、缺 cuDNN、下载失败……
                    errors.append(
                        f"{m}/{device}"
                        f"{'（仅本地）' if local_only else '（含下载）'}: "
                        f"{type(e).__name__}: {e}")
                    continue
                return Loaded(
                    model=model, name=m, device=device,
                    compute_type=compute_type, degraded=(i > 0 or device == "cpu"),
                )
        raise RuntimeError("模型加载全部失败：\n" + "\n".join(errors) + self._hint(name))

    def load(self) -> Loaded:
        """加载配置指定的模型（幂等：已加载就直接用）。GPU 失败自动降级到 CPU。"""
        if self.loaded is not None:
            return self.loaded
        self.loaded = self._load_named(self.cfg.asr.model)
        return self.loaded

    def switch(self, name: str) -> Loaded:
        """热切换模型。加载期间旧模型保持可用；失败则回滚，旧模型继续用。

        只替换 self.loaded 这一步在**新模型加载成功后**发生，所以正在转写的
        线程始终拿得到完整模型，不会出现加载到一半的引用。
        """
        loaded = self._load_named(name)
        self.loaded = loaded
        return loaded

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
