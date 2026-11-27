"""稳定宿主 HTTP 入口：DSA 控制 API 与活动主体透明代理。"""

from __future__ import annotations

import http.client
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from . import __version__
from .server import MAX_BODY, trusted_api_request
from .supervisor import RuntimeSupervisor


HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailer", "transfer-encoding", "upgrade", "server", "date",
}


class SupervisorHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def make_supervisor_handler(supervisor: RuntimeSupervisor):
    class Handler(BaseHTTPRequestHandler):
        server_version = "DeepSeekTermux/0.0.6"
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):
            print(f"[{self.log_date_time_string()}] {fmt % args}", flush=True)

        def _json(self, status: int, data) -> None:
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _read_json(self) -> dict:
            raw = self._read_body()
            if not raw:
                return {}
            data = json.loads(raw.decode("utf-8"))
            if not isinstance(data, dict):
                raise ValueError("JSON 请求体必须是对象")
            return data

        def _read_body(self) -> bytes:
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError as exc:
                raise ValueError("非法 Content-Length") from exc
            if length > MAX_BODY:
                raise ValueError("请求体超过 1 MiB 限制")
            return self.rfile.read(length) if length else b""

        def _api_allowed(self, method: str) -> bool:
            if trusted_api_request(
                self.headers.get("Host", ""),
                self.headers.get("Origin", ""),
                self.headers.get("Content-Type", ""),
                self.headers.get("Sec-Fetch-Site", ""),
                self.server.server_address[1],
                self.server.server_address[0],
                method,
            ):
                return True
            self._json(403, {"error": "请求来源未通过本地 API 校验"})
            return False

        def _dsa(self, method: str, path: str) -> None:
            try:
                if method == "GET" and path == "/api/dsa/status":
                    self._json(200, supervisor.status())
                    return
                if method == "POST":
                    self._read_json()
                    if path == "/api/dsa/candidate":
                        self._json(200, supervisor.prepare_candidate())
                        return
                    if path == "/api/dsa/activate":
                        self._json(200, supervisor.activate())
                        return
                    if path == "/api/dsa/rollback":
                        self._json(200, supervisor.rollback())
                        return
                self._json(404, {"error": "DSA 控制 API 路径不存在"})
            except KeyError as exc:
                self._json(404, {"error": str(exc)})
            except (ValueError, TypeError, json.JSONDecodeError) as exc:
                self._json(400, {"error": str(exc)})
            except RuntimeError as exc:
                self._json(409, {"error": str(exc), "status": supervisor.status()})
            except Exception as exc:
                self._json(500, {"error": str(exc)})

        def _proxy(self, method: str) -> None:
            connection = None
            response = None
            headers_sent = False
            try:
                body = self._read_body()
                handle = supervisor.active_handle()
                connection = http.client.HTTPConnection("127.0.0.1", handle.port, timeout=190)
                excluded = {
                    "host", "origin", "content-length", "connection",
                    "sec-fetch-site", "sec-fetch-mode", "sec-fetch-dest", "sec-fetch-user",
                }
                headers = {
                    key: value for key, value in self.headers.items()
                    if key.lower() not in excluded
                }
                headers["Host"] = f"127.0.0.1:{handle.port}"
                headers["Connection"] = "close"
                connection.request(method, self.path, body=body or None, headers=headers)
                response = connection.getresponse()
                self.send_response(response.status)
                for key, value in response.getheaders():
                    if key.lower() not in HOP_BY_HOP:
                        self.send_header(key, value)
                content_type = response.getheader("Content-Type", "")
                event_stream = content_type.startswith("text/event-stream")
                if event_stream:
                    self.send_header("Connection", "close")
                    self.close_connection = True
                self.end_headers()
                headers_sent = True
                if method != "HEAD":
                    while True:
                        chunk = response.read1(16384)
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        if event_stream:
                            self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                self.close_connection = True
            except (ValueError, RuntimeError) as exc:
                if not headers_sent:
                    self._json(503, {"error": str(exc)})
            except (OSError, http.client.HTTPException) as exc:
                if not headers_sent:
                    self._json(502, {"error": f"无法连接活动 DSA 主体：{exc}"})
                else:
                    self.close_connection = True
            finally:
                if response is not None:
                    response.close()
                if connection is not None:
                    connection.close()

        def _diagnostics(self, method: str, path: str) -> None:
            try:
                logger = self.supervisor.diagnostics
                if method == "GET" and path == "/api/diagnostics/export":
                    archive = logger.export_zip(__version__)
                    self.send_response(200)
                    self.send_header("Content-Type", "application/zip")
                    self.send_header("Content-Length", str(len(archive)))
                    self.send_header("Content-Disposition", 'attachment; filename="dsa-diagnostics.zip"')
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("X-Content-Type-Options", "nosniff")
                    self.end_headers()
                    if self.command != "HEAD":
                        self.wfile.write(archive)
                    return
                if method == "POST" and path == "/api/diagnostics/report":
                    body = self._read_json()
                    logger.record_action(
                        str(body.get("action", "")),
                        str(body.get("observed", "")),
                        str(body.get("expected", "")),
                        str(body.get("steps", "")),
                        str(body.get("note", "")),
                    )
                    self._json(201, {"saved": True})
                    return
                if method == "POST" and path == "/api/diagnostics/client-event":
                    body = self._read_json()
                    event = re.sub(r"[^A-Za-z0-9_.-]", "_", str(body.get("event", "client.event")))[:80]
                    level = str(body.get("level", "INFO")).upper()
                    if level not in {"INFO", "WARN", "ERROR"}:
                        level = "INFO"
                    logger.record(level, "client." + event, {})
                    self._json(202, {"saved": True})
                    return
                self._json(405, {"error": "诊断接口不支持此方法"})
            except (ValueError, TypeError, json.JSONDecodeError) as exc:
                self._json(400, {"error": str(exc)})
            except Exception as exc:
                self._json(500, {"error": str(exc)})

        def _dispatch(self, method: str) -> None:
            path = urlsplit(self.path).path
            if path.startswith("/api/") and not self._api_allowed(method):
                return
            if path in {"/api/diagnostics/report", "/api/diagnostics/client-event", "/api/diagnostics/export"}:
                self._diagnostics(method, path)
            elif path.startswith("/api/dsa/"):
                self._dsa(method, path)
            else:
                self._proxy(method)

        def do_GET(self):
            self._dispatch("GET")

        def do_HEAD(self):
            self._dispatch("HEAD")

        def do_POST(self):
            self._dispatch("POST")

        def do_PUT(self):
            self._dispatch("PUT")

        def do_PATCH(self):
            self._dispatch("PATCH")

        def do_DELETE(self):
            self._dispatch("DELETE")

    return Handler


def create_supervisor_server(supervisor: RuntimeSupervisor, host: str, port: int) -> SupervisorHTTPServer:
    return SupervisorHTTPServer((host, port), make_supervisor_handler(supervisor))
