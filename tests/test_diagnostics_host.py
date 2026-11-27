from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.request
import zipfile
from urllib.error import HTTPError
from io import BytesIO
from pathlib import Path

from termux_chat.diagnostics import DiagnosticLogger
from termux_chat.supervisor_server import create_supervisor_server


class FakeSupervisor:
    def __init__(self, data_dir: Path):
        self.diagnostics = DiagnosticLogger(data_dir)


class StableHostDiagnosticsTests(unittest.TestCase):
    def test_report_event_and_export_are_served_by_host(self):
        with tempfile.TemporaryDirectory() as temp:
            supervisor = FakeSupervisor(Path(temp))
            server = create_supervisor_server(supervisor, "127.0.0.1", 0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base = f"http://127.0.0.1:{server.server_address[1]}"
                report = urllib.request.Request(
                    base + "/api/diagnostics/report",
                    data=json.dumps({
                        "action": "打开日志面板",
                        "observed": "测试记录成功",
                        "expected": "报告保存到诊断日志",
                        "steps": "点击日志按钮并提交",
                    }).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                try:
                    response_context = urllib.request.urlopen(report, timeout=3)
                except HTTPError as error:
                    detail = error.read().decode("utf-8", errors="replace")
                    self.fail(f"diagnostics report returned {error.code}: {detail}")
                with response_context as response:
                    self.assertEqual(response.status, 201)
                    self.assertTrue(json.loads(response.read())["saved"])

                event = urllib.request.Request(
                    base + "/api/diagnostics/client-event",
                    data=json.dumps({"event": "test.event", "level": "ERROR", "message": "do not persist"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(event, timeout=3) as response:
                    self.assertEqual(response.status, 202)

                incident = urllib.request.Request(
                    base + "/api/diagnostics/incident",
                    data=json.dumps({
                        "title": "标记的现场",
                        "note": "复现后导出",
                        "events": [{"ts": "2026-11-27T10:00:00Z", "type": "ui.click", "details": {"id": "issueStart"}}],
                    }).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(incident, timeout=3) as response:
                    self.assertEqual(response.status, 201)
                    issue_id = json.loads(response.read())["id"]

                with urllib.request.urlopen(base + "/api/diagnostics/export?issue_id=" + issue_id, timeout=3) as response:
                    self.assertEqual(response.headers.get_content_type(), "application/zip")
                    archive = zipfile.ZipFile(BytesIO(response.read()))
                    self.assertIn("issue.md", archive.namelist())
                    self.assertIn("timeline.jsonl", archive.namelist())
                    report = archive.read("issue.md").decode("utf-8")
                    self.assertIn("标记的现场", report)

                    self.assertEqual(response.headers.get_content_type(), "application/zip")
                    archive = zipfile.ZipFile(BytesIO(response.read()))
                    log_text = archive.read("logs/app.log").decode("utf-8")
                    self.assertIn("user.test_report", log_text)
                    self.assertIn("client.test.event", log_text)
                    self.assertNotIn("do not persist", log_text)
                    self.assertIn("environment.json", archive.namelist())
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
