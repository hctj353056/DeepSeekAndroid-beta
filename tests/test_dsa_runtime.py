from __future__ import annotations

import http.client
import json
import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from termux_chat.supervisor import RuntimeSupervisor
from termux_chat.supervisor_server import create_supervisor_server


ROOT = Path(__file__).resolve().parents[1]
IGNORE = shutil.ignore_patterns(".git", "data", "__pycache__", "*.pyc", ".pytest_cache")


class RuntimeSupervisorTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        temporary = Path(self.temp.name)
        self.lab = temporary / "lab"
        shutil.copytree(ROOT, self.lab, ignore=IGNORE)
        self.data = temporary / "data"
        self.supervisor = RuntimeSupervisor(self.lab, self.data, startup_timeout=10)
        self.supervisor.start()

    def tearDown(self) -> None:
        self.supervisor.shutdown()
        self.temp.cleanup()

    def set_lab_runtime_version(self, version: str) -> None:
        init_file = self.lab / "termux_chat" / "__init__.py"
        init_file.write_text(
            '"""测试实验主体。"""\n\n__version__ = ' + repr(version) + "\n",
            encoding="utf-8",
        )

    def test_candidate_activation_and_rollback_keep_source_generations_separate(self) -> None:
        original = self.supervisor.active_handle()
        self.assertEqual(original.version_id, "v1")
        self.assertEqual(self.supervisor._health(original.port)["version"], "0.0.5")

        self.set_lab_runtime_version("0.0.5-candidate")
        status = self.supervisor.prepare_candidate()
        self.assertEqual(status["active_version_id"], "v1")
        self.assertEqual(status["active_slot"], "A")
        self.assertEqual(status["candidate"]["status"], "ready")
        self.assertEqual(status["candidate"]["version_id"], "v2")
        self.assertFalse(status["slots"]["B"]["shared_data"])
        self.assertEqual(self.supervisor._health(original.port)["version"], "0.0.5")
        self.assertEqual(self.supervisor._health(self.supervisor.slots["B"].port)["version"], "0.0.5-candidate")

        status = self.supervisor.activate()
        self.assertEqual(status["active_version_id"], "v2")
        self.assertEqual(status["active_slot"], "B")
        self.assertEqual(status["previous_version_id"], "v1")
        self.assertTrue(status["slots"]["B"]["shared_data"])
        self.assertEqual(self.supervisor._health(self.supervisor.active_handle().port)["version"], "0.0.5-candidate")
        self.assertIn("A", self.supervisor.slots)

        status = self.supervisor.rollback()
        self.assertEqual(status["active_version_id"], "v1")
        self.assertEqual(status["active_slot"], "A")
        self.assertEqual(status["previous_version_id"], "v2")
        self.assertEqual(self.supervisor._health(self.supervisor.active_handle().port)["version"], "0.0.5")
        versions = self.supervisor.store.list()
        self.assertEqual([item["id"] for item in versions], ["v2", "v1"])
        self.assertNotEqual(versions[0]["source_hash"], versions[1]["source_hash"])

    def test_active_version_survives_supervisor_restart(self) -> None:
        self.set_lab_runtime_version("0.0.5-restart-check")
        self.supervisor.prepare_candidate()
        self.supervisor.activate()
        self.supervisor.shutdown()

        self.supervisor = RuntimeSupervisor(self.lab, self.data, startup_timeout=10)
        self.supervisor.start()
        status = self.supervisor.status()
        self.assertEqual(status["active_version_id"], "v2")
        self.assertEqual(status["previous_version_id"], "v1")
        self.assertEqual(status["active_slot"], "A")
        self.assertEqual(self.supervisor._health(self.supervisor.active_handle().port)["version"], "0.0.5-restart-check")

    def test_broken_candidate_does_not_interrupt_active_runtime_and_can_be_retried(self) -> None:
        original = self.supervisor.active_handle()
        agent_file = self.lab / "plugins" / "agent" / "plugin.py"
        agent_file.write_text("def broken(:\n    pass\n", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "仍在运行"):
            self.supervisor.prepare_candidate()
        status = self.supervisor.status()
        self.assertEqual(status["active_version_id"], "v1")
        self.assertEqual(status["candidate"]["status"], "failed")
        self.assertEqual(status["candidate"]["version_id"], "v2")
        self.assertTrue(self.supervisor._health(original.port)["ok"])
        self.assertFalse(status["slots"]["B"]["occupied"])

        shutil.copy2(ROOT / "plugins" / "agent" / "plugin.py", agent_file)
        status = self.supervisor.prepare_candidate()
        self.assertEqual(status["candidate"]["status"], "ready")
        self.assertEqual(status["candidate"]["version_id"], "v3")
        self.assertEqual(status["active_version_id"], "v1")

    def test_candidate_can_replace_crashed_active_runtime(self) -> None:
        self.supervisor.prepare_candidate()
        active = self.supervisor.active_handle()
        active.process.terminate()
        active.process.wait(timeout=5)
        status = self.supervisor.activate()
        self.assertEqual(status["active_version_id"], "v2")
        self.assertEqual(status["active_slot"], "B")
        self.assertTrue(self.supervisor._health(self.supervisor.active_handle().port)["ok"])

    def test_rollback_restarts_crashed_standby_process(self) -> None:
        self.supervisor.prepare_candidate()
        self.supervisor.activate()
        standby = self.supervisor.slots["A"]
        standby.process.terminate()
        standby.process.wait(timeout=5)
        status = self.supervisor.rollback()
        self.assertEqual(status["active_version_id"], "v1")
        self.assertEqual(status["active_slot"], "A")
        self.assertTrue(self.supervisor._health(self.supervisor.active_handle().port)["ok"])

    def test_active_generation_blocks_activation(self) -> None:
        self.supervisor.prepare_candidate()
        with patch.object(RuntimeSupervisor, "active_requests", return_value=1):
            with self.assertRaisesRegex(RuntimeError, "请求排空"):
                self.supervisor.activate()
        status = self.supervisor.status()
        self.assertEqual(status["active_version_id"], "v1")
        self.assertEqual(status["candidate"]["status"], "ready")


class SupervisorHTTPTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        temporary = Path(self.temp.name)
        self.lab = temporary / "lab"
        shutil.copytree(ROOT, self.lab, ignore=IGNORE)
        self.supervisor = RuntimeSupervisor(self.lab, temporary / "data", startup_timeout=10)
        self.supervisor.start()
        self.server = create_supervisor_server(self.supervisor, "127.0.0.1", 0)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.supervisor.shutdown()
        self.temp.cleanup()

    def request(self, method: str, path: str, body: dict | None = None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=20)
        payload = json.dumps(body).encode() if body is not None else None
        headers = {"Content-Type": "application/json"} if body is not None else {}
        connection.request(method, path, body=payload, headers=headers)
        response = connection.getresponse()
        return connection, response

    def test_proxy_preserves_demo_chat_sse_and_dsa_control_api(self) -> None:
        connection, response = self.request("GET", "/api/dsa/status")
        status = json.loads(response.read().decode())
        connection.close()
        self.assertEqual(response.status, 200)
        self.assertEqual(status["active_version_id"], "v1")

        connection, response = self.request("POST", "/api/conversations", {})
        conversation = json.loads(response.read().decode())
        connection.close()
        self.assertEqual(response.status, 201)

        connection, response = self.request("POST", "/api/chat", {
            "conversation_id": conversation["id"],
            "content": "验证稳定宿主代理",
            "request_id": "req-supervisor-proxy",
            "options": {"provider": "demo", "thinking": False},
        })
        self.assertEqual(response.status, 200)
        self.assertTrue(response.getheader("Content-Type").startswith("text/event-stream"))
        events = []
        while True:
            line = response.readline()
            if not line:
                break
            if line.startswith(b"data: "):
                event = json.loads(line[6:].decode())
                events.append(event)
                if event.get("type") == "done":
                    break
        connection.close()
        self.assertTrue(any(event["type"] == "meta" for event in events))
        self.assertTrue(any(event["type"] == "delta" for event in events))
        self.assertEqual(events[-1]["type"], "done")
        answer = "".join(event.get("text", "") for event in events if event["type"] == "delta")
        self.assertIn("本地链路已接通", answer)


if __name__ == "__main__":
    unittest.main()
