#!/usr/bin/env python3
from __future__ import annotations

import unittest

import content_sampler
import topic_flow_rework as flow


def item(
    source_type: str,
    title: str,
    body: str,
    *,
    account: str = "有效AI对标账号",
) -> content_sampler.ContentItem:
    return content_sampler.ContentItem(
        source_type=source_type,
        platform="AIHOT" if source_type == "AIHOT热点" else "抖音",
        account_name=account,
        title=title,
        url="https://example.com/item",
        content_shape="hotspot" if source_type == "AIHOT热点" else "short_video",
        cover_text="",
        body_snippet=body,
        published_at="2026-07-06",
        comment_questions="",
        ocr_text="",
        fetch_method="aihot_api" if source_type == "AIHOT热点" else "douyin_paraformer_transcript",
        fetch_status="ok",
        failure_reason="",
        fingerprint=flow.stable_hash([source_type, title, body]),
    )


class TopicFlowReworkTests(unittest.TestCase):
    def test_ordinary_aihot_is_low_weight_and_does_not_enter_review_pool(self) -> None:
        ordinary = item("AIHOT热点", "某 AI 公司融资", "普通公司动态和融资消息，没有可拍业务现场。")
        scene = content_sampler.choose_scene(content_sampler.item_text(ordinary))
        topic = content_sampler.topic_from_breakdown(content_sampler.breakdown(ordinary), ordinary)
        topic = content_sampler.editorial_judgement(topic, ordinary)

        self.assertEqual(flow.source_influence_weight(ordinary), flow.AIHOT_IMPORTANCE_WEIGHT)
        self.assertIn("普通 AI Hot", topic["AIHOT重大性说明"])
        self.assertFalse(content_sampler.include_in_skill_review_pool(topic))
        self.assertLessEqual(content_sampler.score_item(ordinary, scene), 64)

    def test_major_aihot_can_remain_reviewable_by_significance_not_quota(self) -> None:
        major = item("AIHOT热点", "GPT-5 发布新的 Agent API", "重大模型能力更新，影响 Agent 工作流、API 和视频生产。")
        topic = content_sampler.topic_from_breakdown(content_sampler.breakdown(major), major)
        topic = content_sampler.editorial_judgement(topic, major)

        self.assertTrue(flow.is_major_aihot(major))
        self.assertIn("重大 AI Hot", topic["AIHOT重大性说明"])

    def test_competitor_translation_fields_are_added_without_hardcoding_topic_whitelist(self) -> None:
        competitor = item("对标视频", "用 Codex 改造 PPT 工作流", "实战教程，评论很多人收藏，讲如何把 PPT、飞书和自动化流程串起来。")
        topic = content_sampler.topic_from_breakdown(content_sampler.breakdown(competitor), competitor)

        self.assertEqual(topic["来源权重类型"], "有效对标账号核心源")
        self.assertEqual(topic["来源影响权重"], "1.00")
        self.assertIn("有效AI对标账号", topic["市场验证依据"])
        self.assertIn("自己的", topic["Austin转译角度"])
        self.assertNotIn("必选", topic["Austin转译角度"])

    def test_reverse_evaluation_flags_unselected_high_fit_competitor(self) -> None:
        selected_item = item("对标视频", "普通候选", "工作流 自动化 工具 视频")
        missed_item = item("对标视频", "更适合Austin的候选", "Codex 飞书 工作流 自动化 PPT 复盘 实战 教程 评论 收藏")
        selected = [{
            "内容指纹": selected_item.fingerprint,
            "编辑判断分": "70",
            "人设匹配分": "62",
            "AI味风险": "低",
        }]
        candidates = [
            selected[0],
            {
                "内容指纹": missed_item.fingerprint,
                "编辑判断分": "82",
                "人设匹配分": "78",
                "AI味风险": "低",
                "不建议做的原因": "",
            },
        ]

        rows = flow.reverse_evaluation_rows(selected, candidates, {
            selected_item.fingerprint: selected_item,
            missed_item.fingerprint: missed_item,
        })

        flagged = [row for row in rows if row.potentially_better]
        self.assertEqual(len(flagged), 1)
        self.assertEqual(flagged[0].source_title, "更适合Austin的候选")


if __name__ == "__main__":
    unittest.main()
