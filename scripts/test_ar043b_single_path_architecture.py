#!/usr/bin/env python3
from __future__ import annotations

import inspect
import unittest
from pathlib import Path

import canonical_owner_projection
import content_sampler
import daily_pipeline
import push_to_feishu
import scheduled_flow_preflight


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_FILES = (
    "scripts/run_daily_collection_job.py",
    "scripts/daily_pipeline.py",
    "scripts/content_sampler.py",
    "scripts/canonical_owner_projection.py",
    "scripts/finalize_daily_pipeline_after_editorial.py",
    "scripts/push_to_feishu.py",
    "scripts/push_today10_to_feishu.py",
    "scripts/verify_today10_feishu_consistency.py",
    "scripts/run_topic_card_if_fresh.py",
    "scripts/run_topic_decision_card_session.py",
    "scripts/feishu_topic_decision_card.py",
)
FORBIDDEN_RUNTIME_TEXT = (
    "load_reused_content_ledger",
    "enrich_reused_item_from_ledger",
    "record_request_telemetry",
    "optional_telemetry_unavailable",
    "sync_enriched_candidate_mirrors",
    "mirror_run_outputs",
    "recover-historical-participation-only",
    "recover-content-inbox-from-run",
    "latest_write",
    "latest_dry_run",
    "output/latest",
)


class AR043BSinglePathArchitectureTests(unittest.TestCase):
    def test_forbidden_runtime_surfaces_are_physically_absent(self) -> None:
        for relative in RUNTIME_FILES:
            text = (ROOT / relative).read_text(encoding="utf-8")
            for forbidden in FORBIDDEN_RUNTIME_TEXT:
                self.assertNotIn(forbidden, text, f"{forbidden} remains in {relative}")

    def test_removed_functions_are_not_importable(self) -> None:
        for module, names in (
            (content_sampler, (
                "load_reused_content_ledger",
                "enrich_reused_item_from_ledger",
                "mirror_run_outputs",
                "recover_content_inbox_from_run",
                "write_recovery_sampler_log",
            )),
            (daily_pipeline, ("sync_enriched_candidate_mirrors",)),
            (push_to_feishu, (
                "record_request_telemetry",
                "safe_write_feishu_request_telemetry",
                "write_feishu_request_telemetry",
            )),
        ):
            for name in names:
                self.assertFalse(hasattr(module, name), f"{module.__name__}.{name} remains reachable")

    def test_preflight_has_no_optional_observation_parameter(self) -> None:
        parameters = inspect.signature(scheduled_flow_preflight.evaluate_preflight).parameters
        self.assertNotIn("optional_telemetry_paths", parameters)

    def test_owner_projection_is_library_only(self) -> None:
        self.assertFalse(hasattr(canonical_owner_projection, "main"))
        self.assertFalse(hasattr(canonical_owner_projection, "atomic_json"))

    def test_card_and_finalizer_require_exact_run_artifacts(self) -> None:
        card = (ROOT / "scripts/run_topic_card_if_fresh.py").read_text(encoding="utf-8")
        finalizer = (ROOT / "scripts/finalize_daily_pipeline_after_editorial.py").read_text(encoding="utf-8")
        self.assertIn('RUNS_DIR / pipeline_run_id', card)
        self.assertIn('default_today_path(args.run_id).resolve()', finalizer)
        self.assertNotIn("recovered_ok", card)


if __name__ == "__main__":
    unittest.main()
