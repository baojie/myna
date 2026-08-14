"""识别历史存档：每次识别落一行 JSONL，供日后 batch 纠错回看、重跑、比对。

为什么要存：journald 那行 log 会轮转，且只有后处理**之后**的文本；而纠错真正
需要的是 raw（模型原始输出）与 text（实际注入）两者的对照——分不清一个错是
模型听错的还是后处理改坏的，就无从下手。

时间戳带毫秒和本地时区偏移，是为了能和 Claude Code 的 transcript
（`~/.claude/projects/*/*.jsonl`，UTC）按时间对齐：你在输入框里手改的那几个字，
就是现成的纠错标注。

写入全程 fail-safe：存档失败绝不能影响识别本身。
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import uuid
from datetime import datetime
from pathlib import Path

log = logging.getLogger("myna")


def data_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share")
    return Path(base) / "myna"


def root(cfg=None) -> Path:
    """存档根目录。配置里写了就用配置的——本机主盘紧张时可以指到别的盘。"""
    if cfg is not None and getattr(cfg.history, "dir", ""):
        return Path(cfg.history.dir).expanduser()
    return data_dir()


def log_path(cfg=None, when: datetime | None = None) -> Path:
    """按月分文件，免得一个 JSONL 无限长到不好处理。"""
    when = when or datetime.now()
    return root(cfg) / "history" / f"{when:%Y-%m}.jsonl"


def audio_dir(cfg=None, when: datetime | None = None) -> Path:
    when = when or datetime.now()
    return root(cfg) / "audio" / f"{when:%Y-%m}"


def _hotwords_hit(raw: str, hotwords: dict[str, str]) -> list[str]:
    """这次命中了哪些热词。用来回答「加的词到底有没有用」。"""
    return [w for w in hotwords if w and w in raw]


def _prune_audio(cfg, keep_mb: float) -> None:
    """音频总量超上限就删最旧的。语音是隐私数据，无限堆积既占盘也不该。"""
    base = root(cfg) / "audio"
    if not base.exists():
        return
    files = sorted(base.rglob("*.wav"), key=lambda p: p.stat().st_mtime)
    total = sum(p.stat().st_size for p in files)
    cap = keep_mb * 1024 * 1024
    for p in files:
        if total <= cap:
            break
        total -= p.stat().st_size
        p.unlink(missing_ok=True)


def record(
    cfg,
    *,
    raw: str,
    text: str,
    wav: Path | None = None,
    duration: float = 0.0,
    latency: float = 0.0,
    model: str | None = None,
    device: str | None = None,
    injected: str = "",
) -> str | None:
    """写一条历史。返回记录 id；未启用或出错时返回 None（调用方无需处理）。"""
    if not getattr(cfg, "history", None) or not cfg.history.enabled:
        return None
    try:
        now = datetime.now().astimezone()
        rid = uuid.uuid4().hex[:12]

        audio_rel = None
        if cfg.history.save_audio and wav is not None and wav.exists():
            d = audio_dir(cfg, now)
            d.mkdir(parents=True, exist_ok=True)
            dst = d / f"{rid}.wav"
            shutil.copy2(wav, dst)
            dst.chmod(0o600)
            audio_rel = str(dst.relative_to(root(cfg)))
            _prune_audio(cfg, cfg.history.max_audio_mb)

        entry = {
            "id": rid,
            # 带毫秒 + 本地时区偏移，供与 Claude transcript（UTC）对齐
            "ts": now.isoformat(timespec="milliseconds"),
            "raw": raw,                       # 模型原始输出，纠错的真正对象
            "text": text,                     # 后处理之后、实际注入的
            "duration": round(duration, 2),   # 音频秒数
            "latency": round(latency, 2),     # 转写耗时
            "rtf": round(latency / duration, 3) if duration > 0 else None,
            "model": model,
            "device": device,
            "injected": injected,             # ok | clipboard | fail
            "hotwords_hit": _hotwords_hit(raw, cfg.hotwords),
            "audio": audio_rel,
        }

        path = log_path(cfg, now)
        path.parent.mkdir(parents=True, exist_ok=True)
        # 语音内容是隐私数据，只给本人看
        try:
            path.parent.chmod(0o700)
            root(cfg).chmod(0o700)
        except OSError:
            pass
        new = not path.exists()
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        if new:
            path.chmod(0o600)
        return rid
    except Exception:
        # 存档是附加功能，任何失败都不该波及识别流程
        log.debug("写历史失败", exc_info=True)
        return None


def read_recent(limit: int = 20, cfg=None) -> list[dict]:
    """读最近 limit 条，按时间正序返回。从最新月份往回读，不必扫全部文件。"""
    base = root(cfg) / "history"
    if not base.exists():
        return []
    out: list[dict] = []
    for path in sorted(base.glob("*.jsonl"), reverse=True):
        lines = path.read_text(encoding="utf-8").splitlines()
        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # 半行/损坏行跳过，不让一条坏数据毁掉整次查询
            if len(out) >= limit:
                return list(reversed(out))
    return list(reversed(out))
