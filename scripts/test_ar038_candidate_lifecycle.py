from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import douyin_candidate_lifecycle as lifecycle


class CandidateLifecycleTests(unittest.TestCase):
    def test_collected_failure_then_reviewed_terminal_cycle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifact = root / "artifact.json"
            fingerprint = "douyin_cdp_cycle"
            artifact.write_text(json.dumps({"内容指纹": fingerprint}), encoding="utf-8")
            ledger = root / "lifecycle.json"
            ledger.write_text(json.dumps({
                "schema_version": 1,
                "items": {fingerprint: {
                    "fingerprint": fingerprint,
                    "state": "collected_unreviewed",
                    "artifact_path": str(artifact),
                    "artifact_sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
                    "first_seen_run_id": "run_20260719_080000",
                    "first_seen_date": "2026-07-19",
                }},
            }), encoding="utf-8")
            self.assertEqual(lifecycle.validate_artifact(lifecycle.load_ledger(ledger)["items"][fingerprint])["内容指纹"], fingerprint)
            result = lifecycle.mark_reviewed_candidates(
                [{"content_fingerprint": fingerprint, "terminal_decision": "observe"}], run_id="run_20260720_080000", ledger_path=ledger,
            )
            self.assertEqual(result["updated"], [fingerprint])
            saved = lifecycle.load_ledger(ledger)["items"][fingerprint]
            self.assertEqual(saved["state"], "reviewed")
            self.assertEqual(saved["terminal_decision"], "observe")
            self.assertEqual(lifecycle.mark_reviewed_candidates(
                [{"content_fingerprint": fingerprint, "terminal_decision": "select"}], run_id="run_20260721_080000", ledger_path=ledger,
            )["updated"], [])
            self.assertEqual(lifecycle.mark_written_04([fingerprint], run_id="run_20260720_080000", ledger_path=ledger)["updated"], [fingerprint])
            self.assertEqual(lifecycle.load_ledger(ledger)["items"][fingerprint]["state"], "written_04")

    def test_canonical_fingerprint_maps_through_unique_business_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "lifecycle.json"
            ledger.write_text(json.dumps({"schema_version": 1, "items": {"source-fp": {
                "fingerprint": "source-fp", "state": "collected_unreviewed", "url": "https://douyin/video/1",
                "account": "account", "title": "title",
            }}}), encoding="utf-8")
            result = lifecycle.mark_reviewed_candidates([{
                "content_fingerprint": "canonical-fp", "exact_url": "https://douyin/video/1",
                "source_account": "account", "csv_title": "title", "terminal_decision": "reject",
            }], run_id="run_20260720_080000", ledger_path=ledger)
            self.assertEqual(result["updated"], ["source-fp"])
            self.assertEqual(lifecycle.load_ledger(ledger)["items"]["source-fp"]["canonical_fingerprint"], "canonical-fp")

    def test_missing_artifact_is_candidate_local(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            entry = {"fingerprint": "fp", "artifact_path": str(Path(tmp) / "missing.json"), "artifact_sha256": "x"}
            with self.assertRaisesRegex(RuntimeError, "missing_or_corrupt"):
                lifecycle.validate_artifact(entry)


if __name__ == "__main__":
    unittest.main()
