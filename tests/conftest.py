"""全局测试隔离。

跑测试绝不能碰用户真实的数据目录。daemon 的测试会走完整流水线（包括写识别
历史），默认 Config() 的存档路径就是 `~/.local/share/myna/`——不拦住的话，
每跑一次 pytest 就往真实历史里灌几条假记录，而这些记录日后是要拿去做纠错的。
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
