# myna 模型与依赖速查（2026-08-14）

本机 GPU 化后的模型档位、下载信息与依赖全貌。主盘将满（98%），**所有重依赖与
模型一律在 `/data`**，靠环境变量指向。这份文档是安装/排障时的对照表。

## 1. 模型档位

全部走 HuggingFace 缓存：`~/.cache/huggingface`（指向 `/data/cache/huggingface`
的符号链接），不在仓库或主盘另存。下载前 `myna models` 或 `myna model <档位>`
会报体积。

| 档位名 | HF repo id | 磁盘体积 | 布局 | 说明 |
|---|---|---|---|---|
| `qwen3`（**默认**） | cvxhull/qwen3-asr-0.6b-onnx-fp16 | 2.9G | ONNX 在快照**根目录**（encoder.onnx / decoder_init.onnx / decoder_step.onnx / embed_tokens.bin） | 原生 fp16；onnxruntime 有 CUDA 走 GPU+fp16，否则自动回 CPU。中文最准（CER 8.8%），GPU RTF 0.067 |
| `large-v3` | Systran/faster-whisper-large-v3 | 2.9G | faster-whisper 标准（model.bin） | whisper 里 GPU 最快 |
| `turbo` | deepdml/faster-whisper-large-v3-turbo-ct2 | 1.6G | 同上 | 省显存；**Systran 没有出 turbo 的 CT2 版**，写成 Systran 会 401 |
| `medium` | Systran/faster-whisper-medium | 1.5G | 同上 | 均衡 |
| `large-v2` | Systran/faster-whisper-large-v2 | 2.9G | 同上 | 未下载 |
| `small` | Systran/faster-whisper-small | 464M | 同上 | GPU 不可用时的 whisper 回退档 |
| `base` / `tiny` | Systran/faster-whisper-base / tiny | 145M / 75M | 同上 | 未下载 |

> 旧版 `qwen3` 曾用 Daumee/Qwen3-ASR-0.6B-ONNX-CPU（int8，2.5G，onnx 在
> `onnx_models/` 子目录）。int8 量化算子在 CUDA 无 kernel，GPU 化后 RTF 只有
> 0.40，已被 fp16 版取代。`qwen3_asr.py` 仍兼容 `onnx_models/` 布局，旧模型若
> 还在缓存里也能加载，但不再作为档位默认。

### 下载注意事项（都踩过）

- **`HF_HUB_DISABLE_XET=1`**（`models.py` 里 setdefault）：新版 HF 默认 xet 存储
  传输，在本机会**卡死**——小文件下完，大的 model.bin 停在 0 字节且进程不退出、
  不报错。禁用后走直连 CDN（HTTP 206，500KB/s+）。
- **中途掐断**：HF 会在传输中 `peer closed connection`（2.9G 的文件下到 120MB
  就断）。`models.py` 的 `download()` 带 60 次断点续传重试（.incomplete 自动续传），
  界面显示「已下 x%」。
- **`snapshot_download` 返回值不可信**：会在 model.bin 只下 115MB/1.6G 时正常
  返回。以磁盘为准（`is_downloaded()` 查 decoder 权重 / model.bin 是否真实存在）。

## 2. Python 依赖

| 包 | 版本 | 位置 | 用途 |
|---|---|---|---|
| onnxruntime-gpu | 1.26.0 | `/data/pip` | qwen3 推理。**匹配系统 CUDA 12**（1.27 需 CUDA 13 的 libcudart.so.13，装不上）。装法：`pip install --target /data/pip onnxruntime-gpu==1.26.0` |
| nvidia-cudnn-cu12 | 9.24.0.43 | `/data/cudnn` | CUDA EP 必需（缺 libcudnn.so.9 时 onnxruntime **静默回 CPU**，不报错）。libcudnn.so.9 在 `/data/cudnn/nvidia/cudnn/lib`，运行时 `LD_LIBRARY_PATH` 指向 |
| faster-whisper | 1.2.1 | 主环境（~/.local） | whisper 各档后端 |
| ctranslate2 | 4.7.1 | 主环境 | faster-whisper 的 GPU 推理核心 |
| librosa | 1.0.0 | 主环境 | qwen3 音频预处理（mel 特征） |
| tokenizers | 0.23.1 | 主环境 | qwen3 tokenizer |
| opencc-python-reimplemented | 0.1.7 | 主环境 | 繁转简兜底 |

> **版本坑**：onnxruntime-gpu 1.27 需要 CUDA 13（系统只有 CUDA 12 的
> libcudart.so.12），必须用 1.26.0。装 `/data/pip` 时它会拖 numpy 2.5.2，
> 会覆盖主环境 numpy 2.3.5（faster-whisper/librosa 受影响）——装完要删掉
> `/data/pip/numpy*`。装 gpu 版后 site-packages 会留一个 4KB 孤儿 `onnxruntime`
> 目录（`onnxruntime_gpu` 的壳），无碍但看着乱。

## 3. 环境变量

| 变量 | 值 | 作用 |
|---|---|---|
| `PYTHONPATH` | `/data/pip` | 让 onnxruntime-gpu 从 /data 加载（systemd 服务与 run.sh 都已设） |
| `LD_LIBRARY_PATH` | `/data/cudnn/nvidia/cudnn/lib` | 让 onnxruntime 找到 libcudnn.so.9；**不设则静默降级 CPU**（systemd 与 run.sh 都已设） |
| `HF_HOME` / `HF_HUB_CACHE` | `~/.cache/huggingface`（符号链接到 /data） | 模型缓存 |
| `HF_HUB_DISABLE_XET` | `1` | 禁 xet 传输，否则下载卡死（models.py 已 setdefault） |

> 全局 `LD_LIBRARY_PATH` 指向 cudnn 9 不影响 faster-whisper（CT2）各档位——
> 实测 large-v3 走 CUDA 正常，cudnn 版本只对 onnxruntime 的 CUDA EP 有要求。

## 4. GPU 化关键结论（详见 spec 第 15 节）

- 依赖链：`onnxruntime-gpu 1.26.0`（CUDA 12）→ CUDA EP 需要 `libcudnn.so.9` →
  `nvidia-cudnn-cu12` 装 /data，缺任一环都会**静默回 CPU**（RTF 0.067 → 0.64）。
- qwen3 能 GPU 跑的**必要条件**是模型本身 fp16/不依赖 int8 量化算子：
  int8 的 DynamicQuantizeLinear/MatMulInteger 在 CUDA 上无 kernel，onnxruntime
  会插 423 个 Memcpy 节点拷回 CPU，decoder 占 81% 耗时。
- 测 GPU 档位前必须 `systemctl --user stop myna.service` 腾显存，否则被挤到
  降级档，测出来的是错的数字。
