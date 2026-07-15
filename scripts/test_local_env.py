#!/usr/bin/env python3
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import local_env


ROOT = Path(__file__).resolve().parents[1]


class LocalEnvTest(unittest.TestCase):
    def test_explicit_env_file_loads_without_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / ".env.staging.local"
            env_path.write_text("FEISHU_BASE_APP_TOKEN=app_test\nCUSTOM_VALUE='hello'\n", encoding="utf-8")

            with patch.dict(os.environ, {"AI_ACCOUNT_RADAR_ENV_FILE": str(env_path)}, clear=True):
                local_env.load_local_env(required=True)
                self.assertEqual(os.environ["FEISHU_BASE_APP_TOKEN"], "app_test")
                self.assertEqual(os.environ["CUSTOM_VALUE"], "hello")

    def test_required_env_file_reports_actionable_missing_file(self) -> None:
        missing = "/private/tmp/ai-radar-missing-env-file"
        with patch.dict(os.environ, {"AI_ACCOUNT_RADAR_ENV_FILE": missing}, clear=True):
            with self.assertRaisesRegex(SystemExit, "No env file found"):
                local_env.load_local_env(required=True)

    def test_ar011_cli_entries_fail_actionably_when_env_missing(self) -> None:
        missing = "/private/tmp/ai-radar-missing-env-file"
        commands = [
            [
                sys.executable,
                "scripts/setup_script_package_clickable_links.py",
                "--table-id",
                "tbl_test",
            ],
            [
                sys.executable,
                "scripts/script_package_clickable_link_flow_qa.py",
                "--table-id",
                "tbl_test",
            ],
            [
                sys.executable,
                "scripts/backfill_script_package_clickable_links.py",
                "--table-id",
                "tbl_test",
                "--limit",
                "1",
                "--output-json",
                "/private/tmp/ar011_rc_backfill_probe.json",
            ],
        ]
        env = os.environ.copy()
        env["AI_ACCOUNT_RADAR_ENV_FILE"] = missing
        env["PYTHONPATH"] = "scripts"
        for command in commands:
            with self.subTest(command=command[1]):
                result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, env=env)
                combined = result.stdout + result.stderr
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("No env file found", combined)
                self.assertNotIn("unexpected keyword argument 'required'", combined)


if __name__ == "__main__":
    unittest.main()
