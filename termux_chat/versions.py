"""不可变的 DSA 运行源码快照与活动版本记录。"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import __version__


SOURCE_ITEMS = ("termux_chat", "plugins", "web", "profile.json")
IGNORED = shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class VersionStore:
    """版本源码不可变；活动指针单独保存在 state.json 中。"""

    def __init__(self, root: Path, data_dir: Path) -> None:
        self.root = Path(root).resolve()
        self.data_dir = Path(data_dir).resolve()
        self.runtime_dir = self.data_dir / "dsa"
        self.versions_dir = self.runtime_dir / "versions"
        self.state_file = self.runtime_dir / "state.json"
        self._lock = threading.RLock()
        self.versions_dir.mkdir(parents=True, exist_ok=True)

    def _read_state(self) -> dict[str, str]:
        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return {
                    "active_version_id": str(data.get("active_version_id", "")),
                    "previous_version_id": str(data.get("previous_version_id", "")),
                }
        except (FileNotFoundError, ValueError, TypeError):
            pass
        return {"active_version_id": "", "previous_version_id": ""}

    def _write_state(self, state: dict[str, str]) -> None:
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        temp = self.state_file.with_suffix(".tmp")
        temp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp, self.state_file)

    def state(self) -> dict[str, str]:
        with self._lock:
            return self._read_state()

    @staticmethod
    def _valid_id(version_id: str) -> bool:
        return bool(re.fullmatch(r"v[1-9][0-9]*", version_id))

    def path(self, version_id: str) -> Path:
        if not self._valid_id(version_id):
            raise ValueError("非法 DSA 版本 ID")
        path = self.versions_dir / version_id
        if not (path / "manifest.json").is_file():
            raise KeyError(f"DSA 版本不存在：{version_id}")
        return path

    def get(self, version_id: str) -> dict[str, Any]:
        path = self.path(version_id)
        data = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("id") != version_id:
            raise ValueError(f"DSA 版本清单损坏：{version_id}")
        return data

    def _next_id(self) -> str:
        numbers = []
        for path in self.versions_dir.iterdir():
            match = re.fullmatch(r"v([1-9][0-9]*)", path.name)
            if match and path.is_dir():
                numbers.append(int(match.group(1)))
        return f"v{max(numbers, default=0) + 1}"

    @staticmethod
    def _source_hash(source_root: Path) -> str:
        digest = hashlib.sha256()
        for path in sorted(item for item in source_root.rglob("*") if item.is_file()):
            relative = path.relative_to(source_root).as_posix()
            digest.update(relative.encode("utf-8"))
            digest.update(b"\0")
            with path.open("rb") as source:
                for block in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(block)
        return digest.hexdigest()

    def create_version(self, parent_id: str = "") -> dict[str, Any]:
        """把当前实验源码冻结为候选版本，但不改变活动指针。"""
        with self._lock:
            if parent_id:
                self.get(parent_id)
            version_id = self._next_id()
            final_path = self.versions_dir / version_id
            temp_path = self.versions_dir / f".{version_id}.tmp-{uuid.uuid4().hex}"
            source_root = temp_path / "source"
            source_root.mkdir(parents=True)
            try:
                for name in SOURCE_ITEMS:
                    source = self.root / name
                    target = source_root / name
                    if name in {"termux_chat", "plugins", "web"}:
                        if not source.is_dir():
                            raise RuntimeError(f"运行源码目录缺失：{name}")
                        shutil.copytree(source, target, ignore=IGNORED)
                    else:
                        if not source.is_file():
                            raise RuntimeError("运行插件 profile 缺失：profile.json")
                        shutil.copy2(source, target)
                manifest = {
                    "id": version_id,
                    "parent_id": parent_id,
                    "created_at": now_iso(),
                    "runtime_version": __version__,
                    "source_hash": self._source_hash(source_root),
                }
                # manifest 位于版本根目录，source 才是 worker 的启动目录。
                (temp_path / "manifest.json").write_text(
                    json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                os.replace(temp_path, final_path)
            except Exception:
                shutil.rmtree(temp_path, ignore_errors=True)
                raise
            return manifest

    def ensure_initial_version(self) -> str:
        with self._lock:
            state = self._read_state()
            active_id = state["active_version_id"]
            if active_id:
                try:
                    self.path(active_id)
                    return active_id
                except (KeyError, ValueError):
                    pass
            manifest = self.create_version()
            state["active_version_id"] = manifest["id"]
            state["previous_version_id"] = ""
            self._write_state(state)
            return manifest["id"]

    def set_active(self, version_id: str) -> dict[str, str]:
        with self._lock:
            self.path(version_id)
            state = self._read_state()
            current = state["active_version_id"]
            if current != version_id:
                state["previous_version_id"] = current
                state["active_version_id"] = version_id
                self._write_state(state)
            return state

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            state = self._read_state()
            items = []
            for path in self.versions_dir.iterdir():
                if not self._valid_id(path.name) or not path.is_dir():
                    continue
                try:
                    manifest = self.get(path.name)
                except (KeyError, ValueError, json.JSONDecodeError):
                    continue
                items.append({
                    "id": manifest["id"],
                    "parent_id": manifest.get("parent_id", ""),
                    "created_at": manifest.get("created_at", ""),
                    "runtime_version": manifest.get("runtime_version", ""),
                    "source_hash": manifest.get("source_hash", ""),
                    "active": manifest["id"] == state["active_version_id"],
                    "rollback_target": manifest["id"] == state["previous_version_id"],
                })
            return sorted(items, key=lambda item: int(item["id"][1:]), reverse=True)
