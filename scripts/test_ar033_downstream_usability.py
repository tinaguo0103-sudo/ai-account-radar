#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import daily_pipeline
import run_daily_collection_job


def write_probe(path: Path, *, failed_artifact_count: int = 0, planned: int = 31, attempted: int = 31) -> None:
    failed = 2
    success = attempted - failed
    payload = {
        "run_id": "run_20260716_080311",
        "status": "completed_with_failures",
        "coverage": {
            "planned_accounts": planned,
            "attempted_accounts": attempted,
            "successful_accounts": success,
            "failed_account_count": failed,
            "failed_accounts": [
                {"account_name": "铁锤人", "status": "partial_untrusted", "failure_reason": "isolated", "artifact_count": failed_artifact_count},
                {"account_name": "歸藏 guizang.ai", "status": "needs_login_or_verification", "failure_reason": "isolated", "artifact_count": 0},
            ],
            "per_account_artifact_counts": {"ok-a": 3, "ok-b": 3, "铁锤人": failed_artifact_count, "歸藏 guizang.ai": 0},
            "invariants": {
                "attempted_equals_planned": planned == attempted,
                "success_plus_failed_equals_attempted": success + failed == attempted,
                "account_lineage_unique_and_complete": True,
            },
        },
        "item_lineage": {"ok": True, "violation_count": 0},
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def base_steps() -> list[dict[str, object]]:
    return [
        {"name": "start/verify canonical Douyin Chrome CDP", "returncode": 0},
        {"name": "verify canonical Douyin profile login session", "returncode": 0},
        {
            "name": "fetch daily Douyin homepage title/caption samples through Chrome CDP",
            "returncode": 0,
            "optional_returncode": 3,
            "optional_failed": True,
        },
        {"name": "generate content breakdowns and 今日候选池", "returncode": 0},
    ]


class AR033DownstreamUsabilityTests(unittest.TestCase):
    @staticmethod
    def closure() -> dict[str, object]:
        return {
            "run_id": "run_20260716_080311", "manual_artifact_identity_verified": True,
            "combined_sha256": "a" * 64, "content_items_sha256": "b" * 64,
            "comparison_universe_count": 6,
            "feishu_03_identity": {"ok": True, "planned_identity": {"identity_sha256": "c" * 64}},
        }

    def test_29_of_31_isolated_partial_is_downstream_usable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            probe = Path(tmp) / "probe.json"
            write_probe(probe)
            result = daily_pipeline.downstream_usability_report(base_steps(), Path(tmp), 9, probe, self.closure())
        self.assertFalse(result["full_collection_success"])
        self.assertTrue(result["downstream_usable"])
        self.assertEqual(result["downstream_usable_reason"], "account_failures_isolated")
        self.assertEqual(result["isolated_failed_account_count"], 2)

    def test_failed_account_artifact_leak_blocks_downstream(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            probe = Path(tmp) / "probe.json"
            write_probe(probe, failed_artifact_count=1)
            result = daily_pipeline.downstream_usability_report(base_steps(), Path(tmp), 9, probe)
        self.assertFalse(result["downstream_usable"])
        self.assertIn("failed_accounts_have_zero_artifacts", result["downstream_blocked_reasons"])

    def test_missing_feishu_readback_cannot_be_replaced_by_unrelated_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            probe = Path(tmp) / "probe.json"
            write_probe(probe)
            result = daily_pipeline.downstream_usability_report(base_steps(), Path(tmp), 9, probe)
        self.assertFalse(result["downstream_usable"])
        self.assertTrue(result["downstream_usable_checks"]["today_candidates_nonempty"])
        self.assertIn("feishu_03_readback_contract_ok", result["downstream_blocked_reasons"])

    def test_incomplete_plan_login_fail_and_empty_candidates_block_downstream(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            probe = Path(tmp) / "probe.json"
            write_probe(probe, planned=31, attempted=30)
            no_login_steps = base_steps()
            no_login_steps[1] = {"name": "verify canonical Douyin profile login session", "returncode": 4}
            result = daily_pipeline.downstream_usability_report(no_login_steps, Path(tmp), 0, probe)
        self.assertFalse(result["downstream_usable"])
        self.assertFalse(result["downstream_diagnostics"]["canonical_profile_preflight_ok"])
        self.assertFalse(result["downstream_diagnostics"]["planned_equals_attempted"])
        self.assertIn("today_candidates_nonempty", result["downstream_blocked_reasons"])

    def test_outer_scheduled_log_carries_downstream_state_from_daily_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(run_daily_collection_job, "LOG_DIR", Path(tmp)):
            daily = Path(tmp) / "daily_pipeline_2026-07-16.json"
            with mock.patch.object(run_daily_collection_job, "datetime") as fake_datetime:
                fake_datetime.now.return_value.strftime.return_value = "2026-07-16"
                fake_datetime.now.return_value.isoformat.return_value = "2026-07-16T08:08:10"
                daily.write_text(json.dumps({
                    "run_id": "run_20260716_080311",
                    "full_collection_success": False,
                    "downstream_usable": True,
                    "downstream_usable_reason": "account_failures_isolated",
                    "today_candidates": 9,
                }), encoding="utf-8")
                path = run_daily_collection_job.write_job_log([
                    {"name": "run full-source daily pipeline", "returncode": 1},
                ])
            payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["status"], "failed_or_partial")
        self.assertFalse(payload["business_continuation_ok"])
        self.assertTrue(payload["downstream_usable"])
        self.assertEqual(payload["run_id"], "run_20260716_080311")

    def test_outer_log_keeps_partial_truth_when_entrypoint_can_continue(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(run_daily_collection_job, "LOG_DIR", Path(tmp)):
            daily = Path(tmp) / "daily_pipeline_2026-07-16.json"
            with mock.patch.object(run_daily_collection_job, "datetime") as fake_datetime:
                fake_datetime.now.return_value.strftime.return_value = "2026-07-16"
                fake_datetime.now.return_value.isoformat.return_value = "2026-07-16T08:08:10"
                daily.write_text(json.dumps({
                    "run_id": "run_20260716_080311",
                    "full_collection_success": False,
                    "collection_status": "completed_with_failures",
                    "downstream_usable": True,
                    "downstream_usable_reason": "source_failures_isolated",
                    "today_candidates": 9,
                }), encoding="utf-8")
                path = run_daily_collection_job.write_job_log([
                    {"name": "run full-source daily pipeline", "returncode": 0},
                ])
            payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertFalse(payload["ok"])
        self.assertTrue(payload["business_continuation_ok"])
        self.assertEqual("completed_with_failures", payload["status"])
        self.assertTrue(payload["downstream_usable"])


if __name__ == "__main__":
    unittest.main()
