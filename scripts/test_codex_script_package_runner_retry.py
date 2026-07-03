#!/usr/bin/env python3
"""Tests for script package runner retry decisions."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

import codex_script_package_runner as runner


def package(qa_status: str = "revise", qa_result: str = "待 PM 验收，待 QA。", full_markdown: str | None = None) -> dict[str, object]:
    return {
        "topic_title": "测试 06 选题",
        "recommended_template": "测试模板",
        "core_viewpoint": "测试观点",
        "opening_hook": "测试开头",
        "material_reminders": [],
        "release_checks": [],
        "qa_status": qa_status,
        "qa_result": qa_result,
        "can_shoot": "待人工确认",
        "full_markdown": full_markdown or "# 测试 06\n\n## 口播全文\n\n这是一段可供人工验收的草稿。\n",
    }


class CodexScriptPackageRunnerRetryTest(unittest.TestCase):
    def test_revise_waiting_for_external_qa_does_not_retry(self) -> None:
        with patch.object(runner, "run_codex_for_topic", return_value=package()) as run:
            result, attempts, history = runner.generate_package_with_retry({"topic_title": "测试"}, timeout_seconds=1)

        self.assertEqual(result["qa_status"], "revise")
        self.assertEqual(attempts, 1)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(history[0]["retry"], "false")
        self.assertEqual(history[0]["retry_reason"], "revise_waiting_external_qa")

    def test_explicit_rewrite_signal_retries_once(self) -> None:
        first = package(qa_result="需要重写：口播全文结构不可用。")
        second = package(qa_status="pass", qa_result="已修复。")
        with patch.object(runner, "run_codex_for_topic", side_effect=[first, second]) as run:
            result, attempts, history = runner.generate_package_with_retry({"topic_title": "测试"}, timeout_seconds=1)

        self.assertEqual(result["qa_status"], "pass")
        self.assertEqual(attempts, 2)
        self.assertEqual(run.call_count, 2)
        self.assertEqual(history[0]["retry"], "true")
        self.assertIn("qa_result:需要重写", history[0]["retry_reason"])

    def test_visible_internal_boundary_retries(self) -> None:
        bad_markdown = "# 测试\n\n## 口播全文\n\n如果当天没有生成 06，就只作为选题系统复盘。\n"
        first = package(full_markdown=bad_markdown)
        second = package()
        with patch.object(runner, "run_codex_for_topic", side_effect=[first, second]) as run:
            result, attempts, history = runner.generate_package_with_retry({"topic_title": "测试"}, timeout_seconds=1)

        self.assertEqual(result["qa_status"], "revise")
        self.assertEqual(attempts, 2)
        self.assertEqual(run.call_count, 2)
        self.assertEqual(history[0]["retry"], "true")
        self.assertIn("visible_boundary:口播全文", history[0]["retry_reason"])
        self.assertEqual(history[1]["retry_reason"], "revise_waiting_external_qa")

    def test_retry_stops_at_max_attempts(self) -> None:
        always_bad = package(qa_result="需要重写：脚本不可用。")
        with patch.object(runner, "run_codex_for_topic", return_value=always_bad) as run:
            result, attempts, history = runner.generate_package_with_retry({"topic_title": "测试"}, timeout_seconds=1)

        self.assertEqual(result["qa_status"], "revise")
        self.assertEqual(attempts, runner.MAX_REVISE_ATTEMPTS)
        self.assertEqual(run.call_count, runner.MAX_REVISE_ATTEMPTS)
        self.assertEqual(history[0]["retry"], "true")
        self.assertEqual(history[-1]["retry"], "false")
        self.assertTrue(history[-1]["retry_reason"].startswith("max_attempts_reached:qa_result:"))

    def test_codex_exec_error_retries_and_then_succeeds(self) -> None:
        with patch.object(runner, "run_codex_for_topic", side_effect=[RuntimeError("JSON schema missing field"), package(qa_status="pass")]) as run:
            result, attempts, history = runner.generate_package_with_retry({"topic_title": "测试"}, timeout_seconds=1)

        self.assertEqual(result["qa_status"], "pass")
        self.assertEqual(attempts, 2)
        self.assertEqual(run.call_count, 2)
        self.assertEqual(history[0]["qa_status"], "error")
        self.assertEqual(history[0]["retry_reason"], "codex_exec_error")


if __name__ == "__main__":
    unittest.main()
