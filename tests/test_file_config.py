from __future__ import annotations

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

    def test_settings_fallback_when_local_files_are_absent(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            settings = Settings(root / "data", root)
            self.assertEqual(settings.get("api_key"), "")
            self.assertEqual(settings.get("system_prompt"), "You are a helpful assistant.")
            self.assertFalse(settings.public()["system_prompt_from_file"])


if __name__ == "__main__":
    unittest.main()
