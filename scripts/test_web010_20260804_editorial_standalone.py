from __future__ import annotations

import unittest

from run_daily_workflow import WorkflowConflict, validate_editorial


RUN = "run_20260804_080138"


def candidate(identity: str) -> dict:
    return {"candidate_id": identity}


def row(identity: str, decision: str = "select") -> dict:
    return {
        "candidate_id": identity,
        "decision": decision,
        "standalone_eligibility": {"decision": decision, "reason": f"{identity} remains worthwhile alone"},
        "selection_reason": f"{identity} candidate-local reason",
        "title": f"title {identity}" if decision == "select" else "",
        "hook": f"hook {identity}" if decision == "select" else "",
        "structure": ["scene", "experiment", "consequence"] if decision == "select" else [],
        "editorial_thesis": {
            "thesis": f"{identity} has a source-grounded judgment.",
            "audience_conflict": f"{identity} creates a concrete audience choice.",
            "why_now": "The exact run supplies a timely fact.",
            "evidence_boundary": {
                "source_facts": f"{identity} has an exact-run source fact.",
                "interpretation": "The fact supports a candidate-local judgment.",
                "proposed_test": "A bounded test can check the interpretation.",
            },
        } if decision == "select" else None,
    }


class EditorialStandaloneEligibilityTest(unittest.TestCase):
    def test_two_worthwhile_candidates_survive_ranking(self) -> None:
        candidates = [candidate("strong"), candidate("useful")]
        validate_editorial(RUN, {"run_id": RUN, "topics": [row("strong"), row("useful")]}, candidates)

    def test_ranking_cannot_demote_standalone_select(self) -> None:
        demoted = row("useful", "observe")
        demoted["standalone_eligibility"] = {"decision": "select", "reason": "worthwhile alone"}
        with self.assertRaisesRegex(WorkflowConflict, "editorial_standalone_decision_demotion"):
            validate_editorial(RUN, {"run_id": RUN, "topics": [row("strong"), demoted]}, [candidate("strong"), candidate("useful")])

    def test_true_duplicate_may_demote_but_near_topic_may_not(self) -> None:
        duplicate = row("duplicate", "observe")
        duplicate["standalone_eligibility"] = {"decision": "select", "reason": "worthwhile alone"}
        duplicate["duplicate_relation"] = {
            "duplicate_of": "original", "same_user_conflict": True,
            "same_core_judgment": True, "same_action_or_experiment": True,
        }
        candidates = [candidate("original"), candidate("duplicate")]
        validate_editorial(RUN, {"run_id": RUN, "topics": [row("original"), duplicate]}, candidates)
        duplicate["duplicate_relation"]["same_action_or_experiment"] = False
        with self.assertRaisesRegex(WorkflowConflict, "editorial_standalone_decision_demotion"):
            validate_editorial(RUN, {"run_id": RUN, "topics": [row("original"), duplicate]}, candidates)

    def test_duplicate_cannot_reference_itself(self) -> None:
        duplicate = row("duplicate", "observe")
        duplicate["standalone_eligibility"] = {"decision": "select", "reason": "worthwhile alone"}
        duplicate["duplicate_relation"] = {
            "duplicate_of": "duplicate", "same_user_conflict": True,
            "same_core_judgment": True, "same_action_or_experiment": True,
        }
        with self.assertRaisesRegex(WorkflowConflict, "editorial_standalone_decision_demotion"):
            validate_editorial(RUN, {"run_id": RUN, "topics": [duplicate]}, [candidate("duplicate")])

    def test_mutual_duplicate_demotion_cannot_remove_all_representatives(self) -> None:
        first = row("first", "observe")
        second = row("second", "observe")
        for current, target in ((first, "second"), (second, "first")):
            current["standalone_eligibility"] = {"decision": "select", "reason": "worthwhile alone"}
            current["duplicate_relation"] = {
                "duplicate_of": target, "same_user_conflict": True,
                "same_core_judgment": True, "same_action_or_experiment": True,
            }
        with self.assertRaisesRegex(WorkflowConflict, "editorial_standalone_decision_demotion"):
            validate_editorial(
                RUN, {"run_id": RUN, "topics": [first, second]},
                [candidate("first"), candidate("second")],
            )

    def test_duplicate_representative_must_remain_selected(self) -> None:
        representative = row("representative", "observe")
        duplicate = row("duplicate", "observe")
        duplicate["standalone_eligibility"] = {"decision": "select", "reason": "worthwhile alone"}
        duplicate["duplicate_relation"] = {
            "duplicate_of": "representative", "same_user_conflict": True,
            "same_core_judgment": True, "same_action_or_experiment": True,
        }
        with self.assertRaisesRegex(WorkflowConflict, "editorial_standalone_decision_demotion"):
            validate_editorial(
                RUN, {"run_id": RUN, "topics": [representative, duplicate]},
                [candidate("representative"), candidate("duplicate")],
            )

    def test_nonselect_has_candidate_local_standalone_reason(self) -> None:
        observe = row("narrow", "observe")
        validate_editorial(RUN, {"run_id": RUN, "topics": [observe]}, [candidate("narrow")])
        observe.pop("standalone_eligibility")
        with self.assertRaisesRegex(WorkflowConflict, "editorial_standalone_eligibility_missing"):
            validate_editorial(RUN, {"run_id": RUN, "topics": [observe]}, [candidate("narrow")])


if __name__ == "__main__":
    unittest.main()
