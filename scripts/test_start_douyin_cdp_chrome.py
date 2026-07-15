#!/usr/bin/env python3
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

import start_douyin_cdp_chrome as chrome


class StartDouyinCdpChromeTests(unittest.TestCase):
    def test_hidden_launch_uses_explicit_app_path_not_launchservices_app_name(self) -> None:
        app_path = Path("/Applications/Google Chrome.app")
        profile = Path("/tmp/douyin-profile")

        command = chrome.chrome_app_launch_command(app_path, 9333, profile, "https://www.douyin.com/", "hidden")

        self.assertEqual(command[:5], ["/usr/bin/open", "-n", "-g", "-j", "-a"])
        self.assertEqual(command[5], str(app_path))
        self.assertNotIn("-b", command)
        self.assertNotIn(chrome.CHROME_BUNDLE_ID, command)
        self.assertIn("--remote-debugging-port=9333", command)
        self.assertIn(f"--user-data-dir={profile}", command)
        self.assertIn("--start-minimized", command)

    def test_chrome_app_path_can_be_overridden_by_env(self) -> None:
        original = os.environ.get("CHROME_APP_PATH")
        with tempfile.TemporaryDirectory() as tmp:
            override = Path(tmp) / "Google Chrome.app"
            os.environ["CHROME_APP_PATH"] = str(override)
            try:
                self.assertEqual(chrome.chrome_app_path(), override)
            finally:
                if original is None:
                    os.environ.pop("CHROME_APP_PATH", None)
                else:
                    os.environ["CHROME_APP_PATH"] = original

    def test_default_profile_is_canonical_not_repo_local(self) -> None:
        self.assertIn(".codex/ai-account-radar-runtime/browser_profiles", str(chrome.DEFAULT_PROFILE))
        self.assertNotIn(".local_services", str(chrome.DEFAULT_PROFILE))

    def test_not_ready_status_does_not_dereference_missing_identity(self) -> None:
        self.assertEqual(chrome.launch_status(False, None), "launch_failed_or_not_ready")


if __name__ == "__main__":
    unittest.main()
