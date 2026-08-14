"""对比不同模型档位：逐句准确率 + 速度 + 显存。"""
import json, sys, time
from pathlib import Path

REFS = [l.strip() for l in Path("texts.txt").read_text(encoding="utf-8").splitlines() if l.strip()]
WAVS = [f"s{i+1}.wav" for i in range(len(REFS))]


def cer(ref: str, hyp: str) -> float:
    """字错率：编辑距离 / 参考长度。标点和空格不计。"""
    import re
    strip = lambda s: re.sub(r"[\s，。！？、,.!?]", "", s)
    a, b = strip(ref), strip(hyp)
    if not a:
        return 0.0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1] / len(a)


def gpu_mb():
    import subprocess
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-compute-apps=pid,used_memory",
             "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=10).stdout
        import os
        me = str(os.getpid())
        for line in out.splitlines():
            pid, mem = [x.strip() for x in line.split(",")]
            if pid == me:
                return int(mem)
    except Exception:
        pass
    return None


def run(preset, device="cuda", compute="float16"):
    from myna import models as models_mod
    from myna.config import Config
    from myna.postprocess import process

    cfg = Config()

    if models_mod.is_qwen3(preset):
        # Qwen3-ASR 是独立 ONNX 后端，不走 faster-whisper（见 qwen3_asr.py）。
        # Qwen3Asr 内部按 ort.get_available_providers() 自动选 EP：有 CUDA 走
        # GPU+fp16，否则 CPU。device/compute 参数对它无效。
        # onnx 在快照根目录（cvxhull fp16 版）；onnx_models/ 旧布局内部也会回退。
        from myna.qwen3_asr import Qwen3Asr

        t = time.monotonic()
        snap = models_mod.snapshot_dir(preset)
        m = Qwen3Asr(snap, language="zh")
        load = time.monotonic() - t

        def transcribe(wav: str) -> str:
            return m.transcribe(wav)
    else:
        from faster_whisper import WhisperModel
        from myna.models import resolve_model

        t = time.monotonic()
        m = WhisperModel(resolve_model(preset), device=device, compute_type=compute)
        load = time.monotonic() - t

        def transcribe(wav: str) -> str:
            segs, _ = m.transcribe(wav, language="zh", beam_size=5,
                                   initial_prompt=cfg.asr.initial_prompt, vad_filter=True)
            return "".join(s.text for s in segs).strip()

    mem = gpu_mb()

    rows, total_audio, total_time, cers = [], 0.0, 0.0, []
    for wav, ref in zip(WAVS, REFS):
        import wave, contextlib
        with contextlib.closing(wave.open(wav)) as w:
            dur = w.getnframes() / w.getframerate()
        t = time.monotonic()
        hyp = process(transcribe(wav), cfg)
        el = time.monotonic() - t
        c = cer(ref, hyp)
        rows.append({"ref": ref, "hyp": hyp, "cer": round(c, 4), "sec": round(el, 2)})
        total_audio += dur; total_time += el; cers.append(c)

    return {"preset": preset, "device": device, "compute": compute,
            "load_sec": round(load, 1), "gpu_mb": mem,
            "avg_cer": round(sum(cers) / len(cers), 4),
            "total_sec": round(total_time, 2),
            "rtf": round(total_time / total_audio, 3), "rows": rows}


if __name__ == "__main__":
    print(json.dumps(run(sys.argv[1],
                         sys.argv[2] if len(sys.argv) > 2 else "cuda",
                         sys.argv[3] if len(sys.argv) > 3 else "float16"),
                     ensure_ascii=False))
