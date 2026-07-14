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


def normalized_review_decision(index: int = 0, title: str = "公开标题") -> dict:
    return {
        "index": index,
        "editorial_decision_hash": f"hash-{index}",
        "selected_visible_title": title,
        "source_title_hook": f"source-hook-{index}",
    }


def passing_review(decisions: list[dict], index: int = 0) -> dict:
    item = decisions[index]
    return {
        "index": index,
        "editorial_decision_hash": item["editorial_decision_hash"],
        "reviewed_visible_title": item["selected_visible_title"],
        "reviewed_source_hook": item["source_title_hook"],
        "status": "pass",
        "review_note": f"第{index}条复核了来源公共钩子、人物背景与作品身份边界。",
        "checks": {key: True for key in calibration.REQUIRED_HUMAN_CHECKS},
        "source_work_identity": {
            "person_background_terms": [],
            "verified_work_identity_terms": [],
            "title_work_identity_terms": [],
        },
    }


def review_payload(decisions: list[dict], rows: list[dict]) -> dict:
    return {
        "review_surface": "current_codex_task_post_generation_review",
        "bound_decision_set_sha256": calibration.decision_set_hash(decisions),
        "review_rows": rows,
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

    def test_generation_self_review_cannot_override_failed_post_review(self) -> None:
        decisions = [normalized_review_decision()]
        decisions[0]["human_review"] = {key: True for key in calibration.REQUIRED_HUMAN_CHECKS}
        review = passing_review(decisions)
        review["status"] = "fail"
        review["checks"]["source_work_identity_pass"] = False
        validated = calibration.validate_post_generation_reviews(
            decisions, review_payload(decisions, [review]), ["old title"]
        )
        self.assertFalse(calibration.review_summary(validated)["content_self_review_ok"])
        self.assertEqual(calibration.review_summary(validated)["content_self_review_failure_count"], 1)

    def test_post_review_rejects_missing_duplicate_hash_mismatch_and_reused_notes(self) -> None:
        decisions = [normalized_review_decision(0), normalized_review_decision(1)]
        rows = [passing_review(decisions, 0), passing_review(decisions, 1)]
        with self.assertRaisesRegex(RuntimeError, "coverage mismatch"):
            calibration.validate_post_generation_reviews(decisions, review_payload(decisions, rows[:1]), ["a", "b"])
        with self.assertRaisesRegex(RuntimeError, "duplicate"):
            calibration.validate_post_generation_reviews(
                decisions, review_payload(decisions, [rows[0], rows[0]]), ["a", "b"]
            )
        bad_hash = [dict(rows[0]), dict(rows[1])]
        bad_hash[1]["editorial_decision_hash"] = "wrong"
        with self.assertRaisesRegex(RuntimeError, "decision hash mismatch"):
            calibration.validate_post_generation_reviews(decisions, review_payload(decisions, bad_hash), ["a", "b"])
        repeated = [dict(rows[0]), dict(rows[1])]
        repeated[1]["review_note"] = repeated[0]["review_note"]
        with self.assertRaisesRegex(RuntimeError, "reused"):
            calibration.validate_post_generation_reviews(decisions, review_payload(decisions, repeated), ["a", "b"])

    def test_person_background_cannot_become_work_identity(self) -> None:
        review = {
            "source_work_identity": {
                "person_background_terms": ["hospital employee"],
                "verified_work_identity_terms": ["documentary"],
                "title_work_identity_terms": ["hospital employee"],
            }
        }
        issues = calibration.source_work_identity_issues(review)
        self.assertTrue(any("person_background_used_as_work_identity" in issue for issue in issues))
        self.assertTrue(any("unverified_work_identity" in issue for issue in issues))
        decisions = [normalized_review_decision()]
        bad_review = passing_review(decisions)
        bad_review["source_work_identity"] = review["source_work_identity"]
        with self.assertRaisesRegex(RuntimeError, "unresolved issues"):
            calibration.validate_post_generation_reviews(
                decisions, review_payload(decisions, [bad_review]), ["old title"]
            )

    def test_verified_work_identity_passes_without_source_specific_rules(self) -> None:
        review = {
            "source_work_identity": {
                "person_background_terms": ["studio assistant"],
                "verified_work_identity_terms": ["short film"],
                "title_work_identity_terms": ["short film"],
            }
        }
        self.assertEqual(calibration.source_work_identity_issues(review), [])
        source = inspect.getsource(calibration.source_work_identity_issues)
        for forbidden in ("Mx-Shell", "丧尸清道夫", "地产", "real estate"):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
