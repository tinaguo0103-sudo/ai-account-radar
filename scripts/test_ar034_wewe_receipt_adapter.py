from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import wewe_provider_health as health
import wewe_provider_refresh as refresh


class Clock:
    def __init__(self, value: int = 2_000_000): self.value = value
    def now(self) -> int: self.value += 100; return self.value
    def sleep(self, seconds: float) -> None: self.value += int(seconds * 1000)


def create_database(path: Path, feeds: tuple[str, ...] = ("feed-a",), *, account: bool = True) -> None:
    connection = sqlite3.connect(path)
    connection.executescript("""
        create table accounts(id text primary key, status integer, updated_at integer);
        create table feeds(id text primary key, status integer, sync_time integer, update_time integer, updated_at integer);
        create table articles(id text primary key, mp_id text, publish_time integer, updated_at integer);
    """)
    if account: connection.execute("insert into accounts values('account',1,1)")
    for feed_id in feeds: connection.execute("insert into feeds values(?,1,1,1,1000)", (feed_id,))
    connection.commit(); connection.close()


class ReceiptAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.test_key = b"ar034-isolated-test-attestation-key" * 2
        self.real_load_attestation_key = refresh.load_attestation_key
        original_root = refresh.ROOT
        original_lock_path = refresh.PROJECT_REFRESH_LOCK_PATH
        self.addCleanup(setattr, refresh, "ROOT", original_root)
        self.addCleanup(setattr, refresh, "PROJECT_REFRESH_LOCK_PATH", original_lock_path)
        refresh_patch = mock.patch.object(refresh, "load_attestation_key", return_value=self.test_key)
        health_patch = mock.patch.object(health, "load_attestation_key", return_value=self.test_key)
        refresh_patch.start(); health_patch.start()
        self.addCleanup(refresh_patch.stop); self.addCleanup(health_patch.stop)

    def fixture(self, feeds: tuple[str, ...] = ("feed-a",)):
        temporary = tempfile.TemporaryDirectory(); root = Path(temporary.name); data = root / "data"; health_dir = root / "health"; data.mkdir(); create_database(data / "wewe-rss.db", feeds)
        refresh.ROOT = root / "project"
        refresh.PROJECT_REFRESH_LOCK_PATH = refresh.ROOT / "output" / "state" / "wewe-refresh" / "refresh.lock"
        return temporary, data, health_dir

    def completing_request(self, database: Path, clock: Clock, *, add_article: bool = False):
        def request(feed_id: str, _timeout: float) -> int:
            connection = sqlite3.connect(database)
            if add_article:
                connection.execute("insert or ignore into articles values(?,?,?,?)", (f"article-{feed_id}", feed_id, 10, clock.value))
            connection.execute("update feeds set sync_time=?, updated_at=? where id=?", (clock.value // 1000, clock.value, feed_id))
            connection.commit(); connection.close(); return 200
        return request

    def test_one_feed_new_and_no_new_have_proven_completion(self) -> None:
        for add_article, expected in ((True, 1), (False, 0)):
            temporary, data, health_dir = self.fixture(); self.addCleanup(temporary.cleanup); clock = Clock()
            result = refresh.run_refresh("run_20260716_080311", 1_900_000, data_dir=data, health_dir=health_dir, request_fn=self.completing_request(data / "wewe-rss.db", clock, add_article=add_article), clock_ms=clock.now, sleep_fn=clock.sleep)
            self.assertTrue(result["ok"]); self.assertEqual(result["new_item_count"], expected)
            receipt = health.validate_refresh_receipt(Path(result["receipt_path"]), result["receipt_sha256"], run_id=result["run_id"], attempt_id=result["attempt_id"], data_dir=data, health_dir=health_dir, now_ms=clock.now(), run_started_at_ms=1_900_000)
            self.assertEqual(receipt["new_item_count"], expected)

    def test_multiple_feeds_all_complete(self) -> None:
        temporary, data, health_dir = self.fixture(("a", "b", "c")); self.addCleanup(temporary.cleanup); clock = Clock()
        result = refresh.run_refresh("run_20260716_080311", 1_900_000, data_dir=data, health_dir=health_dir, request_fn=self.completing_request(data / "wewe-rss.db", clock), clock_ms=clock.now, sleep_fn=clock.sleep)
        receipt = json.loads(Path(result["receipt_path"]).read_text())
        self.assertEqual(receipt["feed_ids"], ["a", "b", "c"]); self.assertTrue(all(row["completion_advanced"] for row in receipt["per_feed"]))

    def test_http_accept_without_completion_and_one_of_n_timeout(self) -> None:
        temporary, data, health_dir = self.fixture(("a", "b")); self.addCleanup(temporary.cleanup); clock = Clock()
        def partial(feed_id: str, _: float) -> int:
            if feed_id == "a": self.completing_request(data / "wewe-rss.db", clock)(feed_id, 1)
            return 200
        with self.assertRaisesRegex(refresh.RefreshError, "refresh_completion_timeout"):
            refresh.run_refresh("run_20260716_080311", 1_900_000, data_dir=data, health_dir=health_dir, request_fn=partial, clock_ms=clock.now, sleep_fn=clock.sleep, deadline_ms=500, poll_interval_ms=100)
        self.assertFalse(any((health_dir / "receipts").glob("*.json")) if (health_dir / "receipts").exists() else False)

    def test_enqueue_field_advance_without_completion_signal_times_out(self) -> None:
        temporary, data, health_dir = self.fixture(); self.addCleanup(temporary.cleanup); clock = Clock()
        def enqueue_only(feed_id: str, _: float) -> int:
            connection = sqlite3.connect(data / "wewe-rss.db")
            connection.execute("update feeds set update_time=? where id=?", (clock.value, feed_id))
            connection.commit(); connection.close(); return 200
        with self.assertRaisesRegex(refresh.RefreshError, "refresh_completion_timeout"):
            refresh.run_refresh("run_20260716_080311", 1_900_000, data_dir=data, health_dir=health_dir, request_fn=enqueue_only, clock_ms=clock.now, sleep_fn=clock.sleep, deadline_ms=400, poll_interval_ms=100)
        self.assertFalse((health_dir / "receipts").exists())

    def test_request_rejection_and_account_or_feed_drift_fail(self) -> None:
        for mode in ("http", "account", "feed"):
            temporary, data, health_dir = self.fixture(("a",)); self.addCleanup(temporary.cleanup); clock = Clock()
            def request(feed_id: str, _: float) -> int:
                if mode == "http": return 500
                connection = sqlite3.connect(data / "wewe-rss.db")
                if mode == "account": connection.execute("update accounts set status=0")
                else: connection.execute("insert into feeds values('b',1,2,1,?)", (clock.value,))
                connection.execute("update feeds set sync_time=2001,updated_at=? where id='a'", (clock.value,)); connection.commit(); connection.close(); return 200
            with self.subTest(mode=mode), self.assertRaises(refresh.RefreshError):
                refresh.run_refresh("run_20260716_080311", 1_900_000, data_dir=data, health_dir=health_dir, request_fn=request, clock_ms=clock.now, sleep_fn=clock.sleep, deadline_ms=300)

    def test_database_replacement_and_busy_fail_closed(self) -> None:
        temporary, data, health_dir = self.fixture(); self.addCleanup(temporary.cleanup); clock = Clock(); database = data / "wewe-rss.db"
        before = refresh.read_snapshot(database)
        replacement = data / "replacement.db"; create_database(replacement); os.replace(replacement, database)
        after = refresh.read_snapshot(database)
        with self.assertRaisesRegex(refresh.RefreshError, "database_identity_drift"):
            refresh.validate_completion(before, after, clock.now())
        with mock.patch.object(sqlite3, "connect", side_effect=sqlite3.OperationalError("locked")):
            with self.assertRaisesRegex(refresh.RefreshError, "sqlite_busy_or_unreadable"): refresh.read_snapshot(database)

    def test_poll_busy_then_success_and_busy_past_deadline(self) -> None:
        temporary, data, health_dir = self.fixture(); self.addCleanup(temporary.cleanup); clock = Clock(); database = data / "wewe-rss.db"; calls = {"count": 0}
        def transient(path: Path):
            calls["count"] += 1
            if calls["count"] == 2: raise refresh.RefreshError("sqlite_busy_or_unreadable")
            return refresh.read_snapshot(path)
        result = refresh.run_refresh("run_20260716_080311", 1_900_000, data_dir=data, health_dir=health_dir, request_fn=self.completing_request(database, clock), snapshot_fn=transient, clock_ms=clock.now, sleep_fn=clock.sleep)
        self.assertTrue(result["ok"])
        temporary2, data2, health2 = self.fixture(); self.addCleanup(temporary2.cleanup); clock2 = Clock(); calls2 = {"count": 0}
        def always_after_before(path: Path):
            calls2["count"] += 1
            if calls2["count"] > 1: raise refresh.RefreshError("sqlite_busy_or_unreadable")
            return refresh.read_snapshot(path)
        with self.assertRaises(refresh.RefreshError): refresh.run_refresh("run_20260716_080311", 1_900_000, data_dir=data2, health_dir=health2, request_fn=lambda *_: 200, snapshot_fn=always_after_before, clock_ms=clock2.now, sleep_fn=clock2.sleep, deadline_ms=300, poll_interval_ms=100)

    def test_request_exception_leaves_no_receipt(self) -> None:
        temporary, data, health_dir = self.fixture(); self.addCleanup(temporary.cleanup); clock = Clock()
        with self.assertRaisesRegex(refresh.RefreshError, "refresh_request_failed"):
            refresh.run_refresh("run_20260716_080311", 1_900_000, data_dir=data, health_dir=health_dir, request_fn=lambda *_: (_ for _ in ()).throw(TimeoutError()), clock_ms=clock.now, sleep_fn=clock.sleep)
        self.assertFalse((health_dir / "receipts").exists())

    def test_live_and_stale_lease_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "refresh.lock"; now = 1000
            live = {"attempt_id": "live", "pid": os.getpid(), "host": refresh.socket.gethostname(), "expires_at_ms": 2000}
            refresh.atomic_write(path, live)
            with self.assertRaisesRegex(refresh.RefreshError, "refresh_in_progress"): refresh.acquire_lease(path, {"attempt_id": "new"}, now)
            stale = {"attempt_id": "stale", "pid": 99999999, "host": refresh.socket.gethostname(), "expires_at_ms": 1}
            refresh.atomic_write(path, stale); refresh.acquire_lease(path, {"attempt_id": "new", "pid": os.getpid()}, now)
            with self.assertRaisesRegex(refresh.RefreshError, "lease_owner_mismatch"): refresh.release_lease(path, "other")

    def test_receipt_tamper_wrong_identity_partial_and_replay_fail(self) -> None:
        temporary, data, health_dir = self.fixture(); self.addCleanup(temporary.cleanup); clock = Clock()
        result = refresh.run_refresh("run_20260716_080311", 1_900_000, data_dir=data, health_dir=health_dir, request_fn=self.completing_request(data / "wewe-rss.db", clock), clock_ms=clock.now, sleep_fn=clock.sleep)
        path = Path(result["receipt_path"])
        for run_id, attempt_id, digest in (("other", result["attempt_id"], result["receipt_sha256"]), (result["run_id"], "other", result["receipt_sha256"]), (result["run_id"], result["attempt_id"], "0" * 64)):
                with self.subTest(run_id=run_id, attempt=attempt_id), self.assertRaises(ValueError): health.validate_refresh_receipt(path, digest, run_id=run_id, attempt_id=attempt_id, data_dir=data, health_dir=health_dir, now_ms=clock.now(), run_started_at_ms=1_900_000)
        with self.assertRaisesRegex(ValueError, "replayed"):
            health.validate_refresh_receipt(path, result["receipt_sha256"], run_id=result["run_id"], attempt_id=result["attempt_id"], data_dir=data, health_dir=health_dir, now_ms=clock.now(), run_started_at_ms=1_900_000, previous_attempt_id=result["attempt_id"])
        path.write_text("{", encoding="utf-8")
        with self.assertRaises(ValueError): health.validate_refresh_receipt(path, refresh.sha256_bytes(path.read_bytes()), run_id=result["run_id"], attempt_id=result["attempt_id"], data_dir=data, health_dir=health_dir, now_ms=clock.now(), run_started_at_ms=1_900_000)

    def test_manual_external_symlink_and_hardlink_receipts_fail(self) -> None:
        temporary, data, health_dir = self.fixture(); self.addCleanup(temporary.cleanup); clock = Clock()
        result = refresh.run_refresh("run_20260716_080311", 1_900_000, data_dir=data, health_dir=health_dir, request_fn=self.completing_request(data / "wewe-rss.db", clock), clock_ms=clock.now, sleep_fn=clock.sleep)
        canonical = Path(result["receipt_path"]); external = Path(temporary.name) / "manual.json"; external.write_bytes(canonical.read_bytes())
        kwargs = dict(run_id=result["run_id"], attempt_id=result["attempt_id"], data_dir=data, health_dir=health_dir, now_ms=clock.now(), run_started_at_ms=1_900_000)
        with self.assertRaisesRegex(ValueError, "path_not_owned"):
            health.validate_refresh_receipt(external, refresh.sha256_bytes(external.read_bytes()), **kwargs)
        canonical.unlink(); canonical.symlink_to(external)
        with self.assertRaisesRegex(ValueError, "path_not_owned"):
            health.validate_refresh_receipt(canonical, refresh.sha256_bytes(external.read_bytes()), **kwargs)
        canonical.unlink(); os.link(external, canonical)
        with self.assertRaisesRegex(ValueError, "path_not_owned"):
            health.validate_refresh_receipt(canonical, refresh.sha256_bytes(external.read_bytes()), **kwargs)

    def test_relational_receipt_mutations_fail(self) -> None:
        mutations = {
            "wrong_per_feed_id": lambda value: value["per_feed"][0].__setitem__("feed_id", "not-the-live-feed"),
            "reordered_feed_ids": lambda value: value.__setitem__("feed_ids", list(reversed(value["feed_ids"]))),
            "wrong_sync": lambda value: value["per_feed"][0].__setitem__("after_sync_time", 999999),
            "wrong_completion": lambda value: value["per_feed"][0].__setitem__("completion_advanced", False),
            "wrong_new_count": lambda value: value.__setitem__("new_item_count", 99),
            "wrong_revision": lambda value: value.__setitem__("refresh_revision", 99),
            "wrong_refreshed_at": lambda value: value.__setitem__("refreshed_at_ms", 99),
            "unknown_key": lambda value: value.__setitem__("forged", True),
            "bool_as_int": lambda value: value.__setitem__("new_item_count", False),
        }
        for name, mutate in mutations.items():
            temporary, data, health_dir = self.fixture(("a", "b")); self.addCleanup(temporary.cleanup); clock = Clock()
            result = refresh.run_refresh("run_20260716_080311", 1_900_000, data_dir=data, health_dir=health_dir, request_fn=self.completing_request(data / "wewe-rss.db", clock), clock_ms=clock.now, sleep_fn=clock.sleep)
            path = Path(result["receipt_path"]); payload = json.loads(path.read_text()); mutate(payload); payload = refresh.seal_payload(payload, self.test_key); refresh.atomic_write(path, payload)
            with self.subTest(name=name), self.assertRaises(ValueError):
                health.validate_refresh_receipt(path, refresh.sha256_bytes(path.read_bytes()), run_id=result["run_id"], attempt_id=result["attempt_id"], data_dir=data, health_dir=health_dir, now_ms=clock.now(), run_started_at_ms=1_900_000)

    def test_attempt_lineage_missing_or_tampered_fails(self) -> None:
        temporary, data, health_dir = self.fixture(); self.addCleanup(temporary.cleanup); clock = Clock()
        result = refresh.run_refresh("run_20260716_080311", 1_900_000, data_dir=data, health_dir=health_dir, request_fn=self.completing_request(data / "wewe-rss.db", clock), clock_ms=clock.now, sleep_fn=clock.sleep)
        lineage = health_dir / "attempts" / f'{result["run_id"]}_{result["attempt_id"]}.json'
        lineage.write_text("{}", encoding="utf-8")
        with self.assertRaises(ValueError):
            health.validate_refresh_receipt(Path(result["receipt_path"]), result["receipt_sha256"], run_id=result["run_id"], attempt_id=result["attempt_id"], data_dir=data, health_dir=health_dir, now_ms=clock.now(), run_started_at_ms=1_900_000)

    def test_nested_schema_and_time_mutations_are_typed_failures(self) -> None:
        for field, value in (("before", []), ("after", None), ("per_feed", "bad")):
            temporary, data, health_dir = self.fixture(); self.addCleanup(temporary.cleanup); clock = Clock()
            result = refresh.run_refresh("run_20260716_080311", 1_900_000, data_dir=data, health_dir=health_dir, request_fn=self.completing_request(data / "wewe-rss.db", clock), clock_ms=clock.now, sleep_fn=clock.sleep)
            path = Path(result["receipt_path"]); payload = json.loads(path.read_text()); payload[field] = value; payload = refresh.seal_payload(payload, self.test_key); refresh.atomic_write(path, payload)
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "refresh_receipt_"):
                health.validate_refresh_receipt(path, refresh.sha256_bytes(path.read_bytes()), run_id=result["run_id"], attempt_id=result["attempt_id"], data_dir=data, health_dir=health_dir, now_ms=clock.now(), run_started_at_ms=1_900_000)

    def test_canonical_trio_without_adapter_attestation_and_wrong_key_fail(self) -> None:
        temporary, data, health_dir = self.fixture(); self.addCleanup(temporary.cleanup); clock = Clock()
        result = refresh.run_refresh("run_20260716_080311", 1_900_000, data_dir=data, health_dir=health_dir, request_fn=self.completing_request(data / "wewe-rss.db", clock), clock_ms=clock.now, sleep_fn=clock.sleep)
        receipt_path = Path(result["receipt_path"])
        payload = json.loads(receipt_path.read_text())
        payload["attestation_signature"] = "0" * 64
        refresh.atomic_write(receipt_path, payload)
        kwargs = dict(run_id=result["run_id"], attempt_id=result["attempt_id"], data_dir=data, health_dir=health_dir, now_ms=clock.now(), run_started_at_ms=1_900_000)
        with self.assertRaisesRegex(ValueError, "attestation_invalid"):
            health.validate_refresh_receipt(receipt_path, refresh.sha256_bytes(receipt_path.read_bytes()), **kwargs)
        with self.assertRaisesRegex(ValueError, "attestation_invalid"):
            health.validate_refresh_receipt(receipt_path, refresh.sha256_bytes(receipt_path.read_bytes()), signing_key=b"wrong-key" * 8, **kwargs)

    def test_runtime_attestation_key_permissions_and_secret_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp).resolve() / "attestation.key"
            with self.assertRaisesRegex(refresh.RefreshError, "unavailable"):
                self.real_load_attestation_key(path)
            path.write_bytes(b"k" * 32); path.chmod(0o644)
            with self.assertRaisesRegex(refresh.RefreshError, "unsafe"):
                self.real_load_attestation_key(path)
            path.chmod(0o600)
            with mock.patch.object(Path, "read_bytes", side_effect=AssertionError("path reopen forbidden")):
                self.assertEqual(self.real_load_attestation_key(path), b"k" * 32)
            linked = Path(tmp) / "linked.key"; os.link(path, linked)
            with self.assertRaisesRegex(refresh.RefreshError, "unsafe"):
                self.real_load_attestation_key(path)
        source = Path(refresh.__file__).read_text(encoding="utf-8")
        self.assertNotIn("print(key", source)
        self.assertNotIn("json.dumps(key", source)

    def test_attestation_parent_and_owner_gates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve(); safe = root / "safe"; safe.mkdir(mode=0o700); key = safe / "key"; key.write_bytes(b"k" * 32); key.chmod(0o600)
            safe.chmod(0o777)
            with self.assertRaisesRegex(refresh.RefreshError, "parent_unsafe"):
                self.real_load_attestation_key(key)
            safe.chmod(0o700)
            real_fstat = os.fstat; calls = {"count": 0}
            def wrong_file_owner(fd: int):
                info = real_fstat(fd); calls["count"] += 1
                if calls["count"] == 2:
                    values = list(info); values[4] = os.getuid() + 1; return os.stat_result(values)
                return info
            with mock.patch.object(refresh.os, "fstat", side_effect=wrong_file_owner), self.assertRaisesRegex(refresh.RefreshError, "key_unsafe"):
                self.real_load_attestation_key(key)
            parent_link = root / "parent-link"; parent_link.symlink_to(safe, target_is_directory=True)
            with self.assertRaisesRegex(refresh.RefreshError, "parent_unsafe"):
                self.real_load_attestation_key(parent_link / "key")
            key_link = safe / "key-link"; key_link.symlink_to(key)
            with self.assertRaisesRegex(refresh.RefreshError, "unavailable"):
                self.real_load_attestation_key(key_link)

    def test_secret_audit_fields_match_execution(self) -> None:
        temporary, data, health_dir = self.fixture(); self.addCleanup(temporary.cleanup); clock = Clock()
        plan = refresh.check_only_plan(data)
        self.assertFalse(plan["secret_material_read"]); self.assertFalse(plan["secrets_exposed"])
        result = refresh.run_refresh("run_20260716_080311", 1_900_000, data_dir=data, health_dir=health_dir, request_fn=self.completing_request(data / "wewe-rss.db", clock), clock_ms=clock.now, sleep_fn=clock.sleep, signing_key=self.test_key)
        self.assertTrue(result["secret_material_read"]); self.assertFalse(result["secrets_exposed"])
        self.assertNotIn("secrets_read", result); self.assertNotIn("secrets_read", plan)
        health_source = Path(health.__file__).read_text(encoding="utf-8")
        self.assertIn('"secret_material_read": secret_material_read', health_source)
        self.assertNotIn('"secrets_read": False', health_source)

    def test_check_only_has_no_request_or_browser_side_effect(self) -> None:
        temporary, data, _ = self.fixture(); self.addCleanup(temporary.cleanup)
        plan = refresh.check_only_plan(data)
        self.assertEqual(plan["status"], "refresh_required"); self.assertFalse(plan["refresh_requested"]); self.assertFalse(plan["starts_browser"]); self.assertFalse(plan["starts_provider"])

    def test_fixed_trpc_request_requires_auth_and_rejects_error_payload(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True), self.assertRaisesRegex(refresh.RefreshError, "auth_code_missing"):
            refresh.default_request("feed-a", 1)
        class Response:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *_): return None
            def read(self): return b'{"result":{"data":{"json":null}}}'
        with mock.patch.dict(os.environ, {"WEWE_RSS_AUTH_CODE": "secret"}), mock.patch.object(refresh.urllib.request, "urlopen", return_value=Response()) as opened:
            self.assertEqual(refresh.default_request("feed-a", 1), 200)
            request = opened.call_args.args[0]
            self.assertEqual(request.full_url, "http://127.0.0.1:4000/trpc/feed.refreshArticles")
            self.assertNotIn("secret", json.dumps({"url": request.full_url, "body": request.data.decode()}))

        class MalformedResponse(Response):
            def read(self): return b'not-json'
        with mock.patch.dict(os.environ, {"WEWE_RSS_AUTH_CODE": "secret"}), mock.patch.object(refresh.urllib.request, "urlopen", return_value=MalformedResponse()):
            with self.assertRaises(json.JSONDecodeError):
                refresh.default_request("feed-a", 1)

    def test_active_adapter_has_no_async_get_browser_or_provider_fallback(self) -> None:
        source = Path(refresh.__file__).read_text(encoding="utf-8")
        self.assertNotIn("?update=true", source)
        self.assertNotIn("9334", source)
        self.assertNotIn("start_wewe", source)
        self.assertNotIn("docker", source.lower())
        daily = (Path(refresh.__file__).with_name("daily_pipeline.py")).read_text(encoding="utf-8")
        block = daily[daily.index("if args.fetch_wechat_fulltext_provider"):daily.index("fetch_douyin =")]
        self.assertIn("wewe_provider_refresh.py", block)
        self.assertNotIn("start_wewe_rss", block)


if __name__ == "__main__": unittest.main()
