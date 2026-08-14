#!/bin/bash
# 取证探针：myna 每次停止时，记下「是谁停的」。
#
# 背景：myna 反复被停掉（点托盘后、语音识别成功后各发生过一次），systemd 只说
# Stopping——即收到了 stop 请求，但不记录请求方。已排除：gnome-shell 没崩、
# graphical-session.target 没动、不是 OOM、myna 自己除 uninstall 外无 stop 路径。
#
# 走 D-Bus eavesdrop 抓不到，因为 `systemctl --user` 用的是 user manager 的私有
# socket（/run/user/*/systemd/private），根本不经过 session bus。
#
# 而 ExecStopPost 无论请求从哪条路进来都会执行，且此刻 `systemctl stop` 通常
# 还阻塞着等停止完成——进程表里大概率还抓得到它，连同它的父进程。
#
# 装法（drop-in，见 ~/.config/systemd/user/myna.service.d/forensics.conf）：
#   [Service]
#   ExecStopPost=/path/to/who-stopped-myna.sh
# 输出直接进 journal：journalctl --user -u myna
#
# 查清之后记得撤掉 drop-in——这只是取证脚手架，不是常设功能。

set -u

echo "=== 取证：myna 正在停止 ==="
# success = 干净退出（主动 stop 或自己 return 0）；其余值说明是崩溃/被杀
echo "  SERVICE_RESULT=${SERVICE_RESULT:-?} EXIT_CODE=${EXIT_CODE:-?} EXIT_STATUS=${EXIT_STATUS:-?}"

echo "  --- 此刻在跑的 systemctl（stop 会阻塞等待，多半还在）"
ps -eo pid,ppid,etimes,cmd 2>/dev/null |
    grep -E "systemctl|myna" | grep -v grep | sed 's/^/    /'

echo "  --- 上述进程的父链（真正的发起者往往是父进程，比如某个脚本）"
for pid in $(pgrep -f "systemctl.*myna" 2>/dev/null); do
    cur=$pid
    for _ in 1 2 3 4; do
        [ -r "/proc/$cur/cmdline" ] || break
        echo "    $cur: $(tr '\0' ' ' <"/proc/$cur/cmdline")"
        cur=$(awk '/^PPid:/{print $2}' "/proc/$cur/status" 2>/dev/null)
        [ -n "$cur" ] && [ "$cur" != "0" ] || break
    done
done

echo "=== 取证结束 ==="
