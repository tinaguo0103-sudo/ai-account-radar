from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import run_daily_workflow as workflow


ROOT = Path(__file__).resolve().parents[1]
RELEASE_CONFIG = ROOT / "config" / "web010_single_daily_workflow_release.json"
VOICE_SKILL = ROOT / "skills" / "austin-voice-scriptwriter" / "SKILL.md"
NO_OVERTIME_SKILL = ROOT / "skills" / "austin-no-overtime-scripting" / "SKILL.md"
SPOKEN_BODY_METHOD = (
    ROOT / "skills" / "austin-no-overtime-scripting" / "prompts" / "spoken_body_method.md"
)
PRIVATE_CONTEXT_READING = (
    ROOT / "skills" / "austin-voice-scriptwriter" / "references"
    / "austin_private_context_reading.md"
)


class SpokenScriptRestorationTests(unittest.TestCase):
    def fixture(self) -> dict:
        return {
            "run_id": "run_20260808_121000",
            "business_date": "2026-08-08",
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

    def editorial(self) -> dict:
        return {
            "run_id": "run_20260808_121000",
            "topics": [{
                "candidate_id": "douyin:100",
                "decision": "select",
                "title": "长任务真正要留下的是恢复点",
                "hook": "四小时以后，失败能不能接着跑？",
                "structure": "按当前材料自然推进",
                "selection_reason": "来源事实与 Austin 工作流判断直接相关。",
            }],
        }

    def test_builds_content_driven_topic_card_and_silent_constraints(self):
        handoff = workflow.build_scripts_handoff(
            "run_20260808_121000", "2026-08-08", self.fixture(), self.editorial()
        )
        self.assertEqual(handoff["action"], "scripts_required")
        self.assertEqual(handoff["selected_topics"][0]["topic_id"], "douyin:100")
        topic = handoff["selected_topics"][0]
        self.assertEqual(topic["source"]["likes"], 0)
        self.assertNotIn("comments", topic["source"])
        for blueprint_field in (
            "title", "hook", "structure", "unique_judgment", "persona_fit",
            "workflow_context", "traffic_opportunity", "differentiation", "cluster_synthesis",
        ):
            self.assertNotIn(blueprint_field, topic)
        self.assertEqual(
            topic["source_facts"],
            {"details": "A bounded public summary"},
        )
        self.assertEqual(topic["fact_boundary"], "不能声称已经节省固定时长。")
        self.assertEqual(topic["cannot_claim"], None)
        self.assertEqual(topic["video_understanding"]["asr_supplement"], "spoken supplement")
        encoded = json.dumps(handoff, ensure_ascii=False)
        self.assertNotIn("我的制作补充", encoded)
        self.assertNotIn("制作方向", encoded)
        contract = handoff["batch_contract"]
        self.assertTrue(contract["content_driven_form"])
        self.assertTrue(contract["deterministic_controller_owns_order_and_checkpoint"])
        self.assertTrue(contract["one_editorial_child_per_batch"])
        self.assertTrue(contract["one_writer_child_per_selected_topic"])
        self.assertTrue(contract["one_topic_per_writer_child"])
        self.assertNotIn("one_outer_ai_owner", contract)
        self.assertNotIn("one_batch_invocation_per_skill", contract)
        self.assertFalse(contract["universal_content_slots"])
        self.assertTrue(contract["unsupported_first_person_experience_forbidden"])
        self.assertTrue(contract["material_or_angle_insufficiency_is_item_local"])
        self.assertTrue(contract["fact_boundaries_are_silent_generation_context"])

    def test_optional_missing_context_stays_absent(self):
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
            "run_20260808_121000", "2026-08-08", fixture, self.editorial()
        )["selected_topics"][0]
        self.assertEqual(topic["source"], {"url": "https://www.douyin.com/video/100"})
        self.assertNotIn("workflow_context", topic)
        self.assertNotIn("title", topic)
        self.assertNotIn("hook", topic)
        self.assertNotIn("structure", topic)
        self.assertIsNone(topic["fact_boundary"])
        self.assertIsNone(topic["cannot_claim"])
        self.assertIsNone(topic["video_understanding"])

    def test_simple_result_accepts_item_local_material_failure(self):
        selected = {"douyin:100"}
        result = {
            "run_id": "run_20260808_121000",
            "scripts": [],
            "failures": [{
                "topic_id": "douyin:100",
                "reason": "material_or_angle_insufficiency",
                "detail": "当前材料不足以支撑独特正文。",
            }],
        }
        workflow.validate_scripts("run_20260808_121000", result, selected)
        with self.assertRaisesRegex(workflow.WorkflowConflict, "script_result_incomplete"):
            workflow.validate_scripts(
                "run_20260808_121000",
                {**result, "failures": [{"topic_id": "douyin:100", "reason": "evidence_only"}]},
                selected,
            )

    def test_skills_use_content_driven_form_without_private_routing(self):
        voice = VOICE_SKILL.read_text(encoding="utf-8")
        no_overtime = NO_OVERTIME_SKILL.read_text(encoding="utf-8")
        method = SPOKEN_BODY_METHOD.read_text(encoding="utf-8")
        private_reading = PRIVATE_CONTEXT_READING.read_text(encoding="utf-8")
        combined = "\n".join((voice, no_overtime, method, private_reading))
        self.assertIn("不是文章类型字段、模板或 gate", voice)
        self.assertIn("material_or_angle_insufficiency", combined)
        self.assertIn("一次 Author Edit", combined)
        self.assertIn("不虚构经历", no_overtime)
        self.assertIn("不读取 `references/private/three_round_learning.md`", voice)
        self.assertIn("writer child", voice)
        self.assertIn(
            "Raw excerpt text may exist only in the current per-topic writer child context",
            private_reading,
        )
        self.assertIn("不是文章类型字段、模板或 gate", voice)
        self.assertNotIn("scene/conflict/old workflow/experiment/judgment/consequence/close", combined)
        self.assertIn("Writer child 不得递归启动 Codex", no_overtime)
        self.assertNotIn("当前 outer Codex", combined)
        for retired_path in (
            "watch_script_package_queue.py",
            "codex_script_package_runner.py",
            "--write-feishu",
        ):
            self.assertNotIn(retired_path, combined)

    def test_release_contract_uses_separate_bounded_children(self):
        config = json.loads(RELEASE_CONFIG.read_text(encoding="utf-8"))
        protocol = "\n".join(config["externalSchedule"]["outerAgentProtocol"])
        self.assertIn("exactly one fresh bounded Codex child", protocol)
        self.assertIn("fresh bounded Codex child for the current topic", protocol)
        self.assertIn("one selected rich Topic Card at a time", protocol)
        self.assertIn("No full-batch script submission exists", protocol)
        self.assertIn("material_or_angle_insufficiency", protocol)
        self.assertNotIn("same outer Codex", protocol)
        self.assertIn("only austin-voice-scriptwriter", protocol)
        self.assertNotIn("austin-no-overtime-scripting", protocol)
        self.assertNotIn("--script-reference-selection-file", protocol)
        self.assertNotIn("per-topic private case/persona routing or selector receipt", protocol)
        for forbidden in ("watcher", "Feishu", "--write-feishu"):
            self.assertNotIn(forbidden, protocol)
        self.assertIn("recursive codex exec outside the editorial and per-topic writer adapters", "\n".join(config["normalRuntimeForbiddenCalls"]))

    def test_simple_script_artifact_is_idempotent(self):
        result = {
            "run_id": "run_20260808_121000",
            "scripts": [{
                "topic_id": "douyin:100",
                "title": "Title",
                "hook": "Hook",
                "structure": "Structure",
                "body": "A complete spoken body.",
            }],
            "failures": [],
        }
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workflow.write_script_artifacts(root, "run_20260808_121000", result["scripts"])
            before = next(root.rglob("*.md")).read_bytes()
            workflow.write_script_artifacts(root, "run_20260808_121000", result["scripts"])
            self.assertEqual(next(root.rglob("*.md")).read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
