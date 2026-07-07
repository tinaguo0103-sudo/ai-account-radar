#!/usr/bin/env python3
from __future__ import annotations

import unittest
from unittest.mock import patch

import editorial_skill_runner
import feishu_topic_decision_card
import push_today10_to_feishu
import topic_field_contract as contract


class AR020BFieldContractTests(unittest.TestCase):
    def test_compact_candidate_carries_source_governance_context(self) -> None:
        row = {
            "原始来源标题": "Codex联动Obsidian，搭建超强知识库",
            "来源权重类型": "有效对标账号核心源",
            "来源构成": "对标视频 / AIGC自修室",
            "原始来源账号": "AIGC自修室",
            "Austin转译角度": "转成 Austin 的信息雷达复盘",
            "AIHOT重大性说明": "",
        }

        payload = editorial_skill_runner.compact_candidate(row, 0)

        self.assertIn("source_governance_evidence", payload)
        self.assertIn("主编字段所有权", payload)
        self.assertIn("field_contract_guardrails", payload)
        self.assertIn("AIGC自修室", str(payload["source_governance_evidence"]))

    def test_skill_output_schema_includes_all_core_main_fields(self) -> None:
        required = {
            "editorial_thinking_json",
            "field_mapping_json",
            "主编判断摘要",
            "标题思路",
            "选题命题",
            "一句话Brief",
            "我要做的实验",
            "我的工作流痛点",
            "旧流程痛点",
            "AI介入点",
            "验证方式",
            "可沉淀资产",
            "我的思考点",
            "重点体现",
            "对应方向",
            "推荐动作",
            "今日建议级别",
            "title_permission",
            "可发布标题",
        }

        self.assertTrue(required.issubset(set(editorial_skill_runner.SKILL_FIELDS)))

    def test_compact_candidate_keeps_translation_hints_non_authoritative(self) -> None:
        row = {
            "原始来源标题": "Codex联动Obsidian，搭建超强知识库",
            "我的工作流痛点": "旧字段里错误残留分镜验收",
            "Austin转译角度": "转成 Austin 的信息雷达复盘",
            "主题簇": "知识库/内容资产流转",
        }

        payload = editorial_skill_runner.compact_candidate(row, 0)

        self.assertNotIn("Austin转译角度", payload)
        self.assertNotIn("我的工作流痛点", payload)
        self.assertIn("non_authoritative_hints", payload)
        self.assertIn("existing_fields_do_not_copy", payload)
        self.assertIn("信息雷达", str(payload["non_authoritative_hints"]))

    def test_knowledge_base_source_with_video_main_fields_is_downgraded(self) -> None:
        row = {
            "editorial_engine": "codex",
            "fallback_only": "false",
            "来源类型": "对标视频",
            "原始来源标题": "Codex联动Obsidian，搭建超强知识库",
            "来源内容": "Codex 和 Obsidian 资料、双链、知识库、内容资产",
            "选题命题": "检查信息雷达里的知识库资产是否能复用",
            "一句话Brief": "从知识库回到我的内容资产流转。",
            "我要做的实验": "拿一条素材测试从 03 收件箱到 04 选题和复盘记录。",
            "我的工作流痛点": "AI视频交付里分镜和成片验收太难复盘。",
            "旧流程痛点": "素材进来以后找不到后续判断。",
            "AI介入点": "让 AI 标记素材路径。",
            "验证方式": "输入一条素材，检查是否能写回选题和复盘记录。",
            "可沉淀资产": "素材路径记录表",
            "重点体现": "分镜和成片验收。",
            "对应方向": "真实工作流改造",
            "推荐动作": "生成脚本包",
            "今日建议级别": "今日最值得做",
            "title_permission": "内部测试标题",
        }

        normalized = editorial_skill_runner.normalize_batch([row])[0]

        self.assertEqual(normalized["field_contract_status"], "fail")
        self.assertIn("知识库/Obsidian/RAG", normalized["field_contract_issues"])
        self.assertEqual(normalized["今日建议级别"], "暂存观察")
        self.assertEqual(normalized["推荐动作"], "暂存观察")
        self.assertEqual(normalized["title_permission"], "不生成标题")

    def test_aihot_actionable_requires_major_news_and_austin_angle(self) -> None:
        row = {
            "来源类型": "AIHOT热点",
            "来源权重类型": "AI Hot 低权重热点源",
            "原始来源标题": "某 AI 公司日常融资消息",
            "选题命题": "泛 AI 行业消息",
            "我要做的实验": "测试这条消息能不能接进我的工作流。",
            "验证方式": "输入消息，检查是否能输出工作流影响判断。",
            "推荐动作": "生成脚本包",
            "今日建议级别": "可选候选",
            "title_permission": "内部测试标题",
            "对标转译角度": "",
            "AIHOT重大性说明": "",
        }

        issues = contract.validate_field_contract(row)

        self.assertTrue(any(issue.code == "aihot_actionable_without_major_evidence" for issue in issues))

    def test_generate_script_requires_executable_fields(self) -> None:
        row = {
            "来源类型": "对标视频",
            "原始来源标题": "AIGC 多宫格故事板",
            "选题命题": "多宫格故事板",
            "我要做的实验": "讲一讲这个工具",
            "验证方式": "看是否可用",
            "推荐动作": "生成脚本包",
            "title_permission": "不生成标题",
        }

        issues = contract.validate_field_contract(row)
        codes = {issue.code for issue in issues}

        self.assertIn("script_missing_experiment", codes)
        self.assertIn("script_missing_validation", codes)
        self.assertIn("script_title_not_ready", codes)

    def test_actionable_row_requires_public_editorial_trace(self) -> None:
        row = {
            "来源类型": "对标视频",
            "原始来源标题": "AIGC 多宫格故事板",
            "选题命题": "多宫格故事板先进入分镜返修验收",
            "我要做的实验": "拿一条短片 Brief 测试分镜返修验收。",
            "验证方式": "输入 Brief，输出分镜表并记录返修次数。",
            "推荐动作": "生成脚本包",
            "今日建议级别": "可选候选",
            "title_permission": "可发布标题",
            "可发布标题": "多宫格故事板先过一次返修验收",
            "主编判断摘要": "吸收它的选题承诺和结构，再转成自己的业务语言。",
            "标题思路": "用测试能不能的结构。",
        }

        issues = contract.validate_field_contract(row)

        self.assertTrue(any(issue.code == "generic_editorial_trace" for issue in issues))

    def test_batch_title_skeleton_collision_blocks_generated_candidates(self) -> None:
        rows = []
        for index, title in enumerate([
            "我想用 Codex 测试知识库能不能进入选题复盘",
            "我想用 Mx-Shell 测试任务能不能进入交付验收",
            "我想用 Storyboard 测试分镜能不能进入返修流程",
        ]):
            rows.append({
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

        guarded = contract.apply_batch_quality_guards(rows)

        self.assertTrue(all(row["field_contract_status"] == "fail" for row in guarded))
        self.assertTrue(all("标题骨架重复" in row["field_contract_issues"] or "标题里测试" in row["field_contract_issues"] for row in guarded))
        self.assertTrue(all(row["推荐动作"] == "暂存观察" for row in guarded))

    def test_hint_leak_without_skill_trace_is_blocked(self) -> None:
        row = {
            "来源类型": "对标视频",
            "原始来源标题": "Codex联动Obsidian，搭建超强知识库",
            "Austin转译角度": "转成 Austin 的信息雷达复盘，检查素材路径有没有留下来。",
            "选题命题": "转成 Austin 的信息雷达复盘，检查素材路径有没有留下来。",
            "一句话Brief": "转成 Austin 的信息雷达复盘，检查素材路径有没有留下来。",
            "我要做的实验": "拿一条素材测试路径回填。",
            "验证方式": "输入素材，输出路径记录并检查能否复用。",
            "推荐动作": "生成脚本包",
            "今日建议级别": "可选候选",
            "title_permission": "可发布标题",
            "可发布标题": "检查素材路径有没有留下来",
            "主编判断摘要": "这条来源来自对标账号，我会先看资料路径是否可复用，但不直接采用代码提示。",
            "标题思路": "标题写成资料路径复用的现场。",
        }

        issues = contract.validate_field_contract(row)

        self.assertTrue(any(issue.code == "hint_leak_without_skill_trace" for issue in issues))

    def test_deterministic_fallback_is_marked_not_quality_and_omitted_from_04(self) -> None:
        fallback = editorial_skill_runner.enrich({
            "来源内容": "普通来源",
            "对应栏目": "真实工作流改造",
            "推荐动作": "生成脚本包",
            "是否建议进入制作": "是",
            "今日建议级别": "今日最值得做",
        })

        visible, omitted = push_today10_to_feishu.feishu_visible_rows([fallback])

        self.assertEqual(fallback["fallback_only"], "true")
        self.assertEqual(fallback["not_editorial_quality"], "true")
        self.assertEqual(visible, [])
        self.assertEqual(omitted, 1)

    def test_04_writer_prefers_explicit_staging_topic_table_id(self) -> None:
        with patch.dict("os.environ", {"FEISHU_TOPIC_TABLE_ID": "tbl_ar020b_l3_test"}), \
                patch.object(push_today10_to_feishu, "list_tables", side_effect=AssertionError("list_tables should not be called")):
            table_id, source = push_today10_to_feishu.get_topic_table("token", "app")

        self.assertEqual(table_id, "tbl_ar020b_l3_test")
        self.assertEqual(source, "FEISHU_TOPIC_TABLE_ID")

    def test_04_writer_maps_skill_action_and_title_permission_to_feishu_fields(self) -> None:
        row = {
            "选题命题": "Codex 联动 Obsidian 后先测资料回流",
            "推荐动作": "补证据",
            "title_permission": "内部测试标题",
            "可发布标题": "",
            "今日建议级别": "可选候选",
            "AI味风险": "低",
            "对应方向": "真实工作流改造",
            "来源构成": "对标视频 / xuan酱",
            "一句话Brief": "测试资料能不能回到选题台。",
            "我要做的实验": "拿5条资料测试回流。",
        }

        mapped = push_today10_to_feishu.map_row(row, 1, "2026-07-07", "ar020b_l3")

        self.assertEqual(mapped["推荐动作"], "补证据")
        self.assertEqual(mapped["title_permission"], "内部测试标题")
        self.assertEqual(mapped["今日建议级别"], "可选候选")

    def test_04_writer_maps_editorial_trace_fields(self) -> None:
        row = {
            "选题命题": "Codex 联动 Obsidian 后先测资料回流",
            "推荐动作": "补证据",
            "title_permission": "内部测试标题",
            "今日建议级别": "可选候选",
            "主编判断摘要": "来源在讲知识库，我会先看它能不能进入自己的信息雷达；但还缺真实素材路径。",
            "标题思路": "标题先落在资料回流，不写成工具教程。",
        }

        mapped = push_today10_to_feishu.map_row(row, 1, "2026-07-07", "ar020c")

        self.assertIn("信息雷达", mapped["主编判断摘要"])
        self.assertIn("资料回流", mapped["标题思路"])
        self.assertIn("主编", mapped["卡片速读"])

    def test_topic_card_markdown_shows_editorial_trace_and_title_thinking(self) -> None:
        markdown = feishu_topic_decision_card.card_markdown_for_candidate(1, {
            "选题标题": "资料回流测试",
            "一句话Brief": "Brief",
            "主编判断摘要": "来源在讲知识库，我会看它能不能进入信息雷达；但还缺真实素材路径。",
            "标题思路": "标题落在资料回流，不写成工具教程。",
            "推荐动作": "补证据",
            "title_permission": "内部测试标题",
        })

        self.assertIn("主编：来源在讲知识库", markdown)
        self.assertIn("标题思路：标题落在资料回流", markdown)
        self.assertIn("不会进入下方", markdown)


if __name__ == "__main__":
    unittest.main()
