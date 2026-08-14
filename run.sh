#!/usr/bin/env bash
#
# 启动 myna 八哥语音输入。
#
#   ./run.sh                 前台启动守护进程（Ctrl+C 退出），日志直接打在终端
#   ./run.sh install         绑定快捷键 + 装 systemd 服务，之后开机自启
#   ./run.sh doctor          依赖自检
#   ./run.sh status          看状态
#   ./run.sh toggle          开始/停止录音
#   ./run.sh restart         重启后台服务（改完代码用这个让它生效）
#   ./run.sh log             跟踪后台服务日志
#   ./run.sh test            跑单元测试
#   ./run.sh <其他>          原样透传给 myna
#
# 不需要先 pip install：脚本直接把本 checkout 的 src/ 加进 PYTHONPATH，
# clone 下来就能跑。
#
set -euo pipefail

# 解析符号链接，这样从 ~/.local/bin 软链过来也能找到本 checkout
SCRIPT="$(readlink -f "${BASH_SOURCE[0]}")"
HERE="$(cd "$(dirname "$SCRIPT")" && pwd)"

# onnxruntime（GPU 版）装在 /data/pip、cuDNN 在 /data/cudnn（主盘紧张，重依赖一律放 /data）
export PYTHONPATH="/data/pip:$HERE/src${PYTHONPATH:+:$PYTHONPATH}"
export LD_LIBRARY_PATH="/data/cudnn/nvidia/cudnn/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
PY="${PYTHON:-python3}"

cmd="${1:-daemon}"

case "$cmd" in
  restart)
    systemctl --user restart myna.service
    # 等它把模型读进显存再报告，否则用户看到的是「还没起来」的假象
    for _ in $(seq 1 45); do
      "$PY" -m myna status >/dev/null 2>&1 && break
      sleep 1
    done
    "$PY" -m myna status
    ;;
  log)
    exec journalctl --user -u myna.service -f -n 50
    ;;
  test)
    shift
    exec "$PY" -m pytest "$@"
    ;;
  daemon)
    # 后台服务开着的话，前台再起一个会抢同一个 socket 和显存
    if systemctl --user is-active --quiet myna.service 2>/dev/null; then
      echo "后台服务正在运行。要前台调试请先停掉它：" >&2
      echo "    systemctl --user stop myna.service" >&2
      echo "或者直接用：./run.sh restart / ./run.sh log" >&2
      exit 1
    fi
    exec "$PY" -m myna daemon
    ;;
  *)
    exec "$PY" -m myna "$@"
    ;;
esac
