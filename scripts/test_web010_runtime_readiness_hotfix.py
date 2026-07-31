from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

from daily_workflow import DailyWorkflow
from collected_artifact_adoption import adopt_collected_artifacts
from video_runtime_readiness import RuntimeReadinessError, check_runtime_readiness

SCRIPTS = Path(__file__).parent
ENTRYPOINT = SCRIPTS / "run_daily_workflow.py"


class RuntimeReadinessHotfixTest(unittest.TestCase):
    def valid_runtime(self, root: Path) -> tuple[Path, dict[str, Any]]:
        policy = root / "policy.json"
        policy.write_text(json.dumps({
            "schema_version": 1,
            "policy_id": "qa",
            "target_count_min": 0,
            "target_count_max": 1,
            "target_duration_seconds": 30,
            "maximum_duration_seconds": 60,
            "selection_contract": {},
            "models": {},
        }))
        ffmpeg = root / "ffmpeg"
        ffmpeg.write_text("#!/bin/sh\necho 'ffmpeg version qa'\n")
        vision = root / "vision"
        vision.write_text("#!/bin/sh\necho '[]'\n")
        python = root / "sensevoice-python"
        python.write_text("#!/bin/sh\necho 'sensevoice_runtime_ready'\n")
        for path in (ffmpeg, vision, python):
            path.chmod(0o700)
        sense = root / "sense"
        sense.mkdir()
        for name in (
            "model.pt", "config.yaml", "am.mvn", "tokens.json",
            "chn_jpn_yue_eng_ko_spectok.bpe.model",
        ):
            (sense / name).write_text("qa")
        vad = root / "vad"
        vad.mkdir()
        for name in ("model.pt", "config.yaml", "am.mvn"):
            (vad / name).write_text("qa")
        value = {
            "policy_path": str(policy),
            "ffmpeg": str(ffmpeg),
            "vision_ocr_binary": str(vision),
            "sensevoice_python": str(python),
            "sensevoice_model": str(sense),
            "fsmn_vad_model": str(vad),
            "readiness_probe_timeout_seconds": 3,
        }
        config = root / "runtime.json"
        config.write_text(json.dumps(value))
        return config, value

    def command(
        self,
        root: Path,
        config: str | None,
        *extra: str,
    ) -> subprocess.CompletedProcess[str]:
        env = dict(os.environ)
        env.pop("DOUYIN_VIDEO_RUNTIME_CONFIG", None)
        if config is not None:
            env["DOUYIN_VIDEO_RUNTIME_CONFIG"] = config
        return subprocess.run(
            [
                sys.executable, str(ENTRYPOINT),
                "--run-id", "run_20260729_080000",
                "--business-date", "2026-07-29",
                "--workflow-db", str(root / "workflow.sqlite3"),
                "--artifact-root", str(root / "runs"),
                *extra,
            ],
            cwd=SCRIPTS.parent,
            env=env,
            text=True,
            capture_output=True,
        )

    def test_public_collection_handoff_never_starts_second_discovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config, _ = self.valid_runtime(root)
            fixture = root / "collection.json"
            fixture.write_text(json.dumps({
                "run_id": "run_20260729_080000",
                "business_date": "2026-07-29",
                "status": "completed",
                "content_items": [{
                    "external_id": "safe-1", "source": "AIHOT",
                    "source_url": "https://example.com/safe-1", "title": "AI workflow",
                }],
                "candidates": [{
                    "candidate_id": "aihot:safe-1", "item_id": "aihot:safe-1",
                    "title": "AI workflow",
                }],
                "source_runs": [],
            }))
            result = self.command(
                root, str(config), "--collection-fixture", str(fixture),
                "--video-mode", "disabled",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            value = json.loads(result.stdout.strip().splitlines()[-1])
            self.assertEqual(value["action"], "editorial_required")
            self.assertEqual(value["candidate_count"], 1)
            handoff = json.loads(Path(value["handoff_path"]).read_text())
            self.assertEqual(len(handoff["candidates"]), 1)
            producer = root / "runs/run_20260729_080000/video_producer"
            self.assertFalse((producer / "discovery.json").exists())
            self.assertFalse((producer / "candidates.json").exists())

    def test_missing_empty_directory_and_invalid_json_fail_before_db(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cases: list[tuple[str | None, str]] = [
                (None, "video_runtime_config_missing"),
                ("  ", "video_runtime_config_missing"),
                (str(root), "video_runtime_config_not_file"),
                (str(root / "missing.json"), "video_runtime_config_missing"),
            ]
            invalid = root / "invalid.json"
            invalid.write_text("{")
            cases.append((str(invalid), "video_runtime_config_invalid_json"))
            for config, expected in cases:
                db = root / "workflow.sqlite3"
                db.unlink(missing_ok=True)
                result = self.command(root, config)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(json.loads(result.stdout)["error"], expected)
                self.assertFalse(db.exists())
                self.assertFalse((root / "runs").exists())

    def test_missing_field_and_runtime_unavailable_are_typed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            policy = root / "policy.json"
            policy.write_text("{}")
            config = root / "runtime.json"
            config.write_text(json.dumps({"policy_path": str(policy)}))
            with self.assertRaisesRegex(
                RuntimeReadinessError, "video_policy_missing_field:schema_version"
            ):
                check_runtime_readiness(str(config))
            policy.write_text(json.dumps({
                "schema_version": 1,
                "policy_id": "qa",
                "target_count_max": 1,
                "maximum_duration_seconds": 60,
                "selection_contract": {},
                "models": {},
            }))
            with self.assertRaisesRegex(RuntimeReadinessError, "video_ffmpeg_missing"):
                check_runtime_readiness(str(config))

            result = self.command(root, str(config))
            self.assertEqual(result.returncode, 2)
            self.assertEqual(json.loads(result.stdout)["error"], "video_ffmpeg_missing")
            self.assertFalse((root / "workflow.sqlite3").exists())

    def test_unreadable_config_is_typed_before_db(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "runtime.json"
            config.write_text("{}")
            config.chmod(0)
            try:
                result = self.command(root, str(config))
            finally:
                config.chmod(0o600)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(
                json.loads(result.stdout)["error"], "video_runtime_config_unreadable"
            )
            self.assertFalse((root / "workflow.sqlite3").exists())

    def test_public_adversarial_runtime_matrix_has_zero_effects(self):
        cases = (
            ("directory_executable", "video_ffmpeg_not_file"),
            ("non_executable", "video_ffmpeg_not_executable"),
            ("broken_symlink", "video_ffmpeg_broken_symlink"),
            ("probe_nonzero", "video_ffmpeg_probe_nonzero"),
            ("probe_timeout", "video_ffmpeg_probe_timeout"),
            ("empty_model", "video_asr_model_required_file_missing:model.pt"),
            ("missing_model_file", "video_asr_model_required_file_missing:tokens.json"),
        )
        for case, expected in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                config, value = self.valid_runtime(root)
                ffmpeg = Path(value["ffmpeg"])
                if case == "directory_executable":
                    value["ffmpeg"] = str(root)
                elif case == "non_executable":
                    ffmpeg.chmod(0o600)
                elif case == "broken_symlink":
                    ffmpeg.unlink()
                    ffmpeg.symlink_to(root / "missing-target")
                elif case == "probe_nonzero":
                    ffmpeg.write_text("#!/bin/sh\nexit 7\n")
                elif case == "probe_timeout":
                    ffmpeg.write_text("#!/bin/sh\nsleep 4\n")
                elif case == "empty_model":
                    for child in Path(value["sensevoice_model"]).iterdir():
                        child.unlink()
                elif case == "missing_model_file":
                    (Path(value["sensevoice_model"]) / "tokens.json").unlink()
                config.write_text(json.dumps(value))
                result = self.command(root, str(config))
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(json.loads(result.stdout)["error"], expected)
                self.assertFalse((root / "workflow.sqlite3").exists())
                self.assertFalse((root / "runs").exists())

    def test_preexisting_run_is_terminalized_and_can_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "workflow.sqlite3"
            flow = DailyWorkflow(path)
            flow.begin("run_20260729_080000", "2026-07-29")
            flow.db.close()
            result = self.command(root, None)
            self.assertEqual(result.returncode, 2)
            readback = DailyWorkflow(path)
            self.assertEqual(
                readback.read_run("run_20260729_080000")["run"]["status"],
                "failed_recoverable",
            )
            self.assertEqual(
                readback.begin("run_20260729_080000", "2026-07-29"), "resume"
            )
            self.assertEqual(
                readback.read_run("run_20260729_080000")["run"]["status"], "waiting"
            )

    def test_exact_artifact_adoption_recognizes_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_id = "run_20260729_080000"
            run_dir = root / "runs" / run_id
            run_dir.mkdir(parents=True)
            content = [
                {"内容标题": "A", "内容链接": "https://example.com/a", "平台": "AIHOT",
                 "内容指纹": "a"},
                {"内容标题": "B", "内容链接": "https://example.com/b", "平台": "AIHOT",
                 "内容指纹": "b"},
            ]
            breakdowns = [{"内容指纹": "a"}, {"内容指纹": "b"}]
            candidates = [{
                "可发布标题": "A angle", "来源链接": "https://example.com/a",
                "平台": "AIHOT", "内容指纹": "a",
            }]
            for name, rows in (
                ("content_items.csv", content),
                ("content_breakdowns.csv", breakdowns),
                ("today_10_topics.csv", candidates),
            ):
                with (run_dir / name).open("w", encoding="utf-8", newline="") as handle:
                    writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
                    writer.writeheader()
                    writer.writerows(rows)
            log = root / "daily.json"
            log.write_text(json.dumps({
                "run_id": run_id,
                "run_output_dir": f"/production/runs/{run_id}",
                "collection_status": "completed",
                "downstream_usable": True,
                "source_outcomes": [],
            }))
            args = type("Args", (), {
                "run_id": run_id,
                "business_date": "2026-07-29",
                "artifact_root": root / "runs",
                "adopt_collected_artifacts": run_dir,
                "adoption_log": log,
            })()
            value = adopt_collected_artifacts(args)
            self.assertEqual(value["adoption"], {
                "content_count": 2,
                "breakdown_count": 2,
                "candidate_count": 1,
                "collection_calls": 0,
            })

    def test_adoption_rejects_wrong_run_path_and_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            args = type("Args", (), {
                "run_id": "run_20260729_080000",
                "business_date": "2026-07-29",
                "artifact_root": root / "runs",
                "adopt_collected_artifacts": root / "runs/run_20260728_080000",
                "adoption_log": root / "missing.json",
            })()
            with self.assertRaisesRegex(Exception, "adoption_run_path_mismatch"):
                adopt_collected_artifacts(args)

    def test_adoption_log_and_csv_failures_are_typed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_id = "run_20260729_080000"
            run_dir = root / "runs" / run_id
            run_dir.mkdir(parents=True)
            for name in ("content_items.csv", "content_breakdowns.csv", "today_10_topics.csv"):
                (run_dir / name).write_text("identity\nvalue\n")
            log_path = root / "daily.json"
            args = type("Args", (), {
                "run_id": run_id,
                "business_date": "2026-07-29",
                "artifact_root": root / "runs",
                "adopt_collected_artifacts": run_dir,
                "adoption_log": log_path,
            })()
            base = {
                "run_id": run_id,
                "run_output_dir": f"/production/runs/{run_id}",
                "collection_status": "completed",
                "downstream_usable": True,
            }
            cases = (
                ({"run_id": "run_20260728_080000"}, "adoption_log_wrong_run"),
                ({"collection_status": "running"}, "adoption_collection_not_completed"),
                ({"downstream_usable": False}, "adoption_downstream_not_usable"),
            )
            for delta, expected in cases:
                log_path.write_text(json.dumps({**base, **delta}))
                with self.assertRaisesRegex(Exception, expected):
                    adopt_collected_artifacts(args)
            log_path.write_text(json.dumps(base))
            (run_dir / "content_breakdowns.csv").unlink()
            with self.assertRaisesRegex(
                Exception, "adoption_required_csv_missing:content_breakdowns.csv"
            ):
                adopt_collected_artifacts(args)


if __name__ == "__main__":
    unittest.main()
