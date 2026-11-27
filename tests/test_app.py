from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from termux_chat.context import Context
from termux_chat.plugin_manager import PluginManager
from termux_chat.server import trusted_api_request


ROOT = Path(__file__).resolve().parents[1]


class ToolCallingProvider:
    def __init__(self) -> None:
        self.round = 0
        self.requests: list[dict] = []

    def stream(self, request: dict, cancel_event):
        self.round += 1
        self.requests.append(request)
        if self.round == 1:
            yield {"type": "reasoning", "text": "需要先读取设备时间。"}
            yield {
                "type": "tool_call",
                "index": 0,
                "id": "call_test_clock",
                "name": "get_local_time",
                "arguments": "{}",
            }
        else:
            tool_messages = [message for message in request["messages"] if message["role"] == "tool"]
            assert tool_messages, "第二轮缺少工具结果"
            yield {"type": "content", "text": "已读取本地时间。"}
            yield {"type": "usage", "usage": {"total_tokens": 12}}


class AppTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp.name)
        self.context = Context()
        self.context.provide("paths", SimpleNamespace(root=ROOT, data_dir=self.data_dir, web_root=ROOT / "web"))
        self.manager = PluginManager(ROOT, self.context, self.data_dir)
        self.manager.load_profile()
        self.context.settings.update({"provider": "demo"})

    def tearDown(self) -> None:
        self.manager.shutdown()
        self.temp.cleanup()

    def test_profile_loads_services_and_tools(self) -> None:
        self.assertEqual(self.context.models.ids(), ["deepseek", "demo"])
        self.assertIn("get_local_time", self.context.tools.names())
        self.assertIn("deepseek_web_search", self.context.tools.names())
        self.assertTrue(all(item["enabled"] for item in self.manager.list()))

    def test_plugin_disable_revokes_registration_and_enable_restores_it(self) -> None:
        self.manager.disable("web_search")
        self.assertNotIn("deepseek_web_search", self.context.tools.names())
        self.manager.enable("web_search")
        self.assertIn("deepseek_web_search", self.context.tools.names())
        states = json.loads((self.data_dir / "plugin_states.json").read_text(encoding="utf-8"))
        self.assertTrue(states["web_search"])

    def test_settings_never_echo_api_key_and_accept_custom_provider(self) -> None:
        public = self.context.settings.update({"api_key": "secret-for-test", "provider": "custom-provider"})
        self.assertNotIn("api_key", public)
        self.assertNotIn("secret-for-test", json.dumps(public))
        self.assertEqual(public["provider"], "custom-provider")
        self.assertEqual(self.context.settings.get("api_key"), "secret-for-test")

    def test_demo_chat_streams_and_persists(self) -> None:
        conversation = self.context.store.create()
        captured = []
        self.context.events.on("chat.request", captured.append)
        events = list(self.context.chat.stream(conversation["id"], "你好，测试", {"thinking": True}, "req-demo"))
        self.assertTrue(any(event["type"] == "reasoning" for event in events))
        self.assertTrue(any(event["type"] == "delta" for event in events))
        self.assertEqual(events[-1]["type"], "done")
        saved = self.context.store.get(conversation["id"])
        self.assertEqual([message["role"] for message in saved["messages"]], ["user", "assistant"])
        self.assertIn("本地链路已接通", saved["messages"][-1]["content"])
        self.assertEqual(captured[0]["messages"][0]["role"], "system")

    def test_agent_executes_local_tool_and_reconstructs_history(self) -> None:
        provider = ToolCallingProvider()
        dispose = self.context.models.register("mock-tools", provider)
        conversation = self.context.store.create()
        events = list(self.context.chat.stream(conversation["id"], "现在几点？", {"provider": "mock-tools"}, "req-tools"))
        dispose()
        self.assertTrue(any(event["type"] == "tool_start" and event["name"] == "get_local_time" for event in events))
        self.assertTrue(any(event["type"] == "tool_end" for event in events))
        self.assertEqual(events[-1]["type"], "done")
        self.assertEqual(events[-1]["usage"]["total_tokens"], 12)
        self.assertEqual(provider.round, 2)
        second_request = provider.requests[1]
        assistant_with_call = next(message for message in second_request["messages"] if message.get("tool_calls"))
        self.assertEqual(assistant_with_call["reasoning_content"], "需要先读取设备时间。")
        saved = self.context.store.get(conversation["id"])
        self.assertEqual([message["role"] for message in saved["messages"]], ["user", "assistant", "tool", "assistant"])

    def test_cancellation_is_reported(self) -> None:
        conversation = self.context.store.create()
        stream = self.context.chat.stream(conversation["id"], "请回答", {}, "req-cancel")
        next(stream)  # meta，此时请求已注册
        self.assertTrue(self.context.chat.cancel("req-cancel"))
        events = list(stream)
        self.assertEqual(events[-1]["type"], "done")
        self.assertTrue(events[-1]["cancelled"])


if __name__ == "__main__":
    unittest.main()
