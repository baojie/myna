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
