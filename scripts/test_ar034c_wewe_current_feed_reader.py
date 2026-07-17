from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
import urllib.parse
from pathlib import Path
from unittest import mock

import daily_pipeline
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
        self.key = b"isolated-ar034d-attestation-key" * 2
        self.clock = Clock(); self.current_count = 0
        patch = mock.patch.object(health, "load_attestation_key", return_value=self.key); patch.start(); self.addCleanup(patch.stop)

    def refresh_result(self, count: int) -> tuple[dict, Path]:
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
        path = self.root / "refresh.json"; path.write_text(json.dumps(result), encoding="utf-8")
        return result, path

    def response(self, url: str, variants: dict[int, str] | None = None) -> tuple[bytes, str]:
        page = int(urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["page"][0])
        index = self.current_count - page; variant = (variants or {}).get(index, "normal")
        if variant == "timeout": raise TimeoutError("fixture timeout")
        if variant == "malformed": return b'{"items":[{"content_html":"unterminated', "application/feed+json"
        article_id = "wrong" if variant == "identity" else f"new-{index}"
        content = {
            "short": "<p>短正文</p>",
            "image": '<div><img src="image.jpg"></div>',
            "empty": "",
            "placeholder": "<p>获取全文失败，请重试~</p>",
            "login": '<div class="login-qrcode">请先登录</div>',
        }.get(variant, "<p>" + (f"body-{index} " * 150) + "</p>")
        item = {
            "id": f"https://mp.weixin.qq.com/s/{article_id}",
            "url": f"https://mp.weixin.qq.com/s/{article_id}",
            "title": f"New {index}", "date_published": "2026-07-17T00:00:00Z",
        }
        if variant != "missing": item["content_html"] = content
        return json.dumps({"items": [item]}).encode(), "application/feed+json"

    def run_read(self, result: dict, result_path: Path, fetcher, stem: str = "items") -> tuple[dict, Path, Path, Path]:
        out = self.root / f"{stem}.jsonl"; csv_path = self.root / f"{stem}.csv"; report = self.root / f"{stem}.report.json"
        actual = reader.run(
            refresh_result_path=result_path, run_id=result["run_id"], run_started_at_ms=1_900_000,
            data_dir=self.data, health_dir=self.health, out=out, csv_path=csv_path,
            report_path=report, fetcher=fetcher,
        )
        return actual, out, csv_path, report

    def test_real_failure_shape_short_body_is_truthful_success(self) -> None:
        result, path = self.refresh_result(3)
        actual, out, _, report_path = self.run_read(result, path, lambda url: self.response(url, {0: "short", 1: "image"}))
        report = json.loads(report_path.read_text())
        self.assertTrue(actual["full_collection_success"])
        self.assertEqual(report["planned"], 3); self.assertEqual(report["succeeded"], 3); self.assertEqual(report["failed"], 0)
        self.assertEqual([row["content_quality"] for row in report["outcomes"]], ["normal", "short_text", "short_text"])
        rows = [json.loads(line) for line in out.read_text().splitlines()]
        self.assertTrue(all(row["是否全文解析"] == "是" and not row["失败原因"] for row in rows))

    def test_nineteen_mixed_candidate_local_failures_preserve_successes(self) -> None:
        result, path = self.refresh_result(19)
        variants = {0: "short", 1: "image", 2: "timeout", 3: "malformed", 4: "identity"}
        actual, out, _, report_path = self.run_read(result, path, lambda url: self.response(url, variants), "mixed")
        report = json.loads(report_path.read_text())
        self.assertFalse(actual["full_collection_success"]); self.assertTrue(actual["downstream_usable"])
        self.assertEqual((report["planned"], report["attempted"], report["succeeded"], report["failed"]), (19, 19, 16, 3))
        self.assertEqual(report["status"], "completed_with_failures")
        self.assertEqual(len(out.read_text().splitlines()), 16)
        failed = [row for row in report["outcomes"] if row["status"] == "failed"]
        self.assertTrue(all(row["artifact_count"] == 0 for row in failed))
        self.assertEqual(report["outputs"]["raw_artifact_count"], 16)
        serialized = report_path.read_text()
        self.assertNotIn("body-", serialized); self.assertNotIn("content_html", serialized)
        self.assertTrue(all(set(("page", "article_id", "title", "reason", "response_bytes", "html_chars", "text_chars")) <= set(row) for row in failed))

    def test_provider_error_missing_and_empty_html_are_candidate_local(self) -> None:
        result, path = self.refresh_result(5)
        variants = {0: "placeholder", 1: "login", 2: "missing", 3: "empty"}
        actual, _, _, report_path = self.run_read(result, path, lambda url: self.response(url, variants), "provider-errors")
        report = json.loads(report_path.read_text())
        self.assertEqual((actual["succeeded"], actual["failed"]), (1, 4))
        self.assertEqual({row["reason"] for row in report["outcomes"] if row["status"] == "failed"}, {
            "current_feed_provider_error_payload", "current_feed_content_html_missing", "current_feed_content_html_empty",
        })

    def test_all_short_articles_are_success_not_partial(self) -> None:
        result, path = self.refresh_result(6)
        actual, _, _, report_path = self.run_read(result, path, lambda url: self.response(url, {i: "short" for i in range(6)}), "all-short")
        report = json.loads(report_path.read_text())
        self.assertTrue(actual["full_collection_success"]); self.assertTrue(report["downstream_usable"])
        self.assertTrue(all(row["content_quality"] == "short_text" for row in report["outcomes"]))

    def test_system_post_read_drift_is_zero_output_hard_failure(self) -> None:
        result, path = self.refresh_result(3); calls = {"count": 0}
        def drift_after_last(url: str):
            response = self.response(url); calls["count"] += 1
            if calls["count"] == 3:
                connection = sqlite3.connect(self.data / "wewe-rss.db"); connection.execute("update feeds set sync_time=999"); connection.commit(); connection.close()
            return response
        out = self.root / "drift.jsonl"; csv_path = self.root / "drift.csv"; report = self.root / "drift.report.json"
        with self.assertRaises(ValueError):
            reader.run(refresh_result_path=path, run_id=result["run_id"], run_started_at_ms=1_900_000, data_dir=self.data, health_dir=self.health, out=out, csv_path=csv_path, report_path=report, fetcher=drift_after_last)
        self.assertFalse(out.exists()); self.assertFalse(csv_path.exists()); self.assertFalse(report.exists())
        self.assertFalse((self.root / "wewe_current_feed_raw").exists())

    def test_atomic_report_binds_outputs_and_failed_items_have_no_artifact(self) -> None:
        result, path = self.refresh_result(3)
        _, out, csv_path, report_path = self.run_read(result, path, lambda url: self.response(url, {0: "timeout"}), "atomic")
        report = json.loads(report_path.read_text())
        self.assertEqual(hashlib.sha256(out.read_bytes()).hexdigest(), report["outputs"]["jsonl_sha256"])
        self.assertEqual(hashlib.sha256(csv_path.read_bytes()).hexdigest(), report["outputs"]["csv_sha256"])
        failed_id = next(row["article_id"] for row in report["outcomes"] if row["status"] == "failed")
        self.assertNotIn(failed_id, out.read_text())

    def test_partial_downstream_is_usable_but_watermark_is_not_allowed(self) -> None:
        outcome = {"status": "completed_with_failures", "planned": 19, "attempted": 19, "succeeded": 18, "failed": 1, "downstream_usable": True, "full_collection_success": False}
        self.assertTrue(all(daily_pipeline.wechat_candidate_partial_checks(outcome).values()))
        self.assertTrue(daily_pipeline.is_candidate_local_partial({"candidate_local_partial": True}, {}))
        self.assertFalse(daily_pipeline.wechat_watermark_allowed(write_feishu=True, downstream_usable=True, freshness={"state": "updated_with_new_items"}, read_outcome=outcome))
        self.assertTrue(daily_pipeline.wechat_watermark_allowed(write_feishu=True, downstream_usable=True, freshness={"state": "updated_with_new_items"}, read_outcome={"full_collection_success": True}))

    def test_check_only_and_whole_feed_absence(self) -> None:
        result, path = self.refresh_result(3)
        plan = reader.run(refresh_result_path=path, run_id=result["run_id"], run_started_at_ms=1_900_000, data_dir=self.data, health_dir=self.health, check_only=True, fetcher=lambda _: (_ for _ in ()).throw(AssertionError("must not fetch")))
        self.assertEqual(plan["provider_requests"], 0)
        source = Path(reader.__file__).read_text(encoding="utf-8")
        self.assertNotIn("/feeds/all", source); self.assertNotIn("MAX_FEED_BYTES", source); self.assertIn('"limit": 1', source)


if __name__ == "__main__":
    unittest.main()
