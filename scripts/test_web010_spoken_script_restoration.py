from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import run_daily_workflow as workflow


ROOT = Path(__file__).resolve().parents[1]
RELEASE_CONFIG = ROOT / "config" / "web010_single_daily_workflow_release.json"
VOICE_SKILL = ROOT / "skills" / "austin-voice-scriptwriter" / "SKILL.md"


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
                "editorial_thesis": {
                    "thesis": "长任务真正要留下的是恢复点。",
                    "audience_conflict": "做长任务的人想继续推进，却常常只能从头重做。",
                    "why_now": "同一 run 的来源给出了失败后恢复的具体事实。",
                    "evidence_boundary": {
                        "source_facts": "来源记录了同一批次的 checkpoint 与恢复结果。",
                        "interpretation": "恢复点比单次运行时长更能决定流程是否可用。",
                        "proposed_test": "可以在失败后只继续未完成阶段来验证这个判断。",
                    },
                },
            }],
        }

    def test_builds_content_driven_topic_card_without_control_plane_copy(self):
        handoff = workflow.build_scripts_handoff(
            "run_20260808_121000", "2026-08-08", self.fixture(), self.editorial()
        )
        self.assertEqual(handoff["action"], "scripts_required")
        self.assertEqual(handoff["selected_topics"][0]["topic_id"], "douyin:100")
        topic = handoff["selected_topics"][0]
        self.assertEqual(topic["source_evidence"]["source"]["likes"], 0)
        self.assertNotIn("comments", topic["source_evidence"]["source"])
        self.assertNotIn("editorial_thesis", topic)
        for blueprint_field in (
            "title", "hook", "structure", "unique_judgment", "persona_fit",
            "workflow_context", "traffic_opportunity", "differentiation", "cluster_synthesis",
            "editorial_judgment", "video_understanding",
        ):
            self.assertNotIn(blueprint_field, topic)
        self.assertEqual(
            topic["source_evidence"]["source_facts"],
            {"details": "A bounded public summary"},
        )
        self.assertNotIn("selection_reason", topic)
        self.assertNotIn("fact_boundary", topic["source_evidence"])
        self.assertNotIn("cannot_claim", topic["source_evidence"])
        self.assertNotIn("provenance", topic["source_evidence"]["source"])
        self.assertNotIn("missing_reasons", topic["source_evidence"]["source"])
        self.assertEqual(topic["source_evidence"]["video"]["asr_supplement"], "spoken supplement")
        self.assertNotIn("visual_reading", topic["source_evidence"]["video"])
        self.assertNotIn("run_id", topic["source_evidence"]["video"])
        encoded = json.dumps(handoff, ensure_ascii=False)
        self.assertNotIn("我的制作补充", encoded)
        self.assertNotIn("制作方向", encoded)
        contract = handoff["batch_contract"]
        self.assertEqual(
            set(contract),
            {
                "deterministic_controller_owns_order_and_checkpoint",
                "one_automation_codex_direct_writer_stage",
                "one_topic_per_submission",
                "submit_before_next_topic",
            },
        )
        self.assertTrue(contract["deterministic_controller_owns_order_and_checkpoint"])
        self.assertTrue(contract["one_automation_codex_direct_writer_stage"])
        self.assertTrue(contract["one_topic_per_submission"])
        self.assertTrue(contract["submit_before_next_topic"])

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
        self.assertEqual(topic["source_evidence"]["source"], {"url": "https://www.douyin.com/video/100"})
        self.assertNotIn("workflow_context", topic)
        self.assertNotIn("title", topic)
        self.assertNotIn("hook", topic)
        self.assertNotIn("structure", topic)
        self.assertNotIn("fact_boundary", topic["source_evidence"])
        self.assertNotIn("cannot_claim", topic["source_evidence"])
        self.assertIsNone(topic["source_evidence"]["video"])

    def test_passes_existing_editorial_judgment_without_using_blueprint_fields(self):
        fixture = self.fixture()
        editorial = self.editorial()
        editorial["topics"][0].update({
            "title": "Editorial title must not become a writer blueprint",
            "hook": "Editorial hook must not become a writer blueprint",
            "structure": ["Editorial structure must not become a writer blueprint"],
        })
        editorial["topics"][0]["editorial_thesis"].update({
            "unique_judgment": "The source changes the cost of a real recovery decision.",
            "decision_basis": {
                "content": "A source-owned workflow consequence.",
                "persona": "A concrete Austin business angle.",
                "differentiation": "A judgment not interchangeable with a tool summary.",
            },
            "evidence_source_ids": ["douyin:100"],
        })
        topic = workflow.build_scripts_handoff(
            "run_20260808_121000", "2026-08-08", fixture, editorial,
        )["selected_topics"][0]
        self.assertNotIn("selection_reason", topic)
        self.assertNotIn("editorial_thesis", topic)
        self.assertNotIn("title", topic)
        self.assertNotIn("hook", topic)
        self.assertNotIn("structure", topic)

    def test_selected_editorial_without_model_thesis_is_accepted(self):
        editorial = self.editorial()
        del editorial["topics"][0]["editorial_thesis"]
        workflow.validate_editorial(
            "run_20260803_110453",
            {"run_id": "run_20260803_110453", "topics": editorial["topics"]},
            [{"candidate_id": "douyin:100"}],
        )

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

    def test_voice_skill_is_minimal_and_retired_renderer_is_absent(self):
        voice = VOICE_SKILL.read_text(encoding="utf-8")
        self.assertIn("当前一个已选题", voice)
        self.assertIn("输入只有当前题目的 same-run 原始事实/视频材料", voice)
        self.assertNotIn("用户提供的人设、案例和样稿", voice)
        self.assertIn("完整、可直接朗读的 body", voice)
        self.assertIn("topic_id/title/hook/structure/body", voice)
        for retired in (
            "## 写作顺序",
            "## 来源与核验只留在后台",
            "## 必须像口播",
            "## 可借用语气",
            "## 禁止口吻",
            "public_voice_style.md",
            "scripts/austin_voice.py",
        ):
            self.assertNotIn(retired, voice)
        self.assertFalse((VOICE_SKILL.parent / "references" / "public_voice_style.md").exists())
        self.assertFalse((VOICE_SKILL.parent / "scripts" / "austin_voice.py").exists())
        self.assertTrue((ROOT / "skills" / "austin-no-overtime-scripting" / "scripts" / "austin_voice_legacy.py").exists())

    def test_release_contract_uses_one_owner_and_direct_skill_stages(self):
        config = json.loads(RELEASE_CONFIG.read_text(encoding="utf-8"))
        protocol = "\n".join(config["externalSchedule"]["outerAgentProtocol"])
        self.assertIn("current Automation Codex directly applies ai-account-editorial-director", protocol)
        self.assertIn("directly applies austin-voice-scriptwriter", protocol)
        self.assertIn("only the current rich Topic Card", protocol)
        self.assertIn("current rich Topic Card", protocol)
        self.assertIn("compose the complete body before filling title/hook/structure", protocol)
        self.assertIn("exposes one topic at a time", protocol)
        self.assertIn("material_or_angle_insufficiency", protocol)
        self.assertNotIn("austin-no-overtime-scripting", protocol)
        self.assertNotIn("web010_austin_private_context_allowlist", protocol)
        self.assertNotIn("full original user materials", protocol)
        self.assertNotIn("Seedance", protocol)
        self.assertNotIn("same-run representative keyframe", protocol)
        self.assertNotIn("source verification", protocol)
        self.assertNotIn("candidate-specific reason", protocol)
        self.assertNotIn("Facts-first Draft", protocol)
        self.assertNotIn("Author Edit", protocol)
        self.assertNotIn("Semantic Plan", protocol)
        self.assertNotIn("--script-reference-selection-file", protocol)
        self.assertNotIn("per-topic private case/persona routing or selector receipt", protocol)
        for forbidden in ("watcher", "Feishu", "--write-feishu"):
            self.assertNotIn(forbidden, protocol)
        self.assertIn("codex exec", "\n".join(config["normalRuntimeForbiddenCalls"]))
        source = (ROOT / "scripts" / "run_daily_workflow.py").read_text(encoding="utf-8")
        for forbidden in (
            "web010_codex_child",
            "run_editorial_child",
            "run_writer_child",
            "CODEX_BIN",
            "codex-child-timeout",
        ):
            self.assertNotIn(forbidden, source)

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
