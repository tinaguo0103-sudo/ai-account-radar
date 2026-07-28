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

    def test_runtime_preserves_virtualenv_python_launcher(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base_python = root / "base-python"
            base_python.write_text("")
            venv_python = root / "venv-python"
            venv_python.symlink_to(base_python)
            files = {
                "ffmpeg": root / "ffmpeg",
                "vision_ocr_binary": root / "vision",
                "sensevoice_python": venv_python,
                "sensevoice_model": root / "sense",
                "fsmn_vad_model": root / "vad",
            }
            for key, path in files.items():
                if key != "sensevoice_python":
                    path.touch()
            runtime = producer.validate_runtime({key: str(path) for key, path in files.items()})
            self.assertEqual(runtime["sensevoice_python"], venv_python.absolute())
            self.assertNotEqual(runtime["sensevoice_python"], base_python.resolve())

    def test_media_download_uses_public_browser_headers_without_credentials(self):
        response = mock.MagicMock()
        response.status = 200
        response.read.side_effect = [b"media", b""]
        response.__enter__.return_value = response
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "video.mp4"
            with mock.patch.object(producer.urllib.request, "urlopen", return_value=response) as opened:
                producer.download(
                    "https://example.douyinvod.com/video/tos/example",
                    destination,
                    10,
                    1024,
                    "https://www.douyin.com/video/12345678901",
                )
            request = opened.call_args.args[0]
            self.assertIn("Chrome/", request.headers["User-agent"])
            self.assertEqual(
                request.headers["Referer"],
                "https://www.douyin.com/video/12345678901",
            )
            self.assertNotIn("Cookie", request.headers)

    def test_media_download_rejects_non_exact_douyin_referer(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(producer.ProducerError, "video_media_referer_invalid"):
                producer.download(
                    "https://example.douyinvod.com/video/tos/example",
                    Path(tmp) / "video.mp4",
                    10,
                    1024,
                    "https://example.com/video/12345678901",
                )

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
                "douyin_video_media_resolver.mjs",
                "run_daily_workflow.py",
            )
        )
        for forbidden in (
            "/private/tmp/ar050_vision_ocr",
            "/private/tmp/ar050_real_understanding",
            "/private/tmp/ar050_resume_20260727/packages",
        ):
            self.assertNotIn(forbidden, text)

    def test_vision_ocr_is_offline_and_does_not_silently_require_language_correction(self):
        text = (Path(__file__).parent / "douyin_video_vision_ocr.swift").read_text()
        self.assertIn("recognitionLevel = .accurate", text)
        self.assertIn('recognitionLanguages = ["zh-Hans", "en-US"]', text)
        self.assertIn("usesLanguageCorrection = false", text)

    def test_discovery_uses_exact_recommendation_then_dynamic_search(self):
        text = (Path(__file__).parent / "douyin_video_discovery.mjs").read_text()
        recommendation = text.index("https://www.douyin.com/?recommend=1&from_nav=1")
        dynamic = text.index("dynamic_search", recommendation)
        self.assertLess(recommendation, dynamic)

    def test_media_resolver_preserves_exact_video_and_audio_tracks(self):
        text = (Path(__file__).parent / "douyin_video_media_resolver.mjs").read_text()
        self.assertIn("media-video-", text)
        self.assertIn("media-audio-", text)
        self.assertIn("audio_url", text)

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

    def test_failed_workflow_resume_loads_exact_producer_artifacts_without_discovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_id = "run_20260727_080000"
            producer_root = root / run_id / "video_producer"
            producer_root.mkdir(parents=True)
            raw = {
                "run_id": run_id,
                "aweme_id": "12345678901",
                "title": "AI 工具",
                "source_url": "https://www.douyin.com/video/12345678901",
                "discovery_source": "recommendation",
            }
            (producer_root / "discovery.json").write_text(json.dumps({
                "status": "completed",
                "candidates": [raw],
            }))
            (producer_root / "candidates.json").write_text(json.dumps([[raw]]))
            (producer_root / "decisions.json").write_text(json.dumps([{
                "candidate_id": "douyin:12345678901",
                "decision": "parse",
            }]))
            (producer_root / "packages.json").write_text(json.dumps([{
                "run_id": run_id,
                "aweme_id": "12345678901",
                "status": "completed",
            }]))
            state = workflow.load_committed_producer_state(Namespace(
                artifact_root=str(root),
                run_id=run_id,
            ))
            self.assertEqual(state["raw_candidates"], [raw])
            self.assertEqual(state["packages"][0]["aweme_id"], "12345678901")
            with mock.patch.object(
                producer, "load_discovery",
                side_effect=AssertionError("resume must not discover again"),
            ):
                self.assertEqual(len(state["packages"]), 1)

    def test_failed_workflow_resume_rejects_cross_run_producer_package(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_id = "run_20260727_080000"
            producer_root = root / run_id / "video_producer"
            producer_root.mkdir(parents=True)
            raw = {
                "run_id": run_id,
                "aweme_id": "12345678901",
                "source_url": "https://www.douyin.com/video/12345678901",
                "discovery_source": "recommendation",
            }
            (producer_root / "discovery.json").write_text(json.dumps({
                "status": "completed",
                "candidates": [raw],
            }))
            (producer_root / "candidates.json").write_text(json.dumps([[raw]]))
            (producer_root / "decisions.json").write_text("[]")
            (producer_root / "packages.json").write_text(json.dumps([{
                "run_id": run_id,
                "aweme_id": "99999999999",
                "status": "completed",
            }]))
            with self.assertRaisesRegex(
                RuntimeError, "video_producer_recovery_package_identity_invalid",
            ):
                workflow.load_committed_producer_state(Namespace(
                    artifact_root=str(root),
                    run_id=run_id,
                ))

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

    def test_exact_discovery_replay_reuses_run_artifact_without_browser(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_id = "run_20260727_080000"
            artifact = root / run_id / "video_producer/discovery.json"
            artifact.parent.mkdir(parents=True)
            payload = {
                "status": "completed",
                "candidates": [{
                    "run_id": run_id,
                    "aweme_id": "12345678901",
                    "source_url": "https://www.douyin.com/video/12345678901",
                }],
            }
            artifact.write_text(json.dumps(payload))
            args = Namespace(
                mode="normal", video_mode="normal", cdp="http://127.0.0.1:9333",
                search_query="AI",
            )
            with mock.patch.object(
                producer.subprocess, "run",
                side_effect=AssertionError("exact replay must not use browser"),
            ):
                rows = producer.load_discovery(args, run_id, root)
            self.assertEqual(rows[0]["aweme_id"], "12345678901")

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

    def test_media_total_deadline_is_bounded_and_partial_is_local(self):
        class SlowResponse:
            status = 200
            def __enter__(self):
                return self
            def __exit__(self, *_):
                return False
            def read(self, _):
                return b"x"

        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "video.mp4"
            with mock.patch.object(producer.urllib.request, "urlopen", return_value=SlowResponse()), \
                    mock.patch.object(producer.time, "monotonic", side_effect=[0, 2]):
                with self.assertRaisesRegex(
                    producer.ProducerError, "video_media_fetch_deadline_exceeded"
                ):
                    producer.download(
                        "https://example.com/video",
                        destination,
                        1,
                        100,
                        "https://www.douyin.com/video/12345678901",
                    )

    def test_asr_worker_uses_exact_configured_ffmpeg_in_run_scoped_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            audio = root / "audio.wav"
            audio.write_bytes(b"audio")
            ffmpeg = root / "ffmpeg-exact"
            ffmpeg.write_bytes(b"binary")
            result = audio.with_suffix(".sensevoice.json")

            def fake_command(args, error, *, env=None):
                self.assertEqual(error, "video_asr_failed")
                self.assertEqual((audio.parent / "runtime_bin/ffmpeg").resolve(), ffmpeg.resolve())
                self.assertEqual(str(audio.parent / "runtime_bin"), env["PATH"].split(":")[0])
                result.write_text(json.dumps({"text": "ok"}))
                return mock.Mock(returncode=0)

            with mock.patch.object(producer, "command", side_effect=fake_command):
                value = producer.asr_worker({
                    "sensevoice_python": root / "python",
                    "sensevoice_model": root / "sensevoice",
                    "fsmn_vad_model": root / "vad",
                    "ffmpeg": ffmpeg,
                }, audio)
            self.assertEqual(value["text"], "ok")


if __name__ == "__main__":
    unittest.main()
