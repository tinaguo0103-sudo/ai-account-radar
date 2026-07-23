#!/usr/bin/env python3
from __future__ import annotations

import inspect
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import daily_pipeline
import run_daily_collection_job


class Ar031DouyinPreflightGateTests(unittest.TestCase):
    def test_probe_requires_only_the_canonical_existing_endpoint(self) -> None:
        self.assertTrue(daily_pipeline.canonical_douyin_cdp("http://127.0.0.1:9333"))
        self.assertFalse(daily_pipeline.canonical_douyin_cdp("http://127.0.0.1:9222"))
        self.assertFalse(daily_pipeline.canonical_douyin_cdp("http://localhost:9333"))

    def test_active_path_has_no_alternate_profile_or_headless_fallback(self) -> None:
        source = inspect.getsource(daily_pipeline.main)
        self.assertNotIn("check_douyin_session.py", source)
        self.assertNotIn("--headless", source)
        self.assertNotIn("random", source.lower())
        launcher_source = (daily_pipeline.ROOT / "scripts" / "start_douyin_cdp_chrome.py").read_text(encoding="utf-8")
        self.assertNotIn("headless", launcher_source.lower())
        self.assertEqual(source.count("douyin_cdp_source_watch_probe.mjs"), 1)
        probe_source = (daily_pipeline.ROOT / "scripts" / "douyin_cdp_source_watch_probe.mjs").read_text(encoding="utf-8")
        self.assertNotIn("127.0.0.1:9222", probe_source)
        self.assertNotIn(".local_services/douyin-chrome-profile", probe_source)

    def test_deferred_editorial_does_not_mask_preflight_failure(self) -> None:
        failed = [{"name": "verify canonical Douyin profile login session", "returncode": 4}]
        failed.append({"name": "defer editorial", "returncode": 75, "deferred": True})
        self.assertEqual(daily_pipeline.deferred_exit_code(failed), 1)
        healthy = [{"name": "source", "returncode": 0}, {"name": "defer editorial", "returncode": 75, "deferred": True}]
        self.assertEqual(daily_pipeline.deferred_exit_code(healthy), 0)

    def test_deferred_editorial_does_not_mask_partial_douyin_coverage(self) -> None:
        partial = [{
            "name": "fetch daily Douyin homepage title/caption samples through Chrome CDP",
            "returncode": 0,
            "source_returncode": 3,
            "source_local_failure": True,
            "source": "douyin",
        }]
        partial.append({"name": "defer editorial", "returncode": 75, "deferred": True})
        self.assertEqual(daily_pipeline.deferred_exit_code(partial), 1)
        self.assertEqual(len(daily_pipeline.collection_failure_steps(partial)), 1)

    def test_outer_scheduled_log_is_non_success_for_partial_pipeline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(run_daily_collection_job, "LOG_DIR", Path(tmp)):
            path = run_daily_collection_job.write_job_log([
                {"name": "run full-source daily pipeline", "returncode": 1},
            ])
            payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["status"], "failed_or_partial")


if __name__ == "__main__":
    unittest.main()
