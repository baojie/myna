"""历史存档。重点覆盖两件事：字段齐不齐（决定日后能不能纠错），
以及出错时会不会波及识别（存档是附加功能，绝不能拖垮主流程）。"""

from __future__ import annotations

import json
import wave
from pathlib import Path

from myna import history
from myna.config import Config


def _cfg(tmp_path: Path, **kw) -> Config:
    cfg = Config()
    cfg.history.dir = str(tmp_path)
    for k, v in kw.items():
        setattr(cfg.history, k, v)
    return cfg


def _wav(path: Path, seconds: float = 0.5) -> Path:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00" * int(16000 * seconds))
    return path


def test_record_writes_jsonl_with_raw_and_text(tmp_path):
    cfg = _cfg(tmp_path)
    rid = history.record(cfg, raw="散步", text="散步", duration=2.0, latency=0.5,
                         model="large-v3", device="cuda", injected="ok")
    assert rid
    rows = history.read_recent(10, cfg)
    assert len(rows) == 1
    r = rows[0]
    assert r["id"] == rid
    assert r["raw"] == "散步" and r["text"] == "散步"
    assert r["model"] == "large-v3" and r["device"] == "cuda"
    assert r["rtf"] == 0.25
    assert r["injected"] == "ok"


def test_timestamp_has_offset_for_transcript_alignment(tmp_path):
    """时间戳必须带时区偏移，否则没法和 Claude transcript（UTC）对齐。"""
    cfg = _cfg(tmp_path)
    history.record(cfg, raw="a", text="a")
    ts = history.read_recent(1, cfg)[0]["ts"]
    assert ts[-6] in "+-" or ts.endswith("Z")


def test_records_hotword_hits(tmp_path):
    cfg = _cfg(tmp_path)
    cfg.hotwords = {"三步": "散步", "没命中": "x"}
    history.record(cfg, raw="出去三步", text="出去散步")
    assert history.read_recent(1, cfg)[0]["hotwords_hit"] == ["三步"]


def test_disabled_writes_nothing(tmp_path):
    cfg = _cfg(tmp_path, enabled=False)
    assert history.record(cfg, raw="a", text="a") is None
    assert history.read_recent(10, cfg) == []


def test_audio_off_by_default(tmp_path):
    cfg = _cfg(tmp_path)
    wav = _wav(tmp_path / "in.wav")
    history.record(cfg, raw="a", text="a", wav=wav)
    assert history.read_recent(1, cfg)[0]["audio"] is None
    assert not (tmp_path / "audio").exists()


def test_audio_archived_when_enabled(tmp_path):
    cfg = _cfg(tmp_path, save_audio=True)
    wav = _wav(tmp_path / "in.wav")
    history.record(cfg, raw="a", text="a", wav=wav)
    rel = history.read_recent(1, cfg)[0]["audio"]
    assert rel and (tmp_path / rel).exists()


def test_audio_pruned_over_cap(tmp_path):
    """总量超上限就删最旧的，别把盘吃满。"""
    cfg = _cfg(tmp_path, save_audio=True, max_audio_mb=0.05)  # 约 50KB
    for i in range(6):
        history.record(cfg, raw=str(i), text=str(i),
                       wav=_wav(tmp_path / f"in{i}.wav", seconds=1.0))  # 每个 ~32KB
    kept = list((tmp_path / "audio").rglob("*.wav"))
    total = sum(p.stat().st_size for p in kept)
    assert 0 < len(kept) < 6
    assert total <= 0.05 * 1024 * 1024
    # 记录行本身不受影响：文本历史比音频更该留
    assert len(history.read_recent(10, cfg)) == 6


def test_failure_never_raises(tmp_path):
    """存档目录不可写时也只能是静默失败——不能让识别结果因此丢掉。"""
    cfg = _cfg(tmp_path / "nope")
    (tmp_path / "nope").write_text("我不是目录")
    assert history.record(cfg, raw="a", text="a") is None


def test_corrupt_line_is_skipped(tmp_path):
    cfg = _cfg(tmp_path)
    history.record(cfg, raw="好的", text="好的")
    path = history.log_path(cfg)
    with path.open("a", encoding="utf-8") as f:
        f.write("{半行坏数据\n")
    assert [r["text"] for r in history.read_recent(10, cfg)] == ["好的"]


def test_read_recent_returns_chronological_tail(tmp_path):
    cfg = _cfg(tmp_path)
    for i in range(5):
        history.record(cfg, raw=str(i), text=str(i))
    assert [r["text"] for r in history.read_recent(3, cfg)] == ["2", "3", "4"]


def test_file_is_private(tmp_path):
    """说过的每句话都在里面，只能本人可读。"""
    cfg = _cfg(tmp_path)
    history.record(cfg, raw="a", text="a")
    assert history.log_path(cfg).stat().st_mode & 0o077 == 0


def test_default_dir_follows_xdg(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    assert history.root(Config()) == tmp_path / "xdg" / "myna"
