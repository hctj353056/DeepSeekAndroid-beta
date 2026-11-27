"""线程安全的本地设置服务。"""

from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path
from urllib.parse import urlparse


DEFAULTS = {
    "provider": "deepseek",
    "base_url": "https://api.deepseek.com",
    "model": "deepseek-flash",
    "thinking": True,
    "reasoning_effort": "high",
    "system_prompt": "You are a helpful assistant.",
    "web_search": False,
    "read_url": False,
    "api_key": "",
}


class Settings:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = Path(data_dir)
        self.file = self.data_dir / "settings.json"
        self._lock = threading.RLock()
        self._values = dict(DEFAULTS)
        self._values["provider"] = os.environ.get("DEEPSEEK_PROVIDER", self._values["provider"])
        self._values["base_url"] = os.environ.get("DEEPSEEK_BASE_URL", self._values["base_url"])
        self._values["model"] = os.environ.get("DEEPSEEK_MODEL", self._values["model"])
        self._load()

    def _load(self) -> None:
        try:
            saved = json.loads(self.file.read_text(encoding="utf-8"))
            if isinstance(saved, dict):
                self._values.update({key: value for key, value in saved.items() if key in DEFAULTS})
        except FileNotFoundError:
            pass
        except (ValueError, TypeError) as exc:
            raise RuntimeError(f"设置文件损坏：{exc}") from exc

    def _save(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        temp = self.file.with_suffix(".tmp")
        temp.write_text(json.dumps(self._values, ensure_ascii=False, indent=2), encoding="utf-8")
        os.chmod(temp, 0o600)
        os.replace(temp, self.file)

    def get(self, key: str):
        with self._lock:
            if key == "api_key" and os.environ.get("DEEPSEEK_API_KEY"):
                return os.environ["DEEPSEEK_API_KEY"]
            return self._values[key]

    def public(self) -> dict:
        with self._lock:
            result = {key: value for key, value in self._values.items() if key != "api_key"}
            result["api_key_set"] = bool(self.get("api_key"))
            result["api_key_from_env"] = bool(os.environ.get("DEEPSEEK_API_KEY"))
            return result

    def update(self, updates: dict) -> dict:
        allowed = set(DEFAULTS) - {"api_key"}
        with self._lock:
            for key in allowed:
                if key not in updates:
                    continue
                value = updates[key]
                if key in {"thinking", "web_search", "read_url"}:
                    value = bool(value)
                elif key == "reasoning_effort":
                    if value not in {"low", "high", "max"}:
                        raise ValueError("reasoning_effort 仅支持 low、high、max")
                elif key == "provider":
                    value = str(value).strip()
                    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", value):
                        raise ValueError("provider 标识只能包含字母、数字、点、下划线与连字符")
                elif key == "base_url":
                    value = str(value).strip().rstrip("/")
                    if urlparse(value).scheme not in {"http", "https"} or not urlparse(value).netloc:
                        raise ValueError("base_url 必须是有效的 HTTP(S) 地址")
                else:
                    value = str(value).strip()
                    if not value and key in {"model"}:
                        raise ValueError(f"{key} 不能为空")
                self._values[key] = value
            if "api_key" in updates:
                if updates["api_key"] is None:
                    self._values["api_key"] = ""
                elif str(updates["api_key"]).strip():
                    self._values["api_key"] = str(updates["api_key"]).strip()
            self._save()
            return self.public()


def apply(ctx):
    ctx.provide("settings", Settings(ctx.paths.data_dir))
