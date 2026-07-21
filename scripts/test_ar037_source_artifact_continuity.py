from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import daily_pipeline
import reconcile_source_sampling_from_feishu as source_reconcile
import run_daily_collection_job
import source_ingestion_lineage as lineage


RUN_ID = "run_20260719_080212"


class AR037SourceArtifactContinuityTests(unittest.TestCase):
    def write_manual(self, path: Path, run_id: str = RUN_ID) -> None:
        rows = [
            {
                "来源类型": "对标视频",
                "账号名/公众号名": f"ok-{account}",
                "内容标题": f"title-{account}-{item}",
                "内容链接": f"https://www.douyin.com/video/{account:02d}{item}",
                "内容指纹": f"source-{account}-{item}",
                "运行批次": run_id,
            }
            for account in range(29)
            for item in range(3)
        ]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")

    def probe(self, manual: Path, run_id: str = RUN_ID) -> dict:
        return {
            "status": "completed_with_failures",
            "run_id": run_id,
            "coverage": {
                "planned_accounts": 31,
                "attempted_accounts": 31,
                "successful_accounts": 29,
                "failed_account_count": 2,
                "failed_accounts": [
                    {"account_name": "bad-a", "artifact_count": 0},
                    {"account_name": "bad-b", "artifact_count": 0},
                ],
                "per_account_artifact_counts": {
                    **{f"ok-{index}": 3 for index in range(29)},
                    "bad-a": 0,
                    "bad-b": 0,
                },
                "invariants": {
                    "attempted_equals_planned": True,
                    "success_plus_failed_equals_attempted": True,
                    "account_lineage_unique_and_complete": True,
                },
            },
            "item_lineage": {"ok": True},
            "manual_artifact": {
                "run_id": run_id,
                "path": str(manual.resolve()),
                "sha256": lineage.artifact_sha256(manual),
                "size": manual.stat().st_size,
                "row_count": 87,
            },
        }

    def write_artifact(self, result: Path, manual: Path, run_id: str = RUN_ID) -> None:
        self.write_manual(manual, run_id)
        result.parent.mkdir(parents=True, exist_ok=True)
        result.write_text(json.dumps(self.probe(manual, run_id), ensure_ascii=False), encoding="utf-8")

    def test_stale_global_retry_cannot_override_current_primary_29_2_87(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            primary_result = root / "run" / "primary" / "cdp_probe_results.json"
            primary_manual = primary_result.with_name("content_items_manual.jsonl")
            stale_result = root / "global-retry" / "cdp_probe_results.json"
            stale_manual = stale_result.with_name("content_items_manual.jsonl")
            self.write_artifact(primary_result, primary_manual)
            self.write_artifact(stale_result, stale_manual, "run_20260618_080000")
            selected_result, selected_manual, report = daily_pipeline.select_and_validate_douyin_artifact(
                RUN_ID, primary_result, primary_manual, stale_result, stale_manual,
                retry_executed=False,
            )
            self.assertEqual(selected_result, primary_result)
            self.assertEqual(selected_manual, primary_manual)
            self.assertEqual(report["selected_artifact"], "primary")
            self.assertEqual(report["successful_item_count"], 87)
            self.assertEqual(report["failed_accounts"], 2)
            step = {"returncode": 0}
            daily_pipeline.apply_selected_douyin_outcome(step, report)
            self.assertTrue(step["optional_failed"])
            self.assertTrue(step["candidate_local_partial"])

    def test_valid_current_retry_wins(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            primary_result = root / "primary" / "cdp_probe_results.json"
            primary_manual = primary_result.with_name("content_items_manual.jsonl")
            retry_result = root / "retry" / "cdp_probe_results.json"
            retry_manual = retry_result.with_name("content_items_manual.jsonl")
            self.write_artifact(primary_result, primary_manual)
            self.write_artifact(retry_result, retry_manual)
            selected, _, report = daily_pipeline.select_and_validate_douyin_artifact(
                RUN_ID, primary_result, primary_manual, retry_result, retry_manual,
                retry_executed=True,
            )
            self.assertEqual(selected, retry_result)
            self.assertEqual(report["selected_artifact"], "retry")

    def test_invalid_retry_variants_fall_back_to_primary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            primary_result = root / "primary" / "cdp_probe_results.json"
            primary_manual = primary_result.with_name("content_items_manual.jsonl")
            self.write_artifact(primary_result, primary_manual)
            for mutation in ("zero", "malformed", "unpaired", "wrong_run"):
                retry_result = root / mutation / "cdp_probe_results.json"
                retry_manual = retry_result.with_name("content_items_manual.jsonl")
                retry_result.parent.mkdir(parents=True, exist_ok=True)
                if mutation == "zero":
                    retry_manual.write_text("", encoding="utf-8")
                    retry_result.write_text(json.dumps({"run_id": RUN_ID, "coverage": {"per_account_artifact_counts": {}}}), encoding="utf-8")
                elif mutation == "malformed":
                    retry_manual.write_text("{}\n", encoding="utf-8")
                    retry_result.write_text("not-json", encoding="utf-8")
                elif mutation == "unpaired":
                    retry_result.write_text(json.dumps({"run_id": RUN_ID}), encoding="utf-8")
                else:
                    self.write_artifact(retry_result, retry_manual, "run_20260718_080000")
                with self.subTest(mutation=mutation):
                    selected, _, report = daily_pipeline.select_and_validate_douyin_artifact(
                        RUN_ID, primary_result, primary_manual, retry_result, retry_manual,
                        retry_executed=True,
                    )
                    self.assertEqual(selected, primary_result)
                    self.assertEqual(report["selected_artifact"], "primary")

    def test_both_invalid_is_source_local_and_safe_other_candidates_continue(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result, manual, report = daily_pipeline.select_and_validate_douyin_artifact(
                RUN_ID,
                root / "primary.json", root / "primary.jsonl",
                root / "retry.json", root / "retry.jsonl",
                retry_executed=True,
            )
            self.assertIsNone(report)
            step = daily_pipeline.isolate_source_failure(
                {"name": "verify Douyin successful-item source artifact", "returncode": 3},
                source="douyin", state="artifact_unavailable", reason="no_valid_current_run_douyin_artifact",
            )
            downstream = daily_pipeline.downstream_usability_report(
                [step], root, today_candidates=3, probe_result_path=result,
            )
            self.assertTrue(downstream["downstream_usable"])
            self.assertFalse(downstream["full_collection_success"])
            self.assertEqual(downstream["system_failure_count"], 0)

    def test_original_optional_probe_is_superseded_by_single_source_outcome(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            probe_step = {
                "name": "fetch daily Douyin homepage title/caption samples through Chrome CDP",
                "returncode": 0,
                "optional_returncode": 3,
                "optional_failed": True,
            }
            daily_pipeline.isolate_source_failure(
                probe_step,
                source="douyin",
                state="artifact_unavailable",
                reason="no_valid_current_run_douyin_artifact",
            )
            diagnostic = daily_pipeline.source_failure_diagnostic(
                name="verify Douyin successful-item source artifact",
                source="douyin",
                reason="no_valid_current_run_douyin_artifact",
            )
            downstream = daily_pipeline.downstream_usability_report(
                [probe_step, diagnostic], root, today_candidates=4,
                probe_result_path=root / "missing-primary.json",
            )
            self.assertTrue(downstream["downstream_usable"])
            self.assertFalse(downstream["full_collection_success"])
            self.assertEqual(downstream["collection_status"], "completed_with_failures")
            self.assertEqual(downstream["source_failure_count"], 1)
            self.assertEqual(downstream["system_failure_count"], 0)
            self.assertTrue(diagnostic["source_diagnostic"])
            self.assertEqual(daily_pipeline.deferred_exit_code([diagnostic]), 0)

    def test_wechat_and_douyin_source_failures_allow_safe_aihot_defer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wechat_step = daily_pipeline.isolate_source_failure(
                {"name": "refresh fixed WeWe provider", "returncode": 4},
                source="wechat", state="login_required", reason="login_required",
            )
            douyin_step = {
                "name": "fetch daily Douyin homepage title/caption samples through Chrome CDP",
                "returncode": 0,
                "optional_returncode": 3,
                "optional_failed": True,
            }
            daily_pipeline.isolate_source_failure(
                douyin_step,
                source="douyin", state="artifact_unavailable",
                reason="no_valid_current_run_douyin_artifact",
            )
            diagnostic = daily_pipeline.source_failure_diagnostic(
                name="verify Douyin successful-item source artifact",
                source="douyin", reason="no_valid_current_run_douyin_artifact",
            )
            steps = [wechat_step, douyin_step, diagnostic]
            downstream = daily_pipeline.downstream_usability_report(
                steps, root, today_candidates=4,
                probe_result_path=root / "missing-primary.json",
            )
            self.assertTrue(downstream["downstream_usable"])
            self.assertFalse(downstream["full_collection_success"])
            self.assertEqual(downstream["collection_status"], "completed_with_failures")
            self.assertEqual(downstream["source_failure_count"], 2)
            self.assertEqual(downstream["system_failure_count"], 0)
            self.assertEqual(0 if downstream["downstream_usable"] else daily_pipeline.deferred_exit_code(steps), 0)

    def test_run_scoped_paths_do_not_use_global_spike_as_authority(self) -> None:
        paths = daily_pipeline.douyin_run_artifact_paths(RUN_ID)
        for path in paths.values():
            self.assertIn(f"output/runs/{RUN_ID}/sources/douyin", path.as_posix())
            self.assertNotIn("output/spikes", path.as_posix())

    def test_valid_plan_with_optional_followup_failure_starts_collection(self) -> None:
        plan_stdout = "SOURCE_PLAN_STATUS_JSON=" + json.dumps({
            "plan_ready": True,
            "optional_followup_failed": True,
            "optional_followup_reason": "view sync failed",
        })
        steps = [
            {"name": "plan", "returncode": 0, "stdout": plan_stdout, "stderr": ""},
            {"name": "pipeline", "returncode": 0, "stdout": "", "stderr": ""},
        ]
        with mock.patch.object(run_daily_collection_job, "load_local_env"), \
                mock.patch.object(run_daily_collection_job, "evaluate_preflight", return_value={"ok": True}), \
                mock.patch.object(run_daily_collection_job, "check_automation_worktree", return_value=SimpleNamespace(ok=True)), \
                mock.patch.object(run_daily_collection_job, "run_step", side_effect=steps) as runner, \
                mock.patch.object(run_daily_collection_job, "write_job_log", return_value=Path("log.json")), \
                mock.patch.object(run_daily_collection_job.sys, "argv", ["job", "--no-notify"]):
            self.assertEqual(0, run_daily_collection_job.main())
        self.assertEqual(runner.call_count, 2)
        self.assertTrue(steps[0]["optional_followup_failed"])

    def test_source_plan_failure_blocks_before_collection(self) -> None:
        plan_step = {
            "name": "plan", "returncode": 0,
            "stdout": "SOURCE_PLAN_STATUS_JSON=" + json.dumps({"plan_ready": False}),
            "stderr": "",
        }
        with mock.patch.object(run_daily_collection_job, "load_local_env"), \
                mock.patch.object(run_daily_collection_job, "evaluate_preflight", return_value={"ok": True}), \
                mock.patch.object(run_daily_collection_job, "check_automation_worktree", return_value=SimpleNamespace(ok=True)), \
                mock.patch.object(run_daily_collection_job, "run_step", return_value=plan_step) as runner, \
                mock.patch.object(run_daily_collection_job, "write_job_log", return_value=Path("log.json")), \
                mock.patch.object(run_daily_collection_job.sys, "argv", ["job", "--no-notify"]):
            self.assertEqual(2, run_daily_collection_job.main())
        runner.assert_called_once()

    def test_reconcile_keeps_plan_ready_when_feishu_followup_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "content_sources.yaml"
            config_path.write_text(json.dumps({"sources": [{
                "id": "source-a",
                "source_role": "current_main_competitor",
                "source_group": "current_main_competitor",
                "account_name": "source-a",
                "platform": "抖音",
                "url": "https://www.douyin.com/user/source-a",
                "default_enabled": True,
                "participates_main_sampling": True,
                "priority": "high",
            }]}), encoding="utf-8")
            records = [{"record_id": "rec-a", "fields": {
                "名称": "source-a", "来源角色": "current_main_competitor",
                "平台": "抖音", "主页链接": "https://www.douyin.com/user/source-a",
                "默认启用": "是", "是否参与主采样": "是",
            }}]
            stdout = io.StringIO()
            with mock.patch.object(source_reconcile, "CONFIG", config_path), \
                    mock.patch.dict(os.environ, {"FEISHU_BASE_APP_TOKEN": "app"}), \
                    mock.patch.object(source_reconcile.feishu, "tenant_token", return_value="token"), \
                    mock.patch.object(source_reconcile, "list_tables", return_value={}), \
                    mock.patch.object(source_reconcile, "resolve_table_id", return_value="tbl"), \
                    mock.patch.object(source_reconcile, "all_records", return_value=records), \
                    mock.patch.object(source_reconcile, "ensure_fields", side_effect=RuntimeError("view sync unavailable")), \
                    mock.patch.object(sys, "argv", ["reconcile", "--write-config", "--write-feishu"]), \
                    contextlib.redirect_stdout(stdout):
                self.assertEqual(0, source_reconcile.main())
            marker = [line for line in stdout.getvalue().splitlines() if line.startswith("SOURCE_PLAN_STATUS_JSON=")][-1]
            result = json.loads(marker.split("=", 1)[1])
            self.assertTrue(result["plan_ready"])
            self.assertTrue(result["optional_followup_failed"])
            self.assertIn("view sync unavailable", result["optional_followup_reason"])

    def test_wechat_machine_reason_has_priority(self) -> None:
        reason = daily_pipeline.machine_failure_reason(
            {"reason": "login_required", "error": "generic error"},
            {"stderr": "stderr fallback"},
            "fallback",
        )
        self.assertEqual(reason, "login_required")

    def test_successful_selected_retry_clears_superseded_primary_failure(self) -> None:
        step = {"returncode": 0, "optional_returncode": 3, "optional_failed": True}
        daily_pipeline.apply_selected_douyin_outcome(step, {
            "collection_status": "completed",
            "selected_artifact": "retry",
        })
        self.assertEqual(step["returncode"], 0)
        self.assertNotIn("optional_failed", step)
        self.assertEqual(step["selected_artifact"], "retry")


if __name__ == "__main__":
    unittest.main()
