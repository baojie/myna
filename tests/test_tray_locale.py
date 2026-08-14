"""托盘引入的 locale 污染回归测试。

真实事故：加了 GTK 托盘后，转写 100% 失败。GTK 初始化会 setlocale(LC_ALL, "")，
之后 C 库 strerror 返回本地化字符串，PyAV 用 ascii 解码它就抛 UnicodeDecodeError，
把一个良性的 EOF 信号变成了致命错误。UI 库的全局副作用打死了核心功能。
"""

import locale
import os

import pytest

from myna.tray import _fix_locale_after_gtk


@pytest.fixture(autouse=True)
def restore_locale():
    saved = locale.setlocale(locale.LC_ALL)
    yield
    try:
        locale.setlocale(locale.LC_ALL, saved)
    except Exception:
        pass


def test_strerror_is_ascii_after_fix():
    """strerror 必须是纯 ASCII —— PyAV 会用 ascii 解码它。"""
    try:
        locale.setlocale(locale.LC_ALL, "")
    except locale.Error:
        pytest.skip("系统没有配置 locale")

    _fix_locale_after_gtk()

    for errno in (2, 13, 22):  # ENOENT / EACCES / EINVAL
        msg = os.strerror(errno)
        msg.encode("ascii")  # 非 ASCII 会在这里炸，正是我们要防的


def test_lc_numeric_is_c_after_fix():
    """小数点必须是点，不能被 locale 变成逗号。"""
    _fix_locale_after_gtk()
    assert locale.localeconv()["decimal_point"] == "."


def test_idempotent():
    _fix_locale_after_gtk()
    _fix_locale_after_gtk()
    os.strerror(2).encode("ascii")
