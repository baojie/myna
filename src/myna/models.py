"""模型档位预设。

配置里 `[asr] model = "turbo"` 填档位名即可，自动解析为 faster-whisper
完整模型名（HF repo id）。档位表外的名字原样透传——等于支持任意
HuggingFace 模型名，自由度不丢。

档位按本机实测定位（RTX 4060 8G，中文音频）：GPU 上 large-v3 与 medium
同为 0.7s 转写，没必要为省事将就精度；turbo 更快但精度略降；CPU 上
small 最快、large 不现实。
"""

from __future__ import annotations

import os

# HuggingFace 新版默认走 xet 存储传输，实测在本机会**卡死**：小文件都下完，
# 大的 model.bin 停在 0 字节且进程不退出，也不报错。直连 CDN 明明是通的
# （HTTP 206，500KB/s+）。禁用后立刻恢复正常。
# 用 setdefault 而不是硬写，留给用户按需覆盖。
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

# 档位名 → faster-whisper 完整模型名（HF repo id）
PRESETS: dict[str, str] = {
    # Systran 没有出 turbo 的 CTranslate2 版本（曾误写成
    # Systran/faster-whisper-large-v3-turbo，那个仓库根本不存在，下载会 401）。
    # deepdml 这个是社区通行的转换版，faster-whisper 文档用的也是它。
    "turbo": "deepdml/faster-whisper-large-v3-turbo-ct2",
    "large-v3": "Systran/faster-whisper-large-v3",
    "large-v2": "Systran/faster-whisper-large-v2",
    "medium": "Systran/faster-whisper-medium",
    "small": "Systran/faster-whisper-small",
    "base": "Systran/faster-whisper-base",
    "tiny": "Systran/faster-whisper-tiny",
    # Qwen3-ASR 是独立架构（Qwen + ONNX），由 onnxruntime 推理，不走 faster-whisper。
    # 原生 fp16 导出（cvxhull）：GPU 上全速 fp16（实测 RTF 0.067），无 GPU 时
    # onnxruntime 自动提升到 fp32 在 CPU 跑（RTF 0.643），见 qwen3_asr.py。
    # 曾用 Daumee/Qwen3-ASR-0.6B-ONNX-CPU（int8），但 int8 量化算子在 CUDA 上
    # 没 kernel，GPU 只能当 CPU 用；fp16 版 GPU/CPU 都能跑且更快。
    "qwen3": "cvxhull/qwen3-asr-0.6b-onnx-fp16",
}

# faster-whisper 认识的其余短名（不在档位表里，但配置它也能用）
_EXTRA_SIZES = {
    "tiny.en",
    "base.en",
    "small.en",
    "medium.en",
    "large",
    "large-v1",
    "distil-small.en",
    "distil-medium.en",
    "distil-large-v2",
    "distil-large-v3",
}


# 各档位的大致下载体积，用于下载前告知用户。数字是 HF 仓库实际占用，
# 已下载的会以磁盘实测值覆盖，这里只用于「还没下载」时给个预期。
APPROX_SIZES: dict[str, str] = {
    "turbo": "1.6G",
    "large-v3": "2.9G",
    "large-v2": "2.9G",
    "medium": "1.5G",
    "small": "464M",
    "base": "145M",
    "tiny": "75M",
    "qwen3": "2.9G",
}


def cache_root() -> "Path":
    """模型（HuggingFace 缓存）放在哪一层，即直接装着 models--*/ 的目录。

    优先级从高到低：

    1. `HF_HUB_CACHE` 环境变量——外部显式指定的（systemd 单元、命令行前缀）
       压过配置文件；
    2. 配置 `[models] cache_dir`——本机主盘装不下时把模型挪到别的盘，daemon
       和 CLI 都从这里读，不必两头改环境变量；
    3. `HF_HOME/hub`；
    4. `~/.cache/huggingface/hub`。

    注意 2 之所以还要再读一遍配置：`myna models` 这类子命令未必走过
    `config.load()`，只认环境变量的话，它会去看默认目录、报「未下载」，
    而 daemon 明明在别的盘上加载得好好的。
    """
    from pathlib import Path

    import os

    env = os.environ.get("HF_HUB_CACHE")
    if env:
        return Path(env).expanduser()

    try:
        from . import config as config_mod

        configured = config_mod.load().models.cache_dir
    except Exception:  # 配置坏了不该连模型都找不到，退回 HF 的规矩
        configured = ""
    if configured:
        return Path(configured).expanduser()

    home = os.environ.get("HF_HOME")
    if home:
        p = Path(home).expanduser()
        return p / "hub" if p.name != "hub" else p
    return Path.home() / ".cache" / "huggingface" / "hub"


def cache_dir(name: str) -> "Path":
    """某个模型在缓存里的目录，形如 models--Systran--faster-whisper-turbo。"""
    repo = resolve_model(name)
    return cache_root() / ("models--" + repo.replace("/", "--"))


def is_qwen3(name: str) -> bool:
    """这个名字是否指向 Qwen3-ASR（Qwen + ONNX，不走 faster-whisper）。"""
    repo = resolve_model(name).lower()
    return "qwen3-asr" in repo or "qwen3_asr" in repo


def snapshot_dir(name: str):
    """缓存里该模型的快照目录（唯一），未下载返回 None。"""
    d = cache_dir(name)
    snaps = sorted((d / "snapshots").glob("*")) if (d / "snapshots").is_dir() else []
    return snaps[0] if snaps else None


def is_downloaded(name: str) -> bool:
    """是否已完整下载。

    只看目录存在是不够的——中断的下载会留下空壳目录（只有 refs/blobs 而
    没有真正的权重）。Whisper 以 snapshots 下存在 model.bin 为准；Qwen3-ASR
    （ONNX）以 decoder 权重存在为准。
    """
    snap = snapshot_dir(name)
    if snap is None:
        return False
    if is_qwen3(name):
        # cvxhull 的 onnx 在快照根目录（decoder_step.onnx）；Daumee 旧版在
        # onnx_models/（decoder_step.int8.onnx）。两个都认，避免误判未下载。
        return ((snap / "decoder_step.onnx").exists()
                or (snap / "onnx_models" / "decoder_step.int8.onnx").exists())
    return (snap / "model.bin").exists()


def _human(n: float) -> str:
    for unit in ("B", "K", "M", "G"):
        if n < 1024 or unit == "G":
            return f"{n:.0f}{unit}" if unit in "BK" else f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}G"


def partial_bytes(name: str) -> int:
    """半途而废的下载已经攒了多少字节。

    huggingface_hub 把未完成的下载留在 blobs/*.incomplete，下次会从这里续传。
    界面要能说出「已经下了多少」，否则用户面对一个中断过的下载完全没有判断
    依据——是刚开始还是就差一点？
    """
    d = cache_dir(name)
    total = 0
    for f in (d / "blobs").glob("*.incomplete") if (d / "blobs").is_dir() else []:
        try:
            total += f.stat().st_size
        except OSError:
            continue
    return total


def disk_size(name: str) -> str | None:
    """已下载模型在磁盘上的实际大小，未下载返回 None。"""
    d = cache_dir(name)
    if not d.is_dir():
        return None
    total = 0
    # 跳过符号链接是为了不把 snapshots/ 里指向 blobs/ 的链接重复计一遍；但
    # 权重 blob 有可能压根不在本目录下（本机就是 blobs 留在别的盘、这边只放
    # 跨盘链接），那样一律跳过会算出「40B」这种荒唐数字。按 (dev, inode) 去
    # 重，既不重复计数，也不会漏掉链到别处的真身。
    seen = set()
    for f in d.rglob("*"):
        try:
            st = f.stat()  # 跟随符号链接
            if not f.is_file():
                continue
            key = (st.st_dev, st.st_ino)
            if key in seen:
                continue
            seen.add(key)
            total += st.st_size
        except OSError:
            continue
    if total == 0:
        return None
    for unit in ("B", "K", "M", "G"):
        if total < 1024 or unit == "G":
            return f"{total:.0f}{unit}" if unit in "BK" else f"{total:.1f}{unit}"
        total /= 1024
    return None


def download(name: str, *, attempts: int = 10, on_retry=None) -> str:
    """下载模型，带断点续传重试。

    实测 HF 会在传输中途掐断（`peer closed connection without sending complete
    message body`，1.6G 的文件下到 120MB 就断）。一次失败就报错的话，用户从
    托盘点下载基本没几次能成。huggingface_hub 会保留 .incomplete 并续传，
    所以直接重试即可，不必自己拼字节。
    """
    import time

    from huggingface_hub import snapshot_download

    repo = resolve_model(name)
    last = None
    for i in range(1, attempts + 1):
        try:
            # 显式传目录而不是靠环境变量：huggingface_hub 在 import 时就把
            # 默认缓存路径定死了，配置若在它之后才生效就会下到另一个盘去
            path = snapshot_download(repo, max_workers=2,
                                     cache_dir=str(cache_root()))
            # **不能信它的返回值**：实测 snapshot_download 会在 model.bin 只下了
            # 115MB/1.6G 的情况下正常返回，snapshots 里压根没有 model.bin，
            # blobs 里躺着 .incomplete。以磁盘实际情况为准，缺了就继续重试。
            if is_downloaded(name):
                return path
            last = RuntimeError("下载返回成功但 model.bin 不完整")
        except Exception as e:
            last = e
        if on_retry is not None:
            try:
                on_retry(i, attempts, last)
            except Exception:
                pass
        time.sleep(min(3 * i, 15))
    raise RuntimeError(f"{repo} 下载失败（重试 {attempts} 次）：{last}")


def describe(name: str) -> dict:
    """一个档位的完整说明，供下载对话框与 CLI 展示。

    含断点信息：中断过的下载攒了多少、占预计总量的百分之多少。用户看到
    「已下 17%」和看到「未下载」，做的决定是不一样的。
    """
    done = is_downloaded(name)
    partial = 0 if done else partial_bytes(name)
    approx = APPROX_SIZES.get(name, "未知")
    percent = None
    if partial:
        total = _approx_to_bytes(approx)
        if total:
            percent = min(99, int(partial * 100 / total))
    return {
        "preset": name,
        "repo": resolve_model(name),
        "downloaded": done,
        # 下载完了就报磁盘实际占用；没下完必须报**预计总量**，
        # 否则「已下 268M / 272M」看着像快好了，其实才 16%
        "size": (disk_size(name) if done else None) or approx,
        "path": str(cache_dir(name)),
        "partial_bytes": partial,
        "partial": _human(partial) if partial else None,
        "partial_percent": percent,
    }


def _approx_to_bytes(text: str) -> int | None:
    """"1.6G" -> 字节数。仅用于估算百分比，不必精确。"""
    try:
        num, unit = float(text[:-1]), text[-1].upper()
    except (ValueError, IndexError):
        return None
    factor = {"K": 1024, "M": 1024 ** 2, "G": 1024 ** 3}.get(unit)
    return int(num * factor) if factor else None


def resolve_model(name: str) -> str:
    """档位名 → 完整 HF 模型名；未知名字原样返回（透传任意 repo id）。"""
    return PRESETS.get(name, name)


def known(name: str) -> bool:
    """这个名字是否在档位表、faster-whisper 标准短名、或完整 repo id 里。"""
    return name in PRESETS or name in _EXTRA_SIZES or name.startswith(
        "Systran/faster-whisper-"
    )


def presets_help() -> str:
    """档位清单，用于错误提示与 doctor。"""
    return " | ".join(PRESETS)
