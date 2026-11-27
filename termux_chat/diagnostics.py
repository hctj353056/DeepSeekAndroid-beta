"""本地诊断日志：不记录聊天正文或凭据，支持用户现象报告与导出。"""

from __future__ import annotations

import json
import platform
import re
import threading
import uuid
import zipfile
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any


_SECRET_KEY = re.compile(r"(api.?key|authorization|password|passwd|secret|access.?token|refresh.?token|cookie)", re.I)
_SECRET_TEXT = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/-]+=*|\b(sk-[A-Za-z0-9_-]{12,})")
_MAX_TEXT = 3000


def _clean(value: Any, key: str = "", depth: int = 0) -> Any:
    """Bound and redact untrusted diagnostic values before persistence."""
    if _SECRET_KEY.search(key):
        return "[REDACTED]"
    if depth > 5:
        return "[TRUNCATED]"
    if isinstance(value, dict):
        return {str(k)[:80]: _clean(v, str(k), depth + 1) for k, v in list(value.items())[:50]}
    if isinstance(value, (list, tuple)):
        return [_clean(item, "", depth + 1) for item in list(value)[:50]]
    if isinstance(value, str):
        text = value[:_MAX_TEXT]
        return _SECRET_TEXT.sub(lambda match: (match.group(1) if match.group(1) else "") + "[REDACTED]", text)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:500]


class DiagnosticLogger:
    """Thread-safe JSONL logger with bounded rotation and a portable ZIP export."""

    def __init__(self, data_dir: Path, *, max_bytes: int = 512 * 1024, backups: int = 2) -> None:
        self.directory = Path(data_dir) / "diagnostics"
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "app.log"
        self.max_bytes = max(16 * 1024, int(max_bytes))
        self.backups = max(0, min(int(backups), 5))
        self.session_id = uuid.uuid4().hex[:12]
        self._lock = threading.RLock()

    @staticmethod
    def _timestamp() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")

    def record(self, level: str, event: str, details: dict[str, Any] | None = None) -> None:
        entry = {
            "ts": self._timestamp(),
            "session": self.session_id,
            "level": str(level).upper()[:12],
            "event": str(event)[:120],
            "details": _clean(details or {}),
        }
        line = json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n"
        encoded = line.encode("utf-8")
        with self._lock:
            self._rotate_if_needed(len(encoded))
            with self.path.open("ab") as stream:
                stream.write(encoded)

    def _rotate_if_needed(self, incoming: int) -> None:
        if not self.path.exists() or self.path.stat().st_size + incoming <= self.max_bytes:
            return
        if self.backups == 0:
            self.path.unlink(missing_ok=True)
            return
        oldest = self.directory / f"app.log.{self.backups}"
        oldest.unlink(missing_ok=True)
        for index in range(self.backups - 1, 0, -1):
            source = self.directory / f"app.log.{index}"
            if source.exists():
                source.replace(self.directory / f"app.log.{index + 1}")
        self.path.replace(self.directory / "app.log.1")

    def record_action(self, action: str, observed: str, expected: str = "", steps: str = "", note: str = "") -> None:
        self.record("INFO", "user.test_report", {
            "action": action[:500],
            "observed": observed[:1500],
            "expected": expected[:1000],
            "steps": steps[:1500],
            "note": note[:1000],
        })

    def export_zip(self, app_version: str = "unknown") -> bytes:
        """Return a ZIP containing logs and environment metadata, never chat history."""
        buffer = BytesIO()
        manifest = {
            "created_at": self._timestamp(),
            "session_id": self.session_id,
            "app_version": str(app_version)[:80],
            "python": platform.python_version(),
            "platform": platform.platform()[:300],
            "log_files": [],
            "included_data": "diagnostic JSONL only; conversation messages and API credentials are excluded",
        }
        with self._lock, zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name in ["app.log", *(f"app.log.{i}" for i in range(1, self.backups + 1))]:
                source = self.directory / name
                if source.is_file():
                    archive.write(source, f"logs/{name}")
                    manifest["log_files"].append(name)
            archive.writestr("environment.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        return buffer.getvalue()
