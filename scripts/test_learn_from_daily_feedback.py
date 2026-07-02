#!/usr/bin/env python3
from __future__ import annotations

import unittest

from learn_from_daily_feedback import (
    is_test_table_name,
    markdown_report,
    script_feedback_sample,
    select_script_feedback,
    select_topic_samples,
    summarize,
    topic_sample,
)


class LearnFromDailyFeedbackTest(unittest.TestCase):
    def test_selects_pending_topic_samples(self) -> None:
        records = [
            {"record_id": "t1", "fields": {"状态": "生成脚本包", "学习状态": "待学习", "选题标题": "A", "选择原因标签": "真实痛点、可拍"}},
            {"record_id": "t2", "fields": {"状态": "待判断", "学习状态": "待学习", "选题标题": "B"}},
            {"record_id": "t3", "fields": {"状态": "不做", "学习状态": "已学习", "选题标题": "C"}},
        ]
        samples = select_topic_samples(records, include_learned=False)
        self.assertEqual([sample["record_id"] for sample in samples], ["t1"])
        self.assertEqual(topic_sample(records[0])["selection_tags"], ["真实痛点", "可拍"])

    def test_selects_pending_script_feedback(self) -> None:
        records = [
            {"record_id": "s1", "fields": {"脚本标题": "S", "人工质量反馈": "小修可拍", "质量问题标签": "不像我、标题弱", "内容学习状态": "待学习"}},
            {"record_id": "s2", "fields": {"脚本标题": "No feedback", "内容学习状态": "待学习"}},
            {"record_id": "s3", "fields": {"脚本标题": "Learned", "人工修改意见": "已处理", "内容学习状态": "已学习"}},
        ]
        samples = select_script_feedback(records, include_learned=False)
        self.assertEqual([sample["record_id"] for sample in samples], ["s1"])
        self.assertEqual(script_feedback_sample(records[0])["issue_tags"], ["不像我", "标题弱"])

    def test_summarize_and_markdown(self) -> None:
        topics = [
            {"record_id": "t1", "title": "A", "status": "生成脚本包", "is_positive": True, "selection_tags": ["真实痛点"], "direction": "AI业务", "reject_reason": "", "human_note": ""},
            {"record_id": "t2", "title": "B", "status": "不做", "is_positive": False, "selection_tags": ["太泛"], "direction": "AI工具", "reject_reason": "", "human_note": ""},
        ]
        scripts = [
            {"record_id": "s1", "title": "S", "quality": "小修可拍", "issue_tags": ["不像我"], "note": "补真实场景。"},
        ]
        summary = summarize(topics, scripts, "learn_test", "staging")
        self.assertEqual(summary["sample_count"], 3)
        self.assertTrue(summary["preference_rules"])
        report = markdown_report(summary)
        self.assertIn("学习反馈日结", report)
        self.assertIn("不像我", report)

    def test_test_table_name_detection(self) -> None:
        self.assertTrue(is_test_table_name("08 学习记录__测试"))
        self.assertTrue(is_test_table_name("08_learning_TEST"))
        self.assertFalse(is_test_table_name("08 学习记录"))


if __name__ == "__main__":
    unittest.main()
