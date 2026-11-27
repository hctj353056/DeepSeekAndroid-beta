"""同源静态托管 + JSON API + SSE 流式响应。"""

from __future__ import annotations

import json
import mimetypes
import re
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

from . import __version__


MAX_BODY = 1024 * 1024


def trusted_api_request(host_header: str, origin: str, content_type: str, fetch_site: str, server_port: int, bind_host: str, method: str) -> bool:
    """Reject DNS-rebinding and cross-origin browser requests to the local API."""
    if not host_header:
        return False
    try:
        host = urlsplit("//" + host_header)
        if host.port != server_port or not host.hostname:
            return False
    except ValueError:
        return False
    hostname = host.hostname.lower().rstrip(".")
    loopback_names = {"127.0.0.1", "localhost", "::1"}
    if bind_host in loopback_names and hostname not in loopback_names:
        return False
    if fetch_site.lower() == "cross-site":
        return False
    if origin:
        parsed_origin = urlsplit(origin)
        if parsed_origin.scheme != "http" or parsed_origin.netloc.lower() != host_header.lower():
            return False
    if method in {"POST", "PUT", "PATCH"} and not content_type.lower().startswith("application/json"):
        return False
    return True


class AppHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def make_handler(context, plugin_manager, web_root: Path):
    web_root = web_root.resolve()

    def public_config() -> dict:
        config = context.settings.public()
        config["providers"] = context.models.ids()
        return config

    class Handler(BaseHTTPRequestHandler):
        server_version = "DeepSeekTermux/0.0.6"
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):
            print(f"[{self.log_date_time_string()}] {fmt % args}")

        def _json(self, status: int, data) -> None:
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def _read_json(self) -> dict:
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError as exc:
                raise ValueError("非法 Content-Length") from exc
            if length > MAX_BODY:
                raise ValueError("请求体超过 1 MiB 限制")
            if length == 0:
                return {}
            raw = self.rfile.read(length)
            data = json.loads(raw.decode("utf-8"))
            if not isinstance(data, dict):
                raise ValueError("JSON 请求体必须是对象")
            return data

        def _api(self, method: str) -> bool:
            path = urlsplit(self.path).path
            if not path.startswith("/api/"):
                return False
            if not trusted_api_request(
                self.headers.get("Host", ""),
                self.headers.get("Origin", ""),
                self.headers.get("Content-Type", ""),
                self.headers.get("Sec-Fetch-Site", ""),
                self.server.server_address[1],
                self.server.server_address[0],
                method,
            ):
                self._json(403, {"error": "请求来源未通过本地 API 校验"})
                return True
            try:
                if path == "/api/health" and method == "GET":
                    self._json(200, {
                        "ok": True,
                        "version": __version__,
                        "plugins": len(plugin_manager.records),
                        "active_plugins": sum(item.enabled for item in plugin_manager.records.values()),
                        "active_requests": context.chat.active_count(),
                    })
                elif path == "/api/config" and method == "GET":
                    self._json(200, public_config())
                elif path == "/api/config" and method == "PUT":
                    context.settings.update(self._read_json())
                    self._json(200, public_config())
                elif path == "/api/plugins" and method == "GET":
                    self._json(200, {"plugins": plugin_manager.list()})
                elif path == "/api/conversations" and method == "GET":
                    self._json(200, {"conversations": context.store.list()})
                elif path == "/api/conversations" and method == "POST":
                    body = self._read_json()
                    self._json(201, context.store.create(str(body.get("title", "新对话"))))
                elif path == "/api/chat" and method == "POST":
                    self._chat()
                elif path == "/api/chat/cancel" and method == "POST":
                    body = self._read_json()
                    request_id = str(body.get("request_id", ""))
                    self._json(200, {"cancelled": context.chat.cancel(request_id)})
                else:
                    plugin_match = re.fullmatch(r"/api/plugins/([A-Za-z0-9_.-]+)", path)
                    conversation_match = re.fullmatch(r"/api/conversations/([a-f0-9]{24})", path)
                    if plugin_match and method == "POST":
                        body = self._read_json()
                        plugin_manager.set_enabled(plugin_match.group(1), bool(body.get("enabled")))
                        self._json(200, {"plugins": plugin_manager.list()})
                    elif conversation_match and method == "GET":
                        self._json(200, context.store.get(conversation_match.group(1)))
                    elif conversation_match and method == "PATCH":
                        body = self._read_json()
                        self._json(200, context.store.rename(conversation_match.group(1), str(body.get("title", ""))))
                    elif conversation_match and method == "DELETE":
                        context.store.delete(conversation_match.group(1))
                        self._json(200, {"deleted": True})
                    else:
                        self._json(404, {"error": "API 路径不存在"})
            except KeyError as exc:
                self._json(404, {"error": str(exc)})
            except (ValueError, TypeError, json.JSONDecodeError) as exc:
                self._json(400, {"error": str(exc)})
            except Exception as exc:
                self._json(500, {"error": str(exc)})
            return True

        def _chat(self) -> None:
            body = self._read_json()
            conversation_id = str(body.get("conversation_id", ""))
            content = body.get("content", "")
            if not isinstance(content, str):
                raise ValueError("content 必须是字符串")
            options = body.get("options") or {}
            if not isinstance(options, dict):
                raise ValueError("options 必须是对象")
            request_id = str(body.get("request_id", ""))[:80] or uuid.uuid4().hex
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-store")
            self.send_header("X-Accel-Buffering", "no")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            finished = False
            try:
                stream = context.chat.stream(conversation_id, content, options, request_id)
                for event in stream:
                    event_type = str(event.get("type", "message"))
                    payload = json.dumps(event, ensure_ascii=False).encode("utf-8")
                    self.wfile.write(f"event: {event_type}\n".encode("ascii"))
                    self.wfile.write(b"data: " + payload + b"\n\n")
                    self.wfile.flush()
                    if event_type in {"done", "error"}:
                        finished = True
                        if request_id:
                            request_id = None
            except (BrokenPipeError, ConnectionResetError):
                if request_id:
                    context.chat.cancel(request_id)
            finally:
                if not finished and request_id:
                    context.chat.cancel(request_id)

        def _static(self) -> None:
            path = unquote(urlsplit(self.path).path)
            relative = "index.html" if path in {"", "/"} else path.lstrip("/")
            target = (web_root / relative).resolve()
            if not target.is_relative_to(web_root) or not target.is_file():
                self._json(404, {"error": "文件不存在"})
                return
            body = target.read_bytes()
            content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            if target.suffix == ".webmanifest":
                content_type = "application/manifest+json"
            self.send_response(200)
            self.send_header("Content-Type", content_type + ("; charset=utf-8" if content_type.startswith(("text/", "application/javascript", "application/manifest")) else ""))
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def do_GET(self):
            if not self._api("GET"):
                self._static()

        def do_HEAD(self):
            if not self._api("GET"):
                self._static()

        def do_POST(self):
            if not self._api("POST"):
                self._json(404, {"error": "路径不存在"})

        def do_PUT(self):
            if not self._api("PUT"):
                self._json(404, {"error": "路径不存在"})

        def do_PATCH(self):
            if not self._api("PATCH"):
                self._json(404, {"error": "路径不存在"})

        def do_DELETE(self):
            if not self._api("DELETE"):
                self._json(404, {"error": "路径不存在"})

    return Handler


def create_server(context, plugin_manager, web_root: Path, host: str, port: int) -> AppHTTPServer:
    return AppHTTPServer((host, port), make_handler(context, plugin_manager, web_root))
