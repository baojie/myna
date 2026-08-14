# 档位实测数据（2026-08-14，20 句）

复现：

```bash
cd ref/bench
# 用 piper 按 texts.txt 合成 s1.wav ... s20.wav（见 ref/summary/existing-setup.md）
python3 bench.py large-v3 cuda float16 > r_large.json
python3 bench.py turbo    cuda float16 > r_turbo.json
python3 bench.py medium   cuda float16 > r_medium.json
python3 bench.py small    cpu    int8   > r_small.json
python3 bench.py qwen3               > r_qwen3_gpu.json  # 默认档，独立 ONNX 后端
CUDA_VISIBLE_DEVICES="" python3 bench.py qwen3 > r_qwen3_cpu.json  # CPU 回退
```

GPU 档位先 `systemctl --user stop myna.service` 腾出显存，否则会被挤到降级档，
测出来的是错的数字（这个坑踩过：large-v3 被挤成 small 后把「散步」听成「三步」，
一度以为是 vad_filter 的问题）。**qwen3 也不例外**：daemon 占 ~3.9G，不先停掉
onnxruntime 会在 CUDA 上静默回落 CPU；而 CPU 回退档只有藏掉 GPU
（`CUDA_VISIBLE_DEVICES=""`）才测得准——光传 `cpu` 参数没用。

bench.py 的 qwen3 分支忽略 `device`/`compute` 参数：Qwen3Asr 内部按
`ort.get_available_providers()` 自动选 EP，有 CUDA 走 GPU+fp16，否则 CPU。
历史数据 `r_qwen3.json` 是旧 Daumee int8 版（CPU 专用），已被 fp16 版取代。

结论见 `ref/spec/spec.md` 第 15 节。wav 文件未入库（二进制，可重新合成）。
