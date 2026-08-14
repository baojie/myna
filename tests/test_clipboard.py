"""剪贴板写入的回归测试。

真实事故：wl-copy 和 xclip 都**不会退出**（Wayland/X11 的剪贴板要求源进程存活
提供内容），原实现却用 subprocess.run(timeout=3) 等它结束。结果每次都超时、
判定「写剪贴板失败」并直接 return——粘贴那一步压根没执行。用户说完话，文本
静静躺在剪贴板里，屏幕上什么也没出现。
"""

import subprocess

import pytest

from myna import inject


class FakeProc:
    """模拟一个写完就常驻、不退出的 wl-copy。"""

    def __init__(self, *a, **k):
        self.stdin = _FakeStdin()
        self._alive = True

    def poll(self):
        return None if self._alive else 0


class _FakeStdin:
    def __init__(self):
        self.written = b""

    def write(self, b):
        self.written += b

    def close(self):
        pass


@pytest.fixture(autouse=True)
def clean_holders():
    inject._holders.clear()
    yield
    inject._holders.clear()


def test_does_not_wait_for_clipboard_process(monkeypatch):
    """绝不能等 wl-copy 退出——它永远不退。"""
    monkeypatch.setattr(inject.shutil, "which", lambda p: f"/usr/bin/{p}")
    monkeypatch.setattr(inject.subprocess, "Popen", FakeProc)
    monkeypatch.setattr(inject.time, "sleep", lambda s: None)
    monkeypatch.setattr(inject, "clipboard_get", lambda: "你好")

    def forbidden(*a, **k):
        raise AssertionError("不该用 subprocess.run 等剪贴板进程结束")

    monkeypatch.setattr(inject.subprocess, "run", forbidden)

    assert inject.clipboard_set("你好") is True


def test_verifies_readback(monkeypatch):
    """读回校验：写进去的必须真能读出来，否则算失败。"""
    monkeypatch.setattr(inject.shutil, "which", lambda p: f"/usr/bin/{p}")
    monkeypatch.setattr(inject.subprocess, "Popen", FakeProc)
    monkeypatch.setattr(inject.time, "sleep", lambda s: None)
    monkeypatch.setattr(inject, "clipboard_get", lambda: "别的东西")

    assert inject.clipboard_set("你好") is False


def test_readback_tolerates_trailing_whitespace(monkeypatch):
    monkeypatch.setattr(inject.shutil, "which", lambda p: f"/usr/bin/{p}")
    monkeypatch.setattr(inject.subprocess, "Popen", FakeProc)
    monkeypatch.setattr(inject.time, "sleep", lambda s: None)
    monkeypatch.setattr(inject, "clipboard_get", lambda: "你好\n")

    assert inject.clipboard_set("你好") is True


def test_reaps_exited_holders(monkeypatch):
    """回收退出的持有进程，避免僵尸堆积。"""
    p = FakeProc()
    p._alive = False
    inject._holders.append(p)
    inject._reap()
    assert inject._holders == []


def test_keeps_live_holders():
    p = FakeProc()
    inject._holders.append(p)
    inject._reap()
    assert inject._holders == [p]  # 还活着的（正持有剪贴板）不能动


def test_no_clipboard_tool_available(monkeypatch):
    monkeypatch.setattr(inject.shutil, "which", lambda p: None)
    assert inject.clipboard_set("你好") is False
