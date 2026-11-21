#!/usr/bin/env python3
"""Behavior tests for Stage 3A hardening in ai_account_radar."""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import feishu_topic_decision_card
import publish_website_projection
import source_control_http
import website_publisher_client
from source_control import SourceControl


class Stage3HardeningTests(unittest.TestCase):
    def test_feishu_callback_server_is_hard_retired(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "feishu_callback_retired"):
            feishu_topic_decision_card.serve_callback("127.0.0.1", 0, False)

    def test_source_control_http_guards_origin_content_type_secret_and_body_cap(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "source_control.sqlite"
            service = SourceControl(db_path)
            service.initialize()

            class GuardedHandler(source_control_http.Handler):
                pass

            GuardedHandler.service = service
            GuardedHandler.instance_id = "test-inst-01"
            GuardedHandler.shared_secret = "top-secret-token"
            GuardedHandler.request_counts = {}

            server = ThreadingHTTPServer(("127.0.0.1", 0), GuardedHandler)
            port = server.server_address[1]
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base = f"http://127.0.0.1:{port}"

                # 1. Missing shared secret -> 401
                with self.assertRaises(urllib.error.HTTPError) as ctx:
                    urllib.request.urlopen(f"{base}/v1/identity", timeout=5)
                self.assertEqual(ctx.exception.code, 401)

                # 2. Cross-site Origin -> 403
                req = urllib.request.Request(
                    f"{base}/v1/identity",
                    headers={
                        "X-Source-Control-Token": "top-secret-token",
                        "Origin": "https://evil.example",
                    },
                )
                with self.assertRaises(urllib.error.HTTPError) as ctx:
                    urllib.request.urlopen(req, timeout=5)
                self.assertEqual(ctx.exception.code, 403)

                # 3. Valid secret + loopback origin -> 200
                req = urllib.request.Request(
                    f"{base}/v1/identity",
                    headers={
                        "X-Source-Control-Token": "top-secret-token",
                        "Origin": "http://127.0.0.1:4190",
                    },
                )
                with urllib.request.urlopen(req, timeout=5) as resp:
                    self.assertEqual(resp.status, 200)
                    body = json.loads(resp.read())
                    self.assertEqual(body["instance_id"], "test-inst-01")

                # 4. POST with non-JSON Content-Type -> 415
                req = urllib.request.Request(
                    f"{base}/v1/commands",
                    data=b"{}",
                    method="POST",
                    headers={
                        "X-Source-Control-Token": "top-secret-token",
                        "Content-Type": "text/plain",
                    },
                )
                with self.assertRaises(urllib.error.HTTPError) as ctx:
                    urllib.request.urlopen(req, timeout=5)
                self.assertEqual(ctx.exception.code, 415)

                # 5. Oversized Content-Length -> 413
                import http.client
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                conn.putrequest("POST", "/v1/commands")
                conn.putheader("X-Source-Control-Token", "top-secret-token")
                conn.putheader("Content-Type", "application/json")
                conn.putheader("Content-Length", str(source_control_http.MAX_BODY_BYTES + 16))
                conn.endheaders()
                resp = conn.getresponse()
                self.assertEqual(resp.status, 413)
                conn.close()
            finally:
                server.shutdown()
                server.server_close()

    def test_build_workflow_projection_closes_sqlite_on_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "empty.sqlite"
            conn = sqlite3.connect(db_path)
            conn.execute("CREATE TABLE daily_runs (run_id TEXT PRIMARY KEY, status TEXT)")
            conn.execute("CREATE TABLE stage_results (run_id TEXT, stage TEXT, payload_json TEXT)")
            conn.commit()
            conn.close()

            closed_flag = {"closed": False}
            real_connect = sqlite3.connect

            class TrackedConnection:
                def __init__(self, inner: sqlite3.Connection) -> None:
                    self._inner = inner

                def __getattr__(self, name: str):
                    return getattr(self._inner, name)

                def __setattr__(self, name: str, value) -> None:
                    if name == "_inner":
                        super().__setattr__(name, value)
                    else:
                        setattr(self._inner, name, value)

                def close(self) -> None:
                    closed_flag["closed"] = True
                    self._inner.close()

            with mock.patch.object(
                publish_website_projection.sqlite3,
                "connect",
                side_effect=lambda *a, **kw: TrackedConnection(real_connect(*a, **kw)),
            ):
                with self.assertRaises(publish_website_projection.ProjectionError):
                    publish_website_projection.build_workflow_projection(db_path, "missing-run", "auth-1")
            self.assertTrue(closed_flag["closed"], "SQLite connection must be closed even on ProjectionError")

    def test_website_publisher_client_resolves_repo_relative_path_and_does_not_mutate_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg_file = Path(tmp) / "pub.json"
            cfg_file.write_text(
                json.dumps({
                    "website_url": "http://127.0.0.1:4190",
                    "authority_identity": "local-prod",
                    "app_bearer": "app-secret-123",
                    "sites_bearer": "",
                }),
                encoding="utf-8",
            )
            before_app = os.environ.get("WEBSITE_PROJECTION_BEARER")
            before_sites = os.environ.get("WEBSITE_PROJECTION_SIWC_BYPASS_BEARER")
            captured_calls: list[dict] = []

            def fake_request_json(method, url, payload=None, *, app_bearer=None, sites_bearer=None):
                captured_calls.append({"method": method, "app_bearer": app_bearer, "sites_bearer": sites_bearer})
                if method == "GET" and len(captured_calls) == 1:
                    raise publish_website_projection.ProjectionError("business_projection_missing")
                if method == "POST":
                    return {"ok": True}
                return {
                    "run_id": "run-1",
                    "business_date": "2026-11-21",
                    "run_status": "completed",
                    "counts": {"content": 0, "topics": 0, "scripts": 0},
                    "article_count": 0,
                    "authority_identity": "local-prod",
                    "articles": [],
                    "scripts": [],
                }

            fake_payload = {
                "run_id": "run-1",
                "business_date": "2026-11-21",
                "run": {"status": "completed"},
                "collected_items": [],
                "topics": [],
                "scripts": [],
                "articles": [],
            }
            with mock.patch.dict(os.environ, {"WEBSITE_PUBLISHER_CONFIG": str(cfg_file)}, clear=False):
                with mock.patch.object(website_publisher_client, "build_workflow_projection", return_value=fake_payload):
                    with mock.patch.object(website_publisher_client, "request_json", side_effect=fake_request_json):
                        result = website_publisher_client.publish_terminal(Path(tmp) / "wf.sqlite", "run-1")
            self.assertEqual(result["request_ledger"]["terminal_post"], 1)
            self.assertEqual(os.environ.get("WEBSITE_PROJECTION_BEARER"), before_app)
            self.assertEqual(os.environ.get("WEBSITE_PROJECTION_SIWC_BYPASS_BEARER"), before_sites)
            self.assertTrue(all(call["app_bearer"] == "app-secret-123" for call in captured_calls))

        # Default path resolves inside repo root regardless of CWD
        with mock.patch.dict(os.environ, {"WEBSITE_PUBLISHER_CONFIG": ""}, clear=False):
            default_path = website_publisher_client.config_path()
            self.assertEqual(default_path, (website_publisher_client.REPO_ROOT / "output/state/website_publisher.json").resolve())


if __name__ == "__main__":
    unittest.main()
