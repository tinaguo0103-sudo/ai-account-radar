from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest import mock

from daily_workflow import DailyWorkflow, WorkflowConflict
from run_daily_workflow import (
    editorial_handoff_candidates,
    enrich,
    validate_editorial_screening,
    validate_editorial_understanding_consistency,
)


RUN_ID = "run_20260811_080000"
BUSINESS_DATE = "2026-08-11"


def collection() -> dict:
    rows = [
        {
            "aweme_id": "901",
            "source": "Douyin",
            "source_url": "https://www.douyin.com/video/901",
            "title": "低互动但有具体冲突",
            "summary": "一个被忽视的真实工作现场",
            "likes": 1,
            "published_at": "2026-08-11T01:00:00Z",
        },
        {
            "aweme_id": "902",
            "source": "Douyin",
            "source_url": "https://www.douyin.com/video/902",
            "title": "高互动但内容很空",
            "summary": "功能口号，没有可验证动作",
            "likes": 9000,
            "published_at": "2026-08-11T02:00:00Z",
        },
        {
            "external_id": "903",
            "source": "AIHOT",
            "source_url": "https://example.com/903",
            "title": "官方说明",
            "summary": "没有视频，仅作为完整候选池信号",
            "likes": None,
            "published_at": "2026-08-11T03:00:00Z",
        },
    ]
    return {
        "run_id": RUN_ID,
        "business_date": BUSINESS_DATE,
        "content_items": rows,
        "candidates": [
            dict(row, candidate_id=f"candidate-{row.get('aweme_id') or row.get('external_id')}")
            for row in rows
        ],
    }


def args(**overrides):
    values = {
        "run_id": RUN_ID,
        "business_date": BUSINESS_DATE,
        "video_mode": "normal",
        "qa_frozen_packages": None,
    }
    values.update(overrides)
    return Namespace(**values)


class ModelOwnedEditorialFunnelTests(unittest.TestCase):
    def test_editorial_handoff_is_compact_but_keeps_full_identity_and_video_contract(self):
        normalized = enrich(args(), collection(), requested_candidate_ids=set())
        cards = editorial_handoff_candidates(normalized)
        full_size = len(json.dumps(normalized["candidates"], ensure_ascii=False))
        handoff_size = len(json.dumps(cards, ensure_ascii=False))
        self.assertEqual({row["candidate_id"] for row in cards}, {
            row["candidate_id"] for row in normalized["candidates"]
        })
        self.assertLess(handoff_size, full_size)
        self.assertNotIn("cluster_synthesis", cards[0])
        self.assertIn("sources", cards[0])
        self.assertIsNone(cards[0]["video_evidence"])

    def test_editorial_handoff_keeps_requested_video_observations(self):
        normalized = enrich(args(), collection(), requested_candidate_ids=set())
        cards = editorial_handoff_candidates(normalized)
        requested = cards[0]["candidate_id"]
        screening = {
            "run_id": RUN_ID,
            "screening": [
                {
                    "candidate_id": row["candidate_id"],
                    "request_deep_read": row["candidate_id"] == requested,
                    "reason": "candidate-local reason",
                }
                for row in cards
            ],
        }
        rows, ids = validate_editorial_screening(RUN_ID, screening, cards)
        with tempfile.TemporaryDirectory() as tmp:
            frame = Path(tmp) / "frame.jpg"
            frame.write_bytes(b"frame")
            package_path = Path(tmp) / "packages.json"
            package_path.write_text(json.dumps([{
                "run_id": RUN_ID,
                "source_url": cards[0]["editorial_screening"]["available_video_source_ids"][0],
                "status": "completed",
                "asr": {"text": "现场旁白"},
                "screen_facts": [{"kind": "ocr", "text": "画面文字", "time_second": 1}],
                "keyframes": [{
                    "path": str(frame),
                    "time_second": 1,
                    "sha256": hashlib.sha256(frame.read_bytes()).hexdigest(),
                }],
            }]), encoding="utf-8")
            enriched = enrich(
                args(qa_frozen_packages=str(package_path)),
                normalized,
                requested_candidate_ids=ids,
                screening_rows=rows,
            )
        handoff = editorial_handoff_candidates(enriched)
        observed = next(row for row in handoff if row["candidate_id"] == requested)
        representative = observed["video_evidence"]["representative_sources"][0]
        self.assertEqual(representative["asr_supplement"], "现场旁白")
        self.assertEqual(representative["screen_facts"][0]["text"], "画面文字")
        self.assertEqual(representative["keyframes"][0]["time_second"], 1)
        self.assertNotIn("cluster_synthesis", observed["video_evidence"])

    def test_initial_handoff_contains_all_trusted_cards_not_traffic_gate(self):
        normalized = enrich(args(), collection(), requested_candidate_ids=set())
        cards = editorial_handoff_candidates(normalized)
        self.assertEqual(len(cards), 3)
        self.assertEqual(normalized["trusted_candidate_count"], 3)
        self.assertEqual(normalized["representative_source_count"], 0)
        self.assertEqual(normalized["deep_read_summary"]["editorial_candidate_total"], 3)
        self.assertTrue(all("editorial_screening" in row for row in cards))

    def test_screening_is_one_to_one_and_only_requested_video_is_produced(self):
        normalized = enrich(args(), collection(), requested_candidate_ids=set())
        cards = editorial_handoff_candidates(normalized)
        requested = cards[0]["candidate_id"]
        screening = {
            "run_id": RUN_ID,
            "screening": [
                {
                    "candidate_id": row["candidate_id"],
                    "request_deep_read": row["candidate_id"] == requested,
                    "reason": f"candidate-local reason {index}",
                }
                for index, row in enumerate(cards)
            ],
        }
        rows, ids = validate_editorial_screening(RUN_ID, screening, cards)
        self.assertEqual(ids, {requested})
        with tempfile.TemporaryDirectory() as tmp:
            frame = Path(tmp) / "frame.jpg"
            frame.write_bytes(b"frame")
            package = {
                "run_id": RUN_ID,
                "source_url": cards[0]["editorial_screening"]["available_video_source_ids"][0],
                "status": "completed",
                "keyframes": [{
                    "path": str(frame),
                    "time_second": 0,
                    "sha256": hashlib.sha256(frame.read_bytes()).hexdigest(),
                }],
            }
            with mock.patch(
                "run_daily_workflow.produce",
                return_value={"packages": [package], "failures": []},
            ) as producer:
                enriched = enrich(
                    args(),
                    normalized,
                    requested_candidate_ids=ids,
                    screening_rows=rows,
                )
        produced = producer.call_args.kwargs["discovered_candidates"]
        self.assertEqual(
            {row["source_url"] for row in produced},
            set(enriched["screening_requested_source_ids"]),
        )
        self.assertEqual(enriched["screening_requested_candidate_ids"], [requested])
        self.assertEqual(enriched["deep_read_summary"]["editorial_candidate_total"], 3)

    def test_screening_rejects_missing_candidate_and_non_video_request(self):
        normalized = enrich(args(), collection(), requested_candidate_ids=set())
        cards = editorial_handoff_candidates(normalized)
        incomplete = {
            "run_id": RUN_ID,
            "screening": [{
                "candidate_id": cards[0]["candidate_id"],
                "request_deep_read": False,
                "reason": "only one row",
            }],
        }
        with self.assertRaisesRegex(WorkflowConflict, "editorial_screening_coverage_incomplete"):
            validate_editorial_screening(RUN_ID, incomplete, cards)
        non_video = {
            "run_id": RUN_ID,
            "screening": [{
                "candidate_id": row["candidate_id"],
                "request_deep_read": not row["editorial_screening"]["available_video_source_ids"],
                "reason": "candidate-local reason",
            } for row in cards],
        }
        with self.assertRaisesRegex(WorkflowConflict, "editorial_screening_video_request_invalid"):
            validate_editorial_screening(RUN_ID, non_video, cards)

    def test_requested_video_failure_is_item_local_and_unrequested_stays_not_requested(self):
        normalized = enrich(args(), collection(), requested_candidate_ids=set())
        cards = editorial_handoff_candidates(normalized)
        requested = cards[0]["candidate_id"]
        screening = {
            "run_id": RUN_ID,
            "screening": [
                {
                    "candidate_id": row["candidate_id"],
                    "request_deep_read": row["candidate_id"] == requested,
                    "reason": "candidate-local reason",
                }
                for row in cards
            ],
        }
        rows, ids = validate_editorial_screening(RUN_ID, screening, cards)
        with tempfile.TemporaryDirectory() as tmp:
            package_path = Path(tmp) / "packages.json"
            package_path.write_text(json.dumps([{
                "run_id": RUN_ID,
                "source_url": cards[0]["editorial_screening"]["available_video_source_ids"][0],
                "status": "failed",
                "failure": "qa_private_video_fixture_failed",
            }]), encoding="utf-8")
            enriched = enrich(
                args(qa_frozen_packages=str(package_path)),
                normalized,
                requested_candidate_ids=ids,
                screening_rows=rows,
            )
        requested_card = next(row for row in enriched["candidates"] if row["candidate_id"] == requested)
        unrequested_card = next(row for row in enriched["candidates"] if row["candidate_id"] != requested)
        self.assertEqual(requested_card["deep_read"]["status"], "understanding_failed")
        self.assertEqual(requested_card["deep_read"]["failed_count"], 1)
        self.assertEqual(unrequested_card["deep_read"]["status"], "not_requested")
        self.assertEqual(unrequested_card["deep_read"]["failed_count"], 0)

    def test_existing_stage_authority_allows_screening_resume_update(self):
        with tempfile.TemporaryDirectory() as tmp:
            workflow = DailyWorkflow(Path(tmp) / "workflow.sqlite3")
            workflow.begin(RUN_ID, BUSINESS_DATE)
            workflow.commit_stage(
                RUN_ID, "collection_enrichment", {"run_id": RUN_ID}, "in_progress",
            )
            workflow.commit_stage(
                RUN_ID, "editorial", {"run_id": RUN_ID, "phase": "screening_complete"},
                "in_progress",
            )
            workflow.commit_stage(
                RUN_ID, "collection_enrichment", {"run_id": RUN_ID, "enriched": True},
                "completed",
            )
            self.assertEqual(
                workflow.stage(RUN_ID, "collection_enrichment")["status"],
                "completed",
            )
            self.assertEqual(
                workflow.stage(RUN_ID, "editorial")["status"],
                "in_progress",
            )

    def test_completed_material_cannot_be_submitted_as_failed(self):
        collection_value = {
            "candidates": [{
                "candidate_id": "candidate-completed",
                "deep_read": {"status": "completed"},
                "sources": [{
                    "source_id": "source-completed",
                    "understanding_status": "analyzed",
                }],
            }],
            "understanding_results": [{
                "candidate_id": "candidate-completed",
                "package": {"status": "completed", "run_id": RUN_ID},
            }],
        }
        result = {
            "run_id": RUN_ID,
            "topics": [{
                "candidate_id": "candidate-completed",
                "decision": "failed",
                "evidence_source_ids": ["source-completed"],
            }],
        }
        with self.assertRaisesRegex(
            WorkflowConflict, "editorial_completed_material_conflict",
        ):
            validate_editorial_understanding_consistency(RUN_ID, result, collection_value)

    def test_completed_material_requires_analyzed_source_evidence(self):
        collection_value = {
            "candidates": [{
                "candidate_id": "candidate-completed",
                "deep_read": {"status": "completed"},
                "sources": [{
                    "source_id": "source-completed",
                    "understanding_status": "analyzed",
                }],
            }],
            "understanding_results": [{
                "candidate_id": "candidate-completed",
                "package": {"status": "completed", "run_id": RUN_ID},
            }],
        }
        result = {
            "run_id": RUN_ID,
            "topics": [{
                "candidate_id": "candidate-completed",
                "decision": "observe",
                "evidence_source_ids": ["metadata-only"],
            }],
        }
        with self.assertRaisesRegex(
            WorkflowConflict, "editorial_completed_evidence_missing",
        ):
            validate_editorial_understanding_consistency(RUN_ID, result, collection_value)

    def test_not_attempted_material_can_remain_item_local_failed(self):
        collection_value = {
            "candidates": [{
                "candidate_id": "candidate-pending",
                "deep_read": {"status": "not_attempted"},
                "sources": [{
                    "source_id": "source-pending",
                    "understanding_status": "metadata_only",
                }],
            }],
            "understanding_results": [],
        }
        result = {
            "run_id": RUN_ID,
            "topics": [{
                "candidate_id": "candidate-pending",
                "decision": "failed",
                "evidence_source_ids": ["source-pending"],
            }],
        }
        validate_editorial_understanding_consistency(RUN_ID, result, collection_value)


if __name__ == "__main__":
    unittest.main()
