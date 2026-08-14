"""命令行入口。"""

from __future__ import annotations

import argparse
import json
import sys

from . import config as config_mod
from .client import DaemonUnavailable, request

HINT = "守护进程没有运行。启动：systemctl --user start myna    （或前台调试：myna daemon）"


def _simple(cmd: str, payload: dict | None = None) -> int:
    try:
        resp = request(cmd, payload=payload)
    except DaemonUnavailable as e:
        print(f"{e}。{HINT}", file=sys.stderr)
        return 1
    if resp.get("ok"):
        print(resp.get("state", ""))
        return 0
    print(resp.get("error", "失败"), file=sys.stderr)
    return 1


def cmd_model(args) -> int:
    """切换识别模型。切换在后台进行，返回后模型仍在加载。"""
    try:
        resp = request("switch", payload={"model": args.name})
    except DaemonUnavailable as e:
        print(f"{e}。{HINT}", file=sys.stderr)
        return 1
    if resp.get("ok"):
        print(f"正在切换…（{args.name}），完成后会通知你")
        return 0
    print(resp.get("error", "切换失败"), file=sys.stderr)
    return 1


def cmd_status(args) -> int:
    try:
        resp = request("status")
    except DaemonUnavailable as e:
        print(f"{e}。{HINT}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(resp, ensure_ascii=False, indent=2))
        return 0
    label = {"model": "模型", "device": "设备", "compute_type": "计算类型",
             "state": "状态", "degraded": "已降级", "elapsed": "已录制(秒)",
             "last_text": "上次结果", "socket": "socket"}
    for k, v in resp.items():
        if k == "ok" or v in (None, ""):
            continue
        print(f"{label.get(k, k):<10} {v}")
    return 0


def cmd_daemon(args) -> int:
    from .daemon import run

    return run(config_mod.load(args.config))


def cmd_install(args) -> int:
    from . import install

    try:
        key = args.key or install.DEFAULT_BINDING
        slot = install.install_shortcut(key, force=getattr(args, "force", False))
        print(f"✓ 快捷键已绑定 {key}（{slot}）")
    except Exception as e:
        print(f"✗ 快捷键绑定失败：{e}", file=sys.stderr)
        return 1
    try:
        path = install.install_service()
        print(f"✓ 服务已安装并启动：{path}")
    except Exception as e:
        print(f"✗ 服务安装失败：{e}", file=sys.stderr)
        return 1
    print()
    return cmd_doctor(args)


def cmd_uninstall(args) -> int:
    from . import install

    print("✓ 快捷键已移除" if install.remove_shortcut() else "· 没有找到快捷键")
    install.remove_service()
    print("✓ 服务已停止并移除")
    return 0


def cmd_doctor(args) -> int:
    from . import install

    failed = 0
    for ok, item, detail in install.doctor():
        print(f"{'✓' if ok else '✗'} {item:<22} {detail}")
        if not ok:
            failed += 1
    print()
    print("全部就绪。" if not failed else f"有 {failed} 项需要处理。")
    return 0 if not failed else 1


def cmd_paste_key(args) -> int:
    try:
        if not args.key:
            print(request("status").get("paste_key", "?"))
            return 0
        resp = request("paste_key", {"key": args.key})
    except DaemonUnavailable as e:
        print(f"{e}。{HINT}", file=sys.stderr)
        return 1
    if not resp.get("ok"):
        print(resp.get("error", "失败"), file=sys.stderr)
        return 1
    print(f"粘贴键已设为 {resp['paste_key']}"
          + ("" if resp.get("persisted") else "（本次有效，写入配置失败）"))
    return 0


def cmd_models(args) -> int:
    """列出所有档位：下载状态、体积、断点进度、仓库名、保存位置。"""
    try:
        resp = request("models")
    except DaemonUnavailable:
        # daemon 没跑也该能看——这些信息全在磁盘上，不依赖守护进程
        from . import models as models_mod

        resp = {
            "cache_root": str(models_mod.cache_root()),
            "current": None,
            "models": [models_mod.describe(n) for n in models_mod.PRESETS],
        }

    print(f"模型缓存目录：{resp['cache_root']}")
    if resp.get("current"):
        print(f"当前使用：{resp['current']}")
    print()
    for m in resp["models"]:
        # 一律用 get：daemon 可能还跑着旧版本，字段未必齐全，
        # 一个 KeyError 就让整条命令报废不值当
        partial = m.get("partial_bytes") or 0
        if m.get("active"):
            mark = "●"
        elif m.get("downloaded"):
            mark = "✓"
        elif partial:
            mark = "◐"
        else:
            mark = "·"
        size = m.get("size", "?")
        if m.get("downloaded"):
            state = size
        elif partial:
            pct = f" {m['partial_percent']}%" if m.get("partial_percent") else ""
            state = f"已下 {m.get('partial', '?')}/{size}{pct} 可续传"
        else:
            state = f"未下载 约{size}"
        print(f"{mark} {m['preset']:<9} {state:<28} {m['repo']}")
    print()
    print("● 使用中   ✓ 已下载   ◐ 下载中断可续传   · 未下载")
    print("下载：myna model <档位>")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="myna", description="myna 八哥 —— Linux 桌面语音输入法")
    p.add_argument("--config", type=str, default=None, help="配置文件路径")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("daemon", help="前台运行守护进程").set_defaults(func=cmd_daemon)
    sub.add_parser("toggle", help="开始/停止录音（快捷键绑这个）").set_defaults(
        func=lambda a: _simple("toggle"))
    sub.add_parser("start", help="开始录音").set_defaults(func=lambda a: _simple("start"))
    sub.add_parser("stop", help="停止录音并识别").set_defaults(func=lambda a: _simple("stop"))
    sub.add_parser("cancel", help="放弃本次录音").set_defaults(func=lambda a: _simple("cancel"))

    st = sub.add_parser("status", help="查看状态")
    st.add_argument("--json", action="store_true")
    st.set_defaults(func=cmd_status)

    mdl = sub.add_parser("model", help="切换识别模型（如 medium / turbo）")
    mdl.add_argument("name", help="模型档位或 HuggingFace 模型名")
    mdl.set_defaults(func=cmd_model)

    sub.add_parser("models", help="列出所有模型档位与下载状态").set_defaults(
        func=cmd_models)
    ins = sub.add_parser("install", help="安装快捷键与后台服务")
    ins.add_argument("--key", default=None,
                     help="快捷键，默认 <Super>z（<Super>d 被 GNOME 显示桌面占用）")
    ins.add_argument("--force", action="store_true",
                     help="即使快捷键已被占用也照绑")
    ins.set_defaults(func=cmd_install)

    pk = sub.add_parser("paste-key", help="设置粘贴键（终端用 ctrl+shift+v）")
    pk.add_argument("key", nargs="?", help="如 ctrl+v 或 ctrl+shift+v；省略则显示当前值")
    pk.set_defaults(func=cmd_paste_key)

    sub.add_parser("uninstall", help="移除快捷键与服务").set_defaults(func=cmd_uninstall)
    sub.add_parser("doctor", help="依赖自检").set_defaults(func=cmd_doctor)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if getattr(args, "config", None):
        from pathlib import Path

        args.config = Path(args.config)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
