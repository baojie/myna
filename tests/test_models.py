from myna import config as config_mod
from myna import models


def test_preset_resolves_to_full_repo_id():
    assert models.resolve_model("turbo") == "deepdml/faster-whisper-large-v3-turbo-ct2"
    assert models.resolve_model("large-v3") == "Systran/faster-whisper-large-v3"
    assert models.resolve_model("medium") == "Systran/faster-whisper-medium"
    assert models.resolve_model("small") == "Systran/faster-whisper-small"


def test_full_repo_id_passthrough():
    assert models.resolve_model("Systran/faster-whisper-medium") == \
        "Systran/faster-whisper-medium"


def test_unknown_name_passthrough():
    # 档位表外的名字原样返回，等于支持任意 HF 模型名
    assert models.resolve_model("some-user/my-model") == "some-user/my-model"


def test_known_covers_presets_extra_and_repo_ids():
    assert models.known("medium")          # 档位
    assert models.known("large")           # faster-whisper 短名
    assert models.known("Systran/faster-whisper-medium")  # 完整 repo id
    assert not models.known("nonsense")


def test_presets_help_lists_presets():
    help_text = models.presets_help()
    for key in ("large-v3", "turbo", "tiny"):
        assert key in help_text


def test_every_preset_repo_looks_real():
    """档位仓库名必须像真的。

    真实事故：turbo 曾被写成 `Systran/faster-whisper-large-v3-turbo`——那个仓库
    根本不存在（Systran 没出 turbo 的 CTranslate2 版本），下载直接 401，而菜单
    里照样列着它。测试当时还断言了这个错误的名字，等于把 bug 焊死了。

    仓库是否真的存在只能联网验证（见 test_preset_repos_exist_online，默认跳过），
    这里守住能离线检查的部分：形如 `组织/仓库名`，且不是已知的错误名。
    """
    from myna import models

    for preset, repo in models.PRESETS.items():
        assert "/" in repo, f"{preset} 的仓库名不含组织前缀：{repo}"
        org, _, repo_name = repo.partition("/")
        assert org and repo_name, f"{preset} 的仓库名格式不对：{repo}"
        assert repo != "Systran/faster-whisper-large-v3-turbo", (
            "这个仓库不存在，Systran 没有出 turbo 的 CTranslate2 版本")


def test_approx_size_covers_every_preset():
    """每个档位都得有体积估计，否则下载对话框只能显示「未知」。"""
    from myna import models

    for preset in models.PRESETS:
        assert preset in models.APPROX_SIZES, f"{preset} 缺体积估计"


def test_xet_disabled_by_default():
    """HF 的 xet 传输实测会卡死大文件下载，必须默认禁用。

    症状极具迷惑性：小文件全部下完，model.bin 停在 0 字节，进程不退出也不报错，
    而直连 CDN 完全正常（HTTP 206，500KB/s+）。用户从托盘点下载会永远卡住。
    """
    import os

    import myna.models  # noqa: F401  导入即应设好

    assert os.environ.get("HF_HUB_DISABLE_XET") == "1"


def test_xet_setting_respects_user_override(monkeypatch):
    """用 setdefault 而非硬写——用户显式设了就听用户的。"""
    import importlib
    import os

    monkeypatch.setenv("HF_HUB_DISABLE_XET", "0")
    import myna.models

    importlib.reload(myna.models)
    assert os.environ["HF_HUB_DISABLE_XET"] == "0"


def test_download_does_not_trust_return_value(monkeypatch):
    """snapshot_download 会谎报成功，必须自己校验磁盘。

    实测：1.6G 的 model.bin 只下了 115MB，snapshots 里没有 model.bin、
    blobs 里躺着 .incomplete，而 snapshot_download 正常返回了路径。
    只信返回值的话，接下来加载模型才会炸，而且报的是别的错。
    """
    import myna.models as models

    calls = []

    def fake_snapshot(repo, **kw):
        calls.append(repo)
        return "/fake/path"

    monkeypatch.setattr("huggingface_hub.snapshot_download", fake_snapshot)
    monkeypatch.setattr(models, "is_downloaded", lambda n: len(calls) >= 3)
    monkeypatch.setattr(models.time if hasattr(models, "time") else __import__("time"),
                        "sleep", lambda s: None)

    path = models.download("tiny", attempts=5)
    assert path == "/fake/path"
    assert len(calls) == 3, "前两次磁盘校验没过，应当继续重试"


def test_download_gives_up_with_clear_error(monkeypatch):
    import myna.models as models

    monkeypatch.setattr("huggingface_hub.snapshot_download", lambda r, **k: "/p")
    monkeypatch.setattr(models, "is_downloaded", lambda n: False)
    monkeypatch.setattr(__import__("time"), "sleep", lambda s: None)

    import pytest as _pytest

    with _pytest.raises(RuntimeError, match="下载失败"):
        models.download("tiny", attempts=2)


def test_download_retries_on_exception(monkeypatch):
    """HF 会中途掐断连接（peer closed connection），要能续传重试。"""
    import myna.models as models

    n = {"i": 0}

    def flaky(repo, **kw):
        n["i"] += 1
        if n["i"] < 3:
            raise OSError("peer closed connection")
        return "/fake"

    monkeypatch.setattr("huggingface_hub.snapshot_download", flaky)
    monkeypatch.setattr(models, "is_downloaded", lambda x: True)
    monkeypatch.setattr(__import__("time"), "sleep", lambda s: None)

    assert models.download("tiny", attempts=5) == "/fake"
    assert n["i"] == 3


# ---------- 断点进度 ----------


def _make_cache(tmp_path, name, *, partial=0, complete=False):
    """造一个 HF 缓存目录：partial 字节的 .incomplete，或完整的 model.bin。"""
    from myna import models

    d = tmp_path / ("models--" + models.resolve_model(name).replace("/", "--"))
    (d / "blobs").mkdir(parents=True)
    if partial:
        (d / "blobs" / "abc.incomplete").write_bytes(b"x" * partial)
    if complete:
        snap = d / "snapshots" / "rev1"
        snap.mkdir(parents=True)
        (snap / "model.bin").write_bytes(b"y" * 100)
    return d


def test_partial_bytes_counts_incomplete(tmp_path, monkeypatch):
    from myna import models

    monkeypatch.setattr(models, "cache_root", lambda: tmp_path)
    _make_cache(tmp_path, "turbo", partial=1024 * 1024)
    assert models.partial_bytes("turbo") == 1024 * 1024


def test_describe_reports_resume_progress(tmp_path, monkeypatch):
    """中断过的下载要说清已下多少、百分之多少。"""
    from myna import models

    monkeypatch.setattr(models, "cache_root", lambda: tmp_path)
    _make_cache(tmp_path, "turbo", partial=268 * 1024 * 1024)
    d = models.describe("turbo")

    assert not d["downloaded"]
    assert d["partial"] == "268.0M"
    assert d["partial_percent"] == 16          # 268M / 1.6G
    assert d["size"] == "1.6G", "没下完必须报预计总量，不能报磁盘已占量"


def test_size_is_total_not_occupied_while_incomplete(tmp_path, monkeypatch):
    """真实 bug：分母用了磁盘已占量，显示成「已下 268M / 272M」，看着像快好了。"""
    from myna import models

    monkeypatch.setattr(models, "cache_root", lambda: tmp_path)
    _make_cache(tmp_path, "turbo", partial=268 * 1024 * 1024)
    d = models.describe("turbo")
    assert d["size"] != d["partial"]
    assert d["size"] == models.APPROX_SIZES["turbo"]


def test_completed_reports_actual_disk_size(tmp_path, monkeypatch):
    from myna import models

    monkeypatch.setattr(models, "cache_root", lambda: tmp_path)
    _make_cache(tmp_path, "tiny", complete=True)
    d = models.describe("tiny")
    assert d["downloaded"]
    assert d["partial"] is None
    assert d["partial_percent"] is None


def test_no_partial_when_nothing_downloaded(tmp_path, monkeypatch):
    from myna import models

    monkeypatch.setattr(models, "cache_root", lambda: tmp_path)
    d = models.describe("tiny")
    assert not d["downloaded"] and d["partial_bytes"] == 0 and d["partial"] is None


def test_cache_root_prefers_env_over_config(monkeypatch, tmp_path):
    """systemd 单元或命令行前缀里显式设的 HF_HUB_CACHE 应当压过配置文件。"""
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "from-env"))
    cfg = config_mod.Config()
    cfg.models.cache_dir = str(tmp_path / "from-config")
    monkeypatch.setattr(config_mod, "load", lambda path=None: cfg)
    assert models.cache_root() == tmp_path / "from-env"


def test_cache_root_uses_config_when_no_env(monkeypatch, tmp_path):
    monkeypatch.delenv("HF_HUB_CACHE", raising=False)
    monkeypatch.delenv("HF_HOME", raising=False)
    cfg = config_mod.Config()
    cfg.models.cache_dir = str(tmp_path / "from-config")
    monkeypatch.setattr(config_mod, "load", lambda path=None: cfg)
    assert models.cache_root() == tmp_path / "from-config"


def test_cache_root_falls_back_when_config_broken(monkeypatch, tmp_path):
    """配置读不出来不该连模型都找不到，退回 HF 自己的规矩。"""
    monkeypatch.delenv("HF_HUB_CACHE", raising=False)
    monkeypatch.setenv("HF_HOME", str(tmp_path / "hfhome"))

    def boom(path=None):
        raise ValueError("坏掉的 TOML")

    monkeypatch.setattr(config_mod, "load", boom)
    assert models.cache_root() == tmp_path / "hfhome" / "hub"


def test_disk_size_counts_blobs_on_another_disk(monkeypatch, tmp_path):
    """权重 blob 挪到别的盘、这边只剩跨盘符号链接时，不能算成 0 字节。"""
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    blob = elsewhere / "deadbeef"
    blob.write_bytes(b"x" * 4096)

    root = tmp_path / "hub"
    d = root / "models--Systran--faster-whisper-small" / "snapshots" / "rev"
    d.mkdir(parents=True)
    (d / "model.bin").symlink_to(blob)
    monkeypatch.setattr(models, "cache_root", lambda: root)
    assert models.disk_size("small") == "4K"
