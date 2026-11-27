"""DeepSeek Chat Completions 流式适配器，仅使用 urllib。"""

from __future__ import annotations

import json
import socket
import ssl
import time
import urllib.error
import urllib.request


class DeepSeekProvider:
    def __init__(self, settings) -> None:
        self.settings = settings

    def stream(self, request: dict, cancel_event):
        api_key = self.settings.get("api_key")
        if not api_key:
            raise RuntimeError("尚未配置 DeepSeek API Key；请打开设置填写，或切换到本地演示模型。")
        payload = {
            "model": request["model"],
            "messages": request["messages"],
            "thinking": {"type": "enabled" if request.get("thinking", True) else "disabled"},
            "reasoning_effort": request.get("reasoning_effort", "high"),
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if request.get("tools"):
            payload["tools"] = request["tools"]
        url = self.settings.get("base_url") + "/chat/completions"
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        http_request = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
                "User-Agent": "DeepSeek-Termux/0.0.5",
            },
        )
        response = None
        try:
            response = self._open_connection(http_request)
            for data in self._sse_data(response):
                if cancel_event.is_set():
                    return
                if data.strip() == "[DONE]":
                    return
                try:
                    chunk = json.loads(data)
                except ValueError:
                    continue
                if chunk.get("usage"):
                    yield {"type": "usage", "usage": chunk["usage"]}
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                choice = choices[0]
                delta = choice.get("delta") or {}
                if delta.get("reasoning_content"):
                    yield {"type": "reasoning", "text": delta["reasoning_content"]}
                if delta.get("content"):
                    yield {"type": "content", "text": delta["content"]}
                for call in delta.get("tool_calls") or []:
                    function = call.get("function") or {}
                    yield {
                        "type": "tool_call",
                        "index": call.get("index", 0),
                        "id": call.get("id", ""),
                        "name": function.get("name", ""),
                        "arguments": function.get("arguments", ""),
                    }
                # DeepSeek 服务端联网工具的完整调用只在最后一个 chunk 中出现。
                # 必须原样保存并在后续请求中回传，尤其是链接阅读的 signature。
                if "server_tool_calls" in delta:
                    yield {
                        "type": "server_tool_calls",
                        "server_tool_calls": delta["server_tool_calls"],
                    }
                if "annotations" in delta:
                    yield {"type": "annotations", "annotations": delta["annotations"]}
                server_event = choice.get("server_tool_event")
                if server_event:
                    yield {"type": "server_tool_event", "event": server_event}
                if choice.get("finish_reason"):
                    yield {"type": "finish", "reason": choice["finish_reason"]}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:2000]
            try:
                parsed = json.loads(detail)
                detail = parsed.get("error", {}).get("message", detail)
            except ValueError:
                pass
            raise RuntimeError(f"DeepSeek API 返回 HTTP {exc.code}：{detail}") from exc
        except urllib.error.URLError as exc:
            reason = exc.reason
            if isinstance(reason, ssl.SSLError) or "UNEXPECTED_EOF_WHILE_READING" in str(reason):
                raise RuntimeError(
                    "DeepSeek API 的 TLS 连接被提前关闭（SSL EOF）。这通常与网络/VPN/代理线路或 API 地址连接有关，"
                    "通常不是 API Key 错误。请检查 Base URL（默认 https://api.deepseek.com），"
                    "并尝试切换网络或 VPN 后重试。"
                ) from exc
            raise RuntimeError(f"无法连接 DeepSeek API：{reason}") from exc
        except ssl.SSLError as exc:
            raise RuntimeError(
                "DeepSeek API 的 TLS 连接中断（SSL EOF）。请检查网络/VPN/代理和 Base URL；"
                "这类连接错误通常不是 API Key 错误。"
            ) from exc
        except (ConnectionResetError, socket.timeout, TimeoutError) as exc:
            raise RuntimeError(
                f"连接 DeepSeek API 时网络中断：{exc}。请检查网络/VPN/代理后重试。"
            ) from exc
        finally:
            if response is not None:
                response.close()

    @staticmethod
    def _open_connection(http_request):
        """Retry only transient connection setup failures, before stream data is consumed."""
        for attempt in range(3):
            try:
                return urllib.request.urlopen(http_request, timeout=180)
            except urllib.error.URLError as exc:
                reason = exc.reason
                transient = (
                    isinstance(reason, (ssl.SSLError, socket.timeout, ConnectionResetError, TimeoutError))
                    or "UNEXPECTED_EOF_WHILE_READING" in str(reason)
                )
                if not transient or attempt == 2:
                    raise
                time.sleep(attempt + 1)

    @staticmethod
    def _sse_data(response):
        data_lines: list[str] = []
        for raw_line in response:
            line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
            if not line:
                if data_lines:
                    yield "\n".join(data_lines)
                    data_lines = []
                continue
            if line.startswith(":"):
                continue
            if line.startswith("data:"):
                data_lines.append(line[5:].lstrip())
        if data_lines:
            yield "\n".join(data_lines)


def apply(ctx):
    provider = DeepSeekProvider(ctx.settings)
    ctx.effect(ctx.models.register("deepseek", provider))
