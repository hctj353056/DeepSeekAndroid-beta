"""稳定宿主：管理 A/B 独立主体、候选检查、激活与回滚。"""

from __future__ import annotations

import http.client
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .versions import VersionStore
from .diagnostics import DiagnosticLogger


@dataclass
class WorkerHandle:
    slot: str
    version_id: str
    process: subprocess.Popen
    port: int
    shared_data: bool
    log_path: Path


class RuntimeSupervisor:
    def __init__(self, root: Path, data_dir: Path, *, startup_timeout: float = 15.0) -> None:
        self.root = Path(root).resolve()
        self.data_dir = Path(data_dir).resolve()
        self.diagnostics = DiagnosticLogger(self.data_dir)
        self.diagnostics.record("INFO", "supervisor.initialized", {"root": str(self.root)})
        self.store = VersionStore(self.root, self.data_dir)
        self.startup_timeout = float(startup_timeout)
        self.slots: dict[str, WorkerHandle] = {}
        self.active_slot = ""
        self.candidate: dict[str, Any] = {"status": "empty"}
        self._lock = threading.RLock()

    @staticmethod
    def _free_port() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            return int(sock.getsockname()[1])

    @staticmethod
    def _health(port: int, *, timeout: float = 1.0) -> dict[str, Any]:
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
        try:
            connection.request("GET", "/api/health")
            response = connection.getresponse()
            raw = response.read(1024 * 1024)
            if response.status != 200:
                raise RuntimeError(f"主体健康检查返回 HTTP {response.status}")
            data = json.loads(raw.decode("utf-8"))
            if not data.get("ok"):
                raise RuntimeError("主体健康检查未就绪")
            return data
        finally:
            connection.close()

    def _log_tail(self, path: Path, limit: int = 2500) -> str:
        try:
            data = path.read_bytes()[-limit:]
            return data.decode("utf-8", errors="replace").strip()
        except OSError:
            return ""

    def _worker_environment(self) -> dict[str, str]:
        environment = os.environ.copy()
        environment["PYTHONUNBUFFERED"] = "1"
        # 版本源码可以独立变化，身份文件与密钥仍由用户的实验根目录统一提供。
        environment["DEEPSEEK_API_KEY_FILE"] = str(self.root / "001.key")
        environment["DEEPSEEK_SYSTEM_PROMPT_FILE"] = str(self.root / "灵魂架构" / "灵魂架构.md")
        return environment

    def _start_worker(self, slot: str, version_id: str, *, shared_data: bool) -> WorkerHandle:
        if slot not in {"A", "B"}:
            raise ValueError("非法 DSA 槽位")
        if slot in self.slots:
            self._stop_worker(slot)
        version_root = self.store.path(version_id)
        source_root = version_root / "source"
        port = self._free_port()
        worker_data = self.data_dir if shared_data else self.store.runtime_dir / "slot_data" / slot
        if not shared_data:
            shutil.rmtree(worker_data, ignore_errors=True)
        worker_data.mkdir(parents=True, exist_ok=True)
        logs_dir = self.store.runtime_dir / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        log_path = logs_dir / f"slot-{slot}.log"
        command = [
            sys.executable,
            "-m",
            "termux_chat.worker",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--data-dir",
            str(worker_data),
            "--profile",
            "profile.json",
        ]
        environment = self._worker_environment()
        environment["PYTHONPATH"] = str(source_root)
        with log_path.open("ab") as log:
            log.write(f"\n--- {version_id} / slot {slot} / shared={shared_data} ---\n".encode("utf-8"))
            log.flush()
            process = subprocess.Popen(
                command,
                cwd=source_root,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
        handle = WorkerHandle(slot, version_id, process, port, shared_data, log_path)
        self.slots[slot] = handle
        deadline = time.monotonic() + self.startup_timeout
        last_error = ""
        try:
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    detail = self._log_tail(log_path)
                    raise RuntimeError(f"主体进程提前退出（{process.returncode}）：{detail}")
                try:
                    self._health(port, timeout=0.5)
                    return handle
                except Exception as exc:
                    last_error = str(exc)
                    time.sleep(0.12)
            raise RuntimeError(f"主体启动超时：{last_error}")
        except Exception:
            self._stop_worker(slot)
            detail = self._log_tail(log_path)
            if detail and detail not in last_error:
                last_error = f"{last_error}\n{detail}"
            raise RuntimeError(f"版本 {version_id} 启动失败：{last_error}") from None

    def _stop_worker(self, slot: str) -> None:
        handle = self.slots.pop(slot, None)
        if not handle:
            return
        if handle.process.poll() is None:
            handle.process.terminate()
            try:
                handle.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                handle.process.kill()
                handle.process.wait(timeout=5)
        else:
            handle.process.wait(timeout=1)

    def start(self) -> None:
        with self._lock:
            if self.active_slot:
                return
            version_id = self.store.ensure_initial_version()
            self._start_worker("A", version_id, shared_data=True)
            self.active_slot = "A"

    def active_handle(self) -> WorkerHandle:
        with self._lock:
            handle = self.slots.get(self.active_slot)
            if not handle:
                raise RuntimeError("当前没有可处理请求的 DSA 主体")
            return handle

    @staticmethod
    def active_requests(handle: WorkerHandle) -> int:
        health = RuntimeSupervisor._health(handle.port)
        return int(health.get("active_requests", 0))

    def prepare_candidate(self) -> dict[str, Any]:
        """冻结实验源码，在备用槽用隔离数据启动；活动主体不受影响。"""
        with self._lock:
            if not self.active_slot:
                raise RuntimeError("稳定宿主尚未启动")
            active = self.active_handle()
            target_slot = "B" if self.active_slot == "A" else "A"
            existing = self.slots.get(target_slot)
            if existing:
                if existing.process.poll() is None and self.active_requests(existing) > 0:
                    raise RuntimeError("备用槽仍有旧请求正在排空，暂时不能替换")
                self._stop_worker(target_slot)
            manifest = self.store.create_version(parent_id=active.version_id)
            version_id = manifest["id"]
            self.candidate = {"status": "checking", "version_id": version_id, "slot": target_slot, "error": ""}
            try:
                self._start_worker(target_slot, version_id, shared_data=False)
            except Exception as exc:
                self.candidate = {
                    "status": "failed",
                    "version_id": version_id,
                    "slot": target_slot,
                    "error": str(exc),
                }
                raise RuntimeError(f"候选版本检查失败；{active.version_id} 仍在运行。{exc}") from exc
            self.candidate = {"status": "ready", "version_id": version_id, "slot": target_slot, "error": ""}
            return self.status()

    def activate(self) -> dict[str, Any]:
        with self._lock:
            if self.candidate.get("status") != "ready":
                raise RuntimeError("没有已通过隔离检查的候选版本")
            active = self.active_handle()
            if active.process.poll() is None and self.active_requests(active) > 0:
                raise RuntimeError("当前主体仍有回复正在生成，请等待请求排空后再激活")
            slot = self.candidate["slot"]
            version_id = self.candidate["version_id"]
            candidate = self.slots.get(slot)
            if not candidate or candidate.version_id != version_id:
                raise RuntimeError("候选主体已不可用，请重新准备")
            # 隔离检查通过后，再用正式数据做一次启动检查；旧主体始终保留。
            self._stop_worker(slot)
            try:
                self._start_worker(slot, version_id, shared_data=True)
            except Exception as exc:
                self.candidate["status"] = "failed"
                self.candidate["error"] = str(exc)
                raise RuntimeError(f"候选版本接入正式数据失败；{active.version_id} 仍在运行。{exc}") from exc
            self.store.set_active(version_id)
            self.active_slot = slot
            self.candidate = {"status": "empty"}
            return self.status()

    def rollback(self) -> dict[str, Any]:
        with self._lock:
            state = self.store.state()
            target_version = state["previous_version_id"]
            if not target_version:
                raise RuntimeError("没有可回滚的历史版本")
            self.store.path(target_version)
            active = self.active_handle()
            if active.process.poll() is None and self.active_requests(active) > 0:
                raise RuntimeError("当前主体仍有回复正在生成，请等待请求排空后再回滚")
            target_slot = "B" if self.active_slot == "A" else "A"
            target = self.slots.get(target_slot)
            if target and target.process.poll() is not None:
                self._stop_worker(target_slot)
                target = None
            if target and self.active_requests(target) > 0:
                raise RuntimeError("回滚目标仍有旧请求正在执行，请稍后再试")
            if target and (target.version_id != target_version or not target.shared_data):
                self._stop_worker(target_slot)
                target = None
            if not target:
                self._start_worker(target_slot, target_version, shared_data=True)
            self.store.set_active(target_version)
            self.active_slot = target_slot
            self.candidate = {"status": "empty"}
            return self.status()

    def status(self) -> dict[str, Any]:
        with self._lock:
            state = self.store.state()
            slots = {}
            for name in ("A", "B"):
                handle = self.slots.get(name)
                if not handle:
                    slots[name] = {"occupied": False}
                    continue
                running = handle.process.poll() is None
                info = {
                    "occupied": True,
                    "version_id": handle.version_id,
                    "running": running,
                    "ready": running,
                    "shared_data": handle.shared_data,
                    "active_requests": 0,
                    "error": "" if running else f"进程已退出：{handle.process.returncode}",
                }
                if running:
                    try:
                        health = self._health(handle.port)
                        info["active_requests"] = int(health.get("active_requests", 0))
                        info["runtime_version"] = health.get("version", "")
                    except Exception as exc:
                        info["ready"] = False
                        info["error"] = str(exc)
                slots[name] = info
            return {
                "ok": True,
                "active_slot": self.active_slot,
                "active_version_id": state["active_version_id"],
                "previous_version_id": state["previous_version_id"],
                "candidate": dict(self.candidate),
                "slots": slots,
                "versions": self.store.list(),
            }

    def shutdown(self) -> None:
        with self._lock:
            self.diagnostics.record("INFO", "supervisor.shutdown", {"active_slot": self.active_slot})
            for slot in tuple(self.slots):
                try:
                    self._stop_worker(slot)
                except Exception:
                    pass
            self.active_slot = ""
