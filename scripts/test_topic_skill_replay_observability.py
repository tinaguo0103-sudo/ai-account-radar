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

    def test_batch_meta_notes_reflect_final_guarded_rows(self) -> None:
        pool = [{"内容指纹": "fp_1", "原始来源标题": "Claude Cowork"}]
        args = SimpleNamespace(
            engine="codex",
            codex_model="",
            timeout=30,
            batch_timeout_seconds=3,
            batch_size=1,
            resume=False,
            since="2026-07-01",
            max_skill_candidates=1,
        )
        final_rows = [{
            "内容指纹": "fp_1",
            "选题命题": "Claude Cowork 的协作入口有价值，但还缺我的任务回写证据",
            "推荐动作": "暂存观察",
            "今日建议级别": "暂存观察",
            "field_contract_status": "pass",
            "title_quality_status": "pass",
        }]

        decision = {
            "index": 0,
            "editorial_decision_id": "decision_0",
            "editorial_decision_hash": "hash_0",
            "decision": "observe",
            "recommendation_status": "观察",
            "selected_visible_title": final_rows[0]["选题命题"],
            "natural_austin_angle": "协作入口还缺任务回写证据",
            "title_rationale": "先保留观察理由。",
            "public_decision_summary": "Claude Cowork 有协作入口，但证据还不足。",
            "locked_decision": "observe",
            "locked_recommendation_status": "观察",
            "locked_daily_level": "暂存观察",
            "locked_should_produce": "否",
            "locked_global_rank_position": "",
            "locked_global_tradeoff_reason": "证据不足，暂存观察。",
            "global_rank_id": "rank_0",
            "global_rank_hash": "rank_hash_0",
        }

        with TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            with patch.object(replay.editorial_skill_runner, "run_codex_stage1", return_value=(
                [decision],
                {"stage1_batch_notes": "stage1 ok", "provenance_manifest": {}},
            )), patch.object(replay.editorial_skill_runner, "run_codex_global_ranking", return_value=(
                [decision],
                {"global_top_count": 0, "status": "success", "outputs": {}},
            )), patch.object(replay.editorial_skill_runner, "run_codex_stage2", return_value=(
                final_rows,
                {"batch_notes": "本批只给 1 条“今日最值得做”：Claude Cowork。未调用外部 Skill。", "model": "codex-default"},
            )):
                rows, meta, _engine, ok = replay.run_skill_batches(pool, args, out_dir)
            batch_meta = json.loads((out_dir / "batches" / "batch_000" / "meta.json").read_text(encoding="utf-8"))

        self.assertTrue(ok)
        self.assertEqual(rows[0]["今日建议级别"], "暂存观察")
        self.assertIn("暂存观察=1", batch_meta["engine_meta"]["batch_notes"])
        self.assertIn("暂存观察=1", meta["batches"][0]["engine_meta"]["batch_notes"])
        self.assertIn("pre_guard_batch_notes", batch_meta["engine_meta"])
        self.assertNotIn("未调用外部 Skill", batch_meta["engine_meta"]["pre_guard_batch_notes"])
        self.assertIn("Codex exec 按嵌入的 ai-account-editorial-director", batch_meta["engine_meta"]["execution_note"])

    def test_aggregate_refreshes_batch_meta_from_final_rows(self) -> None:
        rows = [{
            "内容指纹": "fp_1",
            "选题命题": "Claude Cowork 的协作入口有价值，但还缺我的任务回写证据",
            "推荐动作": "暂存观察",
            "今日建议级别": "暂存观察",
            "field_contract_status": "pass",
            "title_quality_status": "pass",
        }]
        engine_meta = {
            "mode": "aggregate_existing_batches",
            "batch_count": 1,
            "completed_batch_count": 1,
            "failed_batch_count": 0,
            "batches": [{
                "batch_id": "batch_000",
                "status": "success",
                "row_count": 1,
                "engine_meta": {"batch_notes": "本批只给 1 条今日最值得做：Claude Cowork。未调用外部 Skill。"},
            }],
        }

        with TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            replay.batch_path(out_dir, "batch_000").mkdir(parents=True)
            replay.write_json(replay.batch_meta_path(out_dir, "batch_000"), engine_meta["batches"][0])
            refreshed = replay.refresh_engine_meta_with_final_rows(engine_meta, rows, out_dir)
            batch_meta = json.loads(replay.batch_meta_path(out_dir, "batch_000").read_text(encoding="utf-8"))
            batches_json = json.loads((out_dir / "skill_replay_batches.json").read_text(encoding="utf-8"))

        self.assertIn("暂存观察=1", refreshed["batches"][0]["engine_meta"]["batch_notes"])
        self.assertIn("暂存观察=1", batch_meta["engine_meta"]["batch_notes"])
        self.assertIn("暂存观察=1", batches_json["batches"][0]["engine_meta"]["batch_notes"])
        self.assertNotIn("今日最值得做：Claude Cowork", batch_meta["engine_meta"]["batch_notes"])

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

    def test_sample_rows_cover_user_review_categories_when_available(self) -> None:
        rows = [
            {"内容指纹": "kb", "原始来源标题": "Codex联动Obsidian，搭建超强知识库，手把手教程", "选题命题": "知识库判断留存", "推荐动作": "生成脚本包", "今日建议级别": "今日最值得做"},
            {"内容指纹": "story", "原始来源标题": "多宫格故事板2.0，出视频比你想的还简单", "选题命题": "故事板证据缺口", "推荐动作": "观察", "今日建议级别": "暂存观察"},
            {"内容指纹": "ppt", "原始来源标题": "Codex生成可编辑PPT，按这5步就够了", "选题命题": "Codex PPT 方案交付", "推荐动作": "观察", "今日建议级别": "暂存观察"},
            {"内容指纹": "desk", "原始来源标题": "Claude Cowork 的协作案例", "选题命题": "飞书选题台任务边界", "推荐动作": "观察", "今日建议级别": "暂存观察"},
            {"内容指纹": "video", "原始来源标题": "AI视频导演工作流", "选题命题": "AI视频导演交付", "推荐动作": "生成脚本包", "今日建议级别": "今日最值得做"},
            {"内容指纹": "hot", "原始来源标题": "MIRA：可玩多人世界模型，20 FPS实时生成", "选题命题": "AI Hot 观察", "推荐动作": "观察", "今日建议级别": "暂存观察"},
        ]

        samples = replay.sample_rows(rows)
        labels = {row["sample_label"] for row in samples}

        self.assertIn("知识库 / 信息资产", labels)
        self.assertIn("故事板 / 分镜观察", labels)
        self.assertIn("Codex PPT / 方案交付", labels)
        self.assertIn("Agent / 飞书执行台", labels)
        self.assertIn("AI导演 / 视频交付", labels)
        self.assertIn("AI Hot / 观察池", labels)


if __name__ == "__main__":
    unittest.main()
