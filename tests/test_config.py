from myna import config as config_mod


def test_defaults_without_file(tmp_path):
    cfg = config_mod.load(tmp_path / "nope.toml")
    assert cfg.asr.model == "qwen3"
    assert cfg.asr.initial_prompt  # 必需参数，不能是空
    assert cfg.inject.method == "clipboard"
    assert cfg.hotwords == {}


def test_partial_override(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text(
        '[asr]\nmodel = "medium"\n\n[hotwords]\n"三步" = "散步"\n', encoding="utf-8"
    )
    cfg = config_mod.load(p)
    assert cfg.asr.model == "medium"
    assert cfg.asr.beam_size == 5  # 未指定的字段保持默认
    assert cfg.hotwords == {"三步": "散步"}


def test_unknown_key_ignored(tmp_path):
    """配置里写错一个键不该让整个程序起不来。"""
    p = tmp_path / "config.toml"
    p.write_text('[asr]\nmodel = "small"\nnonsense = 1\n', encoding="utf-8")
    cfg = config_mod.load(p)
    assert cfg.asr.model == "small"


def test_socket_under_runtime_dir():
    assert config_mod.socket_path().name == "control.sock"
