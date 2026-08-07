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
VOICE_SKILL = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "austin-voice-scriptwriter"
    / "SKILL.md"
)
NO_OVERTIME_SKILL = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "austin-no-overtime-scripting"
    / "SKILL.md"
)
SPOKEN_BODY_METHOD = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "austin-no-overtime-scripting"
    / "prompts"
    / "spoken_body_method.md"
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
        skill = VOICE_SKILL.read_text(encoding="utf-8")
        self.assertIn("连续问题只是可选手法，不规定数量", skill)
        self.assertIn("也可以完全不编号", skill)
        self.assertIn("只有当不确定性或责任边界本身就是这条题的冲突时", skill)
        self.assertIn("合理假设/复合场景和示例数据", skill)
        self.assertIn("以本节为准", skill)
        self.assertIn("0 个匹配案例是正常输入", skill)
        self.assertNotIn("用 3-5 个连续问题把痛点拆开", skill)
        self.assertNotIn("结尾必须有边界", skill)

    def test_candidate_voice_skill_keeps_provenance_silent_and_attribution_natural(self):
        skill = VOICE_SKILL.read_text(encoding="utf-8")
        silent_context_fixtures = (
            "公开信息里提到",
            "根据来源",
            "资料显示",
            "目前可验证",
        )
        self.assertIn("provenance、source verification、missing evidence 和 cannot-claim", skill)
        self.assertIn("静默生成与 QA", skill)
        self.assertIn("不要换一组同义词继续解释核验", skill)
        for fixture in silent_context_fixtures:
            self.assertIn(fixture, skill)
        self.assertIn("视频里的作者说，他凌晨四点看到成片", skill)
        self.assertIn("这是人物归属，不是来源", skill)
        self.assertIn("直接讲已经支持的事实、判断、场景、动作和后果", skill)

    def test_candidate_skill_set_has_one_outer_spoken_only_runtime(self):
        voice = VOICE_SKILL.read_text(encoding="utf-8")
        no_overtime = NO_OVERTIME_SKILL.read_text(encoding="utf-8")
        combined = "\n".join((voice, no_overtime))
        for forbidden_entrypoint in (
            "codex exec",
            "watch_script_package_queue.py",
            "codex_script_package_runner.py",
            "--write-feishu",
        ):
            self.assertNotIn(forbidden_entrypoint, combined)
        self.assertIn("唯一 AI owner 是当前 outer Codex", no_overtime)
        self.assertIn("本 Skill 每个批次应用一次", no_overtime)
        self.assertIn("austin-voice-scriptwriter` 每个批次应用一次", no_overtime)
        self.assertIn("输出只包含 `topic_id/title/hook/structure/body`", no_overtime)
        self.assertIn("0 case match 是正常输入", no_overtime)
        self.assertIn("同一上下文直接通读完整正文并完成质量检查", voice)

    def test_each_topic_runs_the_complete_spoken_body_method(self):
        skill = NO_OVERTIME_SKILL.read_text(encoding="utf-8")
        method = SPOKEN_BODY_METHOD.read_text(encoding="utf-8")
        self.assertIn("逐题聚焦", skill)
        self.assertIn("分段规划语义", skill)
        self.assertIn("完整正文起草", skill)
        self.assertIn("提词器视角复读", skill)
        self.assertIn("逐题内容 QA 和必要重写", skill)
        self.assertIn("完成一题再进入下一题", skill)
        self.assertIn("3-5 分钟是语义完整度参考", method)
        self.assertIn("scene、conflict、old workflow", method)
        self.assertIn("identity exact coverage", method)
        self.assertNotIn("字符下限", method)

    def test_complete_method_keeps_simple_output_and_retired_paths_unreachable(self):
        sources = "\n".join((
            NO_OVERTIME_SKILL.read_text(encoding="utf-8"),
            SPOKEN_BODY_METHOD.read_text(encoding="utf-8"),
        ))
        self.assertIn("只返回每题 `title/hook/structure/body`", sources)
        self.assertIn("不创建额外用户产物", sources)
        for forbidden_entrypoint in (
            "codex exec",
            "watch_script_package_queue.py",
            "codex_script_package_runner.py",
            "--write-feishu",
        ):
            self.assertNotIn(forbidden_entrypoint, sources)
        for forced_template in (
            "开头8秒必须",
            "中段实操最多3步",
            "必须出现人工修正点或AI边界",
            "结尾必须回到真人判断",
        ):
            self.assertNotIn(forced_template, sources)

    def test_multi_topic_batch_does_not_collapse_topics_into_one_summary(self):
        skill = NO_OVERTIME_SKILL.read_text(encoding="utf-8")
        method = SPOKEN_BODY_METHOD.read_text(encoding="utf-8")
        self.assertIn("不得先把多题压成一组摘要", skill)
        self.assertIn("不要把五题先压成摘要", method)
        self.assertIn("多选题仍是一次 batch", skill)
        self.assertIn("每题都必须", skill)

    def test_normal_generation_excludes_legacy_three_round_reference(self):
        voice = VOICE_SKILL.read_text(encoding="utf-8")
        self.assertIn(
            "正常口播生成不定位、不读取 `references/private/three_round_learning.md`",
            voice,
        )
        self.assertNotIn("可以借它理解 Austin 的节奏和判断感", voice)
        self.assertIn("只有用户明确要求历史风格研究", voice)
        self.assertIn("历史版本对照或旧方法诊断", voice)

    def test_three_round_calibration_is_a_context_boundary_not_a_number_gate(self):
        sources = "\n".join((
            VOICE_SKILL.read_text(encoding="utf-8"),
            NO_OVERTIME_SKILL.read_text(encoding="utf-8"),
            SPOKEN_BODY_METHOD.read_text(encoding="utf-8"),
        ))
        for forbidden_gate in (
            "禁止数字3",
            "禁止三",
            "三的出现次数",
            "数字3不得出现",
        ):
            self.assertNotIn(forbidden_gate, sources)
        self.assertIn("动作可以拆成两步、三步、四步", sources)
        self.assertIn("是否编号只看这条内容是否因此更清楚", sources)

    def test_corrected_skill_structure_matches_continuous_task_body(self):
        skill = NO_OVERTIME_SKILL.read_text(encoding="utf-8")
        self.assertIn("同步复读并更新该题的", skill)
        self.assertIn("structure 与最终", skill)
        structure = (
            "Skill收藏焦虑 -> 从重复任务建立旧流程基线 -> 同材料接手并中途改brief -> "
            "关键结论回链 -> 按交接与返工成本决定去留"
        )
        body_semantics = {
            "从重复任务建立旧流程基线": "这个旧流程，就是我的基线。",
            "同材料接手并中途改brief": "等它做到一半，我会把真实变化丢进去",
            "关键结论回链": "我会随手点开一条最关键、也最容易写错的结论",
            "按交接与返工成本决定去留": "整个过程，我只记真实发生的成本。",
        }
        corrected_body_fixture = " ".join(body_semantics.values())
        self.assertNotIn("文档任务三轮实测", structure)
        for structure_step, body_evidence in body_semantics.items():
            self.assertIn(structure_step, structure)
            self.assertIn(body_evidence, corrected_body_fixture)

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
        for forbidden_entrypoint in (
            "codex exec",
            "watcher",
            "codex_script_package_runner.py",
            "--write-feishu",
            "Feishu",
        ):
            self.assertNotIn(forbidden_entrypoint, protocol)

    def test_release_prompt_makes_batch_invocation_sequential_per_topic(self):
        config = json.loads(RELEASE_CONFIG.read_text(encoding="utf-8"))
        protocol = "\n".join(config["externalSchedule"]["outerAgentProtocol"])
        self.assertIn("batch-level orchestration boundary", protocol)
        self.assertIn("process selected topics strictly one at a time", protocol)
        self.assertIn("finish the current topic's Topic Focus, Semantic Plan, Full Draft, Teleprompter Read, and Item QA/rewrite before reading or drafting the next topic", protocol)
        self.assertIn("only after every topic has completed that method, serialize the one simple stage result", protocol)
        self.assertNotIn("once for the full batch", protocol)
        self.assertIn("Do not first summarize or draft the whole batch", protocol)


if __name__ == "__main__":
    unittest.main()
