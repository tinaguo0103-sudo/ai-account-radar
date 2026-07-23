#!/usr/bin/env python3
from __future__ import annotations

import io
import json
import subprocess
import sys
import tempfile
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
        self.original_preflight = run_topic_card_if_fresh.evaluate_preflight

        run_topic_card_if_fresh.check_automation_worktree = lambda *_args, **_kwargs: ok_worktree_guard()
        run_topic_card_if_fresh.fresh_collection_status = lambda: (True, "fresh", "run_test")
        run_topic_card_if_fresh.feishu_topic_records_for_run = lambda _run_id: (3, "ok")
        run_topic_card_if_fresh.idempotency.blocking_unknowns = lambda **_kwargs: []
        run_topic_card_if_fresh.load_local_env = lambda: None
        run_topic_card_if_fresh.evaluate_preflight = lambda *_args, **_kwargs: {"ok": True}

    def tearDown(self) -> None:
        sys.argv = self.original_argv
        run_topic_card_if_fresh.check_automation_worktree = self.original_guard
        run_topic_card_if_fresh.fresh_collection_status = self.original_fresh
        run_topic_card_if_fresh.feishu_topic_records_for_run = self.original_count
        run_topic_card_if_fresh.idempotency.blocking_unknowns = self.original_unknowns
        run_topic_card_if_fresh.subprocess.run = self.original_subprocess_run
        run_topic_card_if_fresh.notify = self.original_notify
        run_topic_card_if_fresh.load_local_env = self.original_load_env
        run_topic_card_if_fresh.evaluate_preflight = self.original_preflight

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
            return subprocess.CompletedProcess(
                command, 0,
                stdout='TOPIC_CARD_SESSION_RESULT_JSON={"ok":true,"run_id":"run_test","record_count":1,"sent_count":1,"reason":"sent_or_previewed"}\n',
                stderr="",
            )

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

    def test_dns_preflight_failure_stops_before_sender(self) -> None:
        calls: list[list[str]] = []
        run_topic_card_if_fresh.evaluate_preflight = lambda *_args, **_kwargs: {
            "ok": False,
            "blocking_reasons": ["dns_network_unavailable", "core_external_write_unavailable"],
            "external_calls": 0,
            "business_writes": 0,
        }
        run_topic_card_if_fresh.subprocess.run = lambda command, **_kwargs: calls.append(command)

        result, payload = self.run_main(["run_topic_card_if_fresh.py", "--no-notify"])

        self.assertEqual(result, 2)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["reason"], "scheduled_flow_preflight_failed")
        self.assertEqual(calls, [])

    def test_fresh_guard_accepts_downstream_usable_after_editorial_finalize(self) -> None:
        run_topic_card_if_fresh.fresh_collection_status = self.original_fresh
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            logs = root / "logs"
            runs = root / "runs"
            exact = runs / "run_20260716_080311"
            logs.mkdir()
            exact.mkdir(parents=True)
            (logs / "daily_pipeline_2026-07-16.json").write_text(json.dumps({
                "ok": False,
                "run_id": "run_20260716_080311",
                "downstream_usable": True,
                "editorial_finalized": True,
            }), encoding="utf-8")
            (exact / "content_sampler_log.json").write_text(json.dumps({
                "generated_at": "2026-07-16T09:40:00+08:00",
                "run_id": "run_20260716_080311",
                "mode": "write-feishu",
                "today_candidates": 9,
            }), encoding="utf-8")
            (exact / "today_10_topics.csv").write_text("选题标题\nA\n", encoding="utf-8")
            with unittest.mock.patch.object(run_topic_card_if_fresh, "PIPELINE_LOG_DIR", logs), \
                    unittest.mock.patch.object(run_topic_card_if_fresh, "RUNS_DIR", runs), \
                    unittest.mock.patch.object(run_topic_card_if_fresh, "today_key", lambda: "2026-07-16"), \
                    unittest.mock.patch.object(run_topic_card_if_fresh, "feishu_topic_records_for_run", lambda _run_id: (2, "ok")):
                ok, reason, run_id = run_topic_card_if_fresh.fresh_collection_status()
        self.assertTrue(ok)
        self.assertEqual(reason, "fresh")
        self.assertEqual(run_id, "run_20260716_080311")

    def test_fresh_guard_blocks_before_editorial_finalize_even_if_downstream_usable(self) -> None:
        run_topic_card_if_fresh.fresh_collection_status = self.original_fresh
        with tempfile.TemporaryDirectory() as tmp:
            logs = Path(tmp)
            (logs / "daily_pipeline_2026-07-16.json").write_text(json.dumps({
                "ok": False,
                "run_id": "run_20260716_080311",
                "downstream_usable": True,
            }), encoding="utf-8")
            with unittest.mock.patch.object(run_topic_card_if_fresh, "PIPELINE_LOG_DIR", logs), \
                    unittest.mock.patch.object(run_topic_card_if_fresh, "today_key", lambda: "2026-07-16"):
                ok, reason, run_id = run_topic_card_if_fresh.fresh_collection_status()
        self.assertFalse(ok)
        self.assertEqual(reason, "today_editorial_not_finalized")
        self.assertEqual(run_id, "run_20260716_080311")


if __name__ == "__main__":
    unittest.main()
