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

    def test_aggregate_keeps_contract_and_title_failures_consistent(self) -> None:
        rows = []
        for index, title in enumerate([
            "我想用 Codex 测试知识库能不能进入选题复盘",
            "我想用 Mx-Shell 测试任务能不能进入交付验收",
            "我想用 Storyboard 测试分镜能不能进入返修流程",
        ]):
            rows.append({
                "内容指纹": f"fp_{index}",
                "来源类型": "对标视频",
                "原始来源标题": f"source {index}",
                "选题命题": title,
                "我的选题标题": title,
                "我要做的实验": "输入一条真实素材，测试并记录输出物。",
                "验证方式": "输入素材，输出记录表并检查通过/失败标准。",
                "推荐动作": "生成脚本包",
                "今日建议级别": "可选候选",
                "title_permission": "可发布标题",
                "可发布标题": title,
                "主编判断摘要": "这条来源来自对标账号，我会放进自己的工作流实验，但先保留证据边界。",
                "标题思路": "标题先说明来源触发的具体动作，但避免复述工具教程。",
            })
        args = SimpleNamespace(
            engine="codex",
            codex_model="",
            timeout=30,
            batch_timeout_seconds=3,
            batch_size=3,
            resume=False,
            since="2026-07-01",
            max_skill_candidates=3,
        )

        with TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            summary = replay.aggregate_replay_outputs(
                out_dir,
                args,
                csv_paths=[],
                items=[],
                pre={"candidates": rows, "pre_skill_pool": rows, "item_by_fp": {}},
                skill_rows=rows,
                engine_meta={"failed_batch_count": 0},
                engine="codex",
                completed=True,
            )
            with (out_dir / "skill_replay_rows.csv").open(encoding="utf-8-sig") as handle:
                replay_rows = list(csv.DictReader(handle))
            with (out_dir / "title_body_check.csv").open(encoding="utf-8-sig") as handle:
                title_rows = list(csv.DictReader(handle))
            with (out_dir / "skill_contract_failures.csv").open(encoding="utf-8-sig") as handle:
                failure_rows = list(csv.DictReader(handle))

        self.assertEqual(summary["contract_failure_count"], 3)
        self.assertEqual(summary["title_quality_failure_count"], 3)
        self.assertFalse(summary["quality_gate_ok"])
        self.assertEqual(sum(1 for row in replay_rows if row["field_contract_status"] == "fail"), 3)
        self.assertEqual(sum(1 for row in title_rows if row["title_quality_status"] == "fail"), 3)
        self.assertEqual(len(failure_rows), 3)
        self.assertTrue(all("生成脚本包标题" not in row["title_quality_issues"] for row in title_rows))
        self.assertTrue(all("阻止进入生成脚本包" in row["title_quality_issues"] for row in title_rows))

    def test_sample_summary_separates_internal_label_title_and_excerpt(self) -> None:
        rows = [{
            "内容指纹": "fp_kb",
            "原始来源标题": "Codex联动Obsidian，搭建超强知识库，手把手教程 用Codex+Obsidian 搭建可以“自生长”的知识库 帮你把信息的利用效率 直接拉高到next level 它能定时抓取热点 #AI新星计划 #知识库",
            "原始来源账号": "xuan酱",
            "选题命题": "Codex+Obsidian 搭知识库，最值钱的是留下为什么选它",
            "一句话Brief": "Brief",
            "我要做的实验": "实验",
            "我的工作流痛点": "痛点",
            "重点体现": "重点",
            "主编判断摘要": "来源是 Codex+Obsidian 知识库标题，我借工具组合入口，但落到选题判断留存。",
            "标题思路": "借原始标题里的工具组合和知识库结果承诺，改成 Austin 的选题台长期记忆。",
            "Austin改写理由": "保留 Codex+Obsidian 和知识库入口，舍弃手把手教程口吻。",
            "推荐动作": "生成脚本包",
            "今日建议级别": "今日最值得做",
            "对应方向": "真实工作流改造",
            "field_contract_status": "pass",
            "field_contract_issues": "",
            "fallback_only": "false",
        }]

        with TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            samples = replay.sample_rows(rows)
            replay.write_markdown_report(
                out_dir,
                {"engine": "codex", "outputs": {}, "content_items": 1, "candidate_count": 1, "pre_skill_pool_count": 1, "skill_rows": 1},
                samples,
            )
            markdown = (out_dir / "ar020c_user_sample_summary.md").read_text(encoding="utf-8")

        self.assertEqual(samples[0]["sample_label"], "知识库 / 信息资产")
        self.assertEqual(samples[0]["source_title"], "Codex联动Obsidian，搭建超强知识库，手把手教程")
        self.assertIn("工具组合", samples[0]["source_title_hook"])
        self.assertIn("原始标题：Codex联动Obsidian，搭建超强知识库，手把手教程", markdown)
        self.assertIn("原始标题钩子：工具组合 / 结果承诺 / 学习入口", markdown)
        self.assertIn("Austin rewrite reason: 保留 Codex+Obsidian", markdown)
        self.assertNotIn("knowledge_base |", markdown)
        self.assertNotIn("直接拉高到next level 它能定", markdown)


if __name__ == "__main__":
    unittest.main()
