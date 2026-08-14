"""瘦客户端：连 socket、发一行 JSON、读一行 JSON。快捷键绑的就是它，必须够快。"""

from __future__ import annotations

import json
import socket

from .config import socket_path


class DaemonUnavailable(Exception):
    pass


def request(cmd: str, timeout: float = 5.0) -> dict:
    path = socket_path()
    if not path.exists():
        raise DaemonUnavailable("守护进程没有运行")
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(str(path))
        s.sendall((json.dumps({"cmd": cmd}) + "\n").encode())
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
    except (ConnectionRefusedError, FileNotFoundError) as e:
        raise DaemonUnavailable("守护进程没有运行") from e
    except socket.timeout as e:
        raise DaemonUnavailable("守护进程无响应") from e
    finally:
        s.close()
    if not buf.strip():
        raise DaemonUnavailable("守护进程无响应")
    return json.loads(buf.decode("utf-8", "replace"))


def ping() -> bool:
    try:
        return bool(request("ping", timeout=1.0).get("ok"))
    except Exception:
        return False
