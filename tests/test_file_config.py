from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from plugins.settings.plugin import Settings


class FileBasedSettingsTests(unittest.TestCase):
    def test_local_key_and_prompt_files_are_loaded_without_echoing_secrets(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data = root / "data"
            key_file = root / "001.key"
            prompt_file = root / "灵魂架构" / "灵魂架构.md"
            key_file.write_text("test-api-secret\n", encoding="utf-8")
            prompt_file.parent.mkdir(parents=True)
            prompt_file.write_text("# 主体\n保持独立判断。", encoding="utf-8")

            with patch.dict("os.environ", {}, clear=True):
                settings = Settings(data, root)

            self.assertEqual(settings.get("api_key"), "test-api-secret")
            self.assertEqual(settings.get("system_prompt"), "# 主体\n保持独立判断。")
            public = settings.public()
            self.assertTrue(public["api_key_from_file"])
            self.assertTrue(public["system_prompt_from_file"])
            self.assertEqual(public["system_prompt"], "")
            self.assertNotIn("test-api-secret", str(public))


    def test_api_key_is_written_to_project_key_file_not_settings_json(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            settings = Settings(root / "data", root)
            settings.update({"api_key": "key-for-test"})
            self.assertEqual((root / "001.key").read_text(encoding="utf-8").strip(), "key-for-test")
            saved = json.loads((root / "data" / "settings.json").read_text(encoding="utf-8"))
            self.assertNotIn("api_key", saved)

    def test_settings_fallback_when_local_files_are_absent(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            settings = Settings(root / "data", root)
            self.assertEqual(settings.get("api_key"), "")
            self.assertTrue(settings.get("system_prompt").startswith("# 灵魂架构"))
            self.assertTrue(settings.public()["system_prompt_from_file"])
            self.assertTrue((root / "001.key").exists())
            self.assertTrue((root / "灵魂架构" / "灵魂架构.md").exists())


if __name__ == "__main__":
    unittest.main()
