#!/usr/bin/env python3
from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

import pre_merge_check


class PreMergeCheckSafetyTest(unittest.TestCase):
    def test_topic_card_guard_refuses_to_probe_from_production_root(self) -> None:
        production_root = Path("/tmp/ai_account_radar")

        def fail_if_called(*_args, **_kwargs):
            raise AssertionError("pre_merge_check must not call the Topic Card sender from production")

        with patch.object(pre_merge_check, "ROOT", production_root), \
                patch.object(pre_merge_check, "PRODUCTION_ROOT", production_root), \
                patch.object(pre_merge_check, "run", side_effect=fail_if_called):
            result = pre_merge_check.check_topic_card_guard()

        self.assertFalse(result["ok"])
        self.assertEqual(result["returncode"], 2)
        self.assertIn("Refusing to run Topic Card guard probe", result["stderr"])


if __name__ == "__main__":
    unittest.main()
