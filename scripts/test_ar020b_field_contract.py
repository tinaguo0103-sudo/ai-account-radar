#!/usr/bin/env python3
from __future__ import annotations

import unittest

import editorial_skill_runner
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


if __name__ == "__main__":
    unittest.main()
