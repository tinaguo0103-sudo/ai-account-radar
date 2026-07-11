#!/usr/bin/env python3
from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import editorial_skill_runner as runner
import topic_field_contract as contract
import topic_skill_replay_evaluation as replay


def stage2_stub_row(decision: dict[str, str]) -> dict[str, str]:
    row = {field: "" for field in runner.SKILL_FIELDS}
    row.update({
        "editorial_decision_id": decision["editorial_decision_id"],
        "editorial_decision_hash": decision["editorial_decision_hash"],
        "global_rank_id": decision.get("global_rank_id", ""),
        "global_rank_hash": decision.get("global_rank_hash", ""),
        "locked_decision": decision.get("locked_decision", decision.get("decision", "")),
        "locked_recommendation_status": decision.get("locked_recommendation_status", decision.get("recommendation_status", "")),
        "locked_daily_level": decision.get("locked_daily_level", "可选候选"),
        "locked_should_produce": decision.get("locked_should_produce", "否"),
        "locked_global_rank_position": decision.get("locked_global_rank_position", ""),
        "locked_global_tradeoff_reason": decision.get("locked_global_tradeoff_reason", ""),
        "locked_selected_visible_title": decision["selected_visible_title"],
        "locked_natural_austin_angle": decision["natural_austin_angle"],
        "locked_title_rationale": decision["title_rationale"],
        "locked_public_decision_summary": decision["public_decision_summary"],
        "主编筛选": decision["decision"],
        "主编自由稿": decision["public_decision_summary"],
        "editorial_thinking_json": json.dumps(decision, ensure_ascii=False),
        "field_mapping_json": json.dumps({"mapped_from": decision["editorial_decision_id"]}, ensure_ascii=False),
        "主编判断摘要": decision["public_decision_summary"],
        "标题思路": decision["title_rationale"],
        "原始标题钩子": decision["source_title_hook"],
        "Austin改写理由": decision["source_hook_usage"],
        "title_permission": "可发布标题",
        "选题命题": decision["selected_visible_title"],
        "我的选题标题": decision["selected_visible_title"],
        "选题标题": decision["selected_visible_title"],
        "可发布标题": decision["selected_visible_title"],
        "标题备选": "",
        "我要做的实验": "输入来源素材，生成一版对比字段，并记录通过/失败标准。",
        "验证方式": "输入来源标题和摘要，输出主编判断与字段映射，检查标题是否保留市场入口。",
        "可沉淀资产": "标题钩子判断表",
        "我的工作流痛点": "旧选题容易把市场入口洗成内部任务。",
        "旧流程痛点": "过去先套字段再想标题，用户看起来像工单。",
        "AI介入点": "让主编 Skill 先判断，再映射字段。",
        "重点体现": "标题先像公开判断，再进入实验字段。",
        "对应方向": "真实工作流改造",
        "推荐动作": decision.get("locked_recommendation_status", "生成脚本包"),
        "今日建议级别": decision.get("locked_daily_level", "可选候选"),
        "候选状态": decision.get("locked_daily_level", "可选候选"),
        "是否建议进入制作": decision.get("locked_should_produce", "否"),
        "编辑判断分": "85",
        "标题质量分": "85",
        "证据强度": "中",
    })
    return row


class AR020DEditorialArchitectureTests(unittest.TestCase):
    def test_stage1_payload_excludes_old_visible_and_deterministic_hints(self) -> None:
        row = {
            "原始来源标题": "Codex联动Obsidian，搭建超强知识库，手把手教程",
            "原始来源账号": "AIGC自修室",
            "来源类型": "对标视频",
            "来源权重类型": "有效对标账号核心源",
            "Austin转译角度": "直接写成信息雷达",
            "主题簇": "知识库/内容资产流转",
            "我的工作流痛点": "旧字段不应进入 Stage 1",
            "我要做的实验": "旧实验不应进入 Stage 1",
            "验证方式": "旧验证不应进入 Stage 1",
            "可沉淀资产": "旧资产不应进入 Stage 1",
            "关联母场景": "旧母场景不应进入 Stage 1",
        }

        payload = runner.stage1_candidate_payload(row, 0)
        payload_text = json.dumps(payload, ensure_ascii=False)

        self.assertIn("source_facts", payload)
        self.assertIn("title_hook_reference", payload)
        self.assertIn("account_directions", payload)
        self.assertNotIn("旧字段不应进入 Stage 1", payload_text)
        self.assertNotIn("旧实验不应进入 Stage 1", payload_text)
        self.assertNotIn("旧验证不应进入 Stage 1", payload_text)
        self.assertNotIn("旧资产不应进入 Stage 1", payload_text)
        self.assertNotIn("旧母场景不应进入 Stage 1", payload_text)
        self.assertNotIn("Austin转译角度", payload)
        self.assertNotIn("主题簇", payload)

    def test_provenance_requires_embedded_persona_style_reference(self) -> None:
        manifest = runner.runtime_provenance(fallback_state="false")

        self.assertEqual(manifest["runner_version"], runner.RUNNER_VERSION)
        self.assertTrue(manifest["persona_style_embedded"])
        self.assertTrue(manifest["persona_style_reference_only"])
        self.assertEqual(manifest["persona_style_role"], "style_reference_only_not_source_evidence")
        self.assertTrue(str(manifest["persona_style_sha256"]))

    def test_editorial_decision_schema_has_no_case_anchor_field(self) -> None:
        schema_text = json.dumps(runner.editorial_decision_output_schema(), ensure_ascii=False)

        self.assertNotIn("case_anchor", schema_text)
        self.assertNotIn("案例支撑", schema_text)
        self.assertNotIn("可调用案例", schema_text)

    def test_stage2_invariant_blocks_title_or_hash_divergence(self) -> None:
        decision = runner.normalize_decision({
            "decision": "select",
            "why_i_would_choose": "来源的工具组合能接到我的选题台长期记忆。",
            "why_i_would_not_choose": "不能照抄教程标题。",
            "rejected_common_take": "普通讲法会讲手把手教程。",
            "natural_austin_angle": "知识库不是存资料，而是留下为什么选。",
            "title_directions": "Codex+Obsidian 入口 / 选题台长期记忆",
            "selected_visible_title": "Codex+Obsidian 真正该留下的，是每条选题为什么值得做",
            "title_rationale": "借工具组合和知识库承诺，转成选题判断留存。",
            "source_title_hook": "工具组合：Codex联动Obsidian",
            "source_hook_usage": "借工具组合，舍弃手把手教程。",
            "recommendation_status": "生成脚本包",
            "near_miss_reason": "",
            "public_decision_summary": "这条来源的市场入口是 Codex+Obsidian 知识库，我会把它转成选题台长期记忆。",
        }, 0, {"original_title": "Codex联动Obsidian"})
        mapped = stage2_stub_row(decision)
        mapped["选题命题"] = "被 Stage 2 改坏的标题"

        issues = runner.stage2_invariant_issues(decision, mapped)
        contract_issues = contract.validate_field_contract({**mapped, "stage2_invariant_status": "fail", "stage2_invariant_issues": "；".join(issues)})

        self.assertTrue(any("选题命题 diverged" in issue for issue in issues))
        self.assertTrue(any(issue.code == "stage2_decision_divergence" for issue in contract_issues))

    def test_stage2_invariant_blocks_selection_and_action_drift(self) -> None:
        decision = runner.normalize_decision({
            "decision": "select",
            "why_i_would_choose": "来源证据足够。",
            "why_i_would_not_choose": "不能变成工具教程。",
            "rejected_common_take": "普通讲法会讲教程。",
            "natural_austin_angle": "Agent 要留下任务记录。",
            "title_directions": "Agent / 记录",
            "selected_visible_title": "Agent真正有用的能力，是做完事以后留下可验收记录",
            "title_rationale": "借 Agent 热点，落到业务记录。",
            "source_title_hook": "Agent 能力",
            "source_hook_usage": "借能力入口，舍弃聊天工具教程。",
            "recommendation_status": "生成脚本包",
            "near_miss_reason": "",
            "public_decision_summary": "这条可以进入脚本包。",
        }, 0, {"original_title": "Agent真正有用的能力"})
        ranked = runner.apply_global_ranking(decision and [decision], [{
            "index": 0,
            "editorial_decision_id": decision["editorial_decision_id"],
            "editorial_decision_hash": decision["editorial_decision_hash"],
            "global_daily_level": "今日最值得做",
            "global_rank_position": "1",
            "global_tradeoff_reason": "今天证据最完整。",
        }])[0]
        mapped = stage2_stub_row(ranked)
        mapped["推荐动作"] = "暂存观察"
        mapped["今日建议级别"] = "暂存观察"
        mapped["是否建议进入制作"] = "否"

        issues = runner.stage2_invariant_issues(ranked, mapped)

        self.assertTrue(any("推荐动作 diverged" in issue for issue in issues))
        self.assertTrue(any("今日建议级别 diverged" in issue for issue in issues))
        self.assertTrue(any("是否建议进入制作 diverged" in issue for issue in issues))

    def test_reapply_locked_fields_restores_legacy_normalize_changes(self) -> None:
        decision = runner.normalize_decision({
            "decision": "reject",
            "why_i_would_choose": "来源有热度。",
            "why_i_would_not_choose": "这条没有 Austin 现场证据。",
            "rejected_common_take": "普通讲法会跟热点。",
            "natural_austin_angle": "不进入今天选题。",
            "title_directions": "不做",
            "selected_visible_title": "军事护栏争议值得知道，但不是今天的业务现场",
            "title_rationale": "借新闻入口，说明不进入制作。",
            "source_title_hook": "Claude 军事护栏",
            "source_hook_usage": "只借风险入口，不借军事话题。",
            "recommendation_status": "不做",
            "near_miss_reason": "缺少和账号现场的关系。",
            "public_decision_summary": "这条新闻有关注度，但和今天的账号业务现场距离太远，不进入制作。",
        }, 0, {"original_title": "Anthropic 与五角大楼控权之争"})
        ranked = runner.apply_global_ranking([decision], [{
            "index": 0,
            "editorial_decision_id": decision["editorial_decision_id"],
            "editorial_decision_hash": decision["editorial_decision_hash"],
            "global_daily_level": "不建议制作",
            "global_rank_position": "",
            "global_tradeoff_reason": "新闻影响力成立，不过今天没有 Austin 业务现场证据。",
        }])[0]
        row = stage2_stub_row(ranked)
        row["推荐动作"] = "放弃"
        row["今日建议级别"] = "暂存观察"
        row["是否建议进入制作"] = "是"

        restored = runner.reapply_locked_stage2_fields(row, ranked)

        self.assertEqual(restored["推荐动作"], "不做")
        self.assertEqual(restored["今日建议级别"], "不建议制作")
        self.assertEqual(restored["候选状态"], "不建议制作")
        self.assertEqual(restored["是否建议进入制作"], "否")
        self.assertEqual(runner.stage2_invariant_issues(ranked, restored), [])

    def test_stage1_prompt_forbids_internal_action_titles(self) -> None:
        prompt = runner.build_editorial_decision_prompt([{
            "原始来源标题": "做产品不要先堆功能，先找到购买理由",
            "来源内容": "产品增长和购买理由。",
        }])

        self.assertIn("先把购买理由讲清楚", prompt)
        self.assertIn("观察也要像公开判断", prompt)
        self.assertIn("来源钩子 + Austin 业务矛盾", prompt)

    def test_global_ranking_caps_top_today_across_batches(self) -> None:
        decisions = []
        ranking_rows = []
        for index in range(6):
            decision = runner.normalize_decision({
                "decision": "select",
                "why_i_would_choose": f"第 {index} 条有 Austin 现场。",
                "why_i_would_not_choose": "不能照搬。",
                "rejected_common_take": "普通讲法会做工具介绍。",
                "natural_austin_angle": f"第 {index} 条角度",
                "title_directions": f"方向 {index}",
                "selected_visible_title": f"第 {index} 条公开标题",
                "title_rationale": "来自来源钩子和个人场景。",
                "source_title_hook": "来源钩子",
                "source_hook_usage": "借入口不照抄。",
                "recommendation_status": "生成脚本包",
                "near_miss_reason": "",
                "public_decision_summary": "公开摘要。",
            }, index, {"original_title": f"source {index}"})
            decisions.append(decision)
            ranking_rows.append({
                "index": index,
                "editorial_decision_id": decision["editorial_decision_id"],
                "editorial_decision_hash": decision["editorial_decision_hash"],
                "global_daily_level": "今日最值得做" if index < 3 else "可选候选",
                "global_rank_position": str(index + 1) if index < 3 else "",
                "global_tradeoff_reason": "全日排序后取前三。" if index < 3 else "今天不进前三，但保留为可选。",
            })

        ranked = runner.apply_global_ranking(decisions, ranking_rows)

        self.assertEqual(sum(1 for item in ranked if item["locked_daily_level"] == "今日最值得做"), 3)
        self.assertEqual(sum(1 for item in ranked if item["locked_daily_level"] == "可选候选"), 3)
        self.assertEqual(ranked[0]["locked_should_produce"], "是")
        self.assertEqual(ranked[3]["locked_should_produce"], "否")
        self.assertEqual(ranked[3]["locked_recommendation_status"], "补证据")

    def test_global_ranking_locks_non_top_select_to_non_generation_action(self) -> None:
        decision = runner.normalize_decision({
            "decision": "select",
            "why_i_would_choose": "有 Austin 现场。",
            "why_i_would_not_choose": "证据还不够今天强推。",
            "rejected_common_take": "普通讲法会做工具介绍。",
            "natural_austin_angle": "Agent 价值要落到旧流程。",
            "title_directions": "Agent / 旧流程",
            "selected_visible_title": "Agent 的价值不是功能列表，而是替客户改掉旧流程",
            "title_rationale": "借购买理由入口，落到业务流程。",
            "source_title_hook": "购买理由",
            "source_hook_usage": "借判断，不照抄。",
            "recommendation_status": "生成脚本包",
            "near_miss_reason": "需要补证据。",
            "public_decision_summary": "来源的购买理由入口适合转成 Agent 业务表达，场景是客户旧流程改造；但今天证据弱于前三，需要先补案例再进入制作。",
        }, 0, {"original_title": "做产品不要先堆功能"})

        ranked = runner.apply_global_ranking([decision], [{
            "index": 0,
            "editorial_decision_id": decision["editorial_decision_id"],
            "editorial_decision_hash": decision["editorial_decision_hash"],
            "global_daily_level": "可选候选",
            "global_rank_position": "",
            "global_tradeoff_reason": "全日排序后证据弱于前三，需要补案例。",
        }])[0]

        self.assertEqual(ranked["locked_decision"], "select")
        self.assertEqual(ranked["locked_daily_level"], "可选候选")
        self.assertEqual(ranked["locked_recommendation_status"], "补证据")
        self.assertEqual(ranked["locked_should_produce"], "否")
        row = contract.apply_batch_quality_guards([stage2_stub_row(ranked)])[0]
        self.assertEqual(row["field_contract_status"], "pass")
        self.assertEqual(row["guard_blocked"], "false")

    def test_global_ranking_over_three_top_today_fails(self) -> None:
        decisions = []
        ranking_rows = []
        for index in range(4):
            decision = runner.normalize_decision({
                "decision": "select",
                "why_i_would_choose": "有现场。",
                "why_i_would_not_choose": "不能照搬。",
                "rejected_common_take": "工具介绍。",
                "natural_austin_angle": "角度",
                "title_directions": "方向",
                "selected_visible_title": f"标题 {index}",
                "title_rationale": "理由。",
                "source_title_hook": "钩子",
                "source_hook_usage": "借入口。",
                "recommendation_status": "生成脚本包",
                "near_miss_reason": "",
                "public_decision_summary": "摘要。",
            }, index, {"original_title": f"source {index}"})
            decisions.append(decision)
            ranking_rows.append({
                "index": index,
                "editorial_decision_id": decision["editorial_decision_id"],
                "editorial_decision_hash": decision["editorial_decision_hash"],
                "global_daily_level": "今日最值得做",
                "global_rank_position": str(index + 1),
                "global_tradeoff_reason": "错误地全部进前三。",
            })

        with self.assertRaisesRegex(RuntimeError, "maximum is 3"):
            runner.apply_global_ranking(decisions, ranking_rows)

    def test_resume_stage2_rows_must_match_current_global_rank_lock(self) -> None:
        decision = runner.normalize_decision({
            "decision": "select",
            "why_i_would_choose": "有现场。",
            "why_i_would_not_choose": "不能照搬。",
            "rejected_common_take": "工具介绍。",
            "natural_austin_angle": "角度",
            "title_directions": "方向",
            "selected_visible_title": "公开标题",
            "title_rationale": "理由。",
            "source_title_hook": "钩子",
            "source_hook_usage": "借入口。",
            "recommendation_status": "生成脚本包",
            "near_miss_reason": "",
            "public_decision_summary": "摘要。",
        }, 0, {"original_title": "source"})
        ranked = runner.apply_global_ranking([decision], [{
            "index": 0,
            "editorial_decision_id": decision["editorial_decision_id"],
            "editorial_decision_hash": decision["editorial_decision_hash"],
            "global_daily_level": "今日最值得做",
            "global_rank_position": "1",
            "global_tradeoff_reason": "全日前三。",
        }])[0]
        rows = [stage2_stub_row(ranked)]

        self.assertTrue(replay.completed_rows_match_rank_lock(rows, [ranked]))
        changed = {**ranked, "global_rank_hash": "new-rank-lock"}
        self.assertFalse(replay.completed_rows_match_rank_lock(rows, [changed]))

    def test_stage1_accepts_local_batch_indices_and_locks_global_index(self) -> None:
        row = {"原始来源标题": "Codex PPT", "来源内容": "Codex 做 PPT"}

        def fake_prompt(*, schema, prompt, model, timeout, artifact_dir, artifact_prefix):
            return {
                "engine": "stub",
                "batch_notes": "local index output",
                "editorial_decisions": [{
                    "index": 0,
                    "decision": "select",
                    "why_i_would_choose": "能接到交付现场。",
                    "why_i_would_not_choose": "不能照抄教程。",
                    "rejected_common_take": "普通讲法会讲五步教程。",
                    "natural_austin_angle": "PPT 不是生成页面，而是方案资产。",
                    "title_directions": "Codex PPT / 方案资产",
                    "selected_visible_title": "Codex 做 PPT，真正有用的是把 Word Brief 变成方案资产",
                    "title_rationale": "借 PPT 入口，转成方案交付。",
                    "source_title_hook": "Codex PPT",
                    "source_hook_usage": "借工具组合，舍弃教程感。",
                    "recommendation_status": "生成脚本包",
                    "near_miss_reason": "",
                    "public_decision_summary": "来源入口是 Codex 做 PPT，我会看它能不能变成方案资产。",
                }],
            }

        with patch.object(runner, "run_codex_prompt", side_effect=fake_prompt):
            decisions, _meta = runner.run_codex_stage1([row], "", 30, start_index=3)

        self.assertEqual(decisions[0]["index"], 3)
        self.assertTrue(decisions[0]["editorial_decision_id"].startswith("ar020d_decision_003_"))

    def test_guard_blocked_preserves_locked_selection_fields(self) -> None:
        decision = runner.normalize_decision({
            "decision": "select",
            "why_i_would_choose": "有现场。",
            "why_i_would_not_choose": "不能照搬。",
            "rejected_common_take": "工具介绍。",
            "natural_austin_angle": "角度",
            "title_directions": "方向",
            "selected_visible_title": "公开标题",
            "title_rationale": "理由。",
            "source_title_hook": "钩子",
            "source_hook_usage": "借入口。",
            "recommendation_status": "生成脚本包",
            "near_miss_reason": "",
            "public_decision_summary": "摘要。",
        }, 0, {"original_title": "source"})
        ranked = runner.apply_global_ranking([decision], [{
            "index": 0,
            "editorial_decision_id": decision["editorial_decision_id"],
            "editorial_decision_hash": decision["editorial_decision_hash"],
            "global_daily_level": "今日最值得做",
            "global_rank_position": "1",
            "global_tradeoff_reason": "唯一强候选。",
        }])[0]
        row = stage2_stub_row(ranked)
        row["editorial_architecture"] = runner.RUNNER_VERSION
        row["title_permission"] = "内部测试标题"

        guarded = contract.apply_batch_quality_guards([row])[0]

        self.assertEqual(guarded["推荐动作"], "生成脚本包")
        self.assertEqual(guarded["今日建议级别"], "今日最值得做")
        self.assertEqual(guarded["是否建议进入制作"], "是")
        self.assertEqual(guarded["guard_blocked"], "true")
        self.assertEqual(guarded["field_contract_status"], "fail")

    def test_quality_guards_clear_stale_guard_blocked_when_row_now_passes(self) -> None:
        row = {
            "选题命题": "Agent真正有用的能力，是做完事以后留下可验收记录",
            "我的选题标题": "Agent真正有用的能力，是做完事以后留下可验收记录",
            "选题标题": "Agent真正有用的能力，是做完事以后留下可验收记录",
            "一句话Brief": "这条把 Agent 从聊天入口拉回任务记录，适合业务现场。",
            "我要做的实验": "输入一段真实任务流，输出执行记录、责任人和验收字段。",
            "我的工作流痛点": "Agent 做完事后常常只剩聊天记录，后续复盘和交接断掉。",
            "旧流程痛点": "过去靠人手补记录，容易漏掉关键判断。",
            "AI介入点": "让 Agent 自动留下动作、判断和结果字段。",
            "验证方式": "用同一任务跑两次，对比是否能复盘责任和结果。",
            "可沉淀资产": "Agent 执行记录表",
            "我的思考点": "来源讲能力，我会看它能不能留下业务证据。",
            "重点体现": "不是能力清单，而是执行后可追溯。",
            "对应方向": "真实工作流改造",
            "推荐动作": "生成脚本包",
            "今日建议级别": "今日最值得做",
            "候选状态": "今日最值得做",
            "title_permission": "可发布标题",
            "可发布标题": "Agent真正有用的能力，是做完事以后留下可验收记录",
            "主编判断摘要": "来源证据是 Agent 能力讨论，我会转到 Austin 的任务复盘现场；取舍是保留能力入口，但不做泛泛工具介绍。",
            "标题思路": "借 Agent 能力入口，改成业务记录的公开判断。",
            "guard_blocked": "true",
            "guard_blocked_reason": "stale blocker",
        }

        guarded = contract.apply_batch_quality_guards([row])[0]

        self.assertEqual(guarded["field_contract_status"], "pass")
        self.assertEqual(guarded["guard_blocked"], "false")
        self.assertEqual(guarded["guard_blocked_reason"], "")

    def test_replay_runs_stage1_global_rank_then_stage2_across_batches(self) -> None:
        pool = [
            {
                "原始来源标题": f"测试来源 {index}",
                "来源内容": f"测试来源 {index} 的内容",
                "原始来源账号": "测试账号",
                "来源权重类型": "有效对标账号核心源",
                "内容指纹": f"fp-{index}",
            }
            for index in range(6)
        ]
        args = type("Args", (), {
            "engine": "codex",
            "batch_size": 2,
            "batch_timeout_seconds": 30,
            "timeout": 30,
            "resume": False,
            "codex_model": "",
        })()
        calls: list[str] = []

        def fake_stage1(rows, model, timeout, artifact_dir=None, start_index=0):
            calls.append(f"stage1:{start_index}")
            decisions = []
            for offset, row in enumerate(rows):
                index = start_index + offset
                decisions.append(runner.normalize_decision({
                    "decision": "select",
                    "why_i_would_choose": "有 Austin 现场。",
                    "why_i_would_not_choose": "不能照搬。",
                    "rejected_common_take": "普通讲法会做工具介绍。",
                    "natural_austin_angle": f"第 {index} 条角度",
                    "title_directions": "方向",
                    "selected_visible_title": f"第 {index} 条公开标题",
                    "title_rationale": "借来源钩子和个人场景。",
                    "source_title_hook": "来源钩子",
                    "source_hook_usage": "借入口不照抄。",
                    "recommendation_status": "生成脚本包",
                    "near_miss_reason": "",
                    "public_decision_summary": "公开摘要。",
                }, index, {"original_title": row["原始来源标题"]}))
            return decisions, {"provenance_manifest": runner.runtime_provenance(fallback_state="false")}

        def fake_ranking(rows, decisions, model, timeout, artifact_dir=None):
            calls.append("ranking")
            ranking_rows = []
            for index, decision in enumerate(decisions):
                ranking_rows.append({
                    "index": decision["index"],
                    "editorial_decision_id": decision["editorial_decision_id"],
                    "editorial_decision_hash": decision["editorial_decision_hash"],
                    "global_daily_level": "今日最值得做" if index < 3 else "可选候选",
                    "global_rank_position": str(index + 1) if index < 3 else "",
                    "global_tradeoff_reason": "全日前三。" if index < 3 else "全日排序后保留可选。",
                })
            ranked = runner.apply_global_ranking(decisions, ranking_rows)
            return ranked, {"global_top_count": 3, "status": "success", "outputs": {}}

        def fake_stage2(rows, decisions, model, timeout, artifact_dir=None):
            calls.append(f"stage2:{decisions[0]['index']}")
            return [stage2_stub_row(decision) for decision in decisions], {
                "provenance_manifest": runner.runtime_provenance(fallback_state="false"),
                "batch_notes": "stage2 ok",
            }

        with TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            with patch.object(runner, "run_codex_stage1", side_effect=fake_stage1), \
                patch.object(runner, "run_codex_global_ranking", side_effect=fake_ranking), \
                patch.object(runner, "run_codex_stage2", side_effect=fake_stage2):
                rows, meta, engine, completed = replay.run_skill_batches(pool, args, out_dir)

            self.assertTrue(completed)
            self.assertEqual(engine, "codex")
            self.assertEqual(len(rows), 6)
            self.assertEqual(sum(1 for row in rows if row["今日建议级别"] == "今日最值得做"), 3)
            self.assertEqual(calls, ["stage1:0", "stage1:2", "stage1:4", "ranking", "stage2:0", "stage2:2", "stage2:4"])
            self.assertEqual(meta["global_ranking"]["global_top_count"], 3)

    def test_observe_visible_task_shell_is_blocking_title_quality_issue(self) -> None:
        row = stage2_stub_row(runner.normalize_decision({
            "decision": "observe",
            "why_i_would_choose": "来源有市场入口。",
            "why_i_would_not_choose": "证据不足。",
            "rejected_common_take": "普通讲法会做工具体验。",
            "natural_austin_angle": "AI 入口需要业务流程证据。",
            "title_directions": "AI入口 / 业务流程证据",
            "selected_visible_title": "AI版支付宝开放公测，值得观察的是它会不会重写生活服务流程",
            "title_rationale": "借开放公测入口，观察业务流程证据。",
            "source_title_hook": "AI版支付宝 / 开放公测",
            "source_hook_usage": "借大产品入口，舍弃体验教程。",
            "recommendation_status": "观察",
            "near_miss_reason": "缺少真实流程证据。",
            "public_decision_summary": "这条只能观察，不能包装成可生成选题。",
        }, 0, {"original_title": "AI 版支付宝开放公测"}))
        row["推荐动作"] = "观察"
        row["今日建议级别"] = "暂存观察"
        guarded = contract.apply_batch_quality_guards([row])[0]

        self.assertEqual(guarded["title_quality_status"], "fail")
        self.assertIn("观察/补证据标题仍像内部测试任务", guarded["title_quality_issues"])

    def test_observe_public_judgment_with_colon_is_not_a_task_shell(self) -> None:
        row = {
            "选题命题": "MIRA让我看到AI视频下一步：从生成镜头到控制世界",
            "推荐动作": "暂存观察",
            "今日建议级别": "暂存观察",
            "候选状态": "暂存观察",
            "title_permission": "不生成标题",
            "主编判断摘要": "实时世界模型有想象力，但商业视频仍缺导演控制证据。",
        }
        guarded = contract.apply_batch_quality_guards([row])[0]
        self.assertEqual(guarded["title_quality_status"], "pass")
        self.assertEqual(guarded["title_quality_issues"], "")

    def test_run_codex_skill_two_stage_writes_artifacts_and_clears_case_fields(self) -> None:
        row = {
            "原始来源标题": "Codex联动Obsidian，搭建超强知识库，手把手教程",
            "来源内容": "Codex 联动 Obsidian，自生长知识库，自动整理复盘。",
            "原始来源账号": "AIGC自修室",
            "来源类型": "对标视频",
        }

        stage1_decision_holder: dict[str, str] = {}

        def fake_codex_prompt(*, schema, prompt, model, timeout, artifact_dir, artifact_prefix):
            if artifact_prefix.startswith("stage1"):
                payload = {
                    "engine": "stub",
                    "batch_notes": "stage1 ok",
                    "editorial_decisions": [{
                        "index": 0,
                        "decision": "select",
                        "why_i_would_choose": "这个工具组合能接到我的选题台长期记忆。",
                        "why_i_would_not_choose": "不能照抄手把手教程。",
                        "rejected_common_take": "普通工具号会讲搭库教程。",
                        "natural_austin_angle": "知识库不是存资料，而是留下为什么选。",
                        "title_directions": "Codex+Obsidian / 选题台长期记忆",
                        "selected_visible_title": "Codex+Obsidian 真正该留下的，是每条选题为什么值得做",
                        "title_rationale": "借工具组合和知识库承诺，转成选题判断留存。",
                        "source_title_hook": "工具组合：Codex联动Obsidian",
                        "source_hook_usage": "借工具组合，舍弃手把手教程。",
                        "recommendation_status": "生成脚本包",
                        "near_miss_reason": "",
                        "public_decision_summary": "来源的市场入口是 Codex+Obsidian 知识库，我会把它转成选题台长期记忆。",
                    }],
                }
                stage1_decision_holder.update(payload["editorial_decisions"][0])
                return payload
            if artifact_prefix.startswith("global_ranking"):
                decision = runner.normalize_decision(stage1_decision_holder, 0, runner.stage1_candidate_payload(row, 0))
                return {
                    "engine": "stub",
                    "global_ranking_notes": "global ranking ok",
                    "ranking_rows": [{
                        "index": 0,
                        "editorial_decision_id": decision["editorial_decision_id"],
                        "editorial_decision_hash": decision["editorial_decision_hash"],
                        "global_daily_level": "今日最值得做",
                        "global_rank_position": "1",
                        "global_tradeoff_reason": "唯一强候选。",
                    }],
                }
            decision = runner.normalize_decision(stage1_decision_holder, 0, runner.stage1_candidate_payload(row, 0))
            decision = runner.apply_global_ranking(decision and [decision], [{
                "index": 0,
                "editorial_decision_id": decision["editorial_decision_id"],
                "editorial_decision_hash": decision["editorial_decision_hash"],
                "global_daily_level": "今日最值得做",
                "global_rank_position": "1",
                "global_tradeoff_reason": "唯一强候选。",
            }])[0]
            return {
                "engine": "stub",
                "batch_notes": "stage2 ok",
                "rows": [{**stage2_stub_row(decision), "index": 0}],
            }

        with TemporaryDirectory() as tmp:
            artifact_dir = Path(tmp)
            with patch.object(runner, "run_codex_prompt", side_effect=fake_codex_prompt):
                rows, meta = runner.run_codex_skill([row], "", 30, artifact_dir=artifact_dir)

            self.assertTrue((artifact_dir / "stage1_input_sanitized.json").exists())
            self.assertTrue((artifact_dir / "stage2_input_sanitized.json").exists())
            self.assertTrue((artifact_dir / "ar020d_provenance_manifest.json").exists())

        self.assertEqual(rows[0]["stage2_invariant_status"], "pass")
        self.assertEqual(rows[0]["今日建议级别"], "今日最值得做")
        self.assertEqual(rows[0]["推荐动作"], "生成脚本包")
        self.assertEqual(rows[0]["真实/相邻案例"], "")
        self.assertEqual(rows[0]["可调用案例"], "")
        self.assertEqual(meta["stage_architecture"], "editorial_decision_global_ranking_then_field_mapping")
        self.assertEqual(meta["global_top_count"], 1)
        self.assertTrue(meta["provenance_manifest"]["persona_style_embedded"])

    def test_six_counterexample_payloads_stay_source_fact_only(self) -> None:
        titles = [
            "多宫格故事板2.0，出视频比你想的还简单",
            "Claude Cowork 的协作案例",
            "MIRA 实时世界模型 20 FPS",
            "Agent真正有用的能力，是做完事以后留下可验收记录",
            "Codex联动Obsidian，搭建超强知识库，手把手教程",
            "Codex生成可编辑PPT，按这5步就够了",
        ]
        for index, title in enumerate(titles):
            row = {
                "原始来源标题": title,
                "我的工作流痛点": "旧痛点不应进入 Stage 1",
                "我要做的实验": "旧实验不应进入 Stage 1",
                "关联母场景": "旧母场景不应进入 Stage 1",
                "Austin转译角度": "旧转译不应进入 Stage 1",
            }
            payload_text = json.dumps(runner.stage1_candidate_payload(row, index), ensure_ascii=False)
            self.assertIn(title[:8], payload_text)
            self.assertNotIn("旧痛点不应进入 Stage 1", payload_text)
            self.assertNotIn("旧实验不应进入 Stage 1", payload_text)
            self.assertNotIn("旧母场景不应进入 Stage 1", payload_text)
            self.assertNotIn("旧转译不应进入 Stage 1", payload_text)


if __name__ == "__main__":
    unittest.main()
