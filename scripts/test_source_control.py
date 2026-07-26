#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from source_control import SourceControl, SourceControlError


def account(index: int, *, name: str | None = None, identity: str | None = None):
    sid = identity or f"sec_{index}"
    return {
        "record_id": f"rec{index}",
        "display_name": name or f"账号{index}",
        "platform": "抖音",
        "channel_id": "抖音",
        "homepage_url": f"https://www.douyin.com/user/{sid}",
        "configured_identity": sid,
        "verified_identity": sid,
        "enabled": True,
        "participates_sampling": True,
        "priority": "medium",
        "fetch_method": "douyin_page_owned_xhr",
        "source_role": "current_aux_competitor",
    }


class SourceControlTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.service = SourceControl(Path(self.tmp.name) / "source.sqlite3")
        self.snapshot = self.service.import_accounts([account(index) for index in range(3)])

    def tearDown(self):
        self.tmp.cleanup()

    def test_plan_and_exact_command_rollback(self):
        plan = self.service.build_collection_plan()
        self.assertTrue(plan["ok"])
        self.assertEqual(plan["planned_douyin_accounts"], 3)
        self.assertEqual(plan["feishu_runtime_calls"], 0)
        source = self.snapshot["accounts"][0]
        result = self.service.apply_config_command("sourcecmd_abcdefgh", 1, [{
            "source_id": source["source_id"], "changes": {"priority": "high"},
        }])
        self.assertEqual(result["status"], "applied")
        self.assertEqual(result["applied_revision"], 2)
        duplicate = self.service.apply_config_command("sourcecmd_abcdefgh", 1, [{
            "source_id": source["source_id"], "changes": {"priority": "high"},
        }])
        self.assertEqual(duplicate, result)
        rollback = self.service.rollback_to_revision("sourcecmd_rollback1", 2, 1)
        self.assertEqual(rollback["applied_revision"], 3)
        restored = next(row for row in self.service.get_source_snapshot()["accounts"] if row["source_id"] == source["source_id"])
        self.assertEqual(restored["priority"], "medium")

    def test_identical_import_is_noop_and_conflict_preserves_state(self):
        path = self.service.path
        before = path.read_bytes()
        same = self.service.import_accounts([account(index) for index in range(3)])
        self.assertEqual(same["revision"], 1)
        self.assertEqual(same["count"], 3)
        self.assertEqual(path.read_bytes(), before)
        with self.assertRaisesRegex(SourceControlError, "source_import_identity_conflict"):
            self.service.import_accounts([account(0), account(1), account(3)])
        self.assertEqual(path.read_bytes(), before)

    def test_authority_identity_is_instance_and_database_exact(self):
        first = self.service.get_authority_identity("qa-primary")
        self.assertEqual(first["instance_id"], "qa-primary")
        self.assertEqual(first["authority"], "source_control_sqlite")
        other = SourceControl(Path(self.tmp.name) / "other.sqlite3")
        other.import_accounts([account(index) for index in range(3)])
        second = other.get_authority_identity("qa-primary")
        self.assertNotEqual(first["database_identity"], second["database_identity"])

    def test_stale_command_is_conflict_without_revision_churn(self):
        result = self.service.apply_config_command("sourcecmd_stale001", 9, [{
            "source_id": self.snapshot["accounts"][0]["source_id"], "changes": {"priority": "high"},
        }])
        self.assertEqual(result["status"], "conflict")
        self.assertEqual(self.service.current_revision(), 1)

    def test_health_is_event_derived_and_same_run_idempotent(self):
        source = self.snapshot["accounts"][0]
        event = {
            "source_id": source["source_id"], "attempted_at": "2026-07-26T08:00:00+00:00",
            "outcome": "failed", "failure_class": "douyin_works_response_timeout",
            "artifact_count": 0, "verified_identity": source["verified_identity"], "substitute_count": 0,
        }
        first = self.service.record_run_outcomes("run_20260726_080000", [event])
        second = self.service.record_run_outcomes("run_20260726_080000", [event])
        self.assertEqual(first["event_count"], second["event_count"])
        current = next(row for row in second["snapshot"]["accounts"] if row["source_id"] == source["source_id"])
        self.assertEqual(current["consecutive_failures"], 1)
        self.assertEqual(current["action_required"], 0)
        for day in (27, 28):
            event["attempted_at"] = f"2026-07-{day}T08:00:00+00:00"
            self.service.record_run_outcomes(f"run_202607{day}_080000", [event])
        current = next(row for row in self.service.get_source_snapshot()["accounts"] if row["source_id"] == source["source_id"])
        self.assertEqual(current["consecutive_failures"], 3)
        self.assertEqual(current["action_required"], 1)
        event.update({"attempted_at": "2026-07-29T08:00:00+00:00", "outcome": "success", "failure_class": "", "artifact_count": 2})
        self.service.record_run_outcomes("run_20260729_080000", [event])
        current = next(row for row in self.service.get_source_snapshot()["accounts"] if row["source_id"] == source["source_id"])
        self.assertEqual(current["consecutive_failures"], 0)
        self.assertEqual(current["action_required"], 0)

    def test_wrong_identity_substitute_and_cross_run_conflict_stop(self):
        source = self.snapshot["accounts"][0]
        base = {
            "source_id": source["source_id"], "attempted_at": "2026-07-26T08:00:00+00:00",
            "outcome": "success", "failure_class": "", "artifact_count": 1,
            "verified_identity": source["verified_identity"], "substitute_count": 0,
        }
        with self.assertRaisesRegex(SourceControlError, "wrong_run"):
            self.service.record_run_outcomes("bad", [base])
        bad = dict(base, verified_identity="other")
        with self.assertRaisesRegex(SourceControlError, "run_event_identity_mismatch"):
            self.service.record_run_outcomes("run_20260726_080001", [bad])
        bad = dict(base, substitute_count=1)
        with self.assertRaisesRegex(SourceControlError, "run_event_substitute_forbidden"):
            self.service.record_run_outcomes("run_20260726_080001", [bad])
        self.service.record_run_outcomes("run_20260726_080001", [base])
        with self.assertRaisesRegex(SourceControlError, "same_run_event_conflict"):
            self.service.record_run_outcomes("run_20260726_080001", [dict(base, artifact_count=2)])

    def test_normal_call_graph_uses_sqlite_not_feishu_or_json_health(self):
        root = Path(__file__).resolve().parents[1]
        wrapper = (root / "scripts/run_daily_collection_job.py").read_text()
        pipeline = (root / "scripts/daily_pipeline.py").read_text()
        probe = (root / "scripts/douyin_cdp_source_watch_probe.mjs").read_text()
        self.assertNotIn("reconcile_source_sampling_from_feishu.py", wrapper)
        self.assertNotIn("--write-config", wrapper)
        self.assertIn("source_control_cli.py", wrapper)
        self.assertIn("--source-db", pipeline)
        main_body = probe.split("async function main()", 1)[1]
        self.assertNotIn("persistAccountHealth(", main_body)
        self.assertNotIn("healthLedger", main_body)
        self.assertIn("source_control_cli.py", main_body)

    def test_douyin_risk_checkpoint_is_exact_and_resume_preserves_completed(self):
        accounts = self.snapshot["accounts"]
        rows = [
            {
                "source_id": accounts[0]["source_id"], "status": "completed",
                "artifact_sha256": "a" * 64, "artifact_count": 2, "ordinal": 0,
            },
            {
                "source_id": accounts[1]["source_id"], "status": "not_attempted_waiting_manual_verification",
                "artifact_sha256": "", "artifact_count": 0, "ordinal": 1,
            },
            {
                "source_id": accounts[2]["source_id"], "status": "not_attempted_waiting_manual_verification",
                "artifact_sha256": "", "artifact_count": 0, "ordinal": 2,
            },
        ]
        paused = self.service.record_douyin_checkpoint(
            "run_20260726_080000", "fixed_douyin_profile_9333", rows,
            risk_status="waiting_manual_verification",
            risk_reason="verification_required",
            preflight_state="verification_required",
            notification_status="sent",
        )
        self.assertEqual(paused["state"]["completed_count"], 1)
        self.assertEqual(paused["state"]["remaining_count"], 2)
        resumed = self.service.confirm_douyin_verification(
            "run_20260726_080000", "fixed_douyin_profile_9333", "session_verified"
        )
        self.assertEqual(resumed["state"]["status"], "resume_ready")
        self.assertEqual(
            [row["status"] for row in resumed["checkpoints"]],
            ["completed", "pending", "pending"],
        )
        duplicate = self.service.confirm_douyin_verification(
            "run_20260726_080000", "fixed_douyin_profile_9333", "session_verified"
        )
        self.assertEqual(duplicate["checkpoints"], resumed["checkpoints"])
        with self.assertRaisesRegex(SourceControlError, "completed_checkpoint_immutable"):
            self.service.record_douyin_checkpoint(
                "run_20260726_080000", "fixed_douyin_profile_9333",
                [dict(rows[0], artifact_sha256="b" * 64)],
                risk_status="running",
            )

    def test_douyin_verification_requires_exact_profile_and_green_preflight(self):
        account_row = self.snapshot["accounts"][0]
        self.service.record_douyin_checkpoint(
            "run_20260726_080001", "fixed_douyin_profile_9333",
            [{
                "source_id": account_row["source_id"], "status": "not_attempted_waiting_manual_verification",
                "artifact_sha256": "", "artifact_count": 0, "ordinal": 0,
            }],
            risk_status="waiting_manual_verification",
        )
        waiting = self.service.confirm_douyin_verification(
            "run_20260726_080001", "fixed_douyin_profile_9333", "login_preflight_failed"
        )
        self.assertEqual(waiting["state"]["status"], "waiting_manual_verification")
        with self.assertRaisesRegex(SourceControlError, "douyin_profile_identity_mismatch"):
            self.service.confirm_douyin_verification(
                "run_20260726_080001", "other_profile", "session_verified"
            )


if __name__ == "__main__":
    unittest.main()
