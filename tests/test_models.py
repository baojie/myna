from myna import models


def test_preset_resolves_to_full_repo_id():
    assert models.resolve_model("turbo") == "Systran/faster-whisper-large-v3-turbo"
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
