from myna.config import Config
from myna.postprocess import apply_hotwords, clean, process


def test_hotwords_replaces():
    assert apply_hotwords("去公园三步", {"三步": "散步"}) == "去公园散步"


def test_hotwords_longest_first():
    # 短词先替换会把长词吃掉，必须长词优先
    out = apply_hotwords("八哥语音", {"八哥": "myna", "八哥语音": "myna 输入法"})
    assert out == "myna 输入法"


def test_hotwords_empty_key_ignored():
    assert apply_hotwords("abc", {"": "x"}) == "abc"


def test_clean_strips_wrapping_quotes():
    assert clean(' "你好世界" ') == "你好世界"


def test_clean_joins_newlines():
    assert clean("第一句\n  第二句") == "第一句第二句"


def test_clean_keeps_inner_quotes():
    assert clean('他说“好”然后走了') == '他说“好”然后走了'


def test_process_applies_hotwords():
    cfg = Config()
    cfg.hotwords = {"三步": "散步"}
    assert process("  去公园三步  ", cfg) == "去公园散步"


def test_process_empty():
    assert process("   ", Config()) == ""
