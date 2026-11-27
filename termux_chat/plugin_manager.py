"""目录发现、依赖排序、热启停与可逆卸载。"""

from __future__ import annotations

import importlib.util
import json
import os
import threading
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any

from .context import Context, EffectScope, PluginContext


@dataclass
class PluginRecord:
    meta: dict[str, Any]
    path: Path
    module: ModuleType | None = None
    scope: EffectScope | None = None
    enabled: bool = False
    error: str = ""
    dependents: set[str] = field(default_factory=set)

    @property
    def plugin_id(self) -> str:
        return str(self.meta["id"])


class PluginManager:
    def __init__(self, root: Path, context: Context, data_dir: Path | None = None) -> None:
        self.root = Path(root)
        self.plugin_root = self.root / "plugins"
        self.context = context
        self.data_dir = data_dir or self.root / "data"
        self.state_file = self.data_dir / "plugin_states.json"
        self.records: dict[str, PluginRecord] = {}
        self._states: dict[str, bool] = {}
        self._lock = threading.RLock()
        self._load_generation = 0
        self._enable_stack: list[str] = []
        self.discover()
        self._states = self._read_states()

    def discover(self) -> None:
        self.records.clear()
        if not self.plugin_root.exists():
            return
        for directory in sorted(self.plugin_root.iterdir()):
            manifest = directory / "plugin.json"
            entry = directory / "plugin.py"
            if not directory.is_dir() or not manifest.is_file() or not entry.is_file():
                continue
            try:
                meta = json.loads(manifest.read_text(encoding="utf-8"))
                plugin_id = str(meta["id"])
                if not plugin_id or plugin_id in self.records:
                    raise ValueError("插件 id 为空或重复")
                meta.setdefault("name", plugin_id)
                meta.setdefault("version", "0.1.0")
                meta.setdefault("description", "")
                meta.setdefault("requires", [])
                meta.setdefault("core", False)
                self.records[plugin_id] = PluginRecord(meta=meta, path=directory)
            except Exception as exc:
                # 清单损坏也显示在管理页，而不是静默消失。
                plugin_id = directory.name
                self.records[plugin_id] = PluginRecord(
                    meta={
                        "id": plugin_id,
                        "name": plugin_id,
                        "version": "-",
                        "description": "插件清单读取失败",
                        "requires": [],
                        "core": False,
                    },
                    path=directory,
                    error=str(exc),
                )

    def _read_states(self) -> dict[str, bool]:
        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
            return {str(k): bool(v) for k, v in data.items()}
        except (FileNotFoundError, ValueError, TypeError):
            return {}

    def _save_states(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        temp = self.state_file.with_suffix(".tmp")
        temp.write_text(json.dumps(self._states, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp, self.state_file)

    def load_profile(self, profile_name: str = "profile.json") -> None:
        profile_path = self.root / profile_name
        profile = json.loads(profile_path.read_text(encoding="utf-8"))
        requested = list(dict.fromkeys([*profile.get("plugins", []), *(k for k, v in self._states.items() if v)]))
        for plugin_id in requested:
            if not self._states.get(plugin_id, True):
                continue
            try:
                self.enable(plugin_id, persist=False)
            except Exception as exc:
                record = self.records.get(plugin_id)
                if record:
                    record.error = str(exc)

    def enable(self, plugin_id: str, *, persist: bool = True) -> None:
        with self._lock:
            record = self.records.get(plugin_id)
            if not record:
                raise RuntimeError(f"未发现插件：{plugin_id}")
            if record.enabled:
                return
            if plugin_id in self._enable_stack:
                cycle = self._enable_stack[self._enable_stack.index(plugin_id):] + [plugin_id]
                raise RuntimeError("检测到插件依赖循环：" + " -> ".join(cycle))
            if record.error and record.module is None and not (record.path / "plugin.json").is_file():
                raise RuntimeError(record.error)

            requires = record.meta.get("requires", [])
            if not isinstance(requires, list) or any(not isinstance(item, str) or not item.strip() for item in requires):
                raise RuntimeError(f"插件 {plugin_id} 的 requires 必须是非空字符串组成的列表")
            missing = [item for item in requires if item not in self.records]
            if missing:
                raise RuntimeError(f"插件 {plugin_id} 依赖不存在：" + "、".join(missing))

            self._enable_stack.append(plugin_id)
            scope = EffectScope()
            try:
                for dependency in requires:
                    self.enable(dependency, persist=False)
                    self.records[dependency].dependents.add(plugin_id)

                if record.module is None:
                    self._load_generation += 1
                    module_name = f"deepseek_termux_plugin_{plugin_id}_{self._load_generation}"
                    spec = importlib.util.spec_from_file_location(module_name, record.path / "plugin.py")
                    if not spec or not spec.loader:
                        raise ImportError("无法创建插件加载器")
                    module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(module)
                    record.module = module
                apply = getattr(record.module, "apply", None)
                if not callable(apply):
                    raise AttributeError("plugin.py 必须定义 apply(ctx)")
                plugin_context = PluginContext(self.context, scope, plugin_id)
                cleanup = apply(plugin_context)
                scope.add(cleanup)
                record.scope = scope
                record.enabled = True
                record.error = ""
                if persist:
                    self._states[plugin_id] = True
                    self._save_states()
            except Exception as exc:
                scope.dispose()
                record.scope = None
                record.enabled = False
                record.error = str(exc)
                raise RuntimeError(f"插件 {plugin_id} 启用失败：{exc}") from exc
            finally:
                if self._enable_stack and self._enable_stack[-1] == plugin_id:
                    self._enable_stack.pop()

    def disable(self, plugin_id: str, *, persist: bool = True, force: bool = False) -> None:
        with self._lock:
            record = self.records.get(plugin_id)
            if not record or not record.enabled:
                return
            if record.meta.get("core") and not force:
                raise RuntimeError("核心插件提供当前运行所需服务，不能停用")
            active_dependents = [] if force else [
                item.plugin_id
                for item in self.records.values()
                if item.enabled and plugin_id in item.meta.get("requires", [])
            ]
            if active_dependents:
                raise RuntimeError("请先停用依赖它的插件：" + "、".join(active_dependents))
            errors = record.scope.dispose() if record.scope else []
            record.scope = None
            record.enabled = False
            for dependency in record.meta.get("requires", []):
                dependency_record = self.records.get(dependency)
                if dependency_record:
                    dependency_record.dependents.discard(plugin_id)
            if errors:
                record.error = "；".join(errors)
            if persist:
                self._states[plugin_id] = False
                self._save_states()

    def set_enabled(self, plugin_id: str, enabled: bool) -> None:
        if enabled:
            self.enable(plugin_id)
        else:
            self.disable(plugin_id)

    def list(self) -> list[dict[str, Any]]:
        return [
            {
                "id": record.plugin_id,
                "name": record.meta.get("name", record.plugin_id),
                "version": record.meta.get("version", "-"),
                "description": record.meta.get("description", ""),
                "requires": record.meta.get("requires", []),
                "provides": record.meta.get("provides", []),
                "core": bool(record.meta.get("core")),
                "enabled": record.enabled,
                "error": record.error,
            }
            for record in sorted(self.records.values(), key=lambda item: (not item.meta.get("core"), item.plugin_id))
        ]

    def shutdown(self) -> None:
        for record in sorted(self.records.values(), key=lambda item: item.plugin_id, reverse=True):
            try:
                self.disable(record.plugin_id, persist=False, force=True)
            except Exception:
                traceback.print_exc()
