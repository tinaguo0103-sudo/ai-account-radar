#!/usr/bin/env python3
from __future__ import annotations

import io
import json
import subprocess
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any

import run_topic_card_if_fresh
from automation_worktree_guard import WorktreeGuardResult


def ok_worktree_guard() -> WorktreeGuardResult:
    return WorktreeGuardResult(
        ok=True,
        reason="test",
        root=str(Path.cwd()),
        branch="main",
        expected_production_dir=str(Path.cwd()),
        allowed_branches=["main"],
    )


class RunTopicCardIfFreshCheckOnlyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_argv = sys.argv[:]
        self.original_guard = run_topic_card_if_fresh.check_automation_worktree
        self.original_fresh = run_topic_card_if_fresh.fresh_collection_status
        self.original_count = run_topic_card_if_fresh.feishu_topic_records_for_run
        self.original_unknowns = run_topic_card_if_fresh.idempotency.blocking_unknowns
        self.original_subprocess_run = run_topic_card_if_fresh.subprocess.run
        self.original_notify = run_topic_card_if_fresh.notify
        self.original_load_env = run_topic_card_if_fresh.load_local_env

        run_topic_card_if_fresh.check_automation_worktree = lambda *_args, **_kwargs: ok_worktree_guard()
        run_topic_card_if_fresh.fresh_collection_status = lambda: (True, "fresh", "run_test")
        run_topic_card_if_fresh.feishu_topic_records_for_run = lambda _run_id: (3, "ok")
        run_topic_card_if_fresh.idempotency.blocking_unknowns = lambda **_kwargs: []
        run_topic_card_if_fresh.load_local_env = lambda: None

    def tearDown(self) -> None:
        sys.argv = self.original_argv
        run_topic_card_if_fresh.check_automation_worktree = self.original_guard
        run_topic_card_if_fresh.fresh_collection_status = self.original_fresh
        run_topic_card_if_fresh.feishu_topic_records_for_run = self.original_count
        run_topic_card_if_fresh.idempotency.blocking_unknowns = self.original_unknowns
        run_topic_card_if_fresh.subprocess.run = self.original_subprocess_run
        run_topic_card_if_fresh.notify = self.original_notify
        run_topic_card_if_fresh.load_local_env = self.original_load_env

    def run_main(self, argv: list[str]) -> tuple[int, dict[str, Any]]:
        sys.argv = argv
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            result = run_topic_card_if_fresh.main()
        return result, json.loads(stdout.getvalue())

    def test_check_only_does_not_call_sender_or_notify(self) -> None:
        def fail_if_called(*_args: Any, **_kwargs: Any) -> None:
            raise AssertionError("check-only must not call sender or notify")

        run_topic_card_if_fresh.subprocess.run = fail_if_called
        run_topic_card_if_fresh.notify = fail_if_called

        result, payload = self.run_main(["run_topic_card_if_fresh.py", "--check-only"])

        self.assertEqual(result, 0)
        self.assertEqual(payload["run_id"], "run_test")
        self.assertEqual(payload["reason"], "fresh")
        self.assertTrue(payload["check_only"])
        self.assertFalse(payload["sent"])
        self.assertTrue(payload["would_send"])
        self.assertEqual(payload["candidate_count"], 3)

    def test_send_path_still_invokes_sender(self) -> None:
        calls: list[list[str]] = []

        def fake_run(command: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
            calls.append(command)
            return subprocess.CompletedProcess(command, 0)

        def fail_notify(*_args: Any, **_kwargs: Any) -> None:
            raise AssertionError("successful send path should not notify")

        run_topic_card_if_fresh.subprocess.run = fake_run
        run_topic_card_if_fresh.notify = fail_notify

        result, payload = self.run_main(["run_topic_card_if_fresh.py", "--no-notify"])

        self.assertEqual(result, 0)
        self.assertEqual(payload["run_id"], "run_test")
        self.assertTrue(payload["sent"])
        self.assertEqual(len(calls), 1)
        self.assertIn("run_topic_decision_card_session.py", calls[0][1])


if __name__ == "__main__":
    unittest.main()
