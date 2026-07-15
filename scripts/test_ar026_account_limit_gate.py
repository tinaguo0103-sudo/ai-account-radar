#!/usr/bin/env python3
from __future__ import annotations

import contextlib
import io
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

import daily_pipeline
import full_account_collection_contract as contract
import run_daily_collection_job


MUTATIONS = (
    ("--douyin-account-limit", "1"),
    ("--douyin-account-limit", "3"),
    ("--douyin-account-limit", "12"),
    ("--douyin-account-limit", "31"),
    ("--douyin-account-limit", "-1"),
    ("--douyin-account-limit", "invalid"),
    ("--douyin-account-limit", ""),
    ("--douyin-account-limit",),
    ("--douyin-account-limit=12",),
    ("--douyin-account-limit", "0", "--douyin-account-limit", "0"),
)


class AccountLimitContractTests(unittest.TestCase):
    def test_only_omitted_or_exact_zero_is_accepted(self) -> None:
        self.assertTrue(contract.validate_account_limit_argv([]).ok)
        self.assertTrue(contract.validate_account_limit_argv(["--douyin-account-limit", "0"]).ok)
        for mutation in MUTATIONS:
            with self.subTest(mutation=mutation):
                gate = contract.validate_account_limit_argv(list(mutation))
                self.assertFalse(gate.ok)
                self.assertEqual(contract.rejection_payload("test", gate)["status"], "limited_plan_rejected")

    def _assert_outer_rejects_before_side_effects(self, mutation: tuple[str, ...]) -> None:
        stdout = io.StringIO()
        with mock.patch.object(sys, "argv", ["run_daily_collection_job.py", *mutation]), \
                mock.patch.object(run_daily_collection_job, "load_local_env") as load_env, \
                mock.patch.object(run_daily_collection_job, "run_step") as run_step, \
                mock.patch.object(run_daily_collection_job, "write_job_log") as write_log, \
                mock.patch.object(run_daily_collection_job, "notify") as notify, \
                contextlib.redirect_stdout(stdout):
            result = run_daily_collection_job.main()
        self.assertEqual(result, 2)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["status"], "limited_plan_rejected")
        self.assertFalse(payload["side_effects_started"])
        for sentinel in (load_env, run_step, write_log, notify):
            sentinel.assert_not_called()

    def _assert_daily_rejects_before_side_effects(self, mutation: tuple[str, ...]) -> None:
        stdout = io.StringIO()
        with mock.patch.object(sys, "argv", ["daily_pipeline.py", *mutation]), \
                mock.patch.object(daily_pipeline, "load_local_env") as load_env, \
                mock.patch.object(daily_pipeline, "require_feishu_env") as require_env, \
                mock.patch.object(daily_pipeline, "run_step") as run_step, \
                mock.patch.object(daily_pipeline, "run_optional_step") as optional_step, \
                mock.patch.object(daily_pipeline, "write_run_log") as write_log, \
                mock.patch.object(daily_pipeline, "write_douyin_cache_manifest") as cache_write, \
                mock.patch.object(daily_pipeline, "douyin_cache_ready") as cache_read, \
                contextlib.redirect_stdout(stdout):
            result = daily_pipeline.main()
        self.assertEqual(result, 2)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["status"], "limited_plan_rejected")
        self.assertFalse(payload["env_loaded"])
        for sentinel in (load_env, require_env, run_step, optional_step, write_log, cache_write, cache_read):
            sentinel.assert_not_called()

    def test_outer_rejects_cap_matrix_before_every_side_effect(self) -> None:
        for mutation in MUTATIONS:
            with self.subTest(mutation=mutation):
                self._assert_outer_rejects_before_side_effects(mutation)

    def test_daily_rejects_cap_matrix_before_every_side_effect(self) -> None:
        for mutation in MUTATIONS:
            with self.subTest(mutation=mutation):
                self._assert_daily_rejects_before_side_effects(mutation)

    def test_active_sources_have_no_subset_alias_or_environment_override(self) -> None:
        node = (Path(__file__).parent / "douyin_cdp_source_watch_probe.mjs").read_text(encoding="utf-8")
        daily = Path(daily_pipeline.__file__).read_text(encoding="utf-8")
        outer = Path(run_daily_collection_job.__file__).read_text(encoding="utf-8")
        active = "\n".join((node, daily, outer))
        self.assertNotIn("--only-account-names", active)
        self.assertNotIn("DOUYIN_ACCOUNT_LIMIT", active)
        self.assertNotIn("rows.slice(0", node)
        self.assertLess(daily.index("validate_account_limit_argv(sys.argv"), daily.index("load_local_env()"))
        self.assertLess(outer.index("validate_account_limit_argv(sys.argv"), outer.index("load_local_env()"))


if __name__ == "__main__":
    unittest.main()
