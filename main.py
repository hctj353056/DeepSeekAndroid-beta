#!/usr/bin/env python3
"""Termux 启动入口：python main.py，然后访问 http://127.0.0.1:8080"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import webbrowser
from pathlib import Path
from types import SimpleNamespace

from termux_chat import __version__
from termux_chat.context import Context
from termux_chat.plugin_manager import PluginManager
from termux_chat.server import create_server


def open_browser(url: str) -> None:
    opener = shutil.which("termux-open-url")
    if opener:
        subprocess.Popen([opener, url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        webbrowser.open(url)


def main() -> None:
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description="DeepSeek Termux 本地聊天服务")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址，默认仅回环")
    parser.add_argument("--port", type=int, default=8080, help="监听端口，默认 8080")
    parser.add_argument("--open", action="store_true", help="启动后尝试打开手机浏览器")
    parser.add_argument("--allow-remote", action="store_true", help="允许非回环监听（无认证，慎用）")
    parser.add_argument("--data-dir", type=Path, default=root / "data", help="本地数据目录")
    parser.add_argument("--profile", default="profile.json", help="插件 profile 文件")
    parser.add_argument("--version", action="version", version=__version__)
    args = parser.parse_args()

    if args.host not in {"127.0.0.1", "localhost", "::1"} and not args.allow_remote:
        parser.error("非回环监听必须显式添加 --allow-remote；公共服务请另行配置认证与 TLS。")
    if not 1 <= args.port <= 65535:
        parser.error("端口必须在 1—65535 之间")

    args.data_dir.mkdir(parents=True, exist_ok=True)
    context = Context()
    context.provide("paths", SimpleNamespace(root=root, data_dir=args.data_dir, web_root=root / "web"))
    manager = PluginManager(root, context, args.data_dir)
    manager.load_profile(args.profile)

    failed = [item for item in manager.list() if item["error"]]
    for item in failed:
        print(f"[插件警告] {item['id']}: {item['error']}")
    if not hasattr(context, "chat"):
        manager.shutdown()
        raise SystemExit("Agent 服务未能启动，请检查上方插件错误。")

    server = create_server(context, manager, root / "web", args.host, args.port)
    url_host = "127.0.0.1" if args.host in {"0.0.0.0", "::"} else args.host
    url = f"http://{url_host}:{args.port}"
    print(f"\nDeepSeek Termux v{__version__}")
    print(f"打开：{url}")
    print(f"插件：{sum(item.enabled for item in manager.records.values())}/{len(manager.records)} 已启用")
    print("主体架构文件：灵魂架构/灵魂架构.md（首次启动自动创建）")
    print("API Key 文件：001.key（首次启动自动创建；文件为明文，请勿分享）")
    for warning in getattr(context.settings, "file_warnings", []):
        print(f"[配置文件警告] {warning}")
    print("按 Ctrl+C 停止。\\n")
    if args.open:
        open_browser(url)
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        print("\n正在停止…")
    finally:
        server.server_close()
        manager.shutdown()


if __name__ == "__main__":
    main()
