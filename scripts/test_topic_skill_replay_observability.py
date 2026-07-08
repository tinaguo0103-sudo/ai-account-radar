#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from tempfile import TemporaryDirectory
from unittest.mock import patch

import topic_skill_replay_evaluation as replay


class TopicSkillReplayObservabilityTests(unittest.TestCase):
    def test_progress_and_error_artifacts_are_written_for_failed_replay(self) -> None:
        pool = [{
            "内容指纹": "fp_1",
            "原始来源标题": "Codex 联动 Obsidian",
            "原始来源账号": "AIGC自修室",
            "来源权重类型": "有效对标账号核心源",
            "主题簇": "知识库/内容资产流转",
        }]
        with TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            replay.write_progress(out_dir, pool, "queued_for_real_skill", "engine=codex; timeout=1s")
            payload = replay.write_error_artifacts(
                out_dir,
                SimpleNamespace(engine="codex", since="2026-07-01", timeout=1),
                "real_skill_replay",
                TimeoutError("codex exec timed out"),
                csv_paths=[Path("/tmp/missing_content_items.csv")],
                content_items=1,
                candidate_count=1,
                pre_skill_pool=pool,
            )

            with (out_dir / "skill_replay_progress.csv").open(encoding="utf-8-sig") as handle:
                progress_rows = list(csv.DictReader(handle))
            summary = json.loads((out_dir / "skill_replay_summary.json").read_text(encoding="utf-8"))
            error = json.loads((out_dir / "skill_replay_error.json").read_text(encoding="utf-8"))

        self.assertEqual(progress_rows[0]["status"], "queued_for_real_skill")
        self.assertFalse(payload["ok"])
        self.assertFalse(summary["ok"])
        self.assertEqual(error["stage"], "real_skill_replay")
        self.assertIn("skill_replay_progress.csv", error["outputs"]["skill_replay_progress"])
        self.assertIn("codex exec timed out", error["error"])

    def test_batched_replay_writes_batch_artifacts_and_aggregate(self) -> None:
        pool = [
            {"内容指纹": "fp_1", "原始来源标题": "AIGC 故事板", "推荐动作": "观察", "今日建议级别": "暂存观察"},
            {"内容指纹": "fp_2", "原始来源标题": "Codex Obsidian", "推荐动作": "观察", "今日建议级别": "暂存观察"},
            {"内容指纹": "fp_3", "原始来源标题": "Mx-Shell Skill", "推荐动作": "观察", "今日建议级别": "暂存观察"},
        ]
        args = SimpleNamespace(
            engine="deterministic",
            codex_model="",
            timeout=30,
            batch_timeout_seconds=3,
            batch_size=2,
            resume=False,
            since="2026-07-01",
            max_skill_candidates=3,
        )

        with TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            rows, meta, engine, ok = replay.run_skill_batches(pool, args, out_dir)
            summary = replay.aggregate_replay_outputs(
                out_dir,
                args,
                csv_paths=[],
                items=[],
                pre={"candidates": pool, "pre_skill_pool": pool, "item_by_fp": {}},
                skill_rows=rows,
                engine_meta=meta,
                engine=engine,
                completed=ok,
            )

            self.assertTrue((out_dir / "batches" / "batch_000" / "input.csv").exists())
            self.assertTrue((out_dir / "batches" / "batch_001" / "skill_rows.csv").exists())
            self.assertTrue((out_dir / "skill_replay_batches.json").exists())
            with (out_dir / "skill_replay_progress.csv").open(encoding="utf-8-sig") as handle:
                progress_rows = list(csv.DictReader(handle))

        self.assertTrue(ok)
        self.assertEqual(engine, "deterministic")
        self.assertEqual(len(rows), 3)
        self.assertEqual(summary["skill_rows"], 3)
        self.assertEqual(meta["batch_count"], 2)
        self.assertIn("batch_start", {row["status"] for row in progress_rows})
        self.assertIn("aggregate_success", {row["status"] for row in progress_rows})

    def test_resume_skips_completed_batches_and_runs_remaining(self) -> None:
        pool = [
            {"内容指纹": "fp_1", "原始来源标题": "completed"},
            {"内容指纹": "fp_2", "原始来源标题": "pending"},
        ]
        first_args = SimpleNamespace(
            engine="deterministic",
            codex_model="",
            timeout=30,
            batch_timeout_seconds=3,
            batch_size=1,
            resume=False,
            since="2026-07-01",
            max_skill_candidates=2,
        )
        resume_args = SimpleNamespace(**{**first_args.__dict__, "resume": True})

        with TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            with patch.object(replay, "run_skill", side_effect=[
                ([{"内容指纹": "fp_1", "原始来源标题": "completed"}], {"mode": "stub"}, "codex"),
                TimeoutError("first run stopped"),
            ]):
                rows, meta, _, ok = replay.run_skill_batches(pool, first_args, out_dir)

            self.assertFalse(ok)
            self.assertEqual(len(rows), 1)
            self.assertEqual(meta["completed_batch_count"], 1)
            self.assertEqual(meta["failed_batch_count"], 1)

            with patch.object(replay, "run_skill", return_value=(
                [{"内容指纹": "fp_2", "原始来源标题": "pending"}],
                {"mode": "stub"},
                "codex",
            )) as run_skill:
                rows, meta, _, ok = replay.run_skill_batches(pool, resume_args, out_dir)
            with (out_dir / "skill_replay_progress.csv").open(encoding="utf-8-sig") as handle:
                progress_rows = list(csv.DictReader(handle))

        self.assertTrue(ok)
        self.assertEqual(len(rows), 2)
        self.assertEqual(run_skill.call_count, 1)
        self.assertIn("batch_skip_completed", {row["status"] for row in progress_rows})


if __name__ == "__main__":
    unittest.main()
