from __future__ import annotations

import copy
import unittest

from run_daily_workflow import WorkflowConflict, validate_editorial


RUN = "run_20260816_101807"


def candidate(identity: str) -> dict:
    return {"candidate_id": identity}


def editorial_row(identity: str, decision: str = "observe") -> dict:
    return {
        "candidate_id": identity,
        "decision": decision,
        "selection_reason": f"candidate-specific reason for {identity}",
        "standalone_eligibility": {
            "decision": "select" if decision == "select" else "observe",
            "reason": f"standalone judgment for {identity}",
        },
    }


def result(selected: int, total: int = 12) -> dict:
    return {
        "run_id": RUN,
        "topics": [
            editorial_row(f"candidate-{index}", "select" if index < selected else "observe")
            for index in range(total)
        ],
    }


class DailySelectionLimitTests(unittest.TestCase):
    def candidates(self, total: int = 12) -> list[dict]:
        return [candidate(f"candidate-{index}") for index in range(total)]

    def test_zero_one_and_ten_select_are_accepted(self) -> None:
        for selected in (0, 1, 10):
            with self.subTest(selected=selected):
                validate_editorial(RUN, result(selected), self.candidates())

    def test_eleven_select_is_typed_reject_without_truncation(self) -> None:
        submitted = result(11)
        before = copy.deepcopy(submitted)
        with self.assertRaisesRegex(
            WorkflowConflict, "editorial_select_limit_exceeded",
        ):
            validate_editorial(RUN, submitted, self.candidates())
        self.assertEqual(submitted, before)
        self.assertEqual(
            sum(row["decision"] == "select" for row in submitted["topics"]),
            11,
        )

    def test_full_identity_and_nonselect_rows_are_not_dropped_at_limit(self) -> None:
        submitted = result(10, total=172)
        validate_editorial(RUN, submitted, self.candidates(total=172))
        self.assertEqual(len(submitted["topics"]), 172)
        self.assertEqual(
            {row["candidate_id"] for row in submitted["topics"]},
            {f"candidate-{index}" for index in range(172)},
        )


if __name__ == "__main__":
    unittest.main()
