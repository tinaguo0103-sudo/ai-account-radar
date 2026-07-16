from __future__ import annotations

import unittest
from pathlib import Path

import editorial_skill_runner as runner


class AR034AIHOTOwnerTests(unittest.TestCase):
    def decision(self, *, aihot: bool = True, action: str = "select") -> dict:
        row = {field: f"value-{field}" for field in runner.EDITORIAL_DECISION_FIELDS}
        row.update({
            "index": 0,
            "decision": action,
            "recommendation_status": "生成脚本包" if action == "select" else "观察",
            "research_evidence_ids": "research-1,research-2",
            "hook_evidence_ids": "research-1",
            "aihot_significance_rationale": "这个变化会改变公开工具的成本结构。" if aihot and action == "select" else "",
            "aihot_significance_evidence_ids": "research-2" if aihot and action == "select" else "",
        })
        return row

    def source(self, aihot: bool = True) -> dict[str, str]:
        return {"来源类型": "AIHOT热点" if aihot else "对标文章", "内容指纹": "fp", "来源链接": "https://example.com/x"}

    def test_aihot_actionable_stage1_owns_evidence_bound_rationale(self) -> None:
        decisions, _ = runner.validate_stage1_payload([self.source()], {"editorial_decisions": [self.decision()], "batch_notes": "ok"})
        self.assertEqual(decisions[0]["aihot_significance_evidence_ids"], "research-2")
        for mutation in ("missing", "unknown", "non_aihot", "observe_add"):
            item = self.decision(aihot=mutation != "non_aihot", action="observe" if mutation == "observe_add" else "select")
            source = self.source(aihot=mutation != "non_aihot")
            if mutation == "missing": item["aihot_significance_rationale"] = ""; item["aihot_significance_evidence_ids"] = ""
            if mutation == "unknown": item["aihot_significance_evidence_ids"] = "unknown"
            if mutation in {"non_aihot", "observe_add"}: item["aihot_significance_rationale"] = "bad"; item["aihot_significance_evidence_ids"] = "research-1"
            with self.subTest(mutation=mutation), self.assertRaises(RuntimeError):
                runner.validate_stage1_payload([source], {"editorial_decisions": [item], "batch_notes": "ok"})

    def test_stage2_can_only_apply_locked_aihot_owner(self) -> None:
        decision = runner.normalize_decision(self.decision(), 0, {})
        row = runner.reapply_locked_stage2_fields({}, decision)
        row["editorial_decision_id"] = decision["editorial_decision_id"]
        row["editorial_decision_hash"] = decision["editorial_decision_hash"]
        self.assertEqual(row["AIHOT重大性说明"], decision["aihot_significance_rationale"])
        self.assertEqual(runner.stage2_invariant_issues(decision, row), [])
        for value in ("", "changed", "other-owner-value"):
            mutated = {**row, "AIHOT重大性说明": value}
            with self.subTest(value=value):
                self.assertTrue(any("AIHOT" in issue for issue in runner.stage2_invariant_issues(decision, mutated)))
        self.assertTrue(any("AIHOT" in issue for issue in runner.raw_stage2_drift_issues(decision, {"AIHOT重大性说明": "changed"})))

    def test_candidate_pipeline_has_no_deterministic_aihot_significance_author(self) -> None:
        source = Path(__file__).with_name("topic_flow_rework.py").read_text(encoding="utf-8")
        self.assertNotIn("def aihot_significance_reason", source)
        self.assertNotIn("AIHOT重大性说明\"] = aihot", source)


if __name__ == "__main__":
    unittest.main()
