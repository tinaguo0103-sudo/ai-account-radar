import unittest

from run_daily_workflow import WorkflowConflict, validate_editorial


class EditorialCoverageTests(unittest.TestCase):
    def candidates(self, count=66):
        return [{"candidate_id": f"candidate-{index}"} for index in range(count)]

    def result(self, count=66, selected=2):
        return {
            "run_id": "run_20260729_120000",
            "topics": [{
                "candidate_id": f"candidate-{index}",
                "decision": "select" if index < selected else "observe",
                "title": f"title-{index}" if index < selected else "",
                "hook": "hook" if index < selected else "",
                "structure": "structure" if index < selected else "",
                "selection_reason": "reason" if index < selected else "not selected",
            } for index in range(count)],
        }

    def test_sixty_six_in_sixty_six_out_and_multi_select(self):
        validate_editorial(
            "run_20260729_120000", self.result(), self.candidates(),
        )

    def test_silent_omission_fails_closed(self):
        with self.assertRaisesRegex(
            WorkflowConflict, "editorial_result_coverage_incomplete",
        ):
            validate_editorial(
                "run_20260729_120000", self.result(65), self.candidates(),
            )

    def test_zero_select_is_valid_when_every_candidate_is_explained(self):
        validate_editorial(
            "run_20260729_120000", self.result(selected=0), self.candidates(),
        )


if __name__ == "__main__":
    unittest.main()
