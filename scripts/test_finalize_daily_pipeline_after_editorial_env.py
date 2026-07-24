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

    def exact_input(self, root: Path, run_id: str = "run_test") -> Path:
        run_dir = root / "runs" / run_id
        run_dir.mkdir(parents=True)
        path = run_dir / "today_10_topics.csv"
        path.write_text("内容指纹,选题标题,今日建议级别\nfp,测试,推荐制作\n", encoding="utf-8")
        (run_dir / "content_sampler_log.json").write_text(json.dumps({
            "run_id": run_id,
            "mode": "write-feishu",
            "outputs": {},
        }), encoding="utf-8")
        return path

    def test_zero_recommendation_finishes_before_env_or_external_call(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp) / "output"
            run_id = "run_20260724_080215"
            input_path = self.exact_input(out, run_id)
            input_path.write_text(
                "内容指纹,选题标题,今日建议级别\nfp,观察候选,暂存观察\n",
                encoding="utf-8",
            )
            with mock.patch.object(finalizer, "OUT", out), \
                    mock.patch.object(finalizer, "LOG_DIR", out / "logs"), \
                    mock.patch.object(finalizer, "load_local_env") as load, \
                    mock.patch.object(finalizer, "evaluate_preflight") as preflight, \
                    mock.patch.object(finalizer, "run_step") as run_step, \
                    mock.patch.object(finalizer, "update_pipeline_log", return_value=out / "log.json"):
                code, output = self.run_main([
                    "finalize_daily_pipeline_after_editorial.py",
                    "--run-id", run_id,
                    "--write-feishu",
                ])
            self.assertEqual(code, 0)
            self.assertIn("completed_no_recommendation", output)
            load.assert_not_called()
            preflight.assert_not_called()
            run_step.assert_not_called()

    def test_write_mode_loads_environment_once_before_preflight_and_writer(self) -> None:
        events: list[str] = []
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            input_path = self.exact_input(out)
            with mock.patch.object(finalizer, "OUT", out), \
                    mock.patch.object(finalizer, "LOG_DIR", out / "logs"), \
                    mock.patch.object(finalizer, "load_local_env", side_effect=lambda **kwargs: events.append(f"load:{kwargs['required']}")) as load, \
                    mock.patch.object(finalizer, "evaluate_preflight", side_effect=lambda *_args, **_kwargs: events.append("preflight") or {"ok": True}), \
                    mock.patch.object(finalizer, "run_step", side_effect=lambda name, _command: events.append(f"step:{name}") or {"name": name, "returncode": 0}), \
                    mock.patch.object(finalizer.daily_pipeline, "business_steps_ok", return_value=True), \
                    mock.patch.object(finalizer, "update_pipeline_log", return_value=out / "log.json"), \
                    mock.patch.object(finalizer.douyin_candidate_lifecycle, "mark_written_04"):
                result, _output = self.run_main([
                    "finalize_daily_pipeline_after_editorial.py", "--run-id", "run_test",
                    "--input", str(input_path), "--write-feishu",
                ])
        self.assertEqual(result, 0)
        load.assert_called_once_with(required=True)
        self.assertLess(events.index("load:True"), events.index("preflight"))
        self.assertLess(events.index("preflight"), next(i for i, event in enumerate(events) if event.startswith("step:")))

    def test_non_exact_input_stops_before_environment_or_external_call(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wrong = Path(tmp) / "today.csv"
            wrong.write_text("选题标题\n测试\n", encoding="utf-8")
            with mock.patch.object(finalizer, "OUT", Path(tmp) / "output"), \
                    mock.patch.object(finalizer, "load_local_env") as load, \
                    mock.patch.object(finalizer, "evaluate_preflight") as preflight, \
                    mock.patch.object(finalizer, "run_step") as run_step:
                with self.assertRaisesRegex(SystemExit, "exact run-scoped artifact"):
                    self.run_main([
                        "finalize_daily_pipeline_after_editorial.py", "--run-id", "run_test",
                        "--input", str(wrong), "--write-feishu",
                    ])
        load.assert_not_called()
        preflight.assert_not_called()
        run_step.assert_not_called()

    def test_missing_environment_is_typed_before_external_call(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            input_path = self.exact_input(out)
            with mock.patch.object(finalizer, "OUT", out), \
                    mock.patch.object(finalizer, "load_local_env", side_effect=SystemExit("No env file found")) as load, \
                    mock.patch.object(finalizer, "evaluate_preflight") as preflight, \
                    mock.patch.object(finalizer, "run_step") as run_step:
                result, output = self.run_main([
                    "finalize_daily_pipeline_after_editorial.py", "--run-id", "run_test",
                    "--input", str(input_path), "--write-feishu",
                ])
        payload = json.loads(output)
        self.assertEqual(result, 2)
        self.assertEqual(payload["reason"], "environment_not_loaded")
        load.assert_called_once_with(required=True)
        preflight.assert_not_called()
        run_step.assert_not_called()

    def test_missing_sampler_log_is_not_synthesized(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            input_path = self.exact_input(out)
            (input_path.parent / "content_sampler_log.json").unlink()
            with mock.patch.object(finalizer, "OUT", out), \
                    mock.patch.object(finalizer, "load_local_env"), \
                    mock.patch.object(finalizer, "evaluate_preflight", return_value={"ok": True}), \
                    mock.patch.object(finalizer, "run_step") as run_step:
                result, output = self.run_main([
                    "finalize_daily_pipeline_after_editorial.py", "--run-id", "run_test",
                    "--input", str(input_path), "--write-feishu",
                ])
        self.assertEqual(result, 2)
        self.assertEqual("exact_run_sampler_log_missing", json.loads(output)["reason"])
        run_step.assert_not_called()

    def test_non_write_mode_does_not_require_environment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            input_path = self.exact_input(out)
            with mock.patch.object(finalizer, "OUT", out), \
                    mock.patch.object(finalizer, "LOG_DIR", out / "logs"), \
                    mock.patch.object(finalizer, "load_local_env") as load, \
                    mock.patch.object(finalizer, "run_step", return_value={"name": "dry-run", "returncode": 0}), \
                    mock.patch.object(finalizer.daily_pipeline, "business_steps_ok", return_value=True), \
                    mock.patch.object(finalizer, "update_pipeline_log", return_value=out / "log.json"):
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
