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

    def save_incident(self, payload: dict[str, Any]) -> dict[str, str]:
        """Persist one user-marked issue with a bounded pre-event timeline."""
        now = datetime.now(timezone.utc)
        issue_id = "DSA-" + now.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
        events = payload.get("events", [])
        if not isinstance(events, list):
            events = []
        events = [_clean(item) for item in events[-300:] if isinstance(item, dict)]
        title = str(payload.get("title", "")).strip()[:160] or "用户标记的问题"
        incident = {
            "id": issue_id,
            "title": title,
            "created_at": self._timestamp(),
            "note": str(payload.get("note", ""))[:2000],
            "app_version": str(payload.get("app_version", "unknown"))[:80],
            "user_agent": str(payload.get("user_agent", ""))[:300],
            "events": events,
        }
        directory = self.directory / "issues"
        directory.mkdir(parents=True, exist_ok=True)
        destination = directory / f"{issue_id}.json"
        temporary = destination.with_suffix(".tmp")
        encoded = json.dumps(_clean(incident), ensure_ascii=False, indent=2)
        with self._lock:
            temporary.write_text(encoded, encoding="utf-8")
            temporary.replace(destination)
            self.record("INFO", "issue.captured", {"issue_id": issue_id, "event_count": len(events)})
        return {"id": issue_id, "title": title}

    def _incident_markdown(self, incident: dict[str, Any]) -> str:
        lines = [
            f"# {incident.get('title', '用户标记的问题')}",
            "",
            f"- 问题编号：{incident.get('id', '')}",
            f"- 记录时间：{incident.get('created_at', '')}",
            f"- DSA 版本：{incident.get('app_version', 'unknown')}",
            f"- 客户端环境：{incident.get('user_agent', 'unknown')}",
            "",
            "## 用户补充",
            "",
            incident.get("note", "") or "用户未补充文字说明。",
            "",
            "## 现场摘要",
            "",
            f"现场事件共 {len(incident.get('events', []))} 条。请结合 timeline.jsonl 按时间顺序分析。",
            "",
            "## 隐私说明",
            "",
            "该报告由本地诊断记录生成；未主动附加聊天正文。分享前请检查内容。",
            "",
        ]
        return "\n".join(str(line) for line in lines)

    def export_zip(self, app_version: str = "unknown", incident_id: str = "") -> bytes:
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
            if incident_id:
                safe_id = re.sub(r"[^A-Za-z0-9-]", "", incident_id)[:64]
                source = self.directory / "issues" / f"{safe_id}.json"
                if not source.is_file():
                    raise ValueError("找不到指定的问题记录")
                incident = json.loads(source.read_text(encoding="utf-8"))
                archive.writestr("issue.json", json.dumps(incident, ensure_ascii=False, indent=2))
                archive.writestr("issue.md", self._incident_markdown(incident))
                timeline = "".join(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"
                                   for event in incident.get("events", []))
                archive.writestr("timeline.jsonl", timeline)
                manifest["issue_id"] = safe_id
                # Include only host records tied to this issue; never bundle unrelated user reports.
                related = []
                for name in ["app.log", *(f"app.log.{i}" for i in range(1, self.backups + 1))]:
                    log_source = self.directory / name
                    if not log_source.is_file():
                        continue
                    for line in log_source.read_text(encoding="utf-8", errors="replace").splitlines():
                        try:
                            entry = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if entry.get("event") == "issue.captured" and entry.get("details", {}).get("issue_id") == safe_id:
                            related.append(line)
                if related:
                    archive.writestr("logs/issue-host.jsonl", "\\n".join(related) + "\\n")
                    manifest["log_files"].append("issue-host.jsonl")
            else:
                for name in ["app.log", *(f"app.log.{i}" for i in range(1, self.backups + 1))]:
                    source = self.directory / name
                    if source.is_file():
                        archive.write(source, f"logs/{name}")
                        manifest["log_files"].append(name)
            archive.writestr("environment.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        return buffer.getvalue()
