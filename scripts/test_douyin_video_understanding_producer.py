from __future__ import annotations

import json
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest import mock

import douyin_video_understanding_producer as producer
import run_daily_workflow as workflow


class ProducerTest(unittest.TestCase):
    def test_policy_is_or_semantics_without_fixed_engagement_threshold(self):
        rows = [
            {"aweme_id": "1", "title": "AI 工作流", "discovery_source": "recommendation",
             "likes": 0, "comments": 0, "favorites": 0, "shares": 0},
            {"aweme_id": "2", "title": "普通标题", "discovery_source": "recommendation",
             "likes": 900, "comments": 20, "favorites": 40, "shares": 10},
            {"aweme_id": "3", "title": "新角度", "discovery_source": "dynamic_search",
             "likes": 0, "comments": 0, "favorites": 0, "shares": 0},
        ]
        decisions = {row["candidate_id"]: row for row in producer.policy_decisions(rows)}
        self.assertIn("title_value", decisions["douyin:1"]["reasons"])
        self.assertIn("engagement_relative", decisions["douyin:2"]["reasons"])
        self.assertIn("exploration", decisions["douyin:3"]["reasons"])
        self.assertNotIn("requires_title_and_engagement", json.dumps(decisions))

    def test_runtime_missing_is_typed(self):
        with self.assertRaisesRegex(producer.ProducerError, "video_ffmpeg_missing"):
            producer.validate_runtime({})

    def test_atomic_json_duplicate_is_no_churn_and_conflict_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "value.json"
            producer.atomic_json(path, {"ok": True})
            before = path.read_bytes()
            producer.atomic_json(path, {"ok": True})
            self.assertEqual(path.read_bytes(), before)
            with self.assertRaisesRegex(producer.ProducerError, "producer_artifact_conflict"):
                producer.atomic_json(path, {"ok": False})
            self.assertEqual(path.read_bytes(), before)

    def test_normal_mode_rejects_caller_packages_before_business(self):
        args = Namespace(
            publisher_url="http://127.0.0.1:1",
            publisher_identity="qa",
            video_mode="normal",
            video_candidates="/tmp/candidates.json",
            video_decisions="",
            video_packages="",
            video_runtime_config="",
        )
        with mock.patch.dict(
            "os.environ",
            {
                "WEBSITE_PROJECTION_BEARER": "app",
                "WEBSITE_PROJECTION_SIWC_BYPASS_BEARER": "machine",
            },
        ):
            with self.assertRaisesRegex(RuntimeError, "normal_video_fixture_input_forbidden"):
                workflow.validate_runtime(args)

    def test_product_sources_do_not_reference_prior_private_tmp_producer(self):
        root = Path(__file__).parent
        text = "\n".join(
            (root / name).read_text()
            for name in (
                "douyin_video_understanding_producer.py",
                "douyin_video_discovery.mjs",
                "run_daily_workflow.py",
            )
        )
        for forbidden in (
            "/private/tmp/ar050_vision_ocr",
            "/private/tmp/ar050_real_understanding",
            "/private/tmp/ar050_resume_20260727/packages",
        ):
            self.assertNotIn(forbidden, text)

    def test_discovered_video_enters_exact_collection_without_substitute(self):
        collection = {
            "run_id": "run_20260727_080000",
            "business_date": "2026-07-27",
            "content_items": [],
            "candidates": [],
        }
        state = {
            "raw_candidates": [{
                "aweme_id": "12345678901",
                "author": "账号",
                "title": "AI 工具",
                "source_url": "https://www.douyin.com/video/12345678901",
                "published_at": "1",
            }],
            "packages": [{
                "aweme_id": "12345678901",
                "status": "completed",
                "asr": {"text": "当前视频语音"},
                "caption_timeline": [],
            }],
        }
        merged = workflow.merge_discovered_collection(collection, state)
        self.assertEqual(merged["content_items"][0]["content_fingerprint"], "douyin:12345678901")
        self.assertEqual(merged["content_items"][0]["body"], "当前视频语音")
        self.assertEqual(merged["candidates"][0]["candidate_id"], "douyin:12345678901")

    def test_discovery_failure_is_typed_and_persisted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            args = Namespace(
                mode="normal", video_mode="normal", cdp="http://127.0.0.1:9333",
                search_query="AI", output_root=str(root), artifact_root=str(root),
            )
            failed = mock.Mock(returncode=2, stdout='{"error":"verification_required"}', stderr="")
            with mock.patch.object(producer.subprocess, "run", return_value=failed):
                with self.assertRaisesRegex(producer.ProducerError, "verification_required"):
                    producer.load_discovery(args, "run_20260727_080000", root)
            artifact = root / "run_20260727_080000/video_producer/discovery.failure.json"
            self.assertEqual(json.loads(artifact.read_text())["failure"], "verification_required")
            self.assertEqual(json.loads(artifact.read_text())["substitute_count"], 0)

    def test_cleanup_failure_overrides_prior_local_failure(self):
        candidate = {
            "aweme_id": "123", "source_url": "https://www.douyin.com/video/123",
            "author": "A", "title": "AI", "published_at": "1",
            "discovery_source": "dynamic_search", "raw_identity": "raw",
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = {
                "ffmpeg": root / "ffmpeg",
                "vision_ocr_binary": root / "vision",
                "sensevoice_python": root / "python",
                "sensevoice_model": root / "sense",
                "fsmn_vad_model": root / "vad",
            }
            with mock.patch.object(
                producer, "download", side_effect=producer.ProducerError("video_media_fetch_failed")
            ), mock.patch.object(producer.shutil, "rmtree", side_effect=OSError("busy")):
                result = producer.process_one(
                    candidate, run_id="run_20260727_080000", config={},
                    runtime=runtime, work_root=root / "work",
                    keyframe_root=root / "keys", trigger="automatic",
                )
            self.assertEqual(result["status"], "failed")
            self.assertTrue(result["failure"].startswith("video_cleanup_failed:"))
            self.assertEqual(result["temporary_media_remaining"], 1)

    def test_parameter_fact_is_explicit(self):
        facts, _ = producer.screen_facts(
            "AI", [{"text": "temperature=0.7 model:claude-3", "start": 0, "end": 1}]
        )
        self.assertIn("parameter", {row["kind"] for row in facts})


if __name__ == "__main__":
    unittest.main()
