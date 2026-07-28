from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from daily_workflow import DailyWorkflow, STAGES, WorkflowConflict
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


if __name__ == "__main__":
    unittest.main()
