"""独立 DSA 主体进程：由稳定宿主在 A/B 槽位中启动。"""

from __future__ import annotations

import argparse
import os
import threading
import time
from pathlib import Path
from types import SimpleNamespace

from . import __version__
from .context import Context
from .plugin_manager import PluginManager
from .server import create_server


def build_runtime(root: Path, data_dir: Path, profile: str):
    root = Path(root).resolve()
    data_dir = Path(data_dir).resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    context = Context()
    context.provide("paths", SimpleNamespace(root=root, data_dir=data_dir, web_root=root / "web"))
    manager = PluginManager(root, context, data_dir)
    manager.load_profile(profile)
    failed = [item for item in manager.list() if item["error"]]
    for item in failed:
        print(f"[插件警告] {item['id']}: {item['error']}", flush=True)
    if not hasattr(context, "chat"):
        manager.shutdown()
        raise RuntimeError("Agent 服务未能启动，请检查插件错误")
    return context, manager


def main() -> None:
    parser = argparse.ArgumentParser(description="DSA A/B 独立智能体主体")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--profile", default="profile.json")
    args = parser.parse_args()
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        parser.error("DSA worker 仅允许监听回环地址")
    if not 1 <= args.port <= 65535:
        parser.error("端口必须在 1—65535 之间")

    root = Path(__file__).resolve().parents[1]
    context, manager = build_runtime(root, args.data_dir, args.profile)
    server = create_server(context, manager, root / "web", args.host, args.port)
    parent_pid = os.getppid()

    def watch_parent() -> None:
        while os.getppid() == parent_pid:
            time.sleep(1)
        server.shutdown()

    threading.Thread(target=watch_parent, daemon=True, name="dsa-parent-watchdog").start()
    print(f"DSA worker v{__version__} 已启动：{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        manager.shutdown()


if __name__ == "__main__":
    main()
