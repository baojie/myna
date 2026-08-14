# 档位实测数据（2026-08-14，20 句）

复现：

```bash
cd ref/bench
# 用 piper 按 texts.txt 合成 s1.wav ... s20.wav（见 ref/summary/existing-setup.md）
python3 bench.py large-v3 cuda float16 > r_large.json
python3 bench.py turbo    cuda float16 > r_turbo.json
python3 bench.py medium   cuda float16 > r_medium.json
python3 bench.py small    cpu    int8   > r_small.json
python3 bench.py qwen3    cpu    int8   > r_qwen3.json   # 独立 ONNX 后端，bench.py 内分派
```

GPU 档位先 `systemctl --user stop myna.service` 腾出显存，否则会被挤到降级档，
测出来的是错的数字（这个坑踩过：large-v3 被挤成 small 后把「散步」听成「三步」，
一度以为是 vad_filter 的问题）。qwen3 是 CPU 推理，不用停 daemon。

结论见 `ref/spec/spec.md` 第 15 节。wav 文件未入库（二进制，可重新合成）。
