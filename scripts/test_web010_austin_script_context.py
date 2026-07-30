from __future__ import annotations

import json
import unittest

import run_daily_workflow as workflow


class AustinScriptContextTest(unittest.TestCase):
    def fixture(self):
        collection = {
            "candidates": [
                {
                    "candidate_id": "douyin:1",
                    "source_url": "https://www.douyin.com/video/1",
                    "aweme_id": "1",
                    "title": "来源标题",
                    "author": "公开作者",
                    "likes": 0,
                    "likes_display": "0",
                    "published_at_display": "2小时前",
                    "published_recency": {"minimum_seconds": 7200, "maximum_seconds": 10800},
                    "fact_provenance": {"capture": "visible_card"},
                    "fact_missing_reasons": {"comments": "field_not_visible"},
                    "comments": None,
                    "production_direction": "从责任冲突切入",
                },
                {
                    "candidate_id": "douyin:2",
                    "source_url": "https://www.douyin.com/video/2",
                    "title": "缺理解包的来源",
                },
            ],
            "understanding_results": [{
                "candidate_id": "douyin:1",
                "package": {
                    "status": "completed",
                    "caption_timeline": [{"start": 0, "text": "真实字幕"}],
                    "asr": {"text": "语音补充"},
                    "screen_text": [{"kind": "tool_name", "value": "Codex", "verified": True}],
                    "keyframes": [{"time_second": 3, "sha256": "frame-only-diagnostic"}],
                    "unresolved_terms": ["专名待核验"],
                    "failures": [],
                },
            }],
        }
        editorial = {
            "topics": [
                {
                    "candidate_id": "douyin:1",
                    "decision": "select",
                    "title": "责任边界",
                    "hook": "一个具体冲突",
                    "structure": "责任链推进",
                    "selection_reason": "符合 Austin 的工作流判断",
                    "persona_fit": "可讲责任与验收",
                    "fact_boundary": "点赞为真实0；评论未知",
                    "cannot_claim": "不能声称已经替客户验证",
                    "human_supplement": "只讲建议测试，不写亲历",
                },
                {
                    "candidate_id": "douyin:2",
                    "decision": "select",
                    "title": "有限材料",
                    "hook": "材料不足时如何讲",
                    "structure": "明确限制",
                    "selection_reason": "保留观察价值",
                    "fact_boundary": "只有公开标题和链接",
                },
            ],
        }
        return collection, editorial

    def test_rich_handoff_preserves_facts_boundaries_and_optional_absence(self):
        collection, editorial = self.fixture()
        value = workflow.build_scripts_handoff(
            "run_20260730_120000", "2026-07-30", collection, editorial
        )
        self.assertEqual(value["action"], "scripts_required")
        self.assertEqual(len(value["selected_topics"]), 2)
        first, second = value["selected_topics"]
        self.assertEqual(first["source_facts"]["likes"], 0)
        self.assertNotIn("comments", first["source_facts"])
        self.assertEqual(first["video_understanding"]["asr_supplement"], "语音补充")
        self.assertEqual(first["video_understanding"]["screen_facts"][0]["value"], "Codex")
        self.assertEqual(first["fact_boundary"], "点赞为真实0；评论未知")
        self.assertEqual(first["cannot_claim"], "不能声称已经替客户验证")
        self.assertEqual(first["production_direction"], "从责任冲突切入")
        self.assertIsNone(second["video_understanding"])
        self.assertIsNone(second["persona_fit"])
        self.assertIsNone(second["cannot_claim"])
        self.assertNotIn("personal_scene", json.dumps(value, ensure_ascii=False))

    def test_failed_video_package_is_not_promoted_to_completed_context(self):
        collection, editorial = self.fixture()
        collection["understanding_results"][0]["package"] = {
            "status": "failed",
            "failures": ["media_unavailable"],
        }
        value = workflow.build_scripts_handoff("run_x", "2026-07-30", collection, editorial)
        self.assertIsNone(value["selected_topics"][0]["video_understanding"])

    def test_batch_identity_mapping_covers_every_selected_topic_once(self):
        collection, editorial = self.fixture()
        handoff = workflow.build_scripts_handoff(
            "run_batch", "2026-07-30", collection, editorial
        )
        selected = {row["topic_id"] for row in handoff["selected_topics"]}
        scripts = {
            "run_id": "run_batch",
            "scripts": [
                {
                    "topic_id": row["topic_id"],
                    "title": row["title"],
                    "hook": row["hook"],
                    "structure": row["structure"],
                    "body": f"{row['topic_id']} 的题目独有正文",
                }
                for row in handoff["selected_topics"]
            ],
            "failures": [],
        }
        workflow.validate_scripts("run_batch", scripts, selected)
        self.assertEqual(
            [row["topic_id"] for row in handoff["selected_topics"]],
            [row["topic_id"] for row in scripts["scripts"]],
        )
        self.assertEqual(len(selected), len(scripts["scripts"]))

    def test_skill_sources_reject_shared_template_and_invented_scene(self):
        root = workflow.ROOT
        no_overtime = (
            root / "skills/austin-no-overtime-scripting/SKILL.md"
        ).read_text(encoding="utf-8")
        voice = (
            root / "skills/austin-voice-scriptwriter/SKILL.md"
        ).read_text(encoding="utf-8")
        release = json.loads(
            (root / "config/web010_single_daily_workflow_release.json").read_text()
        )
        combined = no_overtime + voice
        self.assertIn("approved private references", combined)
        self.assertIn("不能写成", combined)
        self.assertIn("不同题目", combined)
        self.assertIn("来源已经支持的事实", combined)
        protocol = "\n".join(release["externalSchedule"]["outerAgentProtocol"])
        self.assertIn("selected_topics rich context", protocol)
        self.assertIn("once for the full batch", protocol)
        self.assertNotIn("codex exec", protocol)


if __name__ == "__main__":
    unittest.main()
