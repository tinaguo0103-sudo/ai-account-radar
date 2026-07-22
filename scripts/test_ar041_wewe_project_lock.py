from __future__ import annotations

import json
import os
import socket
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import daily_pipeline
import wewe_provider_refresh as refresh


def create_database(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.executescript("""
        create table accounts(id text primary key, status integer, updated_at integer);
        create table feeds(id text primary key, status integer, sync_time integer, update_time integer, updated_at integer);
        create table articles(id text primary key, mp_id text, publish_time integer, updated_at integer);
        insert into accounts values('account',1,1);
        insert into feeds values('feed-a',1,1,1,1000);
    """)
    connection.commit()
    connection.close()


class Clock:
    def __init__(self) -> None:
        self.value = 2_000_000

    def now(self) -> int:
        self.value += 100
        return self.value

    def sleep(self, seconds: float) -> None:
        self.value += int(seconds * 1000)


class AR041WeWeProjectLockTests(unittest.TestCase):
    def test_project_lock_probe_acquires_and_releases_without_provider_request(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            lock = root / "output" / "state" / "wewe-refresh" / "refresh.lock"
            with mock.patch.object(refresh, "default_request") as request:
                result = refresh.probe_project_lock(lock, project_root=root, clock_ms=lambda: 1000)
        self.assertTrue(result["lock_path_project_owned"])
        self.assertTrue(result["lock_acquired"])
        self.assertTrue(result["lock_released"])
        self.assertEqual(result["provider_requests"], 0)
        request.assert_not_called()

    def test_concurrent_owner_is_rejected_and_stale_lock_recovery_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "refresh.lock"
            live = {"attempt_id": "live", "pid": os.getpid(), "host": socket.gethostname(), "expires_at_ms": 2000}
            refresh.acquire_lease(path, live, 1000)
            with self.assertRaisesRegex(refresh.RefreshError, "refresh_in_progress"):
                refresh.acquire_lease(path, {"attempt_id": "other"}, 1000)
            refresh.release_lease(path, "live")

            stale = {"attempt_id": "stale", "pid": 99999999, "host": socket.gethostname(), "expires_at_ms": 1}
            refresh.atomic_write(path, stale)
            replacement = {"attempt_id": "replacement", "pid": os.getpid(), "host": socket.gethostname(), "expires_at_ms": 3000}
            refresh.acquire_lease(path, replacement, 2000)
            self.assertEqual(json.loads(path.read_text())["attempt_id"], "replacement")
            self.assertTrue((path.parent / "lease_recovery.jsonl").is_file())
            self.assertTrue(path.with_suffix(".stale.2000").is_file())
            refresh.release_lease(path, "replacement")

    def test_refresh_uses_project_lock_and_keeps_canonical_runtime_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "project"
            data = root / "canonical-runtime" / "data"
            health = data.parent / "health"
            data.mkdir(parents=True)
            create_database(data / "wewe-rss.db")
            lock = project / "output" / "state" / "wewe-refresh" / "refresh.lock"
            clock = Clock()
            acquired: list[Path] = []
            real_acquire = refresh.acquire_lease

            def capture_acquire(path: Path, payload: dict, now_ms: int) -> None:
                acquired.append(path)
                real_acquire(path, payload, now_ms)

            def request(feed_id: str, _timeout: float) -> int:
                connection = sqlite3.connect(data / "wewe-rss.db")
                connection.execute("update feeds set sync_time=?, updated_at=? where id=?", (clock.value // 1000, clock.value, feed_id))
                connection.commit()
                connection.close()
                return 200

            with mock.patch.object(refresh, "acquire_lease", side_effect=capture_acquire):
                result = refresh.run_refresh(
                    "run_20260722_080000", 1_900_000,
                    data_dir=data, health_dir=health, lock_path=lock, project_root=project,
                    request_fn=request, clock_ms=clock.now, sleep_fn=clock.sleep,
                    signing_key=b"ar041-project-lock-test-key-value" * 2,
                )
            self.assertTrue(result["ok"])
            self.assertEqual(acquired, [lock.resolve()])
            self.assertFalse((health / "refresh.lock").exists())
            self.assertTrue(Path(result["receipt_path"]).is_relative_to((health / "receipts").resolve()))
            self.assertEqual(refresh.CANONICAL_DATA_DIR, Path.home() / ".codex" / "ai-account-radar-runtime" / "providers" / "wewe-rss" / "data")
            self.assertEqual(refresh.ATTESTATION_KEY_PATH, refresh.CANONICAL_DATA_DIR.parent / "secrets" / "wewe-refresh-attestation.key")

    def test_check_only_proves_lock_and_never_refreshes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            lock = root / "output" / "state" / "wewe-refresh" / "refresh.lock"
            snapshot = {
                "database_identity": {"path": "redacted", "device": 1, "inode": 2},
                "active_account_count": 1,
                "feeds": [{"feed_id": "feed-a"}],
            }
            with mock.patch.object(refresh, "read_snapshot", return_value=snapshot), \
                    mock.patch.object(refresh, "default_request") as request:
                result = refresh.check_only_plan(Path(tmp) / "canonical-data", lock_path=lock, project_root=root)
        self.assertTrue(result["ok"])
        self.assertTrue(result["lock_acquired"] and result["lock_released"])
        self.assertFalse(result["refresh_requested"])
        self.assertEqual(result["provider_requests"], 0)
        self.assertFalse(result["secret_material_read"])
        request.assert_not_called()

    def test_401_is_wechat_local_and_safe_sources_continue(self) -> None:
        wechat = {"name": "request fixed wewe-rss provider refresh", "returncode": 4, "stderr": "HTTP 401 / -2041"}
        douyin = {"name": "fetch Douyin", "returncode": 0, "source_rows": 3}
        aihot = {"name": "fetch AIHOT", "returncode": 0, "source_rows": 2}
        daily_pipeline.isolate_source_failure(
            wechat, source="wechat", state="login_required", reason="provider_http_401_account_invalid"
        )
        self.assertEqual(wechat["returncode"], 0)
        self.assertEqual(wechat["source_rows"], 0)
        self.assertTrue(wechat["source_local_failure"])
        self.assertEqual((douyin["returncode"], douyin["source_rows"]), (0, 3))
        self.assertEqual((aihot["returncode"], aihot["source_rows"]), (0, 2))


if __name__ == "__main__":
    unittest.main()
