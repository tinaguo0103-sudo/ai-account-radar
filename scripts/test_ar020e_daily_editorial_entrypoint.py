#!/usr/bin/env python3
from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import ar020e_daily_editorial_entrypoint as entrypoint


class AR020EDailyEditorialEntrypointTests(unittest.TestCase):
    def test_check_only_is_read_only_and_current_task(self) -> None:
        result = entrypoint.check_readiness("run_20260714_091500", None)
        self.assertTrue(result["ok"])
        self.assertTrue(result["check_only"])
        self.assertEqual(result["execution_surface"], "current_codex_task")
        self.assertFalse(result["nested_model_execution"])
        self.assertFalse(result["writes_feishu"])
        self.assertFalse(result["sends_topic_card"])
        self.assertEqual(result["dynamic_ranking"], "0..N_no_cap")

    def test_input_must_be_nonempty_and_match_run(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "output" / "runs" / "run_other" / "today_10_topics.csv"
            path.parent.mkdir(parents=True)
            path.write_text("title\n", encoding="utf-8")
            result = entrypoint.check_readiness("run_20260714_091500", path)
            self.assertIn("empty_input_csv", result["failures"])
            self.assertIn("input_run_id_mismatch", result["failures"])

    def test_forbidden_legacy_protocol_fails(self) -> None:
        with TemporaryDirectory() as tmp:
            protocol = Path(tmp) / "protocol.md"
            protocol.write_text("run codex exec", encoding="utf-8")
            with patch.object(entrypoint, "PROTOCOL_PATH", protocol):
                result = entrypoint.check_readiness("run_20260714_091500", None)
            self.assertFalse(result["ok"])
            self.assertTrue(any("forbidden_protocol_text" in value for value in result["failures"]))


if __name__ == "__main__":
    unittest.main()
