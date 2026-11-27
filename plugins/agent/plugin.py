"""模型—工具—模型循环；每个阶段均可通过事件扩展。"""

from __future__ import annotations

import json
import threading
import uuid
from typing import Iterator


class ChatCancelled(Exception):
    pass


class ChatService:
    def __init__(self, context) -> None:
        self.context = context
        self.settings = context.settings
        self.store = context.store
        self._active: dict[str, tuple[threading.Event, str]] = {}
        self._lock = threading.RLock()

    def cancel(self, request_id: str) -> bool:
        with self._lock:
            active = self._active.get(request_id)
        if not active:
            return False
        active[0].set()
        return True

    def _register(self, request_id: str, conversation_id: str) -> threading.Event:
        cancel_event = threading.Event()
        with self._lock:
            if any(conversation_id == item[1] for item in self._active.values()):
                raise RuntimeError("该会话已有正在生成的回复，请先停止或等待完成。")
            self._active[request_id] = (cancel_event, conversation_id)
        return cancel_event

    def _unregister(self, request_id: str) -> None:
        with self._lock:
            self._active.pop(request_id, None)

    @staticmethod
    def _merge_tool_call(accumulated: dict, event: dict) -> None:
        index = int(event.get("index", 0))
        call = accumulated.setdefault(index, {
            "id": "",
            "type": "function",
            "function": {"name": "", "arguments": ""},
        })
        if event.get("id"):
            call["id"] += event["id"]
        if event.get("name"):
            call["function"]["name"] += event["name"]
        if event.get("arguments"):
            call["function"]["arguments"] += event["arguments"]

    def _tools_for_turn(self, options: dict) -> list[dict]:
        tools = []
        for schema in self.context.tools.schemas():
            tool_type = schema.get("type")
            if tool_type == "deepseek_web_search" and not options.get("web_search"):
                continue
            if tool_type == "deepseek_read_url" and not options.get("read_url"):
                continue
            tools.append(schema)
        return tools

    def _request(self, conversation_id: str, options: dict, tools: list[dict]) -> dict:
        history = self.store.model_messages(conversation_id, include_reasoning=bool(tools))
        system_prompt = options.get("system_prompt") or self.settings.get("system_prompt")
        messages = ([{"role": "system", "content": system_prompt}] if system_prompt.strip() else []) + history
        request = {
            "provider": options.get("provider") or self.settings.get("provider"),
            "model": options.get("model") or self.settings.get("model"),
            "thinking": options.get("thinking", self.settings.get("thinking")),
            "reasoning_effort": options.get("reasoning_effort") or self.settings.get("reasoning_effort"),
            "messages": messages,
            "tools": tools,
        }
        return self.context.events.waterfall("chat.request", request)

    def stream(self, conversation_id: str, content: str, options: dict, request_id: str | None = None) -> Iterator[dict]:
        request_id = request_id or uuid.uuid4().hex
        content = content.strip()
        if not content:
            yield {"type": "error", "error": "消息不能为空"}
            return
        try:
            cancel_event = self._register(request_id, conversation_id)
        except RuntimeError as exc:
            yield {"type": "error", "error": str(exc), "request_id": request_id}
            return

        partial_content: list[str] = []
        partial_reasoning: list[str] = []
        try:
            self.store.get(conversation_id)
            user_message = self.store.append(conversation_id, {"role": "user", "content": content})
            self.context.events.emit("message.received", {
                "conversation_id": conversation_id,
                "message": user_message,
                "request_id": request_id,
            })
            yield {
                "type": "meta",
                "request_id": request_id,
                "conversation_id": conversation_id,
                "user_message": user_message,
            }
            tools = self._tools_for_turn(options)
            usage: dict = {}

            for step in range(1, 7):
                if cancel_event.is_set():
                    raise ChatCancelled()
                request = self._request(conversation_id, options, tools)
                provider = self.context.models.get(request["provider"])
                accumulated_calls: dict = {}
                step_content: list[str] = []
                step_reasoning: list[str] = []
                self.context.events.emit("chat.step", {
                    "conversation_id": conversation_id,
                    "request_id": request_id,
                    "step": step,
                    "request": request,
                })

                for event in provider.stream(request, cancel_event):
                    if cancel_event.is_set():
                        raise ChatCancelled()
                    event_type = event.get("type")
                    if event_type == "content":
                        text = event.get("text", "")
                        step_content.append(text)
                        partial_content.append(text)
                        yield {"type": "delta", "text": text}
                    elif event_type == "reasoning":
                        text = event.get("text", "")
                        step_reasoning.append(text)
                        partial_reasoning.append(text)
                        yield {"type": "reasoning", "text": text}
                    elif event_type == "tool_call":
                        self._merge_tool_call(accumulated_calls, event)
                    elif event_type == "usage":
                        usage = event.get("usage") or usage

                calls = [accumulated_calls[index] for index in sorted(accumulated_calls)]
                assistant = {
                    "role": "assistant",
                    "content": "".join(step_content),
                    "reasoning": "".join(step_reasoning),
                }
                if calls:
                    for index, call in enumerate(calls):
                        call["id"] = call["id"] or f"call_{uuid.uuid4().hex[:20]}"
                        if not call["function"]["name"]:
                            raise RuntimeError("模型返回了缺少名称的工具调用")
                    assistant["tool_calls"] = calls
                    self.store.append(conversation_id, assistant)
                    # 这些内容已提交；后续取消时只保存尚未提交的新片段。
                    partial_content = []
                    partial_reasoning = []
                    for call in calls:
                        if cancel_event.is_set():
                            raise ChatCancelled()
                        name = call["function"]["name"]
                        call_id = call["id"]
                        try:
                            arguments = json.loads(call["function"]["arguments"] or "{}")
                            if not isinstance(arguments, dict):
                                raise ValueError("工具参数不是 JSON 对象")
                        except ValueError as exc:
                            arguments = {}
                            result = f"工具参数解析失败：{exc}"
                        else:
                            tool_event = self.context.events.waterfall("tool.call", {
                                "conversation_id": conversation_id,
                                "request_id": request_id,
                                "name": name,
                                "arguments": arguments,
                            })
                            name = tool_event.get("name", name)
                            arguments = tool_event.get("arguments", arguments)
                            yield {"type": "tool_start", "name": name, "call_id": call_id, "arguments": arguments}
                            try:
                                result = self.context.tools.execute(name, arguments)
                            except Exception as exc:
                                result = f"工具执行失败：{exc}"
                        self.store.append(conversation_id, {
                            "role": "tool",
                            "tool_call_id": call_id,
                            "name": name,
                            "content": result,
                        })
                        self.context.events.emit("tool.result", {
                            "conversation_id": conversation_id,
                            "request_id": request_id,
                            "name": name,
                            "result": result,
                        })
                        yield {"type": "tool_end", "name": name, "call_id": call_id, "result": result}
                    continue

                saved = self.store.append(conversation_id, assistant)
                self.context.events.emit("message.completed", {
                    "conversation_id": conversation_id,
                    "request_id": request_id,
                    "message": saved,
                })
                yield {
                    "type": "done",
                    "request_id": request_id,
                    "conversation_id": conversation_id,
                    "message": saved,
                    "usage": usage,
                }
                return

            raise RuntimeError("工具调用已超过 6 步，已停止本轮以避免无限循环。")
        except ChatCancelled:
            if partial_content or partial_reasoning:
                self.store.append(conversation_id, {
                    "role": "assistant",
                    "content": "".join(partial_content),
                    "reasoning": "".join(partial_reasoning),
                    "cancelled": True,
                })
            yield {"type": "done", "request_id": request_id, "conversation_id": conversation_id, "cancelled": True}
        except Exception as exc:
            if partial_content or partial_reasoning:
                self.store.append(conversation_id, {
                    "role": "assistant",
                    "content": "".join(partial_content),
                    "reasoning": "".join(partial_reasoning),
                    "incomplete": True,
                })
            yield {"type": "error", "request_id": request_id, "conversation_id": conversation_id, "error": str(exc)}
        finally:
            self._unregister(request_id)


def apply(ctx):
    ctx.provide("chat", ChatService(ctx.root))
