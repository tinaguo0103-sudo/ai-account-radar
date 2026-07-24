from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

import daily_pipeline
import wewe_provider_refresh as refresh


RUN_ID = "run_20260724_080215"


def write_douyin_fixture(root: Path) -> tuple[Path, Path]:
    manual = root / "content_items_manual.jsonl"
    rows = []
    for index in range(141):
        rows.append({
            "运行批次": RUN_ID,
            "账号名/公众号名": f"success-{index % 28:02d}",
            "候选时态": "today_new" if index < 43 else "historical_unreviewed",
            "内容标题": f"item-{index:03d}",
            "内容指纹": f"fingerprint-{index:03d}",
        })
    manual.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    failed = [
        {
            "account_name": f"failed-{index}",
            "status": "failed",
            "failure_reason": "controlled account-local failure",
            "artifact_count": 0,
        }
        for index in range(3)
    ]
    result = root / "cdp_probe_results.json"
    result.write_text(json.dumps({
        "ok": False,
        "status": "completed_with_failures",
        "run_id": RUN_ID,
        "source_runtime_failure": None,
        "coverage": {
            "planned_accounts": 31,
            "attempted_accounts": 31,
            "successful_accounts": 28,
            "failed_account_count": 3,
            "failed_accounts": failed,
        },
        "item_lineage": {"ok": True, "item_count": 141},
        "manual_artifact": {
            "run_id": RUN_ID,
            "path": str(manual.resolve()),
            "sha256": hashlib.sha256(manual.read_bytes()).hexdigest(),
            "row_count": 141,
        },
        "candidate_lifecycle": {
            "today_new_count": 43,
            "historical_unreviewed_count": 98,
        },
        "rows": [
            *[
                {
                    "account_name": f"success-{index:02d}",
                    "status": "success",
                    "artifact_count": 1,
                }
                for index in range(28)
            ],
            *failed,
        ],
    }, ensure_ascii=False), encoding="utf-8")
    return result, manual


class AR046SourceContinuityTests(unittest.TestCase):
    def test_wewe_real_http_request_uses_exact_route_and_mpid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = root / "data"
            data.mkdir()
            database = data / "wewe-rss.db"
            connection = sqlite3.connect(database)
            connection.executescript(
                "create table accounts(status integer);"
                "create table feeds(id text,status integer,sync_time integer,updated_at integer);"
                "create table articles(mp_id text,publish_time integer);"
                "insert into accounts values(1);"
                "insert into feeds values('feed-exact',1,1,1);"
            )
            connection.commit()
            connection.close()
            requests: list[dict[str, object]] = []

            class Handler(BaseHTTPRequestHandler):
                def do_POST(self) -> None:
                    raw = self.rfile.read(int(self.headers["Content-Length"]))
                    requests.append({"path": self.path, "body": json.loads(raw)})
                    with sqlite3.connect(database) as db:
                        now = int(time.time())
                        db.execute(
                            "update feeds set sync_time=?, updated_at=? where id='feed-exact'",
                            (now, now * 1000),
                        )
                        db.execute(
                            "insert into articles(mp_id,publish_time) values('feed-exact',?)",
                            (now,),
                        )
                        db.commit()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"result":{"data":{"json":{"ok":true}}}}')

                def log_message(self, _format: str, *_args: object) -> None:
                    return

            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with mock.patch.dict(
                    "os.environ",
                    {
                        "WEWE_RSS_PROVIDER_URL": f"http://127.0.0.1:{server.server_port}",
                        "WEWE_RSS_AUTH_CODE": "test-only",
                    },
                    clear=False,
                ):
                    result = refresh.run_refresh(
                        RUN_ID,
                        1,
                        data_dir=data,
                        lock_path=root / "output/state/wewe-refresh/refresh.lock",
                        project_root=root,
                        deadline_ms=2000,
                        poll_interval_ms=10,
                    )
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)
            self.assertTrue(result["ok"])
            self.assertEqual("completed", result["status"])
            self.assertEqual(1, result["provider_request_count"])
            self.assertEqual(1, result["successful_feed_count"])
            self.assertEqual(0, result["failed_feed_count"])
            self.assertEqual(
                [{"path": "/trpc/feed.refreshArticles", "body": {"json": {"mpId": "feed-exact"}}}],
                requests,
            )
            self.assertFalse((root / "output/state/wewe-refresh/refresh.lock").exists())

    def test_wewe_feed_failure_is_typed_and_has_no_substitute(self) -> None:
        before = {
            "active_account_count": 1,
            "feeds": [{
                "feed_id": "feed-exact",
                "sync_time": 1,
                "updated_at_ms": 1,
                "article_count": 0,
                "max_publish_time": 0,
            }],
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = refresh.run_refresh(
                RUN_ID,
                1,
                data_dir=root,
                lock_path=root / "output/state/wewe-refresh/refresh.lock",
                project_root=root,
                request_fn=lambda _feed_id, _timeout: (_ for _ in ()).throw(refresh.RefreshError("provider_http_404")),
                snapshot_fn=lambda _database: before,
                clock_ms=iter([1, 2, 3]).__next__,
            )
        self.assertFalse(result["ok"])
        self.assertEqual("provider_failed", result["status"])
        self.assertEqual(1, result["provider_request_count"])
        self.assertEqual(0, result["successful_feed_count"])
        self.assertEqual(1, result["failed_feed_count"])
        self.assertEqual(0, result["feed_outcomes"][0]["artifact_count"])
        self.assertNotIn("fallback", json.dumps(result))

    def test_wewe_failed_feed_is_zero_while_successful_feed_continues(self) -> None:
        before = {
            "active_account_count": 1,
            "feeds": [
                {"feed_id": "feed-ok", "sync_time": 1, "updated_at_ms": 1, "article_count": 0, "max_publish_time": 0},
                {"feed_id": "feed-fail", "sync_time": 1, "updated_at_ms": 1, "article_count": 0, "max_publish_time": 0},
            ],
        }
        after = {
            "active_account_count": 1,
            "feeds": [
                {"feed_id": "feed-ok", "sync_time": 3, "updated_at_ms": 3, "article_count": 1, "max_publish_time": 3},
                {"feed_id": "feed-fail", "sync_time": 1, "updated_at_ms": 1, "article_count": 0, "max_publish_time": 0},
            ],
        }
        snapshots = iter([before, after])
        clocks = iter([1, 2, 3, 4, 5])

        def request(feed_id: str, _timeout: float) -> int:
            if feed_id == "feed-fail":
                raise refresh.RefreshError("provider_http_404")
            return 200

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = refresh.run_refresh(
                RUN_ID,
                1,
                data_dir=root,
                lock_path=root / "output/state/wewe-refresh/refresh.lock",
                project_root=root,
                request_fn=request,
                snapshot_fn=lambda _database: next(snapshots),
                clock_ms=lambda: next(clocks),
                sleep_fn=lambda _seconds: None,
            )
        self.assertTrue(result["ok"])
        self.assertEqual("completed_with_failures", result["status"])
        self.assertFalse(result["full_collection_success"])
        self.assertTrue(result["downstream_usable"])
        self.assertEqual(2, result["provider_request_count"])
        self.assertEqual(["feed-ok"], result["successful_feed_ids"])
        self.assertEqual(1, result["new_item_count"])
        self.assertEqual(0, result["feed_outcomes"][1]["artifact_count"])

    def test_douyin_31_28_3_partial_artifact_keeps_141_rows_and_temporal_labels(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result_path, manual_path = write_douyin_fixture(Path(tmp))
            outcome = daily_pipeline.current_douyin_artifact(result_path, manual_path, RUN_ID)
        self.assertTrue(outcome["ok"])
        self.assertTrue(outcome["partial"])
        self.assertEqual(141, outcome["row_count"])
        self.assertEqual(3, outcome["failed_account_count"])
        self.assertEqual(43, outcome["today_new"])
        self.assertEqual(98, outcome["historical_unreviewed"])

    def test_nonzero_partial_probe_attaches_successful_rows_instead_of_zeroing_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result_path, manual_path = write_douyin_fixture(Path(tmp))
            outcome = daily_pipeline.current_douyin_artifact(result_path, manual_path, RUN_ID)
            step = {"name": "Douyin", "returncode": 3}
            manual_inputs: list[Path] = []
            attached = daily_pipeline.attach_current_douyin_artifact(
                step, outcome, manual_path, manual_inputs,
            )
        self.assertTrue(attached)
        self.assertEqual([manual_path], manual_inputs)
        self.assertEqual(0, step["returncode"])
        self.assertEqual(3, step["source_returncode"])
        self.assertTrue(step["candidate_local_partial"])
        self.assertEqual(141, step["source_rows"])
        self.assertEqual(43, step["today_new_rows"])
        self.assertEqual(98, step["historical_unreviewed_rows"])

    def test_douyin_wrong_run_shared_failure_and_failed_account_pollution_are_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result_path, manual_path = write_douyin_fixture(root)
            self.assertEqual(
                "douyin_run_mismatch",
                daily_pipeline.current_douyin_artifact(result_path, manual_path, "run_other")["reason"],
            )
            payload = json.loads(result_path.read_text(encoding="utf-8"))
            payload["source_runtime_failure"] = {"reason": "target lost"}
            result_path.write_text(json.dumps(payload), encoding="utf-8")
            self.assertEqual(
                "douyin_shared_runtime_failure",
                daily_pipeline.current_douyin_artifact(result_path, manual_path, RUN_ID)["reason"],
            )
            payload["source_runtime_failure"] = None
            payload["coverage"]["failed_accounts"][0]["artifact_count"] = 1
            result_path.write_text(json.dumps(payload), encoding="utf-8")
            self.assertEqual(
                "douyin_failed_account_artifact_pollution",
                daily_pipeline.current_douyin_artifact(result_path, manual_path, RUN_ID)["reason"],
            )

    def test_removed_refresh_all_route_is_physically_unreachable(self) -> None:
        source = Path(refresh.__file__).read_text(encoding="utf-8")
        self.assertNotIn("refreshAllArticles", source)
        self.assertEqual(1, source.count("/trpc/feed.refreshArticles"))
        pipeline = Path(daily_pipeline.__file__).read_text(encoding="utf-8")
        self.assertNotIn('douyin_step["returncode"] == 0 and row_count > 0', pipeline)


if __name__ == "__main__":
    unittest.main()
