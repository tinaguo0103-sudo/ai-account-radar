import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from daily_workflow import DailyWorkflow, WorkflowConflict, digest
from run_daily_workflow import last_json_object, write_script_artifact


class DailyWorkflowTest(unittest.TestCase):
    def test_three_stage_commit_readback_and_duplicate_noop(self):
        with tempfile.TemporaryDirectory() as tmp:
            flow = DailyWorkflow(Path(tmp) / "workflow.sqlite3")
            flow.begin("run_20260726_120000", "2026-07-26", 9, "contract")
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
                flow.begin("run_20260726_120000", "2026-07-25", 1, "contract")
            flow.begin("run_20260726_120000", "2026-07-26", 1, "contract")
            with self.assertRaises(WorkflowConflict):
                flow.commit_stage("run_20260726_120000", "editorial", "x", {}, "completed")
            flow.commit_stage("run_20260726_120000", "collection", "x", {"content_items": []}, "completed_empty")
            with self.assertRaises(WorkflowConflict):
                flow.commit_stage("run_20260726_120000", "collection", "y", {"content_items": []}, "completed_empty")

    def test_skill_and_pending_projection_are_durable(self):
        with tempfile.TemporaryDirectory() as tmp:
            flow = DailyWorkflow(Path(tmp) / "workflow.sqlite3")
            flow.begin("run_20260726_120000", "2026-07-26", 1, "contract")
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

    def test_completed_begin_is_byte_stable_and_contract_scoped(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "workflow.sqlite3"
            flow = DailyWorkflow(path)
            self.assertEqual(
                flow.begin("run_20260726_120000", "2026-07-26", 1, "contract-a"),
                "new",
            )
            flow.commit_stage(
                "run_20260726_120000", "collection", "input",
                {"run_id": "run_20260726_120000", "content_items": []},
                "completed_empty",
            )
            before = path.read_bytes()
            self.assertEqual(
                flow.begin("run_20260726_120000", "2026-07-26", 1, "contract-a"),
                "completed_replay",
            )
            self.assertEqual(path.read_bytes(), before)
            with self.assertRaises(WorkflowConflict):
                flow.begin("run_20260726_120000", "2026-07-26", 1, "contract-b")

    def test_applied_projection_cannot_be_downgraded(self):
        with tempfile.TemporaryDirectory() as tmp:
            flow = DailyWorkflow(Path(tmp) / "workflow.sqlite3")
            flow.begin("run_20260726_120000", "2026-07-26", 1, "contract")
            flow.record_projection(
                "run_20260726_120000", "collection", 1, "payload", "applied", "green",
            )
            flow.record_projection(
                "run_20260726_120000", "collection", 1, "payload", "pending", "offline",
            )
            receipt = flow.projection("run_20260726_120000", "collection", 1)
            self.assertEqual(receipt["status"], "applied")
            self.assertEqual(receipt["detail"], "green")

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

    def test_all_four_publisher_bindings_fail_before_any_business_side_effect(self):
        script = Path(__file__).with_name("run_daily_workflow.py")
        cases = {
            "publisher_url_missing": {
                "omit": "--publisher-url",
                "env": {"WEBSITE_PROJECTION_BEARER": "app", "WEBSITE_PROJECTION_SIWC_BYPASS_BEARER": "machine"},
            },
            "publisher_identity_missing": {
                "omit": "--publisher-identity",
                "env": {"WEBSITE_PROJECTION_BEARER": "app", "WEBSITE_PROJECTION_SIWC_BYPASS_BEARER": "machine"},
            },
            "website_projection_bearer_missing": {
                "omit_env": "WEBSITE_PROJECTION_BEARER",
                "env": {"WEBSITE_PROJECTION_SIWC_BYPASS_BEARER": "machine"},
            },
            "website_projection_machine_access_bearer_missing": {
                "omit_env": "WEBSITE_PROJECTION_SIWC_BYPASS_BEARER",
                "env": {"WEBSITE_PROJECTION_BEARER": "app"},
            },
        }
        for expected, config in cases.items():
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                database = root / "state" / "workflow.sqlite3"
                artifacts = root / "artifacts"
                command = [
                    sys.executable, str(script),
                    "--run-id", "run_20260726_120000",
                    "--business-date", "2026-07-26",
                    "--source-revision", "1",
                    "--source-db", str(root / "source.sqlite3"),
                    "--workflow-db", str(database),
                    "--collection-fixture", str(root / "must_not_be_read.json"),
                    "--artifact-root", str(artifacts),
                    "--publisher-url", "http://127.0.0.1:1",
                    "--publisher-identity", "qa-private:workflow",
                ]
                omit = config.get("omit")
                if omit:
                    index = command.index(omit)
                    del command[index:index + 2]
                environment = os.environ.copy()
                environment.pop("WEBSITE_PROJECTION_BEARER", None)
                environment.pop("WEBSITE_PROJECTION_SIWC_BYPASS_BEARER", None)
                environment.update(config["env"])
                result = subprocess.run(command, text=True, capture_output=True, env=environment)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(json.loads(result.stdout), {"ok": False, "error": expected})
                self.assertFalse(database.exists())
                self.assertFalse(artifacts.exists())
                self.assertEqual(list(root.rglob("*.sqlite3")), [])
                self.assertFalse((root / "must_not_be_read.json").exists())


if __name__ == "__main__":
    unittest.main()
