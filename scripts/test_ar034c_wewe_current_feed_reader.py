from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
import urllib.parse
from pathlib import Path
from unittest import mock

import wewe_current_feed_reader as reader
import wewe_provider_health as health
import wewe_provider_refresh as refresh


class Clock:
    def __init__(self) -> None: self.value = 2_000_000
    def now(self) -> int: self.value += 100; return self.value
    def sleep(self, seconds: float) -> None: self.value += int(seconds * 1000)


def create_database(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.executescript("""
        create table accounts(id text primary key, status integer, updated_at integer);
        create table feeds(id text primary key, mp_name text, status integer, sync_time integer, update_time integer, updated_at integer);
        create table articles(id text primary key, mp_id text, title text, publish_time integer, updated_at integer);
        insert into accounts values('account',1,1);
        insert into feeds values('feed-a','Feed A',1,1,1,1000);
        insert into articles values('old','feed-a','Old',5,1);
    """)
    connection.commit(); connection.close()


class CurrentFeedReaderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(); self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name); self.data = self.root / "data"; self.health = self.root / "health"; self.data.mkdir()
        create_database(self.data / "wewe-rss.db")
        self.key = b"isolated-ar034c-attestation-key" * 2
        self.clock = Clock()
        self.health_key = mock.patch.object(health, "load_attestation_key", return_value=self.key); self.health_key.start(); self.addCleanup(self.health_key.stop)

    def refresh_result(self, count: int = 3) -> tuple[dict, Path]:
        self.current_count = count
        def request(feed_id: str, _: float) -> int:
            connection = sqlite3.connect(self.data / "wewe-rss.db")
            for index in range(count):
                connection.execute("insert into articles values(?,?,?,?,?)", (f"new-{index}", feed_id, f"New {index}", 10 + index, self.clock.value))
            connection.execute("update feeds set sync_time=?,update_time=?,updated_at=? where id=?", (self.clock.value // 1000, self.clock.value, self.clock.value, feed_id))
            connection.commit(); connection.close(); return 200
        result = refresh.run_refresh(
            "run_20260717_093104", 1_900_000, data_dir=self.data, health_dir=self.health,
            request_fn=request, clock_ms=self.clock.now, sleep_fn=self.clock.sleep, signing_key=self.key,
        )
        result_path = self.root / "refresh.json"; result_path.write_text(json.dumps(result), encoding="utf-8")
        return result, result_path

    def fetcher(self, url: str) -> tuple[bytes, str]:
        query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        page = int(query["page"][0]); index = self.current_count - page
        payload = {"items": [{
            "id": f"https://mp.weixin.qq.com/s/new-{index}", "url": f"https://mp.weixin.qq.com/s/new-{index}",
            "title": f"New {index}", "date_published": f"2026-07-{17-index:02d}T00:00:00Z",
            "content_html": "<p>" + (f"body-{index} " * 150) + "</p>",
        }]}
        return json.dumps(payload).encode(), "application/feed+json"

    def test_receipt_bound_three_item_positive_and_check_only(self) -> None:
        result, result_path = self.refresh_result()
        plan = reader.run(refresh_result_path=result_path, run_id=result["run_id"], run_started_at_ms=1_900_000, data_dir=self.data, health_dir=self.health, check_only=True, fetcher=lambda _: (_ for _ in ()).throw(AssertionError("check-only must not fetch")))
        self.assertEqual(plan["planned_items"], 3); self.assertEqual(plan["provider_requests"], 0); self.assertFalse(plan["uses_full_feed_json"])
        out = self.root / "items.jsonl"; csv_path = self.root / "items.csv"
        actual = reader.run(refresh_result_path=result_path, run_id=result["run_id"], run_started_at_ms=1_900_000, data_dir=self.data, health_dir=self.health, out=out, csv_path=csv_path, fetcher=self.fetcher)
        self.assertEqual(actual["fulltext_items"], 3)
        rows = [json.loads(line) for line in out.read_text().splitlines()]
        self.assertEqual([row["内容标题"] for row in rows], ["New 2", "New 1", "New 0"])
        self.assertTrue(all(row["账号名/公众号名"] == "Feed A" and row["是否全文解析"] == "是" for row in rows))

    def test_equivalent_nineteen_item_fixture_is_lossless(self) -> None:
        result, result_path = self.refresh_result(19)
        out = self.root / "nineteen.jsonl"; csv_path = self.root / "nineteen.csv"
        actual = reader.run(refresh_result_path=result_path, run_id=result["run_id"], run_started_at_ms=1_900_000, data_dir=self.data, health_dir=self.health, out=out, csv_path=csv_path, fetcher=self.fetcher)
        rows = [json.loads(line) for line in out.read_text().splitlines()]
        self.assertEqual(actual["planned_items"], 19)
        self.assertEqual(actual["provider_requests"], 19)
        self.assertEqual(len(rows), 19)
        self.assertEqual(len({row["内容链接"] for row in rows}), 19)

    def test_page_mutations_fail_closed(self) -> None:
        result, result_path = self.refresh_result()
        base = json.loads(self.fetcher("http://x?page=1")[0])
        mutations = {
            "malformed": lambda _: b"{",
            "partial": lambda value: json.dumps({"items": []}).encode(),
            "duplicate": lambda value: json.dumps({"items": value["items"] * 2}).encode(),
            "identity": lambda value: json.dumps({"items": [{**value["items"][0], "id": "https://mp.weixin.qq.com/s/wrong", "url": "https://mp.weixin.qq.com/s/wrong"}]}).encode(),
            "title": lambda value: json.dumps({"items": [{**value["items"][0], "title": "wrong"}]}).encode(),
            "fulltext": lambda value: json.dumps({"items": [{**value["items"][0], "content_html": "short"}]}).encode(),
        }
        for name, mutate in mutations.items():
            def broken(url: str, mutation=mutate):
                value = json.loads(self.fetcher(url)[0]); return mutation(value), "application/feed+json"
            with self.subTest(name=name), self.assertRaises(reader.CurrentFeedError):
                reader.run(refresh_result_path=result_path, run_id=result["run_id"], run_started_at_ms=1_900_000, data_dir=self.data, health_dir=self.health, out=self.root / name, csv_path=self.root / f"{name}.csv", fetcher=broken)

    def test_database_revision_count_watermark_owner_and_identity_drift_fail(self) -> None:
        result, result_path = self.refresh_result()
        mutations = {
            "revision": "update feeds set sync_time=999",
            "count": "delete from articles where id='new-0'",
            "watermark": "update articles set publish_time=5 where id='new-0'",
            "owner": "update feeds set mp_name=''",
        }
        database = self.data / "wewe-rss.db"
        original = database.read_bytes()
        for name, statement in mutations.items():
            with self.subTest(name=name):
                database.write_bytes(original)
                connection = sqlite3.connect(database); connection.execute(statement); connection.commit(); connection.close()
                with self.assertRaises((reader.CurrentFeedError, ValueError)):
                    reader.run(refresh_result_path=result_path, run_id=result["run_id"], run_started_at_ms=1_900_000, data_dir=self.data, health_dir=self.health, check_only=True)

    def test_out_of_order_and_cross_feed_are_rejected_by_identity(self) -> None:
        result, result_path = self.refresh_result()
        def reversed_fetch(url: str):
            parsed = urllib.parse.urlparse(url); query = urllib.parse.parse_qs(parsed.query); page = int(query["page"][0]);
            return self.fetcher(url.replace(f"page={page}", f"page={4-page}"))
        with self.assertRaisesRegex(reader.CurrentFeedError, "identity_mismatch"):
            reader.run(refresh_result_path=result_path, run_id=result["run_id"], run_started_at_ms=1_900_000, data_dir=self.data, health_dir=self.health, out=self.root / "out", csv_path=self.root / "out.csv", fetcher=reversed_fetch)

    def test_giant_feed_path_is_physically_absent(self) -> None:
        source = Path(reader.__file__).read_text(encoding="utf-8")
        self.assertNotIn("/feeds/all", source)
        self.assertNotIn("MAX_FEED_BYTES", source)
        self.assertIn('"limit": 1', source)
        daily = (Path(reader.__file__).parent / "daily_pipeline.py").read_text(encoding="utf-8")
        block = daily[daily.index('"request fixed wewe-rss provider refresh"'):daily.index("fetch_douyin =")]
        self.assertIn("wewe_current_feed_reader.py", block)
        self.assertNotIn("wechat_fulltext_provider_probe.py", block)


if __name__ == "__main__":
    unittest.main()
