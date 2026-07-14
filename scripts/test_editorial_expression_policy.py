#!/usr/bin/env python3
from __future__ import annotations

import inspect
import unittest

import editorial_expression_policy as policy
import ar020e_expression_calibration as calibration
import editorial_skill_runner as runner


def dossier(text: str = "") -> dict:
    return {
        "source": {"content_evidence": [{"text": text}]},
        "results": [],
    }


def decision(title: str, *, angle: str = "这是 Austin 对公开材料的鲜明判断。") -> dict:
    return {
        "selected_visible_title": title,
        "natural_austin_angle": angle,
        "public_decision_summary": "来源给出变化，主编负责判断它为什么值得公众关注。",
        "hard_fact_usage": "none",
        "fact_boundary_note": "来源事实与 Austin 观点已分开。",
        "editorial_expression_mode": "hook_first_aggressive_honest",
    }


class EditorialExpressionPolicyTests(unittest.TestCase):
    def test_hyperbole_trend_metaphor_and_rhetorical_question_are_allowed(self) -> None:
        examples = [
            "AI已经开始接管没人愿意做的运营脏活",
            "这可能是今年最值得盯住的一次工具换挡",
            "一个人顶一支团队，真的是夸张吗？",
            "新一代Agent正在抢饭碗，还是终于开始干活？",
        ]
        for title in examples:
            with self.subTest(title=title):
                result = policy.validate_editorial_decision(decision(title), dossier())
                self.assertEqual(result["hard_fact_boundary_status"], "pass")

    def test_unsupported_exact_statistics_direct_quotes_and_official_claims_fail(self) -> None:
        examples = [
            "92%的团队已经被AI接管",
            "官方宣布这就是最强Agent",
            "负责人说“所有岗位都会被替代”",
            "Claude Cowork最常被交出去的是运营工作",
            "这家公司已经被确认造假",
        ]
        for title in examples:
            with self.subTest(title=title), self.assertRaises(policy.ExpressionPolicyError):
                policy.validate_editorial_decision(decision(title), dossier("材料只确认产品存在。"))

    def test_evidence_backed_exact_fact_is_allowed(self) -> None:
        title = "20 FPS的AI世界已经能四个人一起闯，游戏引擎要变天了？"
        item = decision(title)
        item["hard_fact_usage"] = "20 FPS; four-player"
        result = policy.validate_editorial_decision(
            item,
            dossier("官方页面展示实时生成世界达到20 FPS，并支持四个人进入同一世界。"),
        )
        self.assertEqual(result["hard_fact_boundary_status"], "pass")

    def test_supported_exact_fact_must_still_be_declared(self) -> None:
        item = decision("20 FPS的AI世界已经能四个人一起闯")
        with self.assertRaisesRegex(policy.ExpressionPolicyError, "hard_fact_usage_not_declared"):
            policy.validate_editorial_decision(item, dossier("官方页面展示实时世界达到20 FPS。"))

    def test_expression_mode_is_explicit(self) -> None:
        item = decision("AI已经开始改变小团队的工作方式")
        item["editorial_expression_mode"] = ""
        with self.assertRaisesRegex(policy.ExpressionPolicyError, "invalid_editorial_expression_mode"):
            policy.validate_editorial_decision(item, dossier())

    def test_policy_has_no_example_specific_hardcoding(self) -> None:
        source = inspect.getsource(policy)
        for forbidden in ("Storyboard", "Mx-Shell", "Obsidian", "MIRA", "Claude Cowork", "丧尸清道夫"):
            self.assertNotIn(forbidden, source)

    def test_title_family_is_a_detector_not_a_generator(self) -> None:
        self.assertEqual(calibration.title_family("Agent一旦开始干活，谁来接住它闯的祸？"), "rhetorical_question")
        self.assertEqual(calibration.title_family("一条作品突然出圈，幕后发生了什么"), "story_social_proof")
        source = inspect.getsource(calibration.title_family)
        self.assertNotIn("return title", source)

    def test_ar020e_fields_are_locked_by_editorial_decision_hash(self) -> None:
        base = decision("AI已经开始改变小团队的工作方式")
        base.update({
            "decision": "select",
            "recommendation_status": "生成脚本包",
            "hook_first_rationale": "公共冲突驱动点击。",
        })
        first = runner.editorial_decision_hash(base)
        changed = {**base, "fact_boundary_note": "Changed boundary."}
        self.assertNotEqual(first, runner.editorial_decision_hash(changed))


if __name__ == "__main__":
    unittest.main()
