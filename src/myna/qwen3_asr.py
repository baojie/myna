"""Qwen3-ASR-ONNX 推理后端——faster-whisper 之外的独立架构。

faster-whisper 只认 CTranslate2 格式的 Whisper 模型，而 Qwen3-ASR 是 Qwen
架构 + ONNX，靠 onnxruntime 跑（encoder_conv / encoder_transformer /
decoder_init / decoder_step 四个 .onnx + embed_tokens.bin + tokenizer.json）。
本模块从 `Daumee/Qwen3-ASR-0.6B-ONNX-CPU` 仓库自带的 onnx_inference.py 移植，
去掉 CLI、按 myna 的 Transcriber 接口包装。

依赖 numpy / onnxruntime / librosa / tokenizers，全部延迟导入：只有真正加载
Qwen3 档位时才需要，缺了给出明确提示，不影响 faster-whisper 档位。
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

# ── 与仓库 onnx_inference.py 完全一致的常量 ──────────────────────────────
SAMPLE_RATE = 16000
N_FFT = 400
HOP_LENGTH = 160
N_MELS = 128
CHUNK_SIZE = 100  # n_window * 2

# Special token IDs
AUDIO_START_ID = 151669
AUDIO_END_ID = 151670
AUDIO_PAD_ID = 151676
IM_START_ID = 151644
IM_END_ID = 151645      # EOS
ENDOFTEXT_ID = 151643   # EOS alt
NEWLINE_ID = 198        # '\n'

VOCAB_SIZE = 151936
HIDDEN_SIZE = 1024


class SimpleTokenizer:
    """最小 tokenizer：优先 tokenizers 库读本地 tokenizer.json。

    仓库自带 tokenizer.json，所以 transformers 那条 fallback 实际上用不到，
    保留只是因为原脚本这么写——真要缺 tokenizers 库时它会给更明确的错误。
    """

    def __init__(self, tokenizer_path: Path | None = None):
        if tokenizer_path and tokenizer_path.exists():
            from tokenizers import Tokenizer

            self.tokenizer = Tokenizer.from_file(str(tokenizer_path))
            self._is_hf = False
        else:
            from transformers import AutoTokenizer  # 重依赖，万不得已才用

            self.tokenizer = AutoTokenizer.from_pretrained("Qwen/Qwen3-ASR-0.6B")
            self._is_hf = True

    def encode(self, text: str) -> list:
        if self._is_hf:
            return self.tokenizer.encode(text, add_special_tokens=False)
        return self.tokenizer.encode(text).ids

    def decode(self, ids: list) -> str:
        return self.tokenizer.decode(ids, skip_special_tokens=True)


# mel filterbank 与音频特征跟模型无关，进程内缓存一份即可
_MEL_FILTERS: object | None = None


def get_mel_filters():
    """Whisper 兼容的 128-bin mel filterbank（slaney，与原脚本一致）。"""
    global _MEL_FILTERS
    if _MEL_FILTERS is None:
        import librosa
        import numpy as np

        _MEL_FILTERS = librosa.filters.mel(
            sr=SAMPLE_RATE, n_fft=N_FFT, n_mels=N_MELS,
            fmin=0, fmax=SAMPLE_RATE // 2, norm="slaney", htk=False,
        ).astype(np.float32)
    return _MEL_FILTERS


def _load_audio(path: str) -> "object":
    """读音频为 mono 16kHz float32（librosa 内部完成重采样）。"""
    import librosa

    wav, _ = librosa.load(path, sr=SAMPLE_RATE, mono=True)
    return wav.astype("float32")


def _compute_mel_spectrogram(wav, mel_filters) -> "object":
    """log-mel 频谱，Whisper 兼容，参数照抄原脚本。

    librosa.stft → |X|² → mel 滤波 → log10 → 动态范围裁剪 → 归一化。
    """
    import librosa
    import numpy as np

    stft = librosa.stft(
        wav, n_fft=N_FFT, hop_length=HOP_LENGTH,
        window="hann", center=True, pad_mode="reflect",
    )
    mel_spec = mel_filters @ (np.abs(stft) ** 2)
    log_spec = np.log10(np.maximum(mel_spec, 1e-10))
    log_spec = np.maximum(log_spec, log_spec.max() - 8.0)
    log_spec = (log_spec + 4.0) / 4.0
    return log_spec.astype("float32")


def _feat_extract_output_lengths(input_lengths: "object") -> "object":
    """三次 stride-2 卷积后的特征长度。"""
    lengths = input_lengths
    for _ in range(3):
        lengths = (lengths - 1) // 2 + 1
    return lengths


class Qwen3Asr:
    """端到端 ASR：mel → encoder → 与 prompt 融合 → decoder 自回归。

    `transcribe(path)` 返回识别文本（str），与 myna 的 Transcriber 接口对齐。
    """

    def __init__(self, onnx_dir: Path, *, language: Optional[str] = None,
                 num_threads: int = 0, quantize: str = "int8") -> None:
        # 依赖先查再导入，缺什么一句话说清，别让用户对着 ImportError 猜
        missing = [m for m in ("numpy", "onnxruntime", "librosa", "tokenizers")
                   if _import_ok(m) is False]
        if missing:
            raise RuntimeError(
                "加载 Qwen3-ASR 需要依赖："
                + "、".join(missing)
                + "\n安装：pip install --break-system-packages onnxruntime librosa tokenizers")

        import numpy as np
        import onnxruntime as ort

        onnx_path = Path(onnx_dir)
        # cvxhull 的 onnx 在快照根目录；Daumee 的在 onnx_models/ 子目录。
        # 调用方统一传 onnx_models，缺 onnx 就回退到父目录，两种结构都能加载。
        if not any(onnx_path.glob("*.onnx")) and (onnx_path.parent / "decoder_step.onnx").exists():
            onnx_path = onnx_path.parent

        sess_opts = ort.SessionOptions()
        sess_opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        if num_threads > 0:
            sess_opts.intra_op_num_threads = num_threads
        sess_opts.log_severity_level = 3  # 屏蔽 onnxruntime 的启动噪音

        # 能上 CUDA 就上 CUDA（不支持的算子自动落回 CPU EP）；否则纯 CPU。
        # GPU 上 int8 量化的 decoder 也能跑，CTranslate2 之外的这套 ONNX 后端
        # 从此不再硬编码 CPU。实测数据见 README「实测」。
        available = ort.get_available_providers()
        self.providers = (["CUDAExecutionProvider", "CPUExecutionProvider"]
                          if "CUDAExecutionProvider" in available
                          else ["CPUExecutionProvider"])

        # 两种模型结构都支持：
        #   - 单 encoder.onnx（cvxhull/qwen3-asr-0.6b-onnx-fp16）：encoder 内部
        #     封装了窗口 conv + transformer，decoder 是原生 fp16；CPU 上由
        #     onnxruntime 自动提升到 fp32 跑，GPU 上全速 fp16。Decoder 接口
        #     与 Daumee 版完全一致（input_embeds/position_ids → logits/KV）。
        #   - 双 encoder_conv + encoder_transformer（Daumee/Qwen3-ASR-0.6B-ONNX-CPU）：
        #     decoder 只有 int8，int8 量化算子在 CUDA 上没 kernel，会整体回 CPU
        #     算（实测 20 句 decoder 占 81% 耗时），所以带 CUDA 时优先推荐单模型版。
        single_encoder = (onnx_path / "encoder.onnx").exists()
        self.single_encoder = single_encoder

        if single_encoder:
            decoder_init, decoder_step = "decoder_init.onnx", "decoder_step.onnx"
            needed = ["encoder.onnx", decoder_init, decoder_step, "embed_tokens.bin"]
        else:
            if quantize == "int8" and (onnx_path / "decoder_init.int8.onnx").exists():
                decoder_init, decoder_step = "decoder_init.int8.onnx", "decoder_step.int8.onnx"
            else:
                decoder_init, decoder_step = "decoder_init.onnx", "decoder_step.onnx"
            needed = [
                "encoder_conv.onnx", "encoder_transformer.onnx",
                decoder_init, decoder_step, "embed_tokens.bin",
            ]

        absent = [f for f in needed if not (onnx_path / f).exists()]
        if absent:
            raise RuntimeError(
                f"Qwen3-ASR 模型不完整，缺：{', '.join(absent)}\n"
                f"模型目录：{onnx_path}\n请重新下载（`myna model qwen3` 或托盘模型菜单）")

        if single_encoder:
            self.encoder = ort.InferenceSession(
                str(onnx_path / "encoder.onnx"), sess_opts,
                providers=self.providers)
            self.encoder_conv = self.encoder_transformer = None
        else:
            self.encoder = None
            self.encoder_conv = ort.InferenceSession(
                str(onnx_path / "encoder_conv.onnx"), sess_opts,
                providers=self.providers)
            self.encoder_transformer = ort.InferenceSession(
                str(onnx_path / "encoder_transformer.onnx"), sess_opts,
                providers=self.providers)
        self.decoder_init = ort.InferenceSession(
            str(onnx_path / decoder_init), sess_opts,
            providers=self.providers)
        self.decoder_step = ort.InferenceSession(
            str(onnx_path / decoder_step), sess_opts,
            providers=self.providers)

        # embed_tokens：fp16 导出（cvxhull）的 bin 约 297MB，fp32（Daumee）约 594MB
        embed_bytes = (onnx_path / "embed_tokens.bin").stat().st_size
        self.embed_dtype = np.float16 if embed_bytes < 400_000_000 else np.float32
        self.embed_tokens = np.fromfile(
            str(onnx_path / "embed_tokens.bin"), dtype=self.embed_dtype,
        ).reshape(VOCAB_SIZE, HIDDEN_SIZE)

        # 供上层（myna status / 日志）报告实际设备与计算类型
        self.device = "cuda" if self.providers[0] == "CUDAExecutionProvider" else "cpu"
        self.compute_type = ("fp16" if self.embed_dtype == np.float16 else
                             ("int8" if (onnx_path / "decoder_init.int8.onnx").exists()
                              else "fp32"))

        self.mel_filters = get_mel_filters()

        # tokenizer.json 有的仓库放 onnx_models/ 里，有的放快照根目录
        #（实测 Daumee/Qwen3-ASR-0.6B-ONNX-CPU 就在根目录）。
        # 只认 onnx_models/ 会误落回 transformers fallback——重依赖，尽量别碰。
        tok = onnx_path / "tokenizer.json"
        if not tok.exists():
            tok = onnx_path.parent / "tokenizer.json"
        self.tokenizer = SimpleTokenizer(tok if tok.exists() else None)

        self.language = language
        self.np = np

    # ── encoder ─────────────────────────────────────────────────────

    def _encode_audio(self, mel, mel_len: int) -> "object":
        """mel → 音频特征 [N, 1024]。超长按 CHUNK_SIZE=100 帧分块再拼接。"""
        np = self.np
        if self.single_encoder:
            # 单 encoder.onnx：窗口 conv/attention 都封装在模型内部，
            # 喂 [1,128,T] 直接得到全部特征，无需分块。cvxhull 的导出按
            # WhisperFeatureExtractor 惯例 drop 末帧，mel 需对齐。
            m = mel[:, :mel_len]
            if m.shape[1] > 1:
                m = m[:, :-1]
            x = m[np.newaxis, ...].astype(self.embed_dtype)
            return self.encoder.run(None, {"mel": x})[0][0]
        mel_valid = mel[:, :mel_len]
        chunk_num = int(np.ceil(mel_len / CHUNK_SIZE))

        chunk_lengths = []
        for i in range(chunk_num):
            start = i * CHUNK_SIZE
            end = min(start + CHUNK_SIZE, mel_len)
            chunk_lengths.append(end - start)

        max_chunk_len = max(chunk_lengths)
        padded = np.zeros((chunk_num, 1, N_MELS, max_chunk_len), dtype="float32")
        start = 0
        for i, cl in enumerate(chunk_lengths):
            padded[i, 0, :, :cl] = mel_valid[:, start:start + cl]
            start += cl

        lens_after_cnn = _feat_extract_output_lengths(np.array(chunk_lengths))
        conv_out = self.encoder_conv.run(None, {"padded_mel_chunks": padded})[0]

        # 去掉 chunk 填充，再喂给全注意力 transformer
        features = [conv_out[i, :l, :] for i, l in enumerate(lens_after_cnn)]
        hidden_states = np.concatenate(features, axis=0)
        total_tokens = hidden_states.shape[0]
        attn_mask = np.zeros((1, 1, total_tokens, total_tokens), dtype="float32")
        return self.encoder_transformer.run(None, {
            "hidden_states": hidden_states,
            "attention_mask": attn_mask,
        })[0]

    # ── prompt 与融合 ──────────────────────────────────────────────

    def _build_prompt_ids(self, num_audio_tokens: int) -> list:
        """ChatML 模板：system / user(音频占位) / assistant，可选语言指令。"""
        ids = ([IM_START_ID] + self.tokenizer.encode("system")
               + [NEWLINE_ID, IM_END_ID, NEWLINE_ID])
        ids += ([IM_START_ID] + self.tokenizer.encode("user") + [NEWLINE_ID]
                + [AUDIO_START_ID] + [AUDIO_PAD_ID] * num_audio_tokens
                + [AUDIO_END_ID] + [IM_END_ID, NEWLINE_ID])
        ids += [IM_START_ID] + self.tokenizer.encode("assistant") + [NEWLINE_ID]
        if self.language:
            ids += self.tokenizer.encode(f"language {self.language}<asr_text>")
        return ids

    def _embed_and_fuse(self, token_ids: list, audio_features: "object") -> "object":
        """token 查表嵌入，把 audio_pad 占位替换成 encoder 输出。"""
        np = self.np
        ids_array = np.array(token_ids)
        embeds = self.embed_tokens[ids_array]  # [seq, 1024]

        audio_mask = ids_array == AUDIO_PAD_ID
        audio_positions = np.where(audio_mask)[0]
        if len(audio_positions) != audio_features.shape[0]:
            raise RuntimeError(
                f"音频 token 数不匹配：{len(audio_positions)} vs "
                f"{audio_features.shape[0]}（编码器输出与 prompt 占位对不上）")
        embeds[audio_positions] = audio_features
        return embeds[np.newaxis, :, :]  # [1, seq, 1024]

    # ── 解码 ───────────────────────────────────────────────────────

    def _transcribe_chunk(self, wav, max_new_tokens: int = 512) -> dict:
        """单块音频（≤45s）的完整识别，贪心解码直到 EOS。"""
        np = self.np
        mel = _compute_mel_spectrogram(wav, self.mel_filters)
        mel_len = mel.shape[1]

        audio_features = self._encode_audio(mel, mel_len)
        num_audio_tokens = audio_features.shape[0]

        token_ids = self._build_prompt_ids(num_audio_tokens)
        input_embeds = self._embed_and_fuse(token_ids, audio_features)
        seq_len = input_embeds.shape[1]
        position_ids = np.arange(seq_len, dtype="int64").reshape(1, -1)

        logits, present_keys, present_values = self.decoder_init.run(None, {
            "input_embeds": input_embeds,
            "position_ids": position_ids,
        })

        next_token = int(np.argmax(logits[0, -1, :]))
        generated = [next_token]
        cur_pos = seq_len

        for _ in range(max_new_tokens - 1):
            if next_token in (IM_END_ID, ENDOFTEXT_ID):
                break
            token_embed = self.embed_tokens[next_token][np.newaxis, np.newaxis, :]
            pos = np.array([[cur_pos]], dtype="int64")
            logits, present_keys, present_values = self.decoder_step.run(None, {
                "input_embeds": token_embed,
                "position_ids": pos,
                "past_keys": present_keys,
                "past_values": present_values,
            })
            next_token = int(np.argmax(logits[0, -1, :]))
            generated.append(next_token)
            cur_pos += 1

        if generated and generated[-1] in (IM_END_ID, ENDOFTEXT_ID):
            generated = generated[:-1]

        return self._parse(generated, self.tokenizer.decode(generated))

    def _parse(self, generated: list, raw_text: str) -> dict:
        """把「language zh<asr_text>识别文本」拆出真正的文本。"""
        parsed_lang = ""
        parsed_text = raw_text
        if "language " in raw_text and "<asr_text>" in raw_text:
            lang_part, _, rest = raw_text.partition("<asr_text>")
            if lang_part.startswith("language "):
                parsed_lang = lang_part[len("language "):]
            parsed_text = rest
        elif self.language:
            parsed_lang = self.language
            parsed_text = raw_text
        return {"text": parsed_text, "language": parsed_lang,
                "tokens": len(generated)}

    # ── 对外接口 ────────────────────────────────────────────────────

    def transcribe(self, wav_path: str, max_new_tokens: int = 512) -> str:
        """识别一个音频文件，返回文本。长音频按静音边界分块（30s 基准）。"""
        wav = _load_audio(wav_path)
        split_points = _find_silence_split_points(wav, target_sec=30)

        if not split_points:
            return self._transcribe_chunk(wav, max_new_tokens)["text"].strip()

        boundaries = [0] + split_points + [len(wav)]
        texts = []
        for i in range(len(boundaries) - 1):
            chunk = wav[boundaries[i]:boundaries[i + 1]]
            r = self._transcribe_chunk(chunk, max_new_tokens)
            texts.append(r["text"].strip())
        return " ".join(t for t in texts if t)


# ── 长音频按静音切块（仓库原脚本逻辑）───────────────────────────────────

SILENCE_THRESHOLD_DB = -40
SILENCE_HOP_SEC = 0.1


def _find_silence_split_points(wav, target_sec: int = 30) -> list:
    """RMS 能量找静音点，把长音频切成 ≤45s 的块。短音频直接返回空表。"""
    import librosa
    import numpy as np

    min_sec = target_sec // 2
    max_sec = int(target_sec * 1.5)

    total_samples = len(wav)
    if total_samples <= max_sec * SAMPLE_RATE:
        return []

    hop_samples = int(SILENCE_HOP_SEC * SAMPLE_RATE)
    rms = librosa.feature.rms(
        y=wav, frame_length=hop_samples * 2, hop_length=hop_samples,
    )[0]
    rms_db = librosa.amplitude_to_db(rms, ref=np.max)
    is_silent = rms_db < SILENCE_THRESHOLD_DB

    split_points = []
    cursor = 0
    while cursor + max_sec * SAMPLE_RATE < total_samples:
        search_start_sec = cursor / SAMPLE_RATE + min_sec
        search_end_sec = cursor / SAMPLE_RATE + max_sec
        target_abs_sec = cursor / SAMPLE_RATE + target_sec

        frame_start = int(search_start_sec / SILENCE_HOP_SEC)
        frame_end = min(int(search_end_sec / SILENCE_HOP_SEC), len(is_silent))
        frame_target = int(target_abs_sec / SILENCE_HOP_SEC)

        silent_frames = np.where(is_silent[frame_start:frame_end])[0] + frame_start
        if len(silent_frames) > 0:
            best = int(np.argmin(np.abs(silent_frames - frame_target)))
            split_sample = int(silent_frames[best] * hop_samples)
        else:
            split_sample = int(target_abs_sec * SAMPLE_RATE)
        split_sample = min(split_sample, total_samples)
        split_points.append(split_sample)
        cursor = split_sample

    return split_points


def _import_ok(name: str) -> bool | None:
    """查依赖是否可导入。返回 False=确实缺；None=不确定（静默）。"""
    try:
        __import__(name)
        return True
    except ImportError:
        return False
