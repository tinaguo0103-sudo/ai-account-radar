#!/usr/bin/env python3
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

import finalize_daily_pipeline_after_editorial as finalizer
import local_env


class FinalizerEnvironmentTests(unittest.TestCase):
    def run_main(self, argv: list[str]) -> tuple[int, str]:
        with mock.patch.object(sys, "argv", argv), redirect_stdout(io.StringIO()) as output:
            result = finalizer.main()
        return result, output.getvalue()

    def test_write_mode_loads_environment_once_before_preflight_and_writer(self) -> None:
        events: list[str] = []
        with tempfile.TemporaryDirectory() as tmp:
            input_path = Path(tmp) / "today.csv"
            input_path.write_text("选题标题\n测试\n", encoding="utf-8")
            with mock.patch.object(finalizer, "load_local_env", side_effect=lambda **kwargs: events.append(f"load:{kwargs['required']}")) as load, \
                    mock.patch.object(finalizer.daily_pipeline, "sync_enriched_candidate_mirrors", side_effect=lambda *_args: events.append("sync")), \
                    mock.patch.object(finalizer, "evaluate_preflight", side_effect=lambda *_args, **_kwargs: events.append("preflight") or {"ok": True}), \
                    mock.patch.object(finalizer, "ensure_latest_sampler_log"), \
                    mock.patch.object(finalizer, "run_step", side_effect=lambda name, _command: events.append(f"step:{name}") or {"name": name, "returncode": 0}), \
                    mock.patch.object(finalizer.daily_pipeline, "business_steps_ok", return_value=True), \
                    mock.patch.object(finalizer, "update_pipeline_log", return_value=Path(tmp) / "log.json"), \
                    mock.patch.object(finalizer.douyin_candidate_lifecycle, "mark_written_04"):
                result, _output = self.run_main([
                    "finalize_daily_pipeline_after_editorial.py", "--run-id", "run_test",
                    "--input", str(input_path), "--write-feishu",
                ])

        self.assertEqual(result, 0)
        load.assert_called_once_with(required=True)
        self.assertLess(events.index("load:True"), events.index("sync"))
        self.assertLess(events.index("load:True"), events.index("preflight"))
        self.assertLess(events.index("preflight"), next(index for index, event in enumerate(events) if event.startswith("step:")))

    def test_missing_input_stops_before_environment_or_external_call(self) -> None:
        with mock.patch.object(finalizer, "load_local_env") as load, \
                mock.patch.object(finalizer, "evaluate_preflight") as preflight, \
                mock.patch.object(finalizer, "run_step") as run_step:
            with self.assertRaisesRegex(SystemExit, "Missing enriched topic CSV"):
                self.run_main([
                    "finalize_daily_pipeline_after_editorial.py", "--run-id", "run_test",
                    "--input", "/missing/ar041c.csv", "--write-feishu",
                ])
        load.assert_not_called()
        preflight.assert_not_called()
        run_step.assert_not_called()

    def test_missing_environment_is_typed_and_stops_before_sync_or_external_call(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            input_path = Path(tmp) / "today.csv"
            input_path.write_text("选题标题\n测试\n", encoding="utf-8")
            with mock.patch.object(finalizer, "load_local_env", side_effect=SystemExit("No env file found")) as load, \
                    mock.patch.object(finalizer.daily_pipeline, "sync_enriched_candidate_mirrors") as sync, \
                    mock.patch.object(finalizer, "evaluate_preflight") as preflight, \
                    mock.patch.object(finalizer, "run_step") as run_step:
                result, output = self.run_main([
                    "finalize_daily_pipeline_after_editorial.py", "--run-id", "run_test",
                    "--input", str(input_path), "--write-feishu",
                ])

        payload = json.loads(output)
        self.assertEqual(result, 2)
        self.assertEqual(payload["reason"], "environment_not_loaded")
        self.assertEqual(payload["external_calls"], 0)
        self.assertEqual(payload["business_writes"], 0)
        load.assert_called_once_with(required=True)
        sync.assert_not_called()
        preflight.assert_not_called()
        run_step.assert_not_called()

    def test_non_write_mode_does_not_require_environment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            input_path = Path(tmp) / "today.csv"
            input_path.write_text("选题标题\n测试\n", encoding="utf-8")
            with mock.patch.object(finalizer, "load_local_env") as load, \
                    mock.patch.object(finalizer.daily_pipeline, "sync_enriched_candidate_mirrors"), \
                    mock.patch.object(finalizer, "run_step", return_value={"name": "dry-run", "returncode": 0}), \
                    mock.patch.object(finalizer.daily_pipeline, "business_steps_ok", return_value=True), \
                    mock.patch.object(finalizer, "update_pipeline_log", return_value=Path(tmp) / "log.json"):
                result, _output = self.run_main([
                    "finalize_daily_pipeline_after_editorial.py", "--run-id", "run_test",
                    "--input", str(input_path),
                ])
        self.assertEqual(result, 0)
        load.assert_not_called()

    def test_explicit_staging_environment_does_not_fall_back_to_production(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            staging = root / ".env.staging.local"
            production = root / ".env.local"
            staging.write_text("AR041C_STAGE_SENTINEL=staging\n", encoding="utf-8")
            production.write_text("AR041C_PROD_SENTINEL=production\n", encoding="utf-8")
            clean_env = {
                key: value for key, value in os.environ.items()
                if key not in {"AI_ACCOUNT_RADAR_ENV_FILE", "ENV_FILE", "AI_ACCOUNT_RADAR_ENV", "AR041C_STAGE_SENTINEL", "AR041C_PROD_SENTINEL"}
            }
            clean_env["AI_ACCOUNT_RADAR_ENV_FILE"] = str(staging)
            with mock.patch.dict(os.environ, clean_env, clear=True):
                local_env.load_local_env(required=True)
                self.assertEqual(os.environ.get("AR041C_STAGE_SENTINEL"), "staging")
                self.assertNotIn("AR041C_PROD_SENTINEL", os.environ)


if __name__ == "__main__":
    unittest.main()
