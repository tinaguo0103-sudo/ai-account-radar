#!/usr/bin/env python3
from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

import codex_script_package_runner as runner
from codex_script_package_runner import try_create_feishu_document


class CodexScriptPackageRunnerTest(unittest.TestCase):
    def test_codex_exec_does_not_expose_codex_home_as_writable_dir(self) -> None:
        package = {"full_markdown": "# fixture", "qa_status": "pass"}

        def run(command, **_kwargs):
            output_path = Path(command[command.index("--output-last-message") + 1])
            output_path.write_text(__import__("json").dumps(package), encoding="utf-8")
            return type("Result", (), {"stdout": "", "stderr": "", "returncode": 0})()

        with patch.dict("os.environ", {"CODEX_HOME": "/Users/test/.codex"}), \
                patch("codex_script_package_runner.codex_bin", return_value="/Applications/ChatGPT.app/Contents/Resources/codex"), \
                patch("codex_script_package_runner.subprocess.run", side_effect=run) as subprocess_run, \
                patch("codex_script_package_runner.log"):
            result = runner.run_codex_for_topic({"topic_title": "fixture"}, timeout_seconds=1)

        command = subprocess_run.call_args.args[0]
        self.assertNotIn("--add-dir", command)
        self.assertNotIn("/Users/test/.codex", command)
        self.assertEqual(package, result)

    def test_doc_sync_failure_notifies_for_permission_errors(self) -> None:
        with (
            patch("codex_script_package_runner.doc_sync_preflight_status", return_value=None),
            patch("codex_script_package_runner.script_package_folder_url", return_value="https://folder.example"),
            patch(
                "codex_script_package_runner.create_feishu_document",
                side_effect=PermissionError("Operation not permitted: '.env.local'"),
            ),
            patch("codex_script_package_runner.log"),
            patch("codex_script_package_runner.notify_doc_sync_failure") as notify_failure,
        ):
            result = try_create_feishu_document("tenant-token", "测试选题", {"full_markdown": "# test"})

        self.assertEqual(result.status, "飞书文档同步失败")
        self.assertIn("Operation not permitted", result.error)
        notify_failure.assert_called_once()


if __name__ == "__main__":
    unittest.main()
