from __future__ import annotations

import json
import hashlib
import tempfile
import unittest
from pathlib import Path

import daily_pipeline


class AR037SourceContinuityTests(unittest.TestCase):
    def test_failed_sources_have_zero_rows_and_safe_candidates_continue(self) -> None:
        steps = []
        for source in ("wechat", "douyin"):
            step = {"name": f"{source} collection", "returncode": 4, "stderr": "unavailable"}
            daily_pipeline.isolate_source_failure(
                step, source=source, state="failed", reason="unavailable"
            )
            steps.append(step)
        with tempfile.TemporaryDirectory() as tmp:
            probe = Path(tmp) / "probe.json"
            probe.write_text(json.dumps({"coverage": {"failed_accounts": []}}), encoding="utf-8")
            result = daily_pipeline.downstream_usability_report(steps, Path(tmp), 3, probe)
        self.assertTrue(result["downstream_usable"])
        self.assertEqual("completed_with_failures", result["collection_status"])
        self.assertEqual([0, 0], [row["rows"] for row in result["isolated_source_failures"]])

    def test_failed_source_never_adds_an_artifact_path(self) -> None:
        step = {"name": "douyin collection", "returncode": 4}
        daily_pipeline.isolate_source_failure(step, source="douyin", state="failed")
        self.assertEqual(0, step["source_rows"])
        self.assertNotIn("selected_artifact", step)
        self.assertNotIn("cache", step)
        self.assertNotIn("retry", step)

    def test_exact_current_run_douyin_rows_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = root / "result.json"
            manual = root / "manual.jsonl"
            manual.write_text(json.dumps({
                "运行批次": "run_exact",
                "账号名/公众号名": "account",
                "候选时态": "today_new",
            }) + "\n", encoding="utf-8")
            result.write_text(json.dumps({
                "run_id": "run_exact",
                "status": "completed",
                "source_runtime_failure": None,
                "item_lineage": {"ok": True},
                "coverage": {"failed_accounts": []},
                "manual_artifact": {
                    "run_id": "run_exact",
                    "path": str(manual.resolve()),
                    "row_count": 1,
                    "sha256": hashlib.sha256(manual.read_bytes()).hexdigest(),
                },
                "candidate_lifecycle": {
                    "today_new_count": 1,
                    "historical_unreviewed_count": 0,
                },
            }), encoding="utf-8")
            self.assertEqual(1, daily_pipeline.current_douyin_rows(result, manual, "run_exact"))
            self.assertEqual(0, daily_pipeline.current_douyin_rows(result, manual, "run_other"))


if __name__ == "__main__":
    unittest.main()
