#!/usr/bin/env python3
"""DeepSeek Termux Mobile — 单文件、零第三方依赖的本地聊天客户端。"""
from __future__ import annotations

import argparse
import copy
import json
import os
import re
import signal
import socket
import ssl
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterator

APP_NAME = "DeepSeek Termux Mobile"
VERSION = "0.0.1"
DEFAULT_API_BASE = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-flash"
MODELS = {"deepseek-flash", "deepseek-v4-pro"}
MAX_BODY = 2 * 1024 * 1024
ACTIVE: dict[str, threading.Event] = {}
ACTIVE_LOCK = threading.Lock()


def now() -> float:
    return time.time()


def uid() -> str:
    return uuid.uuid4().hex


def safe_text(value: Any, limit: int = 100_000) -> str:
    return str(value or "")[:limit]


class Store:
    """JSON 原子落盘；数据默认只在 ~/.deepseek-termux。"""

    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)
        try:
            self.data_dir.chmod(0o700)
        except OSError:
            pass
        self.config_path = data_dir / "config.json"
        self.chats_path = data_dir / "chats.json"
        self.lock = threading.RLock()
        self.config = self._read(self.config_path, {})
        raw = self._read(self.chats_path, {"version": 1, "chats": []})
        self.chats = raw.get("chats", []) if isinstance(raw, dict) else []
        recovered = False
        for chat in self.chats:
            for message in chat.get("messages", []):
                if message.get("role") == "assistant" and message.get("status") == "streaming":
                    message["status"] = "stopped"
                    message["error"] = "本地服务重启，本次回复已中断"
                    recovered = True
        if recovered:
            self._write(self.chats_path, {"version": 1, "chats": self.chats})
        self.env_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
        self.env_base = os.environ.get("DEEPSEEK_API_BASE", DEFAULT_API_BASE).strip()
        self.env_model = os.environ.get("DEEPSEEK_MODEL", DEFAULT_MODEL).strip()

    def _read(self, path: Path, fallback: Any) -> Any:
        try:
            with path.open("r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return copy.deepcopy(fallback)

    def _write(self, path: Path, value: Any) -> None:
        tmp = path.with_suffix(path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(value, f, ensure_ascii=False, separators=(",", ":"))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        try:
            path.chmod(0o600)
        except OSError:
            pass

    def save_chats(self) -> None:
        with self.lock:
            self._write(self.chats_path, {"version": 1, "chats": self.chats})

    def api_key(self) -> str:
        return safe_text(self.config.get("api_key"), 512).strip() or self.env_key

    def api_base(self) -> str:
        return safe_text(
            self.config.get("api_base") or self.env_base or DEFAULT_API_BASE, 2048
        ).rstrip("/")

    def model(self) -> str:
        value = safe_text(self.config.get("model") or self.env_model, 100)
        return value if value in MODELS else DEFAULT_MODEL

    def system_prompt(self) -> str:
        return safe_text(
            self.config.get("system_prompt")
            or "你是 DeepSeek，由深度求索公司开发。请清晰、准确、简洁地回答。",
            12_000,
        )

    def public_settings(self) -> dict[str, Any]:
        local_key = bool(safe_text(self.config.get("api_key"), 512).strip())
        return {
            "configured": bool(self.api_key()),
            "key_source": "local" if local_key else ("environment" if self.env_key else "none"),
            "key_hint": ("········" + self.api_key()[-4:]) if self.api_key() else "",
            "model": self.model(),
            "api_base": self.api_base(),
            "system_prompt": self.system_prompt(),
            "app": APP_NAME,
            "version": VERSION,
        }

    def update_settings(self, body: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            if "api_key" in body:
                key = safe_text(body.get("api_key"), 512).strip()
                if key:
                    self.config["api_key"] = key
            if body.get("clear_key"):
                self.config.pop("api_key", None)
            if "model" in body:
                model = safe_text(body.get("model"), 100)
                if model not in MODELS:
                    raise ValueError("不支持的模型")
                self.config["model"] = model
            if "api_base" in body:
                base = safe_text(body.get("api_base"), 2048).strip().rstrip("/")
                parsed = urllib.parse.urlparse(base)
                if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                    raise ValueError("API 地址必须是有效的 http(s) 地址")
                self.config["api_base"] = base or DEFAULT_API_BASE
            if "system_prompt" in body:
                self.config["system_prompt"] = safe_text(body.get("system_prompt"), 12_000)
            self._write(self.config_path, self.config)
            return self.public_settings()

    def summaries(self) -> list[dict[str, Any]]:
        with self.lock:
            rows = [
                {
                    "id": c["id"],
                    "title": c.get("title") or "新对话",
                    "created_at": c.get("created_at", 0),
                    "updated_at": c.get("updated_at", 0),
                    "message_count": len(c.get("messages", [])),
                }
                for c in self.chats
            ]
            return sorted(rows, key=lambda x: x["updated_at"], reverse=True)

    def get_chat(self, chat_id: str) -> dict[str, Any] | None:
        with self.lock:
            chat = self._find(chat_id)
            return copy.deepcopy(chat) if chat else None

    def _find(self, chat_id: str) -> dict[str, Any] | None:
        return next((c for c in self.chats if c.get("id") == chat_id), None)

    def rename(self, chat_id: str, title: str) -> bool:
        with self.lock:
            chat = self._find(chat_id)
            if not chat:
                return False
            chat["title"] = title.strip()[:80] or "新对话"
            chat["title_custom"] = True
            chat["updated_at"] = now()
            self.save_chats()
            return True

    def delete(self, chat_id: str) -> bool:
        self.stop(chat_id)
        with self.lock:
            before = len(self.chats)
            self.chats = [c for c in self.chats if c.get("id") != chat_id]
            if len(self.chats) == before:
                return False
            self.save_chats()
            return True

    def clear(self) -> None:
        for row in self.summaries():
            self.stop(row["id"])
        with self.lock:
            self.chats = []
            self.save_chats()

    def begin_turn(
        self, chat_id: str | None, message: str, regenerate: bool
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], bool]:
        with self.lock:
            chat = self._find(chat_id or "") if chat_id else None
            created = False
            if chat is None:
                chat = {
                    "id": uid(),
                    "title": message.strip().replace("\n", " ")[:28] or "新对话",
                    "title_custom": False,
                    "created_at": now(),
                    "updated_at": now(),
                    "messages": [],
                }
                self.chats.append(chat)
                created = True
            if regenerate:
                msgs = chat.setdefault("messages", [])
                if msgs and msgs[-1].get("role") == "assistant":
                    msgs.pop()
                if not msgs or msgs[-1].get("role") != "user":
                    raise ValueError("找不到可重新生成的上一条提问")
            else:
                chat.setdefault("messages", []).append(
                    {"id": uid(), "role": "user", "content": message, "created_at": now()}
                )
            assistant = {
                "id": uid(),
                "role": "assistant",
                "content": "",
                "reasoning": "",
                "blocks": [],
                "citations": [],
                "tools": [],
                "created_at": now(),
                "status": "streaming",
                "model": self.model(),
            }
            chat["messages"].append(assistant)
            chat["updated_at"] = now()
            self.save_chats()
            return copy.deepcopy(chat), copy.deepcopy(chat["messages"][-2]), copy.deepcopy(assistant), created

    def update_turn(self, chat_id: str, assistant: dict[str, Any], title: str | None = None) -> None:
        with self.lock:
            chat = self._find(chat_id)
            if not chat:
                return
            for i, msg in enumerate(chat.get("messages", [])):
                if msg.get("id") == assistant.get("id"):
                    chat["messages"][i] = copy.deepcopy(assistant)
                    break
            if title and not chat.get("title_custom"):
                chat["title"] = title.strip()[:80] or chat.get("title") or "新对话"
            chat["updated_at"] = now()
            self.save_chats()

    def stop(self, chat_id: str) -> None:
        with ACTIVE_LOCK:
            event = ACTIVE.get(chat_id)
            if event:
                event.set()


STORE: Store


def upstream_messages(chat: dict[str, Any], assistant_id: str) -> list[dict[str, Any]]:
    result = []
    for msg in chat.get("messages", []):
        if msg.get("id") == assistant_id:
            break
        role = msg.get("role")
        if role == "user":
            result.append({"role": "user", "content": safe_text(msg.get("content"), 200_000)})
        elif role == "assistant" and msg.get("status") != "error":
            blocks = msg.get("blocks") or []
            content = copy.deepcopy(blocks) if blocks else safe_text(msg.get("content"), 200_000)
            if content:
                result.append({"role": "assistant", "content": content})
    return result


def iter_sse(response: Any) -> Iterator[dict[str, Any]]:
    event_name = "message"
    data_lines: list[str] = []
    for raw in response:
        line = raw.decode("utf-8", "replace").rstrip("\r\n")
        if not line:
            if data_lines:
                raw_data = "\n".join(data_lines)
                try:
                    payload = json.loads(raw_data)
                except ValueError:
                    payload = {"type": "error", "error": {"message": raw_data}}
                if isinstance(payload, dict):
                    payload["_event"] = event_name
                    yield payload
            event_name, data_lines = "message", []
            continue
        if line.startswith(":"):
            continue
        if line.startswith("event:"):
            event_name = line[6:].strip()
        elif line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
    if data_lines:
        try:
            payload = json.loads("\n".join(data_lines))
            if isinstance(payload, dict):
                payload["_event"] = event_name
                yield payload
        except ValueError:
            return


def api_error(exc: urllib.error.HTTPError) -> str:
    try:
        text = exc.read(65_536).decode("utf-8", "replace")
        body = json.loads(text)
        return safe_text(body.get("error", {}).get("message") or text, 2000)
    except Exception:
        return f"DeepSeek API 返回 HTTP {exc.code}"


def tool_view(block: dict[str, Any], status: str) -> dict[str, Any]:
    view: dict[str, Any] = {
        "id": block.get("id") or block.get("tool_use_id") or uid(),
        "kind": block.get("kind", ""),
        "status": status,
        "type": block.get("type", ""),
        "name": block.get("name", ""),
        "input": block.get("input", {}),
        "sources": [],
    }
    content = block.get("content")
    if block.get("type") == "web_search_tool_result" and isinstance(content, list):
        view["sources"] = [
            {
                "title": safe_text(x.get("title"), 500),
                "url": safe_text(x.get("url"), 2048),
                "snippet": safe_text(x.get("snippet"), 1200),
                "site_name": safe_text(x.get("site_name"), 200),
                "page_age": x.get("page_age"),
            }
            for x in content
            if isinstance(x, dict) and x.get("type") == "web_search_result" and x.get("url")
        ]
    elif block.get("type") == "web_fetch_tool_result" and isinstance(content, dict):
        view["page"] = {
            "url": safe_text(content.get("url"), 2048),
            "title": safe_text(content.get("content", {}).get("title"), 500),
            "published_at": content.get("published_at"),
        }
        if content.get("type", "").endswith("_error"):
            view["error"] = content.get("error_code", "unavailable")
    return view


def run_upstream(
    handler: "Handler", chat: dict[str, Any], assistant: dict[str, Any], thinking: bool,
    search: bool, stop_event: threading.Event,
) -> None:
    chat_id = chat["id"]
    first_turn = len([m for m in chat.get("messages", []) if m.get("role") == "user"]) == 1
    body: dict[str, Any] = {
        "model": STORE.model(),
        "system": STORE.system_prompt(),
        "messages": upstream_messages(chat, assistant["id"]),
        "thinking": {"type": "enabled" if thinking else "disabled"},
        "tools": [{"type": "deepseek_read_url", "name": "deepseek_read_url"}],
        "safety_check": {"level": "high"},
        "stream": True,
    }
    if search:
        body["tools"].append({"type": "deepseek_web_search", "name": "deepseek_web_search"})
    if first_turn:
        body["title"] = {"enabled": True}
    request = urllib.request.Request(
        STORE.api_base() + "/anthropic/v1/messages",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "x-api-key": STORE.api_key(),
            "User-Agent": f"DeepSeek-Termux-Mobile/{VERSION}",
        },
        method="POST",
    )
    blocks: list[dict[str, Any]] = assistant.setdefault("blocks", [])
    tools: list[dict[str, Any]] = assistant.setdefault("tools", [])
    citations: list[dict[str, Any]] = assistant.setdefault("citations", [])
    title = None
    stopped = False
    complete = False
    last_save = 0.0
    response = None
    try:
        response = urllib.request.urlopen(request, timeout=600)
        for event in iter_sse(response):
            if stop_event.is_set():
                stopped = True
                break
            kind = event.get("type") or event.get("_event")
            if kind == "error" or event.get("_event") == "error":
                raise RuntimeError(safe_text(event.get("error", {}).get("message"), 2000) or "上游生成中断")
            if kind == "content_block_start":
                block = copy.deepcopy(event.get("content_block") or {})
                blocks.append(block)
                if block.get("type") in {"server_tool_use", "web_search_tool_result", "web_fetch_tool_result"}:
                    status = "running" if block.get("type") == "server_tool_use" else "done"
                    view = tool_view(block, status)
                    existing = next((t for t in tools if t.get("id") == view.get("id")), None)
                    if existing is not None:
                        view["input"] = existing.get("input", {})
                        view["name"] = existing.get("name", "")
                        view["type"] = existing.get("type", view.get("type", ""))
                        existing.update(view)
                        view = existing
                    else:
                        tools.append(view)
                    handler.send_event("tool", {"tool": view})
            elif kind == "content_block_delta":
                index = event.get("index", -1)
                if not isinstance(index, int) or index < 0 or index >= len(blocks):
                    continue
                block = blocks[index]
                delta = event.get("delta") or {}
                dtype = delta.get("type")
                if dtype == "thinking_delta":
                    text = safe_text(delta.get("thinking"), 100_000)
                    block["thinking"] = safe_text(block.get("thinking"), 1_000_000) + text
                    assistant["reasoning"] = safe_text(assistant.get("reasoning"), 1_000_000) + text
                    handler.send_event("reasoning", {"delta": text})
                elif dtype == "signature_delta":
                    block["signature"] = safe_text(block.get("signature"), 1_000_000) + safe_text(delta.get("signature"), 100_000)
                elif dtype == "text_delta":
                    text = safe_text(delta.get("text"), 100_000)
                    block["text"] = safe_text(block.get("text"), 1_000_000) + text
                    assistant["content"] = safe_text(assistant.get("content"), 1_000_000) + text
                    handler.send_event("content", {"delta": text})
                elif dtype == "citations_delta":
                    citation = copy.deepcopy(delta.get("citation") or {})
                    if citation.get("url"):
                        block.setdefault("citations", []).append(citation)
                        if not any(c.get("url") == citation.get("url") for c in citations):
                            citations.append(citation)
                        handler.send_event("citation", {"citation": citation})
            elif kind == "message_delta":
                delta = event.get("delta") or {}
                title = delta.get("title") or title
                assistant["finish_reason"] = delta.get("stop_reason")
                assistant["usage"] = event.get("usage") or assistant.get("usage")
            elif kind == "message_stop":
                complete = True
            if time.monotonic() - last_save > 1.5:
                STORE.update_turn(chat_id, assistant, title)
                last_save = time.monotonic()
        if not complete and not stopped:
            raise RuntimeError("连接在回复完成前中断，已保留已生成内容")
        assistant["status"] = "stopped" if stopped else "done"
        STORE.update_turn(chat_id, assistant, title)
        handler.send_event("done", {
            "chat_id": chat_id, "status": assistant["status"], "title": title,
            "assistant": assistant, "chats": STORE.summaries(),
        })
    except (BrokenPipeError, ConnectionResetError):
        stop_event.set()
        assistant["status"] = "stopped"
        STORE.update_turn(chat_id, assistant, title)
    except Exception as exc:
        if isinstance(exc, urllib.error.HTTPError):
            message = api_error(exc)
        elif isinstance(exc, urllib.error.URLError):
            message = f"无法连接 DeepSeek API：{exc.reason}"
        elif isinstance(exc, (socket.timeout, TimeoutError)):
            message = "DeepSeek API 响应超时，请稍后重试"
        else:
            message = safe_text(exc, 2000) or exc.__class__.__name__
        assistant["status"] = "error"
        assistant["error"] = message
        STORE.update_turn(chat_id, assistant, title)
        try:
            handler.send_event("error", {"message": message, "assistant": assistant, "chats": STORE.summaries()})
        except (BrokenPipeError, ConnectionResetError):
            pass
    finally:
        if response is not None:
            try:
                response.close()
            except Exception:
                pass
        with ACTIVE_LOCK:
            ACTIVE.pop(chat_id, None)
        handler.finish_stream()


HTML = r'''<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#ffffff">
<title>DeepSeek · Termux</title>
<style>
:root{
  --p-blue-500:oklch(58.5% .235 267);--p-blue-450:oklch(64% .21 267);--p-blue-400:oklch(70% .17 267);
  --p-blue-100:oklch(93.5% .035 267);--p-blue-50:oklch(96.5% .018 267);
  --p-n-0:oklch(100% 0 0);--p-n-50:oklch(98.7% .002 250);--p-n-75:oklch(96.2% .003 250);
  --p-n-100:oklch(94% .004 250);--p-n-200:oklch(90% .005 250);--p-n-400:oklch(72% .008 250);
  --p-n-600:oklch(59% .008 250);--p-n-700:oklch(48% .008 250);--p-n-950:oklch(18% .004 250);
  --p-n-900:oklch(22% .004 250);--p-n-850:oklch(27% .004 250);--p-n-800:oklch(31% .004 250);
  --p-red-600:oklch(55% .22 27);--p-green-600:oklch(57% .13 155);
  --bg:var(--p-n-0);--surface:var(--p-n-50);--raised:var(--p-n-0);--text:var(--p-n-950);
  --secondary:var(--p-n-700);--muted:var(--p-n-600);--caption:var(--p-n-600);
  --line:oklch(0% 0 0 / .09);--line-strong:oklch(0% 0 0 / .14);--hover:oklch(30% .02 260 / .07);
  --brand:var(--p-blue-500);--brand-text:var(--p-blue-500);--bubble:var(--p-blue-50);
  --active-blue:var(--p-blue-100);--code-bg:var(--p-n-50);--shadow:0 8px 32px oklch(35% .03 267 / .07);
  --danger:var(--p-red-600);--on-brand:var(--p-n-0);--s1:4px;--s2:8px;--s3:12px;--s4:16px;--s5:24px;--s6:32px;
  color-scheme:light;
}
:root[data-theme="dark"]{
  --bg:var(--p-n-950);--surface:var(--p-n-900);--raised:var(--p-n-850);--text:var(--p-n-50);
  --secondary:var(--p-n-200);--muted:oklch(75% .006 250);--caption:var(--p-n-600);
  --line:oklch(100% 0 0 / .11);--line-strong:oklch(100% 0 0 / .17);--hover:oklch(100% 0 0 / .08);
  --brand:var(--p-blue-450);--brand-text:var(--p-blue-400);--bubble:oklch(31% .035 267);
  --active-blue:oklch(34% .05 267);--code-bg:var(--p-n-900);--shadow:none;--on-brand:var(--p-n-0);
  color-scheme:dark;
}
*{box-sizing:border-box}html,body{margin:0;width:100%;height:100%;overflow:hidden}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","Microsoft YaHei","Noto Sans CJK SC",sans-serif;background:var(--bg);color:var(--text);font-size:16px;line-height:1.6;-webkit-tap-highlight-color:transparent}
button,input,textarea,select{font:inherit}button{color:inherit}button,a{touch-action:manipulation}button{cursor:pointer}
button:focus-visible,a:focus-visible,input:focus-visible,textarea:focus-visible,select:focus-visible,summary:focus-visible{outline:2px solid var(--brand);outline-offset:2px}
button:disabled{cursor:not-allowed;opacity:.45}a{color:var(--brand-text)}[hidden]{display:none!important}
.svg-icon{width:22px;height:22px;display:block;fill:none;stroke:currentColor;stroke-width:1.8;stroke-linecap:round;stroke-linejoin:round;flex:none}
.icon-btn{width:44px;height:44px;border:0;border-radius:50%;background:transparent;display:inline-flex;align-items:center;justify-content:center;padding:0;transition:background .18s ease-out,transform .12s ease-out}
.icon-btn:hover{background:var(--hover)}.icon-btn:active{transform:scale(.94)}
#app{height:100vh;height:100dvh;min-height:100vh;min-height:100dvh;display:flex;overflow:hidden;background:var(--bg)}
.sidebar{position:fixed;inset:0 auto 0 0;width:min(86vw,320px);z-index:30;display:flex;flex-direction:column;background:var(--surface);transform:translateX(-102%);transition:transform .22s ease-out;border-right:1px solid var(--line);padding-top:env(safe-area-inset-top);padding-bottom:env(safe-area-inset-bottom)}
.sidebar.open{transform:translateX(0)}.backdrop{position:fixed;inset:0;z-index:20;border:0;background:oklch(10% 0 0 / .38);opacity:0;pointer-events:none;transition:opacity .2s ease-out}
.backdrop.show{opacity:1;pointer-events:auto}.side-head{height:64px;display:flex;align-items:center;justify-content:space-between;padding:0 12px 0 20px}
.brand{display:flex;align-items:center;gap:10px;font-weight:650;letter-spacing:-.3px}.brand-mark{width:29px;height:29px;color:var(--brand)}
.new-chat{margin:4px 12px 16px;height:44px;border:1px solid var(--line-strong);border-radius:12px;background:var(--raised);display:flex;align-items:center;justify-content:center;gap:8px;font-weight:550;transition:background .18s,border-color .18s}
.new-chat:hover{background:var(--hover);border-color:var(--brand)}
.side-label{padding:0 20px 8px;color:var(--muted);font-size:12px}.chat-list{flex:1;min-height:0;overflow:auto;padding:0 8px 16px;overscroll-behavior:contain}
.chat-row{display:flex;align-items:center;border-radius:10px;margin:2px 0;position:relative}.chat-row.active{background:var(--active-blue)}
.chat-open{min-width:0;flex:1;height:44px;padding:0 4px 0 12px;border:0;background:transparent;text-align:left;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-size:14px}
.chat-more{width:44px;height:44px;flex:none;border:0;background:transparent;border-radius:50%;display:grid;place-items:center;color:var(--muted)}
.chat-more:hover{background:var(--hover);color:var(--text)}.empty-history{text-align:center;color:var(--muted);font-size:13px;padding:32px 12px}
.side-foot{border-top:1px solid var(--line);padding:8px 12px 12px}.settings-btn{width:100%;height:44px;border:0;background:transparent;border-radius:10px;display:flex;align-items:center;gap:12px;padding:0 10px;text-align:left}.settings-btn:hover{background:var(--hover)}
.unofficial{padding:4px 10px 0;font-size:11px;color:var(--muted)}
.main{position:relative;display:flex;flex-direction:column;flex:1;min-width:0;height:100vh;height:100dvh;background:var(--bg)}
.topbar{height:calc(60px + env(safe-area-inset-top));padding:env(safe-area-inset-top) 8px 0;display:flex;align-items:center;justify-content:space-between;flex:none;background:var(--bg);z-index:5}
.top-title{position:absolute;left:50%;transform:translateX(-50%);max-width:45%;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-size:16px;font-weight:600}
.top-actions{display:flex;align-items:center}.chat-scroll{flex:1;min-height:0;overflow-y:auto;overscroll-behavior-y:contain;scroll-behavior:smooth;scrollbar-width:thin;scrollbar-color:var(--line-strong) transparent}
.message-column{width:min(100% - 32px,760px);margin:0 auto;padding:20px 0 32px}.message{margin-bottom:24px;position:relative}.user-message{display:flex;flex-direction:column;align-items:flex-end;padding-top:8px}
.user-bubble{max-width:calc(100% - 56px);padding:10px 16px;border-radius:22px;background:var(--bubble);white-space:pre-wrap;overflow-wrap:anywhere;font-size:16px;line-height:1.55}
.assistant-message{width:100%}.markdown{font-size:16px;line-height:1.72;overflow-wrap:anywhere}.markdown p{margin:0 0 14px}.markdown p:last-child{margin-bottom:0}
.markdown h1,.markdown h2,.markdown h3{line-height:1.35;margin:24px 0 12px;font-weight:650}.markdown h1{font-size:23px}.markdown h2{font-size:20px}.markdown h3{font-size:18px}
.markdown ul,.markdown ol{padding-left:24px;margin:8px 0 16px}.markdown li{margin:5px 0}.markdown blockquote{border-left:2px solid var(--line-strong);margin:16px 0;padding:2px 0 2px 16px;color:var(--secondary)}
.markdown a{text-decoration:none;border-bottom:1px solid var(--line-strong)}.markdown a:hover{border-color:var(--brand)}
.inline-code{font-family:"Cascadia Code","JetBrains Mono",monospace;font-size:.88em;background:var(--p-n-100);padding:2px 5px;border-radius:5px}
.code-wrap{margin:16px 0;border:1px solid var(--line);border-radius:12px;overflow:hidden;background:var(--code-bg)}.code-head{height:44px;display:flex;align-items:center;justify-content:space-between;padding:0 8px 0 14px;border-bottom:1px solid var(--line);font:12px "Cascadia Code",monospace;color:var(--muted)}
.code-copy{height:44px;min-width:44px;border:0;border-radius:7px;background:transparent;color:var(--muted);display:flex;align-items:center;gap:5px;font:12px inherit}.code-copy:hover{background:var(--hover);color:var(--text)}.code-copy .svg-icon{width:16px;height:16px}
pre{margin:0;padding:16px;overflow:auto;font:13px/1.65 "Cascadia Code","JetBrains Mono","Noto Sans Mono CJK SC",monospace;tab-size:2}code{font-family:inherit}
.table-wrap{overflow-x:auto;margin:16px 0}table{border-collapse:collapse;width:100%;font-size:14px}th,td{border:1px solid var(--line-strong);padding:8px 12px;text-align:left}th{background:var(--surface)}
.message-actions{height:44px;display:flex;align-items:center;gap:4px;margin-top:6px}.action-btn{min-width:44px;height:44px;padding:0 8px;border:0;border-radius:8px;background:transparent;color:var(--muted);display:inline-flex;align-items:center;justify-content:center;gap:5px;font-size:12px}.action-btn:hover{background:var(--hover);color:var(--text)}.action-btn .svg-icon{width:18px;height:18px}
.thought,.tool-trace{margin:0 0 12px;border-radius:10px;color:var(--secondary);font-size:14px}.thought>summary,.tool-trace>summary{list-style:none;cursor:pointer;width:fit-content;min-height:44px;display:flex;align-items:center;gap:7px;padding:4px 10px 4px 0;color:var(--muted);user-select:none}.thought>summary::-webkit-details-marker,.tool-trace>summary::-webkit-details-marker{display:none}
.thought>summary .svg-icon,.tool-trace>summary .svg-icon{width:17px;height:17px}.thought-text{white-space:pre-wrap;overflow-wrap:anywhere;border-left:2px solid var(--line);padding:4px 0 4px 14px;color:var(--muted);max-height:300px;overflow:auto;font-size:14px;line-height:1.65}
.tool-body{padding:4px 0 8px}.source-list{display:flex;flex-direction:column;gap:8px}.source-item{display:flex;gap:9px;text-decoration:none;color:var(--secondary);padding:7px 10px;border-radius:9px;background:var(--surface);border:0;font-size:13px;line-height:1.45}.source-item:hover{background:var(--hover)}.source-num{color:var(--brand-text);font-weight:650;flex:none}.source-title{display:block;color:var(--text);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.source-site{display:block;color:var(--muted);font-size:12px}
.answer-sources{margin-top:16px}.answer-sources>summary{cursor:pointer;color:var(--muted);font-size:13px;min-height:44px;display:flex;align-items:center}.error-box{padding:12px 14px;border-radius:10px;background:oklch(55% .22 27 / .08);color:var(--danger);font-size:14px;white-space:pre-wrap}
.waiting{height:38px;display:flex;align-items:center;gap:6px}.waiting i{display:block;width:6px;height:6px;border-radius:50%;background:var(--caption);animation:pulse 1s infinite}.waiting i:nth-child(2){animation-delay:.12s}.waiting i:nth-child(3){animation-delay:.24s}@keyframes pulse{0%,100%{opacity:.3;transform:translateY(0)}50%{opacity:1;transform:translateY(-2px)}}
.welcome{min-height:100%;display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center;padding:32px 20px 44px}.welcome .brand-mark{width:52px;height:52px;margin-bottom:18px}.welcome h1{font-size:26px;line-height:1.35;letter-spacing:-.5px;margin:0 0 8px;font-weight:600}.welcome p{margin:0;color:var(--muted);font-size:14px}.suggestions{display:flex;flex-wrap:wrap;justify-content:center;gap:8px;margin-top:28px}.suggestion{min-height:44px;padding:8px 14px;border:1px solid var(--line);border-radius:18px;background:var(--raised);font-size:13px;color:var(--secondary);transition:background .18s,border-color .18s}.suggestion:hover{background:var(--hover);border-color:var(--brand)}
.key-banner{margin-top:20px;border:0;background:transparent;color:var(--brand-text);min-height:44px;padding:8px;font-size:13px}.key-banner:hover{text-decoration:underline}
.composer-area{flex:none;padding:8px 12px calc(10px + env(safe-area-inset-bottom));background:var(--bg)}.composer-inner{width:min(100%,792px);margin:0 auto}.composer{border:1px solid var(--line-strong);border-radius:24px;background:var(--raised);box-shadow:var(--shadow);overflow:hidden;transition:border-color .18s}.composer:focus-within{border-color:var(--brand)}
#prompt{display:block;width:100%;min-height:52px;max-height:160px;resize:none;overflow-y:auto;border:0;outline:0;background:transparent;color:var(--text);padding:14px 16px 4px;font-size:16px;line-height:24px}#prompt::placeholder{color:var(--caption)}
.composer-row{display:flex;align-items:center;gap:8px;padding:8px}.mode-btn{height:44px;padding:0 11px;border:1px solid transparent;border-radius:18px;background:transparent;color:var(--secondary);display:flex;align-items:center;gap:6px;font-size:13px;white-space:nowrap;transition:background .18s,border-color .18s}.mode-btn .svg-icon{width:17px;height:17px}.mode-btn:hover{background:var(--hover)}.mode-btn[aria-pressed="true"]{color:var(--brand-text);background:var(--bubble);border-color:var(--active-blue)}
.send-btn{margin-left:auto;width:44px;height:44px;border:0;border-radius:50%;background:var(--brand);color:var(--on-brand);display:grid;place-items:center;padding:0;transition:transform .12s,opacity .18s}.send-btn:active{transform:scale(.93)}.send-btn.stop{background:var(--text);color:var(--bg)}.send-btn .svg-icon{width:19px;height:19px}
.disclaimer{text-align:center;font-size:11px;color:var(--muted);margin:6px 0 0;line-height:1.4}.scroll-latest{position:absolute;right:18px;bottom:150px;z-index:6;width:44px;height:44px;border:1px solid var(--line);border-radius:50%;background:var(--raised);box-shadow:var(--shadow);display:grid;place-items:center}.scroll-latest .svg-icon{width:18px;height:18px}
dialog{border:1px solid var(--line);border-radius:20px;background:var(--raised);color:var(--text);padding:0;width:min(calc(100% - 32px),460px);max-height:min(88dvh,720px);box-shadow:0 24px 80px oklch(5% 0 0 / .28)}dialog::backdrop{background:oklch(8% 0 0 / .48)}.dialog-head{display:flex;align-items:center;justify-content:space-between;padding:14px 12px 10px 20px;border-bottom:1px solid var(--line)}.dialog-head h2{margin:0;font-size:18px}.dialog-body{padding:18px 20px 20px;overflow:auto;max-height:calc(88dvh - 70px)}
.field{margin-bottom:16px}.field label,.field-label{display:block;font-size:13px;font-weight:550;margin-bottom:6px}.field input,.field textarea,.field select{width:100%;min-height:44px;border:1px solid var(--line-strong);border-radius:10px;background:var(--bg);color:var(--text);padding:10px 12px;outline:0}.field textarea{min-height:88px;resize:vertical}.field input:focus,.field textarea:focus,.field select:focus{border-color:var(--brand)}.hint{font-size:12px;color:var(--muted);margin:6px 0 0;line-height:1.55}.form-actions{display:flex;justify-content:flex-end;gap:8px;margin-top:20px}.btn{min-height:44px;border-radius:11px;border:1px solid var(--line-strong);background:var(--raised);padding:9px 16px;font-weight:550}.btn:hover{background:var(--hover)}.btn.primary{background:var(--brand);border-color:var(--brand);color:var(--on-brand)}.btn.danger{color:var(--danger)}.btn.full{width:100%}
.settings-section{border-top:1px solid var(--line);margin-top:20px;padding-top:16px}.settings-section h3{font-size:14px;margin:0 0 12px}.inline-status{font-size:12px;color:var(--muted);margin-left:6px}
.action-sheet{margin:auto auto 12px;width:min(calc(100% - 24px),420px);border-radius:20px}.action-sheet .dialog-body{padding:8px}.sheet-title{font-size:13px;color:var(--muted);padding:8px 12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.sheet-btn{width:100%;min-height:48px;border:0;background:transparent;border-radius:10px;padding:10px 12px;display:flex;align-items:center;gap:12px;text-align:left}.sheet-btn:hover{background:var(--hover)}.sheet-btn.danger{color:var(--danger)}
.toast{position:fixed;left:50%;bottom:calc(128px + env(safe-area-inset-bottom));transform:translate(-50%,12px);z-index:60;max-width:calc(100% - 40px);padding:9px 15px;border-radius:10px;background:var(--p-n-800);color:var(--p-n-0);font-size:13px;opacity:0;pointer-events:none;transition:opacity .18s,transform .18s;box-shadow:var(--shadow);text-align:center}.toast.show{opacity:1;transform:translate(-50%,0)}
@media(min-width:768px){.sidebar{position:relative;transform:none;width:288px;flex:none;z-index:10}.backdrop,.menu-button,#close-sidebar{display:none!important}.topbar{height:60px;padding-top:0}.composer-area{padding:10px 20px 16px}.message-column{padding-top:28px}.welcome h1{font-size:30px}.top-title{max-width:55%}}
@media(max-width:380px){.mode-label{font-size:12px}.mode-btn{padding:0 9px;gap:4px}.message-column{width:calc(100% - 24px)}.composer-area{padding-left:8px;padding-right:8px}}
@media(prefers-reduced-motion:reduce){*,*::before,*::after{animation-duration:.01ms!important;animation-iteration-count:1!important;transition-duration:.01ms!important;scroll-behavior:auto!important}}
</style>
</head>
<body>
<svg width="0" height="0" style="position:absolute" aria-hidden="true"><defs>
<symbol id="i-logo" viewBox="0 0 32 32"><path d="M5 19c3-1 4-5 7-8 3-4 8-5 13-3-1 2-1 4 0 6 1 3 0 7-3 10-4 4-10 4-14 1l-4 2 1-5c-1-1-1-2 0-3Z" fill="currentColor" stroke="none"/><path d="M13 19c3 2 7 1 9-2-1 5-7 7-11 4" fill="none" stroke="white" stroke-width="1.4"/><circle cx="20" cy="12" r="1" fill="white" stroke="none"/></symbol>
<symbol id="i-menu" viewBox="0 0 24 24"><path d="M4 7h16M4 12h16M4 17h16"/></symbol>
<symbol id="i-plus" viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"/></symbol>
<symbol id="i-close" viewBox="0 0 24 24"><path d="m6 6 12 12M18 6 6 18"/></symbol>
<symbol id="i-settings" viewBox="0 0 24 24"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .34 1.88l.06.06-2.83 2.83-.06-.06a1.7 1.7 0 0 0-1.88-.34 1.7 1.7 0 0 0-1.03 1.56V21h-4v-.09A1.7 1.7 0 0 0 9 19.36a1.7 1.7 0 0 0-1.88.34l-.06.06-2.83-2.83.06-.06A1.7 1.7 0 0 0 4.63 15a1.7 1.7 0 0 0-1.55-1.03H3v-4h.09A1.7 1.7 0 0 0 4.64 9a1.7 1.7 0 0 0-.34-1.88l-.06-.06 2.83-2.83.06.06A1.7 1.7 0 0 0 9 4.63a1.7 1.7 0 0 0 1.03-1.55V3h4v.09A1.7 1.7 0 0 0 15 4.64a1.7 1.7 0 0 0 1.88-.34l.06-.06 2.83 2.83-.06.06A1.7 1.7 0 0 0 19.37 9a1.7 1.7 0 0 0 1.55 1.03H21v4h-.09A1.7 1.7 0 0 0 19.4 15Z"/></symbol>
<symbol id="i-more" viewBox="0 0 24 24"><circle cx="5" cy="12" r="1" fill="currentColor" stroke="none"/><circle cx="12" cy="12" r="1" fill="currentColor" stroke="none"/><circle cx="19" cy="12" r="1" fill="currentColor" stroke="none"/></symbol>
<symbol id="i-copy" viewBox="0 0 24 24"><rect x="8" y="8" width="11" height="11" rx="2"/><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2"/></symbol>
<symbol id="i-refresh" viewBox="0 0 24 24"><path d="M20 7v5h-5M4 17v-5h5"/><path d="M6.1 8a7 7 0 0 1 11.6-1L20 12M4 12l2.3 5a7 7 0 0 0 11.6-1"/></symbol>
<symbol id="i-think" viewBox="0 0 24 24"><path d="M9 18h6M10 21h4M8.2 14.5a6 6 0 1 1 7.6 0c-.9.7-1.3 1.4-1.3 2.5h-5c0-1.1-.4-1.8-1.3-2.5Z"/></symbol>
<symbol id="i-globe" viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/></symbol>
<symbol id="i-send" viewBox="0 0 24 24"><path d="M12 19V5M6 11l6-6 6 6"/></symbol>
<symbol id="i-stop" viewBox="0 0 24 24"><rect x="7" y="7" width="10" height="10" rx="1" fill="currentColor" stroke="none"/></symbol>
<symbol id="i-down" viewBox="0 0 24 24"><path d="M12 5v14M6 13l6 6 6-6"/></symbol>
<symbol id="i-edit" viewBox="0 0 24 24"><path d="m14 5 5 5M4 20l3.5-.7L20 6.8a1.4 1.4 0 0 0 0-2L19.2 4a1.4 1.4 0 0 0-2 0L4.7 16.5 4 20Z"/></symbol>
<symbol id="i-trash" viewBox="0 0 24 24"><path d="M4 7h16M9 7V4h6v3M6 7l1 14h10l1-14M10 11v6M14 11v6"/></symbol>
<symbol id="i-chevron" viewBox="0 0 24 24"><path d="m9 6 6 6-6 6"/></symbol>
</defs></svg>
<div id="app">
<aside class="sidebar" id="sidebar" aria-label="会话侧栏">
  <div class="side-head"><div class="brand"><svg class="brand-mark"><use href="#i-logo"/></svg><span>DeepSeek</span></div><button class="icon-btn" id="close-sidebar" aria-label="关闭侧栏"><svg class="svg-icon"><use href="#i-close"/></svg></button></div>
  <button class="new-chat" id="new-chat-side"><svg class="svg-icon"><use href="#i-plus"/></svg>开启新对话</button>
  <div class="side-label">最近对话</div><div class="chat-list" id="chat-list"></div>
  <div class="side-foot"><button class="settings-btn" id="open-settings"><svg class="svg-icon"><use href="#i-settings"/></svg>设置<span class="inline-status" id="key-status"></span></button><div class="unofficial">非官方 Termux 本地客户端</div></div>
</aside>
<button class="backdrop" id="backdrop" aria-label="关闭侧栏"></button>
<main class="main">
<header class="topbar"><button class="icon-btn menu-button" id="open-sidebar" aria-label="打开会话侧栏"><svg class="svg-icon"><use href="#i-menu"/></svg></button><div class="top-title" id="top-title">DeepSeek</div><div class="top-actions"><button class="icon-btn" id="new-chat-top" aria-label="新对话"><svg class="svg-icon"><use href="#i-plus"/></svg></button></div></header>
<section class="chat-scroll" id="chat-scroll"><div class="message-column" id="messages" hidden></div><div class="welcome" id="welcome"><svg class="brand-mark"><use href="#i-logo"/></svg><h1>有什么可以帮你？</h1><p>在手机上，与 DeepSeek 开始一段对话</p><div class="suggestions"><button class="suggestion" data-prompt="用通俗的例子解释量子纠缠">解释一个概念</button><button class="suggestion" data-prompt="帮我写一个 Python 小工具，并解释关键代码">写一个 Python 工具</button><button class="suggestion" data-prompt="为我制定一个可执行的 30 天学习计划">制定学习计划</button></div><button class="key-banner" id="key-banner" hidden>尚未配置 API Key，点击设置</button></div></section>
<button class="scroll-latest" id="scroll-latest" aria-label="回到最新回复" hidden><svg class="svg-icon"><use href="#i-down"/></svg></button>
<footer class="composer-area"><div class="composer-inner"><div class="composer" id="composer"><textarea id="prompt" rows="1" maxlength="32000" placeholder="给 DeepSeek 发送消息" aria-label="消息内容"></textarea><div class="composer-row"><button class="mode-btn" id="thinking-btn" aria-pressed="false"><svg class="svg-icon"><use href="#i-think"/></svg><span class="mode-label">深度思考</span></button><button class="mode-btn" id="search-btn" aria-pressed="false"><svg class="svg-icon"><use href="#i-globe"/></svg><span class="mode-label">联网搜索</span></button><button class="send-btn" id="send-btn" aria-label="发送消息" disabled><svg class="svg-icon"><use href="#i-send"/></svg></button></div></div><p class="disclaimer">内容由 AI 生成，请仔细甄别</p></div></footer>
</main></div>
<dialog id="settings-dialog"><div class="dialog-head"><h2>设置</h2><button class="icon-btn" data-close="settings-dialog" aria-label="关闭设置"><svg class="svg-icon"><use href="#i-close"/></svg></button></div><div class="dialog-body"><form id="settings-form"><div class="field"><label for="api-key">DeepSeek API Key</label><input id="api-key" type="password" autocomplete="off" placeholder="留空则保留当前 Key"><p class="hint" id="api-key-hint">Key 仅保存在本机，不会显示在聊天页面中。</p></div><div class="field"><label for="model">模型</label><select id="model"><option value="deepseek-flash">deepseek-flash</option><option value="deepseek-v4-pro">deepseek-v4-pro</option></select></div><div class="field"><label for="theme">外观</label><select id="theme"><option value="auto">跟随系统</option><option value="light">浅色</option><option value="dark">深色</option></select></div><div class="field"><label for="api-base">API 地址</label><input id="api-base" type="url" inputmode="url" required><p class="hint">通常保持默认；末尾无需添加接口路径。</p></div><div class="field"><label for="system-prompt">系统提示词</label><textarea id="system-prompt" maxlength="12000"></textarea></div><div class="form-actions"><button class="btn danger" type="button" id="clear-key" hidden>清除 Key</button><button class="btn primary" type="submit">保存设置</button></div></form><div class="settings-section"><h3>本地数据</h3><button class="btn danger full" id="clear-chats">删除全部对话</button><p class="hint">对话保存在运行此程序的 Termux 用户目录中。删除后无法恢复。</p></div><div class="settings-section"><h3>关于</h3><p class="hint" id="version-info"></p></div></div></dialog>
<dialog class="action-sheet" id="chat-actions"><div class="dialog-body"><div class="sheet-title" id="sheet-title"></div><button class="sheet-btn" id="rename-chat"><svg class="svg-icon"><use href="#i-edit"/></svg>重命名</button><button class="sheet-btn danger" id="delete-chat"><svg class="svg-icon"><use href="#i-trash"/></svg>删除对话</button><button class="sheet-btn" data-close="chat-actions">取消</button></div></dialog>
<dialog id="rename-dialog"><div class="dialog-head"><h2>重命名对话</h2><button class="icon-btn" data-close="rename-dialog" aria-label="关闭"><svg class="svg-icon"><use href="#i-close"/></svg></button></div><div class="dialog-body"><form id="rename-form"><div class="field"><label for="rename-input">对话名称</label><input id="rename-input" maxlength="80" required></div><div class="form-actions"><button class="btn" type="button" data-close="rename-dialog">取消</button><button class="btn primary" type="submit">保存</button></div></form></div></dialog>
<dialog id="confirm-dialog"><div class="dialog-head"><h2 id="confirm-title">确认删除？</h2></div><div class="dialog-body"><p id="confirm-text" style="margin:0;color:var(--secondary)"></p><div class="form-actions"><button class="btn" data-close="confirm-dialog">取消</button><button class="btn danger" id="confirm-yes">确认删除</button></div></div></dialog>
<div class="toast" id="toast" role="status" aria-live="polite"></div>
<script>
'use strict';
const $=s=>document.querySelector(s), $$=s=>[...document.querySelectorAll(s)];
const icon=n=>`<svg class="svg-icon" aria-hidden="true"><use href="#i-${n}"/></svg>`;
const state={settings:null,chats:[],active:null,generating:false,assistantId:null,selectedChat:null,confirmAction:null,thinking:localStorage.getItem('ds-thinking')==='true',search:localStorage.getItem('ds-search')==='true'};
let toastTimer,renderTimer,shouldStick=true;
function esc(s){return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
function safeUrl(s){try{const u=new URL(String(s));return ['http:','https:'].includes(u.protocol)?u.href:''}catch{return ''}}
function toast(text){const el=$('#toast');el.textContent=text;el.classList.add('show');clearTimeout(toastTimer);toastTimer=setTimeout(()=>el.classList.remove('show'),2600)}
async function api(url,options={}){const r=await fetch(url,{headers:{'Content-Type':'application/json',...(options.headers||{})},...options});let data={};try{data=await r.json()}catch{}if(!r.ok)throw new Error(data.error||`请求失败 (${r.status})`);return data}
function applyTheme(value){const theme=value||localStorage.getItem('ds-theme')||'auto';const dark=theme==='dark'||(theme==='auto'&&matchMedia('(prefers-color-scheme: dark)').matches);document.documentElement.dataset.theme=dark?'dark':'light';$('#theme').value=theme;$('meta[name="theme-color"]').content=dark?'#151517':'#ffffff'}
function setSidebar(open){$('#sidebar').classList.toggle('open',open);$('#backdrop').classList.toggle('show',open);$('#open-sidebar').setAttribute('aria-expanded',String(open))}
function busyGuard(){if(state.generating){toast('请先停止当前回复，再切换对话');return true}return false}
function nearBottom(){const el=$('#chat-scroll');return el.scrollHeight-el.scrollTop-el.clientHeight<140}
function scrollBottom(force=false){if(!force&&!shouldStick)return;requestAnimationFrame(()=>{const el=$('#chat-scroll');el.scrollTop=el.scrollHeight;$('#scroll-latest').hidden=true})}
function sourceHtml(s,i){const url=safeUrl(s.url);if(!url)return '';return `<a class="source-item" href="${esc(url)}" target="_blank" rel="noopener noreferrer"><span class="source-num">${i+1}</span><span><span class="source-title">${esc(s.title||s.site_name||url)}</span><span class="source-site">${esc(s.site_name||new URL(url).hostname)}</span></span></a>`}
function inlineMd(raw){const held=[];const hold=html=>`\u0001${held.push(html)-1}\u0002`;let s=String(raw||'');s=s.replace(/`([^`\n]+)`/g,(_,c)=>hold(`<code class="inline-code">${esc(c)}</code>`));s=s.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,(_,label,url)=>{const u=safeUrl(url);return u?hold(`<a href="${esc(u)}" target="_blank" rel="noopener noreferrer">${esc(label)}</a>`):_});s=esc(s);s=s.replace(/\*\*([^*\n]+)\*\*/g,'<strong>$1</strong>').replace(/~~([^~\n]+)~~/g,'<del>$1</del>');return s.replace(/\u0001(\d+)\u0002/g,(_,i)=>held[+i])}
function renderMarkdown(text){
  const lines=String(text||'').replace(/\r/g,'').split('\n'),out=[];let i=0;
  while(i<lines.length){
    const line=lines[i];
    if(!line.trim()){i++;continue}
    const fence=line.match(/^\s*```([^\s`]*)\s*$/);
    if(fence){const code=[];i++;while(i<lines.length&&!/^\s*```\s*$/.test(lines[i]))code.push(lines[i++]);if(i<lines.length)i++;out.push(`<div class="code-wrap"><div class="code-head"><span>${esc(fence[1]||'code')}</span><button class="code-copy" data-action="copy-code" aria-label="复制代码">${icon('copy')}复制</button></div><pre><code>${esc(code.join('\n'))}</code></pre></div>`);continue}
    if(i+1<lines.length&&line.includes('|')&&/^\s*\|?\s*:?-{3,}/.test(lines[i+1])){const rows=[];const split=x=>x.trim().replace(/^\||\|$/g,'').split('|').map(c=>c.trim());const heads=split(line);i+=2;while(i<lines.length&&lines[i].includes('|')&&lines[i].trim()){rows.push(split(lines[i++]));}out.push(`<div class="table-wrap"><table><thead><tr>${heads.map(h=>`<th>${inlineMd(h)}</th>`).join('')}</tr></thead><tbody>${rows.map(r=>`<tr>${heads.map((_,j)=>`<td>${inlineMd(r[j]||'')}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`);continue}
    const head=line.match(/^(#{1,3})\s+(.+)$/);if(head){const n=head[1].length;out.push(`<h${n}>${inlineMd(head[2])}</h${n}>`);i++;continue}
    if(/^\s*>\s?/.test(line)){const q=[];while(i<lines.length&&/^\s*>\s?/.test(lines[i]))q.push(lines[i++].replace(/^\s*>\s?/,''));out.push(`<blockquote>${inlineMd(q.join('\n')).replace(/\n/g,'<br>')}</blockquote>`);continue}
    if(/^\s*[-*+]\s+/.test(line)){const li=[];while(i<lines.length&&/^\s*[-*+]\s+/.test(lines[i]))li.push(lines[i++].replace(/^\s*[-*+]\s+/,''));out.push(`<ul>${li.map(x=>`<li>${inlineMd(x)}</li>`).join('')}</ul>`);continue}
    if(/^\s*\d+[.)]\s+/.test(line)){const li=[];while(i<lines.length&&/^\s*\d+[.)]\s+/.test(lines[i]))li.push(lines[i++].replace(/^\s*\d+[.)]\s+/,''));out.push(`<ol>${li.map(x=>`<li>${inlineMd(x)}</li>`).join('')}</ol>`);continue}
    const p=[line];i++;while(i<lines.length&&lines[i].trim()&&!/^\s*```|^\s*#{1,3}\s|^\s*[-*+]\s+|^\s*\d+[.)]\s+|^\s*>\s?/.test(lines[i]))p.push(lines[i++]);out.push(`<p>${inlineMd(p.join('\n')).replace(/\n/g,'<br>')}</p>`);
  }
  return out.join('')
}
function toolHtml(t){const input=t.input||{},action=input.action||'';let label='正在处理';if(action==='search')label=`搜索 ${Array.isArray(input.queries)?input.queries.join('、'):''}`;else if(action==='open_page')label='打开网页';else if(action==='find_in_page')label='在网页中查找';else if(action==='read_link')label='阅读链接';else if(t.type==='web_search_tool_result')label='搜索完成';const sources=(t.sources||[]).map(sourceHtml).join('');const page=t.page&&safeUrl(t.page.url)?sourceHtml({...t.page,site_name:new URL(safeUrl(t.page.url)).hostname},0):'';return `<details class="tool-trace"><summary>${icon(t.name==='deepseek_read_url'?'globe':'globe')}<span>${esc(label)}${t.status==='running'?'…':''}</span></summary><div class="tool-body"><div class="source-list">${sources||page||'<span class="hint">等待结果…</span>'}</div></div></details>`}
function assistantHtml(m,canRegenerate=true){
  const streaming=m.status==='streaming',reason=String(m.reasoning||''),content=String(m.content||'');
  const thought=reason||streaming?`<details class="thought" ${streaming&&reason?'open':''}><summary>${icon('think')}<span>${streaming?'正在深度思考':'已深度思考'}</span></summary><div class="thought-text">${esc(reason||'等待模型输出…')}</div></details>`:'';
  const tools=(m.tools||[]).map(toolHtml).join('');
  const waiting=streaming&&!content?'<div class="waiting" aria-label="正在生成"><i></i><i></i><i></i></div>':'';
  const cites=(m.citations||[]).filter(c=>safeUrl(c.url));const sources=cites.length?`<details class="answer-sources"><summary>参考来源 · ${cites.length}</summary><div class="source-list">${cites.map(sourceHtml).join('')}</div></details>`:'';
  const error=m.error?`<div class="error-box">${esc(m.error)}</div>`:'';
  return `${thought}${tools}<div class="markdown">${renderMarkdown(content)}</div>${waiting}${error}${sources}<div class="message-actions"><button class="action-btn" data-action="copy" aria-label="复制回复">${icon('copy')}</button>${!streaming&&canRegenerate?`<button class="action-btn" data-action="regenerate" aria-label="重新生成">${icon('refresh')}</button>`:''}</div>`;
}
function messageHtml(m,canRegenerate=true){if(m.role==='user')return `<article class="message user-message" data-id="${esc(m.id)}"><div class="user-bubble">${esc(m.content)}</div><div class="message-actions"><button class="action-btn" data-action="copy" aria-label="复制提问">${icon('copy')}</button></div></article>`;return `<article class="message assistant-message" data-id="${esc(m.id)}">${assistantHtml(m,canRegenerate)}</article>`}
function renderActive(scroll=false){const active=state.active,has=active&&active.messages&&active.messages.length;$('#welcome').hidden=!!has;$('#messages').hidden=!has;$('#top-title').textContent=active?active.title||'新对话':'DeepSeek';if(has)$('#messages').innerHTML=active.messages.map((m,i)=>messageHtml(m,i===active.messages.length-1)).join('');else $('#messages').innerHTML='';renderChatList();updateSendState();if(scroll)scrollBottom(true)}
function renderChatList(){const activeId=state.active&&state.active.id;$('#chat-list').innerHTML=state.chats.length?state.chats.map(c=>`<div class="chat-row ${c.id===activeId?'active':''}"><button class="chat-open" data-chat="${esc(c.id)}" title="${esc(c.title)}">${esc(c.title)}</button><button class="chat-more" data-more="${esc(c.id)}" aria-label="管理 ${esc(c.title)}">${icon('more')}</button></div>`).join(''):'<div class="empty-history">暂无历史对话</div>'}
function queueAssistantRender(){if(renderTimer)return;renderTimer=setTimeout(()=>{renderTimer=null;const m=state.active&&state.active.messages.find(x=>x.id===state.assistantId);if(!m)return;const old=document.querySelector(`.assistant-message[data-id="${m.id}"]`);if(!old){renderActive();return}const open=[...old.querySelectorAll('details')].map(x=>x.open);old.outerHTML=messageHtml(m);const fresh=document.querySelector(`.assistant-message[data-id="${m.id}"]`);[...fresh.querySelectorAll('details')].forEach((x,i)=>{if(i<open.length)x.open=open[i]});scrollBottom()},45)}
function updateSendState(){const btn=$('#send-btn');btn.classList.toggle('stop',state.generating);btn.disabled=state.generating?false:!$('#prompt').value.trim();btn.setAttribute('aria-label',state.generating?'停止生成':'发送消息');btn.innerHTML=icon(state.generating?'stop':'send');$('#thinking-btn').disabled=state.generating;$('#search-btn').disabled=state.generating;$('#thinking-btn').setAttribute('aria-pressed',String(state.thinking));$('#search-btn').setAttribute('aria-pressed',String(state.search))}
async function loadChats(){const data=await api('/api/chats');state.chats=data.chats;renderChatList()}
async function openChat(id){if(busyGuard())return;try{const data=await api('/api/chats/'+encodeURIComponent(id));state.active=data.chat;setSidebar(false);renderActive(true)}catch(e){toast(e.message)}}
function newChat(){if(busyGuard())return;state.active=null;state.assistantId=null;setSidebar(false);renderActive();$('#prompt').focus()}
function localMessage(role,content,status){return{id:'local-'+crypto.randomUUID(),role,content,status:status||'done',created_at:Date.now()/1000,reasoning:'',blocks:[],citations:[],tools:[]}}
async function sendMessage(regenerate=false){
  if(state.generating)return;const text=$('#prompt').value.trim();if(!regenerate&&!text)return;
  if(!state.settings||!state.settings.configured){openSettings();toast('请先配置 DeepSeek API Key');return}
  if(regenerate&&(!state.active||!state.active.id))return;
  if(regenerate&&state.active.messages.at(-1)?.role==='assistant')state.active.messages.pop();
  if(!regenerate){if(!state.active)state.active={id:null,title:'新对话',messages:[]};state.active.messages.push(localMessage('user',text));$('#prompt').value='';autoResize()}
  const assistant=localMessage('assistant','', 'streaming');state.active.messages.push(assistant);state.assistantId=assistant.id;state.generating=true;shouldStick=true;renderActive(true);
  try{
    const response=await fetch('/api/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({chat_id:state.active.id,message:text,thinking:state.thinking,search:state.search,regenerate})});
    if(!response.ok){let d={};try{d=await response.json()}catch{}throw new Error(d.error||`请求失败 (${response.status})`)}
    if(!response.body)throw new Error('浏览器不支持流式响应');const reader=response.body.getReader(),decoder=new TextDecoder();let buffer='';
    while(true){const{done,value}=await reader.read();if(done)break;buffer+=decoder.decode(value,{stream:true});buffer=buffer.replace(/\r\n/g,'\n');let cut;while((cut=buffer.indexOf('\n\n'))>=0){const raw=buffer.slice(0,cut);buffer=buffer.slice(cut+2);const data=raw.split('\n').filter(x=>x.startsWith('data:')).map(x=>x.slice(5).trim()).join('\n');if(data)handleStreamEvent(JSON.parse(data))}}
  }catch(e){assistant.status='error';assistant.error=e.message||'连接失败';state.generating=false;renderActive();try{await loadChats()}catch{}}
  finally{if(state.generating){state.generating=false;renderActive()}updateSendState();$('#prompt').focus()}
}
function handleStreamEvent(event){
  const m=state.active&&state.active.messages.find(x=>x.id===state.assistantId);if(event.type==='start'){
    if(!state.active)return;state.active.id=event.chat_id;state.active.title=event.title||state.active.title;const users=state.active.messages.filter(x=>x.role==='user');if(users.length)users[users.length-1].id=event.user_id;if(m)m.id=event.assistant_id;state.assistantId=event.assistant_id;renderActive(true);return;
  }
  if(!m)return;
  if(event.type==='content'){m.content+=event.delta;queueAssistantRender()}
  else if(event.type==='reasoning'){m.reasoning+=event.delta;queueAssistantRender()}
  else if(event.type==='tool'){const i=m.tools.findIndex(x=>x.id===event.tool.id);if(i>=0)m.tools[i]=event.tool;else m.tools.push(event.tool);queueAssistantRender()}
  else if(event.type==='citation'){if(!m.citations.some(x=>x.url===event.citation.url))m.citations.push(event.citation);queueAssistantRender()}
  else if(event.type==='done'||event.type==='error'){
    const incoming=event.assistant;if(incoming){const idx=state.active.messages.findIndex(x=>x.id===m.id||x.id===incoming.id);if(idx>=0)state.active.messages[idx]=incoming}
    if(event.title)state.active.title=event.title;if(event.chats)state.chats=event.chats;state.generating=false;renderActive();if(event.type==='error')toast('回复未完成，已保留已生成内容');
  }
}
async function stopGeneration(){if(!state.generating||!state.active||!state.active.id)return;try{await api('/api/stop',{method:'POST',body:JSON.stringify({chat_id:state.active.id})});toast('正在停止…')}catch(e){toast(e.message)}}
async function copyText(text){try{await navigator.clipboard.writeText(text);toast('已复制')}catch{const t=document.createElement('textarea');t.value=text;t.style.position='fixed';t.style.opacity='0';document.body.appendChild(t);t.select();document.execCommand('copy');t.remove();toast('已复制')}}
function autoResize(){const el=$('#prompt');el.style.height='auto';el.style.height=Math.min(el.scrollHeight,160)+'px';updateSendState()}
function openSettings(){const s=state.settings;$('#api-key').value='';$('#clear-key').hidden=!s||s.key_source!=='local';if(s){$('#model').value=s.model;$('#api-base').value=s.api_base;$('#system-prompt').value=s.system_prompt;$('#api-key-hint').textContent=s.configured?`当前 ${s.key_hint}（${s.key_source==='environment'?'来自环境变量':'已保存在本机'}）；留空表示不修改。`:'尚未配置。也可通过 DEEPSEEK_API_KEY 环境变量提供。';$('#version-info').textContent=`${s.app} v${s.version} · 单文件 Python 标准库实现 · 非官方客户端`}$('#settings-dialog').showModal()}
function confirmAction(title,text,fn){state.confirmAction=fn;$('#confirm-title').textContent=title;$('#confirm-text').textContent=text;$('#confirm-dialog').showModal()}
function bind(){
  $('#open-sidebar').onclick=()=>setSidebar(true);$('#close-sidebar').onclick=()=>setSidebar(false);$('#backdrop').onclick=()=>setSidebar(false);$('#new-chat-top').onclick=newChat;$('#new-chat-side').onclick=newChat;
  $('#open-settings').onclick=()=>{setSidebar(false);openSettings()};$('#key-banner').onclick=openSettings;
  $('#prompt').addEventListener('input',autoResize);$('#prompt').addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey&&matchMedia('(pointer:fine)').matches&&!e.isComposing){e.preventDefault();sendMessage()}});
  $('#send-btn').onclick=()=>state.generating?stopGeneration():sendMessage();$('#thinking-btn').onclick=()=>{state.thinking=!state.thinking;localStorage.setItem('ds-thinking',state.thinking);updateSendState()};$('#search-btn').onclick=()=>{state.search=!state.search;localStorage.setItem('ds-search',state.search);updateSendState()};
  $$('.suggestion').forEach(b=>b.onclick=()=>{$('#prompt').value=b.dataset.prompt;autoResize();$('#prompt').focus()});
  $('#chat-list').onclick=e=>{const open=e.target.closest('[data-chat]'),more=e.target.closest('[data-more]');if(open)openChat(open.dataset.chat);if(more){if(busyGuard())return;state.selectedChat=state.chats.find(c=>c.id===more.dataset.more);$('#sheet-title').textContent=state.selectedChat.title;$('#chat-actions').showModal()}};
  $('#rename-chat').onclick=()=>{if(!state.selectedChat)return;$('#chat-actions').close();$('#rename-input').value=state.selectedChat.title;$('#rename-dialog').showModal();$('#rename-input').focus()};
  $('#delete-chat').onclick=()=>{if(!state.selectedChat)return;$('#chat-actions').close();const c=state.selectedChat;confirmAction('删除这段对话？',`“${c.title}”将从本机永久删除。`,async()=>{await api('/api/chats/'+c.id,{method:'DELETE'});if(state.active&&state.active.id===c.id)state.active=null;await loadChats();renderActive();toast('对话已删除')})};
  $('#rename-form').onsubmit=async e=>{e.preventDefault();try{await api('/api/chats/'+state.selectedChat.id,{method:'PATCH',body:JSON.stringify({title:$('#rename-input').value})});if(state.active&&state.active.id===state.selectedChat.id)state.active.title=$('#rename-input').value;$('#rename-dialog').close();await loadChats();renderActive();toast('已重命名')}catch(err){toast(err.message)}};
  $('#confirm-yes').onclick=async()=>{const fn=state.confirmAction;$('#confirm-dialog').close();if(fn)try{await fn()}catch(e){toast(e.message)}};
  $$('[data-close]').forEach(b=>b.onclick=()=>$('#'+b.dataset.close).close());$$('dialog').forEach(d=>d.addEventListener('click',e=>{if(e.target===d)d.close()}));
  $('#theme').onchange=()=>{localStorage.setItem('ds-theme',$('#theme').value);applyTheme($('#theme').value)};
  $('#settings-form').onsubmit=async e=>{e.preventDefault();const btn=e.submitter;btn.disabled=true;try{const data=await api('/api/settings',{method:'POST',body:JSON.stringify({api_key:$('#api-key').value,model:$('#model').value,api_base:$('#api-base').value,system_prompt:$('#system-prompt').value})});state.settings=data;$('#api-key').value='';$('#settings-dialog').close();$('#key-banner').hidden=data.configured;$('#key-status').textContent=data.configured?'':'未配置 Key';toast('设置已保存')}catch(err){toast(err.message)}finally{btn.disabled=false}};
  $('#clear-key').onclick=async()=>{try{const data=await api('/api/settings',{method:'POST',body:JSON.stringify({clear_key:true})});state.settings=data;$('#settings-dialog').close();$('#key-banner').hidden=data.configured;$('#key-status').textContent=data.configured?'':'未配置 Key';toast('本机保存的 API Key 已清除')}catch(e){toast(e.message)}};
  $('#clear-chats').onclick=()=>{if(busyGuard())return;$('#settings-dialog').close();confirmAction('删除全部对话？','此操作会清空本机保存的所有消息，且无法恢复。',async()=>{await api('/api/chats',{method:'DELETE'});state.active=null;state.chats=[];renderActive();toast('全部对话已删除')})};
  $('#messages').onclick=e=>{const btn=e.target.closest('[data-action]');if(!btn)return;const article=btn.closest('.message'),m=state.active&&state.active.messages.find(x=>x.id===article.dataset.id);if(btn.dataset.action==='copy-code'){copyText(btn.closest('.code-wrap').querySelector('code').textContent);return}if(!m)return;if(btn.dataset.action==='copy')copyText(m.content);if(btn.dataset.action==='regenerate')sendMessage(true)};
  $('#chat-scroll').addEventListener('scroll',()=>{shouldStick=nearBottom();$('#scroll-latest').hidden=shouldStick||!state.active}, {passive:true});$('#scroll-latest').onclick=()=>{shouldStick=true;scrollBottom(true)};
  document.addEventListener('keydown',e=>{if(e.key==='Escape')setSidebar(false)});matchMedia('(prefers-color-scheme: dark)').addEventListener('change',()=>applyTheme($('#theme').value));
}
async function init(){applyTheme();bind();updateSendState();try{const [settings,chats]=await Promise.all([api('/api/status'),api('/api/chats')]);state.settings=settings;state.chats=chats.chats;$('#key-banner').hidden=settings.configured;$('#key-status').textContent=settings.configured?'':'未配置 Key';renderActive()}catch(e){toast('无法连接本地后端：'+e.message);$('#key-banner').hidden=false}}
init();
</script>
</body></html>'''


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "DeepSeekTermux/0.0.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write("[%s] %s\n" % (self.log_date_time_string(), fmt % args))

    def security_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Cache-Control", "no-store")

    def json_response(self, value: Any, status: int = 200) -> None:
        data = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.security_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def read_json(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise ValueError("无效的 Content-Length")
        if length <= 0 or length > MAX_BODY:
            raise ValueError("请求为空或超过大小限制")
        body = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(body, dict):
            raise ValueError("请求必须是 JSON 对象")
        return body

    def route_parts(self) -> list[str]:
        return [urllib.parse.unquote(x) for x in urllib.parse.urlparse(self.path).path.split("/") if x]

    def do_GET(self) -> None:
        try:
            path = urllib.parse.urlparse(self.path).path
            if path == "/":
                data = HTML.encode("utf-8")
                self.send_response(200)
                self.security_headers()
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data: https:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
                self.end_headers()
                self.wfile.write(data)
                return
            if path == "/favicon.ico":
                self.send_response(204)
                self.security_headers()
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if path == "/api/status":
                self.json_response(STORE.public_settings())
                return
            if path == "/api/chats":
                self.json_response({"chats": STORE.summaries()})
                return
            parts = self.route_parts()
            if len(parts) == 3 and parts[:2] == ["api", "chats"]:
                chat = STORE.get_chat(parts[2])
                if chat:
                    self.json_response({"chat": chat})
                else:
                    self.json_response({"error": "对话不存在"}, 404)
                return
            self.json_response({"error": "Not Found"}, 404)
        except Exception as exc:
            self.json_response({"error": safe_text(exc, 1000)}, 500)

    def do_POST(self) -> None:
        try:
            path = urllib.parse.urlparse(self.path).path
            body = self.read_json()
            if path == "/api/settings":
                self.json_response(STORE.update_settings(body))
                return
            if path == "/api/stop":
                STORE.stop(safe_text(body.get("chat_id"), 100))
                self.json_response({"ok": True})
                return
            if path == "/api/chat":
                self.handle_chat(body)
                return
            self.json_response({"error": "Not Found"}, 404)
        except ValueError as exc:
            self.json_response({"error": safe_text(exc, 1000)}, 400)
        except Exception as exc:
            self.json_response({"error": safe_text(exc, 1000)}, 500)

    def do_PATCH(self) -> None:
        try:
            parts = self.route_parts()
            if len(parts) == 3 and parts[:2] == ["api", "chats"]:
                body = self.read_json()
                if STORE.rename(parts[2], safe_text(body.get("title"), 200)):
                    self.json_response({"ok": True, "chats": STORE.summaries()})
                else:
                    self.json_response({"error": "对话不存在"}, 404)
                return
            self.json_response({"error": "Not Found"}, 404)
        except Exception as exc:
            self.json_response({"error": safe_text(exc, 1000)}, 400)

    def do_DELETE(self) -> None:
        try:
            path = urllib.parse.urlparse(self.path).path
            if path == "/api/chats":
                STORE.clear()
                self.json_response({"ok": True})
                return
            parts = self.route_parts()
            if len(parts) == 3 and parts[:2] == ["api", "chats"]:
                if STORE.delete(parts[2]):
                    self.json_response({"ok": True})
                else:
                    self.json_response({"error": "对话不存在"}, 404)
                return
            self.json_response({"error": "Not Found"}, 404)
        except Exception as exc:
            self.json_response({"error": safe_text(exc, 1000)}, 500)

    def start_stream(self) -> None:
        self.send_response(200)
        self.security_headers()
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Connection", "keep-alive")
        self.send_header("Transfer-Encoding", "chunked")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        self._stream_open = True

    def send_event(self, kind: str, value: dict[str, Any]) -> None:
        payload = ("data: " + json.dumps({"type": kind, **value}, ensure_ascii=False, separators=(",", ":")) + "\n\n").encode("utf-8")
        self.wfile.write((f"{len(payload):X}\r\n").encode("ascii") + payload + b"\r\n")
        self.wfile.flush()

    def finish_stream(self) -> None:
        if getattr(self, "_stream_open", False):
            self._stream_open = False
            try:
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass

    def handle_chat(self, body: dict[str, Any]) -> None:
        if not STORE.api_key():
            self.json_response({"error": "尚未配置 DeepSeek API Key"}, 400)
            return
        message = safe_text(body.get("message"), 32_001).strip()
        regenerate = bool(body.get("regenerate"))
        if not regenerate and not message:
            self.json_response({"error": "消息不能为空"}, 400)
            return
        if len(message) > 32_000:
            self.json_response({"error": "单条消息不能超过 32000 个字符"}, 400)
            return
        chat_id = safe_text(body.get("chat_id"), 100) or None
        if chat_id and not STORE.get_chat(chat_id):
            self.json_response({"error": "对话不存在"}, 404)
            return
        with ACTIVE_LOCK:
            if chat_id and chat_id in ACTIVE:
                self.json_response({"error": "该对话正在生成回复"}, 409)
                return
            chat, _user, assistant, _created = STORE.begin_turn(chat_id, message, regenerate)
            stop_event = threading.Event()
            ACTIVE[chat["id"]] = stop_event
        try:
            self.start_stream()
            self.send_event("start", {
                "chat_id": chat["id"], "title": chat.get("title"),
                "user_id": chat["messages"][-2]["id"], "assistant_id": assistant["id"],
                "chats": STORE.summaries(),
            })
        except (BrokenPipeError, ConnectionResetError):
            with ACTIVE_LOCK:
                ACTIVE.pop(chat["id"], None)
            assistant["status"] = "stopped"
            STORE.update_turn(chat["id"], assistant)
            self.finish_stream()
            return
        run_upstream(self, chat, assistant, bool(body.get("thinking")), bool(body.get("search")), stop_event)


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def open_browser(url: str) -> None:
    time.sleep(0.6)
    try:
        import shutil
        import subprocess
        opener = shutil.which("termux-open-url")
        if opener:
            subprocess.Popen([opener, url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            webbrowser.open(url)
    except Exception:
        pass


def main() -> int:
    global STORE
    parser = argparse.ArgumentParser(description=APP_NAME)
    parser.add_argument("--host", default="127.0.0.1", help="监听地址，默认 127.0.0.1")
    parser.add_argument("--port", type=int, default=8080, help="监听端口，默认 8080")
    parser.add_argument("--data-dir", type=Path, default=Path.home() / ".deepseek-termux", help="本地数据目录")
    parser.add_argument("--no-open", action="store_true", help="启动后不自动打开浏览器")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("端口必须在 1—65535 之间")
    STORE = Store(args.data_dir.expanduser().resolve())
    try:
        server = Server((args.host, args.port), Handler)
    except OSError as exc:
        print(f"启动失败：{exc}\n可尝试：python deepseek_mobile.py --port 8081", file=sys.stderr)
        return 1
    browser_host = "127.0.0.1" if args.host in {"0.0.0.0", "::"} else args.host
    url = f"http://{browser_host}:{args.port}"
    print(f"\n{APP_NAME} v{VERSION}\n")
    print(f"请在浏览器打开：{url}")
    print(f"本地数据目录：{STORE.data_dir}")
    print("API Key：" + ("已配置" if STORE.api_key() else "尚未配置（可在网页设置中填写）"))
    if args.host not in {"127.0.0.1", "localhost"}:
        print("警告：当前不是纯回环监听；本程序没有访问认证，请勿暴露到不可信网络。")
    print("按 Ctrl+C 停止服务。\n")
    if not args.no_open:
        threading.Thread(target=open_browser, args=(url,), daemon=True).start()
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        print("\n正在停止…")
    finally:
        for row in STORE.summaries():
            STORE.stop(row["id"])
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
