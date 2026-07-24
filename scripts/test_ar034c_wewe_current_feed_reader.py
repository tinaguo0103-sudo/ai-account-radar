from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import wewe_current_feed_reader as reader
import wewe_provider_health as health


RUN_ID = "run_20260723_080323"
RUN_STARTED = 1000


def direct_result() -> dict:
    before = {
        "active_account_count": 1,
        "feeds": [{"feed_id": "feed", "sync_time": 1, "updated_at_ms": 1, "article_count": 1, "max_publish_time": 10}],
    }
    after = {
        "active_account_count": 1,
        "feeds": [{"feed_id": "feed", "sync_time": 2, "updated_at_ms": 2, "article_count": 2, "max_publish_time": 20}],
    }
    return {
        "ok": True,
        "status": "completed",
        "full_collection_success": True,
        "downstream_usable": True,
        "run_id": RUN_ID,
        "run_started_at_ms": RUN_STARTED,
        "requested_at_ms": 1100,
        "completed_at_ms": 1200,
        "provider_request_count": 1,
        "planned_feed_count": 1,
        "successful_feed_count": 1,
        "failed_feed_count": 0,
        "successful_feed_ids": ["feed"],
        "feed_outcomes": [{"feed_id": "feed", "status": "success", "reason": "", "artifact_count": 1}],
        "new_item_count": 1,
        "secret_material_read": False,
        "secrets_exposed": False,
        "before": before,
        "after": after,
    }


class CurrentFeedReaderTests(unittest.TestCase):
    def test_health_accepts_exact_direct_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "result.json"
            path.write_text(json.dumps(direct_result()), encoding="utf-8")
            payload = health.validate_result(path, RUN_ID, RUN_STARTED)
            result = health.health_result(payload)
            self.assertTrue(result["ok"])
            self.assertEqual(1, result["provider_request_count"])
            self.assertNotIn("receipt", result)

    def test_wrong_run_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "result.json"
            path.write_text(json.dumps(direct_result()), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "refresh_result_run_mismatch"):
                health.validate_result(path, "run_wrong", RUN_STARTED)

    def test_result_rejects_success_set_drift_and_failed_feed_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "result.json"
            payload = direct_result()
            payload["successful_feed_ids"] = ["other"]
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(reader.CurrentFeedError, "refresh_successful_feed_set_invalid"):
                reader.load_refresh_result(path)

            payload = direct_result()
            payload["feed_outcomes"].append({
                "feed_id": "failed",
                "status": "failed",
                "reason": "provider_http_404",
                "artifact_count": 1,
            })
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(reader.CurrentFeedError, "refresh_failed_feed_artifact_pollution"):
                reader.load_refresh_result(path)

    def test_live_database_delta_is_the_only_feed_input(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            database = Path(tmp) / "wewe-rss.db"
            connection = sqlite3.connect(database)
            connection.executescript(
                "create table feeds(id text,status integer,sync_time integer,updated_at integer,mp_name text);"
                "create table articles(id text,title text,publish_time integer,mp_id text);"
                "insert into feeds values('feed',1,2,2,'owner');"
                "insert into articles values('old','old title',10,'feed');"
                "insert into articles values('new','new title',20,'feed');"
            )
            connection.commit()
            connection.close()
            planned = reader.current_run_plan(database, direct_result())
            self.assertEqual(["new"], [row["article_id"] for row in planned])


if __name__ == "__main__":
    unittest.main()
