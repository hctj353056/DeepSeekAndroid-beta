"""无数据库依赖的 JSON 会话存储。"""

from __future__ import annotations

import json
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class JsonStore:
    def __init__(self, data_dir: Path) -> None:
        self.directory = Path(data_dir) / "conversations"
        self.directory.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def _path(self, conversation_id: str) -> Path:
        if not re.fullmatch(r"[a-f0-9]{24}", conversation_id):
            raise ValueError("非法会话 ID")
        return self.directory / f"{conversation_id}.json"

    def _write(self, conversation: dict) -> None:
        conversation["updated_at"] = now_iso()
        path = self._path(conversation["id"])
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(conversation, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp, path)

    def create(self, title: str = "新对话") -> dict:
        with self._lock:
            conversation_id = uuid.uuid4().hex[:24]
            timestamp = now_iso()
            conversation = {
                "id": conversation_id,
                "title": title.strip() or "新对话",
                "created_at": timestamp,
                "updated_at": timestamp,
                "messages": [],
            }
            self._write(conversation)
            return conversation

    def list(self) -> list[dict]:
        with self._lock:
            items = []
            for path in self.directory.glob("*.json"):
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    items.append({
                        "id": data["id"],
                        "title": data.get("title", "新对话"),
                        "created_at": data.get("created_at", ""),
                        "updated_at": data.get("updated_at", ""),
                        "message_count": len(data.get("messages", [])),
                    })
                except (ValueError, KeyError, TypeError):
                    continue
            return sorted(items, key=lambda item: item["updated_at"], reverse=True)

    def get(self, conversation_id: str) -> dict:
        with self._lock:
            path = self._path(conversation_id)
            if not path.exists():
                raise KeyError("会话不存在")
            return json.loads(path.read_text(encoding="utf-8"))

    def rename(self, conversation_id: str, title: str) -> dict:
        title = title.strip()
        if not title:
            raise ValueError("标题不能为空")
        with self._lock:
            conversation = self.get(conversation_id)
            conversation["title"] = title[:80]
            self._write(conversation)
            return conversation

    def delete(self, conversation_id: str) -> None:
        with self._lock:
            path = self._path(conversation_id)
            if path.exists():
                path.unlink()

    def append(self, conversation_id: str, message: dict) -> dict:
        with self._lock:
            conversation = self.get(conversation_id)
            message = dict(message)
            message.setdefault("id", uuid.uuid4().hex)
            message.setdefault("created_at", now_iso())
            conversation["messages"].append(message)
            if len([m for m in conversation["messages"] if m.get("role") == "user"]) == 1:
                first_user = next(m for m in conversation["messages"] if m.get("role") == "user")
                conversation["title"] = first_user.get("content", "新对话").strip().splitlines()[0][:28] or "新对话"
            self._write(conversation)
            return message

    def model_messages(self, conversation_id: str, *, include_reasoning: bool) -> list[dict]:
        conversation = self.get(conversation_id)
        messages = []
        for saved in conversation["messages"]:
            role = saved.get("role")
            if role not in {"user", "assistant", "tool"}:
                continue
            item = {"role": role, "content": saved.get("content", "")}
            if role == "assistant" and saved.get("tool_calls"):
                item["tool_calls"] = saved["tool_calls"]
            if role == "assistant" and include_reasoning and saved.get("reasoning"):
                item["reasoning_content"] = saved["reasoning"]
            if role == "tool":
                item["tool_call_id"] = saved["tool_call_id"]
            messages.append(item)
        return messages


def apply(ctx):
    data_dir = ctx.paths.data_dir
    ctx.provide("store", JsonStore(data_dir))
