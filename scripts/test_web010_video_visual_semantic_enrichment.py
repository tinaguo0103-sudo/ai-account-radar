from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import douyin_video_understanding_producer as producer
import run_daily_workflow as workflow
from trend_hotspot_cards import attach_understanding, viewable_keyframe_failure


RUN_ID = "run_20260804_080138"
DATE = "2026-08-04"


class VideoVisualSemanticEnrichmentTests(unittest.TestCase):
    def _card_with_sources(self, source_rows, representative_ids):
        for source in source_rows:
            source.setdefault("understanding_status", "pending")
        return {
            "candidate_id": "trend:visual-aggregate",
            "source_url": source_rows[0]["url"],
            "event_name": "visual aggregate event",
            "representative_item_id": source_rows[0]["item_id"],
            "representative_source_ids": representative_ids,
            "qualification": {"eligible_for_deep_read": True},
            "sources": source_rows,
            "differentiation": {},
            "persona_stability": {},
        }

    def _package(self, url, frame_path, run_id=RUN_ID):
        payload = frame_path.read_bytes()
        return {
            "run_id": run_id,
            "source_url": url,
            "status": "completed",
            "keyframes": [{
                "time_second": 0,
                "path": str(frame_path),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }],
        }

    def test_representative_frames_cover_start_middle_end_without_duplicates(self):
        self.assertEqual(producer.representative_frame_indices(0), [])
        self.assertEqual(producer.representative_frame_indices(1), [0])
        self.assertEqual(producer.representative_frame_indices(2), [0, 1])
        self.assertEqual(producer.representative_frame_indices(6), [0, 3, 5])

    def test_viewable_keyframes_require_same_run_path_and_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "frame.jpg"
            payload = b"qa-private-keyframe"
            path.write_bytes(payload)
            package = {
                "run_id": RUN_ID,
                "keyframes": [{
                    "time_second": 4,
                    "path": str(path),
                    "sha256": hashlib.sha256(payload).hexdigest(),
                }],
            }
            self.assertEqual(viewable_keyframe_failure(package, run_id=RUN_ID), "")
            self.assertEqual(
                viewable_keyframe_failure(package, run_id="run_20260805_080110"),
                "video_keyframe_run_mismatch",
            )
            path.write_bytes(b"changed")
            self.assertEqual(
                viewable_keyframe_failure(package, run_id=RUN_ID),
                "video_keyframe_hash_mismatch",
            )

    def test_missing_visual_package_is_item_local_and_not_substituted(self):
        card = {
            "candidate_id": "trend:visual",
            "source_url": "https://www.douyin.com/video/visual",
            "event_name": "visual event",
            "representative_item_id": "douyin:visual",
            "representative_source_ids": ["source:visual"],
            "qualification": {"eligible_for_deep_read": True},
            "sources": [{
                "source_id": "source:visual",
                "item_id": "douyin:visual",
                "url": "https://www.douyin.com/video/visual",
                "title": "title-only fallback must not pass",
                "source_role": "primary",
                "understanding_status": "pending",
            }],
            "differentiation": {},
            "persona_stability": {},
        }
        cards, results = attach_understanding(
            [card],
            [{
                "run_id": RUN_ID,
                "source_url": "https://www.douyin.com/video/visual",
                "status": "completed",
                "title": "title-only fallback must not pass",
                "asr": {"text": "摘要不能替代画面"},
                "keyframes": [],
            }],
            [],
            require_viewable_keyframes=True,
            run_id=RUN_ID,
        )
        self.assertEqual(cards[0]["review_stage"], "understanding_failed")
        self.assertEqual(cards[0]["deep_read"]["failed_count"], 1)
        self.assertEqual(cards[0]["sources"][0]["understanding_failure"], "video_keyframes_missing")
        self.assertEqual(results, [])

    def test_aggregate_run_id_uses_explicit_run_when_tail_source_has_no_package(self):
        with tempfile.TemporaryDirectory() as tmp:
            frame = Path(tmp) / "representative.jpg"
            frame.write_bytes(b"representative-frame")
            first = {
                "source_id": "source:representative",
                "item_id": "douyin:representative",
                "url": "https://www.douyin.com/video/representative",
                "title": "代表来源",
                "source_role": "primary",
            }
            tail = {
                "source_id": "source:tail-without-package",
                "item_id": "douyin:tail-without-package",
                "url": "https://www.douyin.com/video/tail-without-package",
                "title": "尾部无包来源",
                "source_role": "supporting",
            }
            cards, results = attach_understanding(
                [self._card_with_sources([first, tail], [first["source_id"]])],
                [self._package(first["url"], frame)],
                [],
                require_viewable_keyframes=True,
                run_id=RUN_ID,
            )
            self.assertEqual(cards[0]["review_stage"], "ready_for_editorial")
            self.assertEqual(results[0]["package"]["run_id"], RUN_ID)
            self.assertEqual(results[0]["package"]["representative_packages"][0]["run_id"], RUN_ID)

    def test_aggregate_run_id_uses_first_validated_package_for_multi_package_compatibility(self):
        with tempfile.TemporaryDirectory() as tmp:
            frame_one = Path(tmp) / "one.jpg"
            frame_two = Path(tmp) / "two.jpg"
            frame_one.write_bytes(b"frame-one")
            frame_two.write_bytes(b"frame-two")
            first = {
                "source_id": "source:one",
                "item_id": "douyin:one",
                "url": "https://www.douyin.com/video/one",
                "title": "来源一",
                "source_role": "primary",
            }
            second = {
                "source_id": "source:two",
                "item_id": "douyin:two",
                "url": "https://www.douyin.com/video/two",
                "title": "来源二",
                "source_role": "supporting",
            }
            cards, results = attach_understanding(
                [self._card_with_sources([first, second], [first["source_id"], second["source_id"]])],
                [
                    self._package(first["url"], frame_one),
                    self._package(second["url"], frame_two),
                ],
                [],
                require_viewable_keyframes=True,
                run_id=RUN_ID,
            )
            self.assertEqual(cards[0]["review_stage"], "ready_for_editorial")
            self.assertEqual(cards[0]["deep_read"]["completed_count"], 2)
            self.assertEqual(results[0]["package"]["run_id"], RUN_ID)

    def test_wrong_run_package_remains_item_local_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            frame = Path(tmp) / "wrong-run.jpg"
            frame.write_bytes(b"wrong-run-frame")
            source = {
                "source_id": "source:wrong-run",
                "item_id": "douyin:wrong-run",
                "url": "https://www.douyin.com/video/wrong-run",
                "title": "错误 run 来源",
                "source_role": "primary",
            }
            card = self._card_with_sources([source], [source["source_id"]])
            cards, results = attach_understanding(
                [card],
                [self._package(source["url"], frame, run_id="run_20260805_080110")],
                [],
                require_viewable_keyframes=True,
                run_id=RUN_ID,
            )
            self.assertEqual(cards[0]["review_stage"], "understanding_failed")
            self.assertEqual(cards[0]["sources"][0]["understanding_failure"], "video_keyframe_run_mismatch")
            self.assertEqual(results, [])

    def test_paths_survive_editorial_and_writer_handoffs(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "frame.jpg"
            path.write_bytes(b"frame")
            keyframe = {
                "time_second": 12,
                "path": str(path),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
            url = "https://www.douyin.com/video/visual-handoff"
            package = {
                "source_url": url,
                "status": "completed",
                "representative_packages": [{
                    "run_id": RUN_ID,
                    "source_url": url,
                    "status": "completed",
                    "asr": {"text": "同一题的口播事实"},
                    "screen_text": [{"kind": "other", "text": "屏幕事实", "time_second": 12}],
                    "keyframes": [keyframe],
                    "unresolved_terms": [],
                }],
                "cluster_synthesis": {},
            }
            card = {
                "candidate_id": "trend:visual-handoff",
                "source_url": url,
                "event_name": "visual event",
                "representative_item_id": "douyin:visual-handoff",
                "representative_source_ids": ["source:visual-handoff"],
                "qualification": {"eligible_for_deep_read": True},
                "sources": [{
                    "source_id": "source:visual-handoff",
                    "item_id": "douyin:visual-handoff",
                    "url": url,
                    "title": "视觉候选",
                    "source_role": "primary",
                }],
                "differentiation": {},
                "persona_stability": {},
            }
            _, understanding_results = attach_understanding(
                [card], [package["representative_packages"][0]], [],
                require_viewable_keyframes=True,
                run_id=RUN_ID,
            )
            collection = {
                "candidates": [{
                    "candidate_id": "trend:visual-handoff",
                    "item_id": "douyin:visual-handoff",
                    "source_url": url,
                    "source_title": "视觉候选",
                    "source_summary": "同 run 事实",
                    "sources": [{
                        "source_id": "source:visual-handoff",
                        "url": url,
                        "title": "视觉候选",
                        "source_role": "primary",
                    }],
                }],
                "content_items": [{
                    "item_id": "douyin:visual-handoff",
                    "source_url": url,
                    "title": "视觉候选",
                }],
                "understanding_results": understanding_results,
            }
            editorial = {"topics": [{
                "candidate_id": "trend:visual-handoff",
                "decision": "select",
                "selection_reason": "同 run 画面与文本共同支持这个题。",
                "unique_judgment": "画面关系决定这个题不能只按标题理解。",
                "decision_basis": {"traffic": "fresh", "content": "frame", "persona": "fit", "differentiation": "visual"},
                "evidence_source_ids": ["source:visual-handoff"],
            }]}
            handoff = workflow.build_scripts_handoff(RUN_ID, DATE, collection, editorial)
            topic = handoff["selected_topics"][0]
            self.assertEqual(topic["video_understanding"]["run_id"], RUN_ID)
            self.assertTrue(topic["video_understanding"]["visual_reading"]["direct_view_required"])
            writer_frames = topic["video_understanding"]["representative_sources"][0]["keyframes"]
            self.assertEqual(writer_frames[0]["path"], str(path))
            self.assertEqual(writer_frames[0]["time_second"], 12)
            self.assertTrue(topic["video_understanding"]["representative_sources"][0]["visual_reading"]["direct_view_required"])


if __name__ == "__main__":
    unittest.main()
