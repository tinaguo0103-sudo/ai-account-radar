#!/usr/bin/env python3
from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

import feishu_idempotency as idempotency
import feishu_topic_decision_card
import push_today10_to_feishu
import run_topic_card_if_fresh
from automation_worktree_guard import WorktreeGuardResult


def sample_topic_row(run_id: str = "run_20260704_080730", title: str = "Topic A") -> dict[str, str]:
    row = {key: "" for key in push_today10_to_feishu.REQUIRED_FIELDS}
    row.update({
        "选题标题": title,
        "推荐日期": "2026-07-04",
        "原始来源标题": "Source A",
        "一句话Brief": "private brief should not be in ledger",
        "运行批次": run_id,
    })
    return row


class FeishuIdempotencyPhase1Test(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.original_ledger_dir = idempotency.LEDGER_DIR
        idempotency.LEDGER_DIR = Path(self.tmpdir.name) / "ledger"

    def tearDown(self) -> None:
        idempotency.LEDGER_DIR = self.original_ledger_dir
        self.tmpdir.cleanup()

    def ledger_events(self) -> list[dict]:
        return idempotency.read_ledger_events()

    def ledger_text(self) -> str:
        return "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted(idempotency.LEDGER_DIR.glob("*/*.jsonl"))
        )

    def test_topic_candidate_batch_create_writes_intent_and_receipt(self) -> None:
        row = sample_topic_row()
        calls = {"count": 0}
        original_request_json = push_today10_to_feishu.feishu.request_json

        def fake_request_json(*_args, **_kwargs):
            calls["count"] += 1
            return {"code": 0, "data": {"records": [{"record_id": "rec_created"}]}}

        try:
            push_today10_to_feishu.feishu.request_json = fake_request_json
            created = push_today10_to_feishu.batch_create("token", "app", "table", [row], row["运行批次"])
        finally:
            push_today10_to_feishu.feishu.request_json = original_request_json

        self.assertEqual(created, 1)
        self.assertEqual(calls["count"], 1)
        events = self.ledger_events()
        self.assertEqual([event["status"] for event in events], ["pending", "succeeded"])
        self.assertEqual(events[-1]["remote_id"], "rec_created")
        text = self.ledger_text()
        self.assertNotIn("token", text)
        self.assertNotIn("private brief", text)

    def test_topic_candidate_unknown_recovers_by_read_back(self) -> None:
        row = sample_topic_row()
        calls = {"count": 0}
        original_request_json = push_today10_to_feishu.feishu.request_json
        original_all_records = push_today10_to_feishu.all_records

        def fake_request_json(*_args, **_kwargs):
            calls["count"] += 1
            raise TimeoutError("The read operation timed out")

        def fake_all_records(*_args, **_kwargs):
            return [{"record_id": "rec_found", "fields": dict(row)}]

        try:
            push_today10_to_feishu.feishu.request_json = fake_request_json
            push_today10_to_feishu.all_records = fake_all_records
            created = push_today10_to_feishu.batch_create("token", "app", "table", [row], row["运行批次"])
        finally:
            push_today10_to_feishu.feishu.request_json = original_request_json
            push_today10_to_feishu.all_records = original_all_records

        self.assertEqual(created, 1)
        self.assertEqual(calls["count"], 1)
        events = self.ledger_events()
        self.assertEqual(events[-1]["status"], "recovered_by_read_back")
        self.assertEqual(events[-1]["remote_id"], "rec_found")
        self.assertEqual(idempotency.blocking_unknowns(run_id=row["运行批次"], kinds={"topic_candidate_create"}), [])

    def test_topic_candidate_unknown_not_found_blocks_card_guard(self) -> None:
        row = sample_topic_row()
        original_request_json = push_today10_to_feishu.feishu.request_json
        original_all_records = push_today10_to_feishu.all_records
        original_fresh = run_topic_card_if_fresh.fresh_collection_status
        original_guard = run_topic_card_if_fresh.check_automation_worktree
        original_subprocess_run = run_topic_card_if_fresh.subprocess.run
        original_argv = sys.argv[:]

        def fake_request_json(*_args, **_kwargs):
            raise TimeoutError("The read operation timed out")

        try:
            push_today10_to_feishu.feishu.request_json = fake_request_json
            push_today10_to_feishu.all_records = lambda *_args, **_kwargs: []
            with self.assertRaisesRegex(RuntimeError, "status unknown"):
                push_today10_to_feishu.batch_create("token", "app", "table", [row], row["运行批次"])

            run_topic_card_if_fresh.fresh_collection_status = lambda: (True, "fresh", row["运行批次"])
            run_topic_card_if_fresh.check_automation_worktree = lambda *_args, **_kwargs: WorktreeGuardResult(
                ok=True,
                reason="test",
                root=str(Path.cwd()),
                branch="main",
                expected_production_dir=str(Path.cwd()),
                allowed_branches=["main"],
            )

            def fail_if_called(*_args, **_kwargs):
                raise AssertionError("Topic Card subprocess should be blocked by idempotency guard")

            run_topic_card_if_fresh.subprocess.run = fail_if_called
            sys.argv = ["run_topic_card_if_fresh.py", "--no-notify"]
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                result = run_topic_card_if_fresh.main()
        finally:
            push_today10_to_feishu.feishu.request_json = original_request_json
            push_today10_to_feishu.all_records = original_all_records
            run_topic_card_if_fresh.fresh_collection_status = original_fresh
            run_topic_card_if_fresh.check_automation_worktree = original_guard
            run_topic_card_if_fresh.subprocess.run = original_subprocess_run
            sys.argv = original_argv

        self.assertEqual(result, 0)
        self.assertIn("feishu_idempotency_unknown_guard", stdout.getvalue())
        unknowns = idempotency.blocking_unknowns(run_id=row["运行批次"], kinds={"topic_candidate_create"})
        self.assertEqual(len(unknowns), 1)
        self.assertEqual(unknowns[0]["status"], "unknown_not_found")

    def test_topic_candidate_unknown_ambiguous_is_blocking(self) -> None:
        row = sample_topic_row()
        original_request_json = push_today10_to_feishu.feishu.request_json
        original_all_records = push_today10_to_feishu.all_records

        try:
            push_today10_to_feishu.feishu.request_json = lambda *_args, **_kwargs: (_ for _ in ()).throw(TimeoutError("timeout"))
            push_today10_to_feishu.all_records = lambda *_args, **_kwargs: [
                {"record_id": "rec_1", "fields": dict(row)},
                {"record_id": "rec_2", "fields": dict(row)},
            ]
            with self.assertRaisesRegex(RuntimeError, "status unknown"):
                push_today10_to_feishu.batch_create("token", "app", "table", [row], row["运行批次"])
        finally:
            push_today10_to_feishu.feishu.request_json = original_request_json
            push_today10_to_feishu.all_records = original_all_records

        unknowns = idempotency.blocking_unknowns(run_id=row["运行批次"], kinds={"topic_candidate_create"})
        self.assertEqual(len(unknowns), 1)
        self.assertEqual(unknowns[0]["status"], "unknown_ambiguous")

    def test_topic_card_send_writes_intent_and_receipt(self) -> None:
        original_request_json = feishu_topic_decision_card.feishu.request_json

        def fake_request_json(*_args, **_kwargs):
            return {"code": 0, "data": {"message_id": "msg_1"}}

        try:
            feishu_topic_decision_card.feishu.request_json = fake_request_json
            payload = feishu_topic_decision_card.send_card(
                "token",
                {"schema": "2.0", "body": {"elements": [{"tag": "markdown", "content": "private card body"}]}},
                "run_20260704_080730",
                "ou_private_target",
                "open_id",
                preview_path="/private/tmp/card.json",
            )
        finally:
            feishu_topic_decision_card.feishu.request_json = original_request_json

        self.assertEqual(payload["data"]["message_id"], "msg_1")
        events = self.ledger_events()
        self.assertEqual([event["status"] for event in events], ["pending", "succeeded"])
        self.assertEqual(events[-1]["remote_id"], "msg_1")
        text = self.ledger_text()
        self.assertNotIn("token", text)
        self.assertNotIn("ou_private_target", text)
        self.assertNotIn("private card body", text)

    def test_topic_card_send_unknown_blocks_same_run(self) -> None:
        original_request_json = feishu_topic_decision_card.feishu.request_json
        calls = {"count": 0}

        def fake_request_json(*_args, **_kwargs):
            calls["count"] += 1
            raise RuntimeError("POST /im/v1/messages failed with transient error; status unknown")

        try:
            feishu_topic_decision_card.feishu.request_json = fake_request_json
            with self.assertRaisesRegex(RuntimeError, "status unknown"):
                feishu_topic_decision_card.send_card(
                    "token",
                    {"schema": "2.0", "body": {"elements": []}},
                    "run_20260704_080730",
                    "ou_private_target",
                    "open_id",
                )
        finally:
            feishu_topic_decision_card.feishu.request_json = original_request_json

        self.assertEqual(calls["count"], 1)
        unknowns = feishu_topic_decision_card.ensure_no_blocking_unknown_for_card_send("run_20260704_080730")
        self.assertEqual(len(unknowns), 1)
        self.assertEqual(unknowns[0]["status"], "delivery_unknown")


if __name__ == "__main__":
    unittest.main()
