import unittest

from run_daily_workflow import (
    WorkflowConflict,
    merge_video_discovery_checkpoint,
    normalize_source_ledger,
    validate_editorial,
    validate_scripts,
)


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


class SourceLedgerTests(unittest.TestCase):
    def ledger(self):
        return [
            {"source": "configured_account", "attempted": True, "status": "completed",
             "discovered_count": 2, "reason": ""},
            {"source": "recommendation", "attempted": True, "status": "completed_empty",
             "discovered_count": 0, "reason": "no_safe_visible_candidates"},
            {"source": "dynamic_search", "attempted": True, "status": "partial",
             "discovered_count": 1, "reason": "one_card_missing_optional_metadata"},
        ]

    def candidates(self):
        return [
            {"discovery_source": "configured_account"},
            {"discovery_source": "configured_account"},
            {"discovery_source": "dynamic_search"},
        ]

    def test_source_local_empty_and_partial_keep_safe_survivors(self):
        rows = normalize_source_ledger(
            {"source_ledger": self.ledger()},
            video_candidates=self.candidates(),
        )
        self.assertEqual([row["discovered_count"] for row in rows], [2, 0, 1])

    def test_every_source_must_be_truthfully_attempted(self):
        with self.assertRaisesRegex(
            WorkflowConflict, "source_ledger_attempt_missing:recommendation",
        ):
            normalize_source_ledger(
                {"source_ledger": [self.ledger()[0], self.ledger()[2]]},
                video_candidates=self.candidates(),
            )

    def test_ledger_count_must_match_merged_candidates(self):
        ledger = self.ledger()
        ledger[0]["discovered_count"] = 3
        with self.assertRaisesRegex(
            WorkflowConflict, "source_ledger_count_conflict:configured_account",
        ):
            normalize_source_ledger(
                {"source_ledger": ledger}, video_candidates=self.candidates(),
            )

    def test_public_handoff_merges_configured_and_partial_discovery(self):
        collection = {
            "content_items": [{
                "aweme_id": "1", "source_url": "https://www.douyin.com/video/1",
            }],
            "candidates": [{
                "aweme_id": "1", "source_url": "https://www.douyin.com/video/1",
                "discovery_source": "configured_account",
            }],
            "configured_account_status": "completed",
            "configured_account_reason": "",
        }
        checkpoint = {
            "status": "completed",
            "candidates": [{
                "run_id": "", "aweme_id": "2",
                "source_url": "https://www.douyin.com/video/2",
                "discovery_source": "dynamic_search",
            }],
            "source_ledger": [
                {"source": "recommendation", "status": "completed_empty",
                 "reason": "no_safe_visible_candidates"},
                {"source": "dynamic_search", "status": "completed", "reason": ""},
            ],
        }
        merged = merge_video_discovery_checkpoint(
            collection, checkpoint, run_id="run_20260729_120000",
        )
        self.assertEqual(
            [row["discovered_count"] for row in merged["source_ledger"]],
            [1, 0, 1],
        )
        self.assertEqual(len(merged["candidates"]), 2)
        self.assertEqual(len(merged["content_items"]), 2)


class ScriptBatchCoverageTests(unittest.TestCase):
    def test_both_batch_skills_cover_every_selected_topic(self):
        selected = {"topic-a", "topic-b"}
        result = {
            "run_id": "run_20260729_120000",
            "scripts": [
                {
                    "topic_id": identity, "title": f"title-{identity}",
                    "hook": "hook", "structure": "structure", "body": "body",
                }
                for identity in sorted(selected)
            ],
            "failures": [],
        }
        validate_scripts("run_20260729_120000", result, selected)
        self.assertEqual(
            {row["topic_id"] for row in result["scripts"]}, selected,
        )

    def test_batch_identity_omission_fails_closed(self):
        with self.assertRaisesRegex(
            WorkflowConflict, "script_result_coverage_incomplete",
        ):
            validate_scripts(
                "run_20260729_120000",
                {
                    "run_id": "run_20260729_120000",
                    "scripts": [{
                        "topic_id": "topic-a", "title": "title",
                        "hook": "hook", "structure": "structure", "body": "body",
                    }],
                    "failures": [],
                },
                {"topic-a", "topic-b"},
            )


if __name__ == "__main__":
    unittest.main()
