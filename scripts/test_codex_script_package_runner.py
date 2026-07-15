#!/usr/bin/env python3
from __future__ import annotations

import unittest
from unittest.mock import patch

from codex_script_package_runner import try_create_feishu_document


class CodexScriptPackageRunnerTest(unittest.TestCase):
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
