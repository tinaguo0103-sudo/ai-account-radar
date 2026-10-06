from __future__ import annotations

import json
import io
import sqlite3
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from daily_workflow import DailyWorkflow, STAGES, WorkflowConflict
import run_daily_workflow
from run_daily_workflow import canonical_url, normalize_items, stable_item_id


class V2WorkflowTest(unittest.TestCase):
    def test_fresh_schema_is_minimal(self):
        with tempfile.TemporaryDirectory() as tmp:
            flow = DailyWorkflow(Path(tmp) / "workflow.sqlite3")
            tables = {
                row[0] for row in flow.db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            self.assertTrue({
                "daily_runs", "stage_results", "workflow_items", "skill_diagnostics",
            }.issubset(tables))
            for removed in ("skill_requests", "projection_receipts", "stages", "skill_attempts"):
                self.assertNotIn(removed, tables)
            columns = {row["name"] for row in flow.db.execute("PRAGMA table_info(daily_runs)")}
            self.assertNotIn("contract_hash", columns)
            self.assertNotIn("stage_plan", columns)

    def test_three_stage_checkpoint_and_terminal_publish_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "workflow.sqlite3"
            flow = DailyWorkflow(path)
            run = "run_20260728_080000"
            self.assertEqual(flow.begin(run, "2026-07-28"), "new")
            for stage in STAGES:
                flow.commit_stage(run, stage, {"run_id": run, stage: True}, "completed")
            flow.complete(run, "completed", f"terminal:{run}")
            result = flow.read_run(run)
            self.assertEqual([row["stage"] for row in result["stages"]], list(STAGES))
            self.assertEqual(result["run"]["status"], "completed")
            self.assertEqual(result["run"]["publish_status"], "pending")
            before = path.read_bytes()
            self.assertEqual(flow.begin(run, "2026-07-28"), "terminal_replay")
            self.assertEqual(path.read_bytes(), before)

    def test_wrong_date_and_stage_conflicts_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            flow = DailyWorkflow(Path(tmp) / "workflow.sqlite3")
            with self.assertRaises(ValueError):
                flow.begin("run_20260728_080000", "2026-07-27")
            run = "run_20260728_080000"
            flow.begin(run, "2026-07-28")
            with self.assertRaises(WorkflowConflict):
                flow.commit_stage(run, "editorial", {}, "completed")
            flow.commit_stage(run, "collection_enrichment", {}, "completed")
            with self.assertRaises(WorkflowConflict):
                flow.commit_stage(run, "collection_enrichment", {"changed": True}, "completed")

    def test_item_identity_duplicate_merge_and_local_conflict(self):
        same = {
            "aweme_id": "7001", "source_url": "https://www.douyin.com/video/7001",
            "title": "same",
        }
        survivor = {"external_id": "x2", "source": "AIHOT", "title": "safe"}
        rows, failures = normalize_items([
            same, dict(same),
            {"external_id": "x1", "source": "AIHOT", "title": "one"},
            {"external_id": "x1", "source": "AIHOT", "title": "conflict"},
            survivor,
        ])
        self.assertEqual({row["item_id"] for row in rows}, {"douyin:7001", "aihot:x2"})
        self.assertEqual(failures, [{"item_id": "aihot:x1", "reason": "stable_item_conflict"}])

    def test_identity_priority_and_url_canonicalization(self):
        self.assertEqual(stable_item_id({"aweme_id": "9"}), "douyin:9")
        self.assertEqual(
            stable_item_id({"external_id": "abc", "source": "WeChat"}), "wechat:abc"
        )
        self.assertEqual(
            canonical_url("HTTPS://Example.COM/a/?b=2&a=1#x"),
            "https://example.com/a?a=1&b=2",
        )
        row: dict[str, str] = {}
        self.assertTrue(stable_item_id(row).startswith("local:"))
        self.assertEqual(stable_item_id(row), row["local_id"])

    def test_skill_diagnostic_change_is_not_a_runtime_conflict(self):
        with tempfile.TemporaryDirectory() as tmp:
            flow = DailyWorkflow(Path(tmp) / "workflow.sqlite3")
            run = "run_20260728_080000"
            flow.begin(run, "2026-07-28")
            flow.record_skill_diagnostic(
                run, "editorial", "daily", "skill", {"path": "/qa/a", "sha256": "old"},
            )
            flow.record_skill_diagnostic(
                run, "editorial", "daily", "skill", {"path": "/qa/b", "sha256": "new"},
            )
            row = flow.read_run(run)["skill_diagnostics"][0]
            self.assertEqual(json.loads(row["details_json"])["sha256"], "old")

    def test_old_schema_is_additively_upgraded_without_use(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "workflow.sqlite3"
            db = sqlite3.connect(path)
            db.execute("""CREATE TABLE runs(
              run_id TEXT PRIMARY KEY,business_date TEXT,status TEXT,source_revision INTEGER,
              created_at TEXT,updated_at TEXT,contract_hash TEXT,stage_plan TEXT
            )""")
            db.execute("CREATE TABLE skill_requests(request_id TEXT)")
            db.commit()
            flow = DailyWorkflow(path)
            columns = {row["name"] for row in flow.db.execute("PRAGMA table_info(daily_runs)")}
            self.assertIn("publish_status", columns)
            self.assertNotIn("contract_hash", columns)
            self.assertIn("contract_hash", {
                row["name"] for row in flow.db.execute("PRAGMA table_info(runs)")
            })
            self.assertEqual(flow.begin("run_20260728_080000", "2026-07-28"), "new")
            source = Path(__file__).with_name("run_daily_workflow.py").read_text()
            self.assertNotIn("contract_hash", source)
            self.assertNotIn("skill_requests", source)

    def test_main_normal_entry_consumes_explicit_completed_empty_discovery_checkpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = "run_20261006_153811"
            business_date = "2026-10-06"
            source_url = "https://www.douyin.com/video/7001"
            candidate = {
                "item_id": "douyin:7001",
                "candidate_id": "douyin:7001",
                "run_id": run,
                "discovery_source": "configured_account",
                "aweme_id": "7001",
                "source": "Douyin",
                "source_url": source_url,
                "title": "QA synthetic candidate; not a factual claim",
                "summary": "Isolated formal-entry assembly fixture.",
                "author": "QA synthetic source",
            }
            collection_path = root / "collection.json"
            collection_path.write_text(json.dumps({
                "run_id": run,
                "business_date": business_date,
                "status": "completed",
                "content_items": [{key: value for key, value in candidate.items() if key != "candidate_id" and key != "run_id" and key != "discovery_source"}],
                "candidates": [candidate],
                "configured_account_status": "completed",
                "configured_account_reason": "",
                "configured_account_captured_at": business_date,
            }), encoding="utf-8")
            discovery_path = root / "video-discovery.json"
            discovery_path.write_text(json.dumps({
                "status": "completed",
                "candidates": [],
                "source_ledger": [
                    {"source": "recommendation", "status": "completed_empty", "reason": "qa_fixture_empty"},
                    {"source": "dynamic_search", "status": "completed_empty", "reason": "qa_fixture_empty"},
                ],
            }), encoding="utf-8")
            argv = [
                "run_daily_workflow.py",
                "--run-id", run,
                "--business-date", business_date,
                "--workflow-db", str(root / "workflow.sqlite3"),
                "--source-db", str(root / "source.sqlite3"),
                "--artifact-root", str(root / "artifacts"),
                "--collection-fixture", str(collection_path),
                "--video-discovery-checkpoint", str(discovery_path),
                "--video-runtime-config", str(root / "runtime.json"),
                "--video-mode", "normal",
            ]
            output = io.StringIO()
            with (
                mock.patch.object(sys, "argv", argv),
                mock.patch.object(
                    run_daily_workflow,
                    "check_runtime_readiness",
                    return_value={"config_path": str(root / "runtime.json"), "policy_path": str(root / "policy.json")},
                ),
                mock.patch.object(run_daily_workflow, "load_discovery_payload") as live_loader,
                redirect_stdout(output),
            ):
                result = run_daily_workflow.main()
            self.assertEqual(result, 0, output.getvalue())
            self.assertFalse(live_loader.called)
            summary = json.loads(output.getvalue().strip().splitlines()[-1])
            self.assertEqual(summary["action"], "editorial_required")
            handoff_path = root / "artifacts" / run / "workflow_handoff.json"
            handoff = json.loads(handoff_path.read_text(encoding="utf-8"))
            self.assertEqual(handoff["action"], "editorial_required")
            self.assertEqual(handoff["run_id"], run)
            collection_stage = DailyWorkflow(root / "workflow.sqlite3").stage(run, "collection_enrichment")
            self.assertEqual(collection_stage["payload"]["source_ledger"][2]["status"], "completed_empty")


if __name__ == "__main__":
    unittest.main()
