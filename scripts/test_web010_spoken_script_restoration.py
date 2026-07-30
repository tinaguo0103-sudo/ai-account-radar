import json
import tempfile
import unittest
from pathlib import Path

import run_daily_workflow as workflow


RELEASE_CONFIG = (
    Path(__file__).resolve().parents[1]
    / "config"
    / "web010_single_daily_workflow_release.json"
)


class SpokenScriptRestorationTests(unittest.TestCase):
    def fixture(self):
        return {
            "run_id": "run_20260730_120000",
            "business_date": "2026-07-30",
            "content_items": [{
                "item_id": "douyin:100",
                "source_url": "https://www.douyin.com/video/100",
                "source_title": "AI workflow source",
                "source_summary": "A bounded public summary",
                "author": "Public author",
                "likes": 0,
                "comments": None,
                "fact_missing_reasons": {"comments": "not_public"},
                "我的工作流痛点": "资料每次都要重新找。",
                "旧流程痛点": "靠当天状态重做。",
                "AI介入点": "先整理同一批资料。",
                "我要做的实验": "用一批资料验证能否续跑。",
                "验证方式": "失败后只继续未完成阶段。",
                "可展示证据": "同一批次 checkpoint。",
                "需要补的证据": "尚无生产长期数据。",
                "事实边界": "不能声称已经节省固定时长。",
                "我的制作补充": "must not leave runtime boundary",
            }],
            "candidates": [{
                "candidate_id": "douyin:100",
                "item_id": "douyin:100",
                "我的账号为什么能讲": "真实 AI 工作流复盘。",
                "我的独家判断": "长任务的价值在恢复，不在运行时长。",
                "制作方向": "must not leave runtime boundary",
            }],
            "understanding_results": [{
                "candidate_id": "douyin:100",
                "package": {
                    "status": "completed",
                    "caption_timeline": [{"start": 1.0, "text": "checkpoint"}],
                    "asr": {"text": "spoken supplement"},
                    "screen_facts": [{"kind": "tool", "value": "Codex", "time_second": 2.0}],
                    "keyframes": [{"time_second": 2.0, "sha256": "abc"}],
                    "unresolved": ["duration"],
                },
            }],
        }

    def editorial(self):
        return {
            "run_id": "run_20260730_120000",
            "topics": [{
                "candidate_id": "douyin:100",
                "decision": "select",
                "title": "长任务真正要留下的是恢复点",
                "hook": "四小时以后，失败能不能接着跑？",
                "structure": "失败现场 -> 恢复判断 -> 可执行边界",
                "selection_reason": "来源事实与 Austin 工作流判断直接相关。",
            }],
        }

    def test_builds_compact_same_run_topic_card_without_human_direction(self):
        handoff = workflow.build_scripts_handoff(
            "run_20260730_120000", "2026-07-30", self.fixture(), self.editorial()
        )
        self.assertEqual(handoff["action"], "scripts_required")
        self.assertEqual(len(handoff["selected_topics"]), 1)
        topic = handoff["selected_topics"][0]
        self.assertEqual(topic["topic_id"], "douyin:100")
        self.assertEqual(topic["source"]["likes"], 0)
        self.assertNotIn("comments", topic["source"])
        self.assertEqual(topic["source"]["missing_reasons"], {"comments": "not_public"})
        self.assertEqual(topic["workflow_context"]["experiment"], "用一批资料验证能否续跑。")
        self.assertEqual(topic["video_understanding"]["asr_supplement"], "spoken supplement")
        self.assertEqual(topic["video_understanding"]["screen_facts"][0]["value"], "Codex")
        encoded = json.dumps(handoff, ensure_ascii=False)
        self.assertNotIn("我的制作补充", encoded)
        self.assertNotIn("制作方向", encoded)
        self.assertTrue(handoff["batch_contract"]["human_supplement_excluded"])
        self.assertTrue(
            handoff["batch_contract"]["fact_boundaries_are_silent_generation_context"]
        )
        self.assertTrue(
            handoff["batch_contract"]["plausible_hypothetical_or_composite_scenes_allowed"]
        )
        self.assertTrue(handoff["batch_contract"]["illustrative_experiment_data_allowed"])
        self.assertTrue(
            handoff["batch_contract"][
                "fabricated_actual_client_team_or_measured_results_forbidden"
            ]
        )
        self.assertTrue(
            handoff["batch_contract"]["defensive_disclaimer_pattern_forbidden"]
        )

    def test_optional_context_stays_absent_or_null(self):
        fixture = self.fixture()
        fixture["content_items"][0] = {
            "item_id": "douyin:100",
            "source_url": "https://www.douyin.com/video/100",
        }
        fixture["candidates"][0] = {
            "candidate_id": "douyin:100",
            "item_id": "douyin:100",
        }
        fixture["understanding_results"] = []
        topic = workflow.build_scripts_handoff(
            "run_20260730_120000", "2026-07-30", fixture, self.editorial()
        )["selected_topics"][0]
        self.assertEqual(topic["source"], {"url": "https://www.douyin.com/video/100"})
        self.assertEqual(topic["workflow_context"], {})
        self.assertIsNone(topic["fact_boundary"])
        self.assertIsNone(topic["cannot_claim"])
        self.assertIsNone(topic["video_understanding"])

    def test_missing_same_run_candidate_fails_closed(self):
        fixture = self.fixture()
        fixture["candidates"] = []
        with self.assertRaisesRegex(workflow.WorkflowConflict, "scripts_context_candidate_missing"):
            workflow.build_scripts_handoff(
                "run_20260730_120000", "2026-07-30", fixture, self.editorial()
            )

    def test_public_submit_and_replay_do_not_change_simple_result_contract(self):
        selected = {"douyin:100"}
        result = {
            "run_id": "run_20260730_120000",
            "scripts": [{
                "topic_id": "douyin:100",
                "title": "Title",
                "hook": "Hook",
                "structure": "Structure",
                "body": "A complete spoken body.",
            }],
            "failures": [],
        }
        workflow.validate_scripts("run_20260730_120000", result, selected)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workflow.write_script_artifacts(root, "run_20260730_120000", result["scripts"])
            before = next(root.rglob("*.md")).read_bytes()
            workflow.write_script_artifacts(root, "run_20260730_120000", result["scripts"])
            self.assertEqual(next(root.rglob("*.md")).read_bytes(), before)

    def test_candidate_voice_skill_owns_optional_narrative_contract(self):
        skill = (
            Path(__file__).resolve().parents[1]
            / "skills"
            / "austin-voice-scriptwriter"
            / "SKILL.md"
        ).read_text(encoding="utf-8")
        self.assertIn("连续问题只是可选手法，不规定数量", skill)
        self.assertIn("也可以完全不编号", skill)
        self.assertIn("只有当不确定性或责任边界本身就是这条题的冲突时", skill)
        self.assertIn("合理假设/复合场景和示例数据", skill)
        self.assertIn("以本节为准", skill)
        self.assertIn("0 个匹配案例是正常输入", skill)
        self.assertNotIn("用 3-5 个连续问题把痛点拆开", skill)
        self.assertNotIn("结尾必须有边界", skill)

    def test_release_prompt_delegates_style_instead_of_copying_skill_rules(self):
        config = json.loads(RELEASE_CONFIG.read_text(encoding="utf-8"))
        protocol = "\n".join(config["externalSchedule"]["outerAgentProtocol"])
        self.assertIn("candidate Voice Skill owns spoken narrative structure", protocol)
        self.assertIn("candidate austin-no-overtime-scripting once", protocol)
        self.assertIn("candidate austin-voice-scriptwriter once", protocol)
        for duplicated_detail in (
            "hypothetical or composite Austin-use scene",
            "Illustrative numbers",
            "distinct scene and narrative engine",
            "我还没有所以我不会",
            "AI不是万能",
        ):
            self.assertNotIn(duplicated_detail, protocol)
        for forbidden_claim in (
            "measured personal saving",
            "real client outcome",
            "completed team result",
            "verified third-party statistic",
        ):
            self.assertIn(forbidden_claim, protocol)


if __name__ == "__main__":
    unittest.main()
