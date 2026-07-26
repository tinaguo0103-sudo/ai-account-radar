import json
import tempfile
import unittest
from pathlib import Path

from daily_workflow import DailyWorkflow, WorkflowConflict, digest
from run_daily_workflow import last_json_object, write_script_artifact


class DailyWorkflowTest(unittest.TestCase):
    def test_three_stage_commit_readback_and_duplicate_noop(self):
        with tempfile.TemporaryDirectory() as tmp:
            flow = DailyWorkflow(Path(tmp) / "workflow.sqlite3")
            flow.begin("run_20260726_120000", "2026-07-26", 9)
            collection = {"content_items": [{"id": "c1"}], "candidates": [{"id": "c1"}]}
            first = flow.commit_stage("run_20260726_120000", "collection", "source-9", collection, "completed")
            self.assertEqual(first["action"], "committed")
            duplicate = flow.commit_stage("run_20260726_120000", "collection", "source-9", collection, "completed")
            self.assertEqual(duplicate["action"], "noop")
            editorial = {"topics": [{"candidate_id": "c1", "decision": "select"}]}
            flow.commit_stage("run_20260726_120000", "editorial", first["output_hash"], editorial, "completed")
            flow.commit_stage("run_20260726_120000", "scripts", digest(editorial), {"scripts": []}, "completed_empty")
            self.assertEqual(len(flow.read_run("run_20260726_120000")["stages"]), 3)

    def test_conflict_wrong_date_and_stage_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            flow = DailyWorkflow(Path(tmp) / "workflow.sqlite3")
            with self.assertRaises(ValueError):
                flow.begin("run_20260726_120000", "2026-07-25", 1)
            flow.begin("run_20260726_120000", "2026-07-26", 1)
            with self.assertRaises(WorkflowConflict):
                flow.commit_stage("run_20260726_120000", "editorial", "x", {}, "completed")
            flow.commit_stage("run_20260726_120000", "collection", "x", {"content_items": []}, "completed_empty")
            with self.assertRaises(WorkflowConflict):
                flow.commit_stage("run_20260726_120000", "collection", "y", {"content_items": []}, "completed_empty")

    def test_skill_and_pending_projection_are_durable(self):
        with tempfile.TemporaryDirectory() as tmp:
            flow = DailyWorkflow(Path(tmp) / "workflow.sqlite3")
            flow.begin("run_20260726_120000", "2026-07-26", 1)
            flow.record_skill(
                run_id="run_20260726_120000", stage="editorial", unit_id="daily",
                attempt=1, skill_name="skill", skill_path="/active/SKILL.md",
                skill_hash="a", input_hash="b", output_hash="c", status="completed",
            )
            flow.record_skill(
                run_id="run_20260726_120000", stage="editorial", unit_id="daily",
                attempt=1, skill_name="second-skill", skill_path="/active/SECOND.md",
                skill_hash="d", input_hash="b", output_hash="e", status="completed",
            )
            flow.record_projection("run_20260726_120000", "collection", 1, "h", "pending", "offline")
            self.assertEqual(len(flow.read_run("run_20260726_120000")["skill_attempts"]), 2)

    def test_normal_entrypoint_has_no_feishu_or_notification_calls(self):
        source = Path(__file__).with_name("run_daily_workflow.py").read_text()
        for forbidden in ("push_today10_to_feishu", "finalize_daily_pipeline_after_editorial",
                          "notify(", "Topic Card", "--write-feishu", "--resolve-url-intake"):
            self.assertNotIn(forbidden, source)
        self.assertIn("--no-feishu-runtime", source)

    def test_script_artifact_is_run_scoped_and_hash_read_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            value = write_script_artifact(Path(tmp), "run_20260726_120000", {
                "topic_id": "topic/unsafe", "title": "标题", "hook": "钩子",
                "structure": "结构", "body": "正文",
            })
            path = Path(value["artifact_path"])
            self.assertEqual(path.parent.name, "scripts")
            self.assertEqual(path.parents[1].name, "run_20260726_120000")
            self.assertTrue(path.is_file())
            self.assertEqual(len(value["artifact_sha256"]), 64)

    def test_collection_uses_last_complete_json_object(self):
        self.assertEqual(
            last_json_object('child output {"rows": 2}\nfinal\\n{"run_id":"run_exact","ok":true}\\n'),
            {"run_id": "run_exact", "ok": True},
        )


if __name__ == "__main__":
    unittest.main()
