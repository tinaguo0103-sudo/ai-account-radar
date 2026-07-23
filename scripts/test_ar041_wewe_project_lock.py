from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import wewe_provider_refresh as refresh


class ProjectRefreshMutexTests(unittest.TestCase):
    def test_acquire_release_and_concurrent_owner(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "output/state/wewe-refresh/refresh.lock"
            owner = refresh.acquire_lock(path, 1000)
            self.assertTrue(path.is_file())
            with self.assertRaisesRegex(refresh.RefreshError, "refresh_in_progress"):
                refresh.acquire_lock(path, 1001)
            refresh.release_lock(path, owner)
            self.assertFalse(path.exists())

    def test_stale_dead_owner_is_recovered_without_journal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "output/state/wewe-refresh/refresh.lock"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({
                "pid": 99999999,
                "host": os.uname().nodename,
                "created_at_ms": 1,
                "expires_at_ms": 2,
            }), encoding="utf-8")
            owner = refresh.acquire_lock(path, 1000)
            refresh.release_lock(path, owner)
            self.assertEqual([], list(path.parent.iterdir()))

    def test_lock_must_be_project_owned(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaisesRegex(refresh.RefreshError, "refresh_lock_not_project_owned"):
                refresh.project_lock(Path("/tmp/outside-refresh.lock"), root)

    def test_one_request_and_final_lock_absent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lock = root / "output/state/wewe-refresh/refresh.lock"
            snapshots = [
                {"active_account_count": 1, "feeds": [{"feed_id": "f", "sync_time": 1, "updated_at_ms": 1, "article_count": 1, "max_publish_time": 1}]},
                {"active_account_count": 1, "feeds": [{"feed_id": "f", "sync_time": 3, "updated_at_ms": 3, "article_count": 2, "max_publish_time": 2}]},
            ]
            requests: list[float] = []
            clocks = iter([1000, 2000, 3000, 4000])
            result = refresh.run_refresh(
                "run_20260723_100000",
                1000,
                data_dir=root,
                lock_path=lock,
                project_root=root,
                request_fn=lambda timeout: requests.append(timeout) or 200,
                snapshot_fn=lambda _path: snapshots.pop(0),
                clock_ms=lambda: next(clocks),
                sleep_fn=lambda _seconds: None,
            )
            self.assertTrue(result["ok"])
            self.assertEqual(1, result["provider_request_count"])
            self.assertEqual(1, len(requests))
            self.assertFalse(lock.exists())
            self.assertFalse(result["secret_material_read"])


if __name__ == "__main__":
    unittest.main()
