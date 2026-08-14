"""命令行入口。"""

from __future__ import annotations

import argparse
import json
import sys

from . import config as config_mod
from .client import DaemonUnavailable, request

HINT = "守护进程没有运行。启动：systemctl --user start myna    （或前台调试：myna daemon）"


def _simple(cmd: str) -> int:
    try:
        resp = request(cmd)
    except DaemonUnavailable as e:
        print(f"{e}。{HINT}", file=sys.stderr)
        return 1
    if resp.get("ok"):
        print(resp.get("state", ""))
        return 0
    print(resp.get("error", "失败"), file=sys.stderr)
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
        slot = install.install_shortcut(args.key)
        print(f"✓ 快捷键已绑定 {args.key}（{slot}）")
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

    ins = sub.add_parser("install", help="安装快捷键与后台服务")
    ins.add_argument("--key", default="<Super>d", help="快捷键，默认 <Super>d")
    ins.set_defaults(func=cmd_install)

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
