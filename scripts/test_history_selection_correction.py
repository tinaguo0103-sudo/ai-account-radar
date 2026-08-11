import unittest

from run_daily_workflow import (
    WorkflowConflict,
    merge_video_discovery_checkpoint,
    normalize_source_ledger,
    stable_item_id,
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
                "editorial_thesis": {
                    "thesis": f"candidate-{index} has a source-grounded judgment.",
                    "audience_conflict": f"candidate-{index} creates a concrete audience choice.",
                    "why_now": "The exact run supplies a timely fact.",
                    "evidence_boundary": {
                        "source_facts": "The source-owned fact is present in this run.",
                        "interpretation": "The fact supports a candidate-local judgment.",
                        "proposed_test": "A bounded test can check the interpretation.",
                    },
                } if index < selected else None,
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
            {"item_id": "douyin:1", "discovery_source": "configured_account",
             "discovery_sources": ["configured_account"]},
            {"item_id": "douyin:2", "discovery_source": "configured_account",
             "discovery_sources": ["configured_account"]},
            {"item_id": "douyin:3", "discovery_source": "dynamic_search",
             "discovery_sources": ["dynamic_search"]},
        ]

    def collection(self, ledger=None):
        return {
            "source_ledger": ledger or self.ledger(),
            "source_local_identities": {
                "configured_account": ["douyin:1", "douyin:2"],
                "recommendation": [],
                "dynamic_search": ["douyin:3"],
            },
        }

    def test_source_local_empty_and_partial_keep_safe_survivors(self):
        rows = normalize_source_ledger(
            self.collection(),
            video_candidates=self.candidates(),
        )
        self.assertEqual([row["discovered_count"] for row in rows], [2, 0, 1])

    def test_every_source_must_be_truthfully_attempted(self):
        with self.assertRaisesRegex(
            WorkflowConflict, "source_ledger_attempt_missing:recommendation",
        ):
            normalize_source_ledger(
                self.collection([self.ledger()[0], self.ledger()[2]]),
                video_candidates=self.candidates(),
            )

    def test_ledger_count_must_match_merged_candidates(self):
        ledger = self.ledger()
        ledger[0]["discovered_count"] = 3
        with self.assertRaisesRegex(
            WorkflowConflict, "source_ledger_count_conflict:configured_account",
        ):
            normalize_source_ledger(
                self.collection(ledger), video_candidates=self.candidates(),
            )

    def test_cross_source_overlap_keeps_both_ledgers_and_one_global_candidate(self):
        collection = {
            "content_items": [{"aweme_id": "1", "source_url": "https://www.douyin.com/video/1"}],
            "candidates": [{
                "aweme_id": "1", "source_url": "https://www.douyin.com/video/1",
                "discovery_source": "configured_account", "title": "configured",
            }],
            "configured_account_status": "completed", "configured_account_reason": "",
        }
        checkpoint = {
            "status": "completed",
            "candidates": [{
                "run_id": "", "aweme_id": "1", "source_url": "https://www.douyin.com/video/1",
                "discovery_source": "dynamic_search", "title": "dynamic",
            }],
            "source_ledger": [
                {"source": "recommendation", "status": "completed_empty", "reason": "none"},
                {"source": "dynamic_search", "status": "completed", "reason": ""},
            ],
        }
        merged = merge_video_discovery_checkpoint(collection, checkpoint, run_id="run_20260802_104213")
        from run_daily_workflow import normalize_collection_candidates, normalize_items
        items, _ = normalize_items(merged["content_items"])
        _, videos, _ = normalize_collection_candidates(
            merged["candidates"], items=items, run_id="run_20260802_104213",
        )
        rows = normalize_source_ledger(merged, video_candidates=videos)
        self.assertEqual([row["discovered_count"] for row in rows], [1, 0, 1])
        self.assertEqual(len(videos), 1)
        self.assertEqual(videos[0]["discovery_sources"], ["configured_account", "dynamic_search"])

    def test_same_source_duplicate_fails_closed(self):
        checkpoint = {
            "status": "completed",
            "candidates": [{
                "run_id": "", "aweme_id": "2", "source_url": "https://www.douyin.com/video/2",
                "discovery_source": "dynamic_search",
            }] * 2,
            "source_ledger": [
                {"source": "recommendation", "status": "completed_empty", "reason": "none"},
                {"source": "dynamic_search", "status": "completed", "reason": ""},
            ],
        }
        with self.assertRaisesRegex(WorkflowConflict, "source_ledger_source_duplicate:dynamic_search"):
            merge_video_discovery_checkpoint({
                "content_items": [], "candidates": [],
                "configured_account_status": "completed_empty", "configured_account_reason": "",
            }, checkpoint, run_id="run_20260802_104213")

    def test_missing_source_identity_fails_closed(self):
        collection = self.collection()
        collection["source_local_identities"]["dynamic_search"] = []
        with self.assertRaisesRegex(WorkflowConflict, "source_ledger_count_conflict:dynamic_search"):
            normalize_source_ledger(collection, video_candidates=self.candidates())

    def test_cross_source_overlap_replay_is_deterministic(self):
        collection = {
            "content_items": [{"aweme_id": "1", "source_url": "https://www.douyin.com/video/1"}],
            "candidates": [{
                "aweme_id": "1", "source_url": "https://www.douyin.com/video/1",
                "discovery_source": "configured_account", "title": "configured",
            }],
            "configured_account_status": "completed", "configured_account_reason": "",
        }
        checkpoint = {
            "status": "completed",
            "candidates": [
                {
                    "run_id": "", "aweme_id": "2", "source_url": "https://www.douyin.com/video/2",
                    "discovery_source": "dynamic_search", "title": "second",
                },
                {
                    "run_id": "", "aweme_id": "1", "source_url": "https://www.douyin.com/video/1",
                    "discovery_source": "dynamic_search", "title": "overlap",
                },
            ],
            "source_ledger": [
                {"source": "recommendation", "status": "completed_empty", "reason": "none"},
                {"source": "dynamic_search", "status": "completed", "reason": ""},
            ],
        }
        first = merge_video_discovery_checkpoint(collection, checkpoint, run_id="run_20260802_104213")
        second = merge_video_discovery_checkpoint(collection, checkpoint, run_id="run_20260802_104213")
        self.assertEqual(first, second)
        self.assertEqual(first["source_local_identities"]["dynamic_search"], ["douyin:1", "douyin:2"])
        overlap = next(row for row in first["candidates"] if stable_item_id(row) == "douyin:1")
        self.assertEqual(overlap["discovery_sources"], ["configured_account", "dynamic_search"])

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
