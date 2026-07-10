#!/usr/bin/env python3
from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import editorial_skill_runner as runner
import topic_field_contract as contract


def stage2_stub_row(decision: dict[str, str]) -> dict[str, str]:
    row = {field: "" for field in runner.SKILL_FIELDS}
    row.update({
        "editorial_decision_id": decision["editorial_decision_id"],
        "editorial_decision_hash": decision["editorial_decision_hash"],
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
        "推荐动作": "生成脚本包",
        "今日建议级别": "可选候选",
        "是否建议进入制作": "是",
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
            decision = runner.normalize_decision(stage1_decision_holder, 0, runner.stage1_candidate_payload(row, 0))
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
        self.assertEqual(rows[0]["真实/相邻案例"], "")
        self.assertEqual(rows[0]["可调用案例"], "")
        self.assertEqual(meta["stage_architecture"], "editorial_decision_then_field_mapping")
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
