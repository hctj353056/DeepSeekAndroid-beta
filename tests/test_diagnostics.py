from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from io import BytesIO
from pathlib import Path

from termux_chat.diagnostics import DiagnosticLogger


class DiagnosticLoggerTests(unittest.TestCase):
    def test_records_structured_events_and_redacts_sensitive_fields(self):
        with tempfile.TemporaryDirectory() as temp:
            logger = DiagnosticLogger(Path(temp))
            logger.record("error", "test.failure", {
                "message": "request failed with Bearer abcdefghijklmnop",
                "api_key": "never-write-this",
                "nested": {"password": "also-secret"},
            })
            entry = json.loads(logger.path.read_text(encoding="utf-8").strip())
            self.assertEqual(entry["event"], "test.failure")
            self.assertNotIn("never-write-this", logger.path.read_text(encoding="utf-8"))
            self.assertNotIn("also-secret", logger.path.read_text(encoding="utf-8"))
            self.assertIn("[REDACTED]", json.dumps(entry))

    def test_user_report_has_action_observation_and_expected_result(self):
        with tempfile.TemporaryDirectory() as temp:
            logger = DiagnosticLogger(Path(temp))
            logger.record_action("打开页面", "无法滚动", "页面应能滚动", "点击后向上滑")
            entry = json.loads(logger.path.read_text(encoding="utf-8").strip())
            self.assertEqual(entry["event"], "user.test_report")
            self.assertEqual(entry["details"]["observed"], "无法滚动")
            self.assertEqual(entry["details"]["expected"], "页面应能滚动")

    def test_rotation_keeps_bounded_number_of_files(self):
        with tempfile.TemporaryDirectory() as temp:
            logger = DiagnosticLogger(Path(temp), max_bytes=16 * 1024, backups=1)
            for index in range(120):
                logger.record("INFO", "rotation.test", {"index": index, "payload": "x" * 500})
            self.assertTrue((logger.directory / "app.log.1").exists())
            self.assertLessEqual(logger.path.stat().st_size, logger.max_bytes)

    def test_export_is_zip_and_excludes_conversation_store(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            logger = DiagnosticLogger(root)
            logger.record("INFO", "startup", {"provider": "demo"})
            (root / "conversations.json").write_text("private conversation", encoding="utf-8")
            archive_bytes = logger.export_zip("test-version")
            with zipfile.ZipFile(BytesIO(archive_bytes)) as archive:
                names = archive.namelist()
                self.assertIn("logs/app.log", names)
                self.assertIn("environment.json", names)
                self.assertNotIn("conversations.json", names)
                manifest = json.loads(archive.read("environment.json"))
                self.assertEqual(manifest["app_version"], "test-version")
                self.assertIn("excluded", manifest["included_data"])


    def test_issue_capture_exports_readable_report_and_jsonl_timeline(self):
        with tempfile.TemporaryDirectory() as temp:
            logger = DiagnosticLogger(Path(temp))
            result = logger.save_incident({
                "title": "公式没有渲染",
                "note": "响应中出现 Bearer abcdefghijklmnop",
                "events": [
                    {"ts": "2026-11-27T10:00:00Z", "type": "network.completed", "details": {"status": 200}},
                    {"ts": "2026-11-27T10:00:01Z", "type": "javascript.error", "details": {"message": "render failed"}},
                ],
            })
            archive_bytes = logger.export_zip("test-version", result["id"])
            with zipfile.ZipFile(BytesIO(archive_bytes)) as archive:
                names = archive.namelist()
                self.assertIn("issue.md", names)
                self.assertIn("issue.json", names)
                self.assertIn("timeline.jsonl", names)
                report = archive.read("issue.md").decode("utf-8")
                self.assertIn("公式没有渲染", report)
                lines = archive.read("timeline.jsonl").decode("utf-8").splitlines()
                self.assertEqual(len(lines), 2)
                self.assertEqual(json.loads(lines[0])["type"], "network.completed")
                issue = json.loads(archive.read("issue.json"))
                self.assertIn("[REDACTED]", issue["note"])

    def test_issue_export_rejects_unknown_or_path_like_id(self):
        with tempfile.TemporaryDirectory() as temp:
            logger = DiagnosticLogger(Path(temp))
            with self.assertRaises(ValueError):
                logger.export_zip("test-version", "../../private")


if __name__ == "__main__":
    unittest.main()
