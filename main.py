#!/usr/bin/env python3
"""稳定宿主入口：python main.py，然后访问 http://127.0.0.1:8080"""

from __future__ import annotations

import argparse
import signal
import shutil
import subprocess
import webbrowser
from pathlib import Path

from termux_chat import __version__
from termux_chat.supervisor import RuntimeSupervisor
from termux_chat.supervisor_server import create_supervisor_server


def open_browser(url: str) -> None:
    opener = shutil.which("termux-open-url")
    if opener:
        subprocess.Popen([opener, url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        webbrowser.open(url)


def stop_from_signal(signum, frame) -> None:
    raise KeyboardInterrupt


def main() -> None:
    signal.signal(signal.SIGTERM, stop_from_signal)
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description="DeepSeek Termux DSA A/B 稳定宿主")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址，默认仅回环")
    parser.add_argument("--port", type=int, default=8080, help="监听端口，默认 8080")
    parser.add_argument("--open", action="store_true", help="启动后尝试打开手机浏览器")
    parser.add_argument("--allow-remote", action="store_true", help="允许非回环监听（无认证，慎用）")
    parser.add_argument("--data-dir", type=Path, default=root / "data", help="共享数据与版本快照目录")
    parser.add_argument("--version", action="version", version=__version__)
    args = parser.parse_args()

    if args.host not in {"127.0.0.1", "localhost", "::1"} and not args.allow_remote:
        parser.error("非回环监听必须显式添加 --allow-remote；公共服务请另行配置认证与 TLS。")
    if not 1 <= args.port <= 65535:
        parser.error("端口必须在 1—65535 之间")

    args.data_dir.mkdir(parents=True, exist_ok=True)
    supervisor = RuntimeSupervisor(root, args.data_dir)
    server = None
    try:
        supervisor.start()
        server = create_supervisor_server(supervisor, args.host, args.port)
        status = supervisor.status()
        url_host = "127.0.0.1" if args.host in {"0.0.0.0", "::"} else args.host
        url = f"http://{url_host}:{args.port}"
        print(f"\nDeepSeek Termux v{__version__} · DSA 稳定宿主")
        print(f"打开：{url}")
        print(f"活动主体：{status['active_version_id']} / 槽位 {status['active_slot']}")
        print("修改项目源码后，在「主体」页准备候选版本，再执行激活。")
        print("按 Ctrl+C 停止宿主与全部主体。\n", flush=True)
        if args.open:
            open_browser(url)
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        print("\n正在停止…", flush=True)
    finally:
        if server is not None:
            server.server_close()
        supervisor.shutdown()


if __name__ == "__main__":
    main()
