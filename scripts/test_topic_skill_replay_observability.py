#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from tempfile import TemporaryDirectory

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


if __name__ == "__main__":
    unittest.main()
