"""插件共享的服务容器、事件总线和能力注册表。"""

from __future__ import annotations

import copy
import threading
from collections import defaultdict
from typing import Any, Callable


Disposer = Callable[[], None]


class EventBus:
    """同步事件总线；waterfall 允许插件依次改写同一个请求对象。"""

    def __init__(self) -> None:
        self._listeners: dict[str, list[Callable[..., Any]]] = defaultdict(list)
        self._lock = threading.RLock()

    def on(self, event: str, listener: Callable[..., Any]) -> Disposer:
        with self._lock:
            self._listeners[event].append(listener)

        def dispose() -> None:
            with self._lock:
                if listener in self._listeners.get(event, []):
                    self._listeners[event].remove(listener)

        return dispose

    def emit(self, event: str, payload: Any) -> None:
        with self._lock:
            listeners = tuple(self._listeners.get(event, ()))
        for listener in listeners:
            listener(payload)

    def waterfall(self, event: str, payload: Any) -> Any:
        with self._lock:
            listeners = tuple(self._listeners.get(event, ()))
        current = payload
        for listener in listeners:
            result = listener(current)
            if result is not None:
                current = result
        return current


class ModelRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, Any] = {}
        self._lock = threading.RLock()

    def register(self, provider_id: str, provider: Any) -> Disposer:
        with self._lock:
            if not provider_id or provider_id in self._providers:
                raise ValueError(f"模型提供方已存在或名称为空：{provider_id!r}")
            self._providers[provider_id] = provider

        def dispose() -> None:
            with self._lock:
                if self._providers.get(provider_id) is provider:
                    del self._providers[provider_id]

        return dispose

    def get(self, provider_id: str) -> Any:
        with self._lock:
            try:
                return self._providers[provider_id]
            except KeyError as exc:
                raise RuntimeError(f"模型提供方不可用：{provider_id}") from exc

    def ids(self) -> list[str]:
        with self._lock:
            return sorted(self._providers)


class ToolRegistry:
    """同时容纳本地 function 工具与 DeepSeek 服务端工具。"""

    def __init__(self) -> None:
        self._tools: dict[str, tuple[dict[str, Any], Callable[..., Any] | None, str]] = {}
        self._lock = threading.RLock()

    @staticmethod
    def name_of(schema: dict[str, Any]) -> str:
        if schema.get("type") == "function":
            return str(schema.get("function", {}).get("name", ""))
        return str(schema.get("type", ""))

    def register(
        self,
        schema: dict[str, Any],
        handler: Callable[..., Any] | None = None,
        *,
        plugin_id: str = "",
    ) -> Disposer:
        name = self.name_of(schema)
        if not name:
            raise ValueError("工具 schema 缺少名称")
        with self._lock:
            if name in self._tools:
                raise ValueError(f"工具已注册：{name}")
            self._tools[name] = (copy.deepcopy(schema), handler, plugin_id)

        def dispose() -> None:
            with self._lock:
                current = self._tools.get(name)
                if current and current[2] == plugin_id:
                    del self._tools[name]

        return dispose

    def schemas(self) -> list[dict[str, Any]]:
        with self._lock:
            return [copy.deepcopy(item[0]) for item in self._tools.values()]

    def has_local(self, name: str) -> bool:
        with self._lock:
            item = self._tools.get(name)
            return bool(item and item[1])

    def execute(self, name: str, arguments: dict[str, Any]) -> str:
        with self._lock:
            item = self._tools.get(name)
        if not item or not item[1]:
            raise RuntimeError(f"没有可本地执行的工具：{name}")
        result = item[1](arguments)
        return result if isinstance(result, str) else str(result)

    def names(self) -> list[str]:
        with self._lock:
            return sorted(self._tools)


class Context:
    """稳定服务键的容器；插件不直接 import 彼此的具体实现。"""

    def __init__(self) -> None:
        self.events = EventBus()
        self.models = ModelRegistry()
        self.tools = ToolRegistry()
        self._services: dict[str, Any] = {}

    def provide(self, name: str, service: Any) -> Disposer:
        if not name or name in self._services:
            raise ValueError(f"服务已存在或名称为空：{name!r}")
        self._services[name] = service
        object.__setattr__(self, name, service)

        def dispose() -> None:
            if self._services.get(name) is service:
                del self._services[name]
                object.__delattr__(self, name)

        return dispose

    def require(self, name: str) -> Any:
        if name not in self._services:
            raise RuntimeError(f"所需服务尚未就绪：{name}")
        return self._services[name]


class EffectScope:
    """记录插件副作用，并在卸载时按相反顺序撤销。"""

    def __init__(self) -> None:
        self._disposers: list[Disposer] = []

    def add(self, disposer: Disposer | None) -> None:
        if callable(disposer):
            self._disposers.append(disposer)

    def dispose(self) -> list[str]:
        errors: list[str] = []
        while self._disposers:
            disposer = self._disposers.pop()
            try:
                disposer()
            except Exception as exc:  # 保证其余资源仍能释放
                errors.append(str(exc))
        return errors


class PluginContext:
    """插件看到的 Context；其注册会自动绑定到当前插件生命周期。"""

    def __init__(self, root: Context, scope: EffectScope, plugin_id: str) -> None:
        self.root = root
        self.scope = scope
        self.plugin_id = plugin_id

    def __getattr__(self, name: str) -> Any:
        return getattr(self.root, name)

    def effect(self, disposer: Disposer | None) -> None:
        self.scope.add(disposer)

    def provide(self, name: str, service: Any) -> None:
        self.scope.add(self.root.provide(name, service))

    def on(self, event: str, listener: Callable[..., Any]) -> None:
        self.scope.add(self.root.events.on(event, listener))
