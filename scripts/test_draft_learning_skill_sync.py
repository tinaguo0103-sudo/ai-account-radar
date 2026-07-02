#!/usr/bin/env python3
from __future__ import annotations

import unittest

from draft_learning_skill_sync import (
    classify_rule,
    markdown_draft,
    select_ready_records,
    summarize_for_draft,
)


class DraftLearningSkillSyncTest(unittest.TestCase):
    def test_selects_confirmed_pending_sync_records(self) -> None:
        records = [
            {"record_id": "r1", "fields": {"确认状态": "已采纳", "Skill同步状态": "待同步", "学习批次": "b1"}},
            {"record_id": "r2", "fields": {"确认状态": "部分采纳", "Skill同步状态": "待同步", "学习批次": "b2"}},
            {"record_id": "r3", "fields": {"确认状态": "暂不采纳", "Skill同步状态": "不同步", "学习批次": "b3"}},
            {"record_id": "r4", "fields": {"确认状态": "待确认", "Skill同步状态": "未同步", "学习批次": "b4"}},
        ]
        selected = select_ready_records(records)
        self.assertEqual([item["record_id"] for item in selected], ["r1", "r2"])

    def test_classifies_rules_conservatively(self) -> None:
        self.assertEqual(classify_rule("当反馈为需要重写时，必须先人工复核。"), "hard")
        self.assertEqual(classify_rule("选题更应关注真实业务痛点。"), "preference")
        self.assertEqual(classify_rule("标题可以更直接一点。"), "candidate")

    def test_builds_markdown_draft(self) -> None:
        records = [
            {
                "record_id": "r1",
                "fields": {
                    "学习批次": "learn_1",
                    "确认状态": "部分采纳",
                    "Skill同步状态": "待同步",
                    "样本数量": "3",
                    "选题样本数": "2",
                    "内容反馈样本数": "1",
                    "建议沉淀规则": "当 06 反馈为需要重写时，必须先人工复核。\n选题更应关注真实业务痛点。",
                    "不应沉淀的个案": "单条测试样本不进入长期规则。",
                    "确认备注": "只采纳选题偏好。",
                },
            },
        ]
        selected = select_ready_records(records)
        summary = summarize_for_draft(selected, "ai-account-editorial-director", "staging")
        draft = markdown_draft(summary)
        self.assertIn("Skill 同步草稿", draft)
        self.assertIn("必须先人工复核", draft)
        self.assertIn("更应关注真实业务痛点", draft)
        self.assertIn("只采纳选题偏好", draft)


if __name__ == "__main__":
    unittest.main()
