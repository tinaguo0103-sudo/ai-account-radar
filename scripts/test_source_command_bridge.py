#!/usr/bin/env python3
from __future__ import annotations

import os
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import source_command_bridge as bridge


class SourceCommandBridgeTest(unittest.TestCase):
    def env(self):
        return patch.dict(os.environ, {
            "SOURCE_BRIDGE_URL": "http://127.0.0.1:4274",
            "SOURCE_BRIDGE_BEARER": "runtime-only",
            "SOURCE_BRIDGE_SIWC_BYPASS_BEARER": "sites-runtime-only",
            "SOURCE_CONTROL_URL": "http://127.0.0.1:4280",
            "SOURCE_CONTROL_INSTANCE_ID": "qa-source",
            "SOURCE_CONTROL_DATABASE_ID": "qa-db",
        }, clear=True)

    def test_idle_claim_has_no_domain_action(self):
        calls = []

        def fake(method, url, **kwargs):
            calls.append((method, url))
            if url.endswith("/v1/identity"):
                return {"instance_id": "qa-source", "database_identity": "qa-db", "authority": "source_control_sqlite"}
            return {"ok": True, "command": None}

        with self.env(), patch.object(bridge, "request_json", side_effect=fake):
            result = bridge.run_once()
        self.assertEqual(result["status"], "idle")
        self.assertEqual(result["domain_actions"], 0)
        self.assertFalse(any("/v1/commands" in url for _, url in calls))

    def test_applied_command_uses_domain_then_receipt(self):
        calls = []

        def fake(method, url, **kwargs):
            calls.append((method, url, kwargs))
            if url.endswith("/v1/identity"):
                return {"instance_id": "qa-source", "database_identity": "qa-db", "authority": "source_control_sqlite"}
            if url.endswith("/claim"):
                return {"command": {
                    "command_id": "sourcecmd_abcdefgh", "expected_revision": 1,
                    "operations": [], "claim_token": "private-token",
                }}
            if method == "GET" and url.endswith("/v1/commands/sourcecmd_abcdefgh"):
                raise bridge.BridgeError("command_not_found")
            if url.endswith("/v1/commands"):
                return {"status": "applied", "applied_revision": 2, "result_sha256": "result"}
            if url.endswith("/v1/sources"):
                return {"revision": 2, "accounts": []}
            if url.endswith("/complete"):
                return {"receipt": {"status": "applied"}}
            raise AssertionError(url)

        with self.env(), patch.object(bridge, "request_json", side_effect=fake):
            result = bridge.run_once()
        self.assertEqual(result["status"], "applied")
        self.assertEqual(result["projection_revision"], 2)
        self.assertEqual([url.rsplit("/", 1)[-1] for _, url, _ in calls], [
            "identity", "claim", "sourcecmd_abcdefgh", "commands", "sources", "complete",
        ])
        bridge_calls = [kwargs for _, url, kwargs in calls if "/api/" in url]
        self.assertTrue(all(call.get("sites_bypass_bearer") == "sites-runtime-only" for call in bridge_calls))
        self.assertTrue(all(
            call.get("bearer") == "runtime-only"
            for _, url, call in calls
            if url.endswith("/claim") or url.endswith("/complete")
        ))
        self.assertTrue(all(
            "sites_bypass_bearer" not in call
            for _, url, call in calls
            if "/v1/" in url
        ))
        self.assertNotIn("private-token", str(result))
        self.assertNotIn("sites-runtime-only", str(result))

    def test_unknown_post_reconciles_existing_command(self):
        def fake(method, url, **kwargs):
            if url.endswith("/v1/identity"):
                return {"instance_id": "qa-source", "database_identity": "qa-db", "authority": "source_control_sqlite"}
            if url.endswith("/claim"):
                return {"command": {
                    "command_id": "sourcecmd_abcdefgh", "expected_revision": 1,
                    "operations": [], "claim_token": "private-token",
                }}
            if method == "GET" and url.endswith("/v1/commands/sourcecmd_abcdefgh"):
                return {"status": "applied", "applied_revision": 2, "result_sha256": "result"}
            if url.endswith("/v1/sources"):
                return {"revision": 2, "accounts": []}
            if url.endswith("/complete"):
                return {"receipt": {"status": "applied"}}
            raise AssertionError(url)

        with self.env(), patch.object(bridge, "request_json", side_effect=fake):
            result = bridge.run_once()
        self.assertTrue(result["domain_reconciled"])
        self.assertEqual(result["domain_actions"], 0)

    def test_unknown_receipt_post_reconciles_exact_public_readback(self):
        def fake(method, url, **kwargs):
            if url.endswith("/v1/identity"):
                return {"instance_id": "qa-source", "database_identity": "qa-db", "authority": "source_control_sqlite"}
            if url.endswith("/claim"):
                return {"command": {
                    "command_id": "sourcecmd_abcdefgh", "expected_revision": 1,
                    "operations": [], "claim_token": "private-token",
                }}
            if method == "GET" and url.endswith("/v1/commands/sourcecmd_abcdefgh"):
                raise bridge.BridgeError("command_not_found")
            if url.endswith("/v1/commands"):
                return {"status": "applied", "applied_revision": 2, "result_sha256": "result"}
            if url.endswith("/v1/sources"):
                return {"revision": 2, "accounts": []}
            if method == "POST" and url.endswith("/complete"):
                raise bridge.BridgeError("bridge_transport_unavailable")
            if method == "GET" and url.endswith("/api/source-commands/sourcecmd_abcdefgh"):
                return {"receipt": {"status": "applied"}}
            raise AssertionError(url)

        with self.env(), patch.object(bridge, "request_json", side_effect=fake):
            result = bridge.run_once()
        self.assertTrue(result["receipt_reconciled"])
        self.assertEqual(result["receipt_writes"], 0)

    def test_missing_secret_fails_without_outputting_value(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(bridge.BridgeError, "source_bridge_url_missing"):
                bridge.run_once()

    def test_missing_sites_machine_identity_stops_before_claim(self):
        with self.env():
            del os.environ["SOURCE_BRIDGE_SIWC_BYPASS_BEARER"]
            with patch.object(bridge, "source_identity", return_value={"ok": True}), \
                    patch.object(bridge, "request_json") as request:
                with self.assertRaisesRegex(
                    bridge.BridgeError,
                    "source_bridge_siwc_bypass_bearer_missing",
                ):
                    bridge.run_once()
                request.assert_not_called()

    def test_request_json_sends_distinct_sites_and_app_bearers(self):
        received = {}

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                received["sites"] = self.headers.get("OAI-Sites-Authorization")
                received["app"] = self.headers.get("Authorization")
                body = json.dumps({"ok": True}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args):
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            result = bridge.request_json(
                "POST",
                f"http://127.0.0.1:{server.server_port}/claim",
                bearer="app-secret",
                sites_bypass_bearer="sites-secret",
                payload={},
            )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
        self.assertEqual(result, {"ok": True})
        self.assertEqual(received["sites"], "Bearer sites-secret")
        self.assertEqual(received["app"], "Bearer app-secret")
        self.assertNotIn("secret", str(result))

    def test_siwc_rejection_is_typed_without_response_body(self):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(403)
                self.end_headers()

            def log_message(self, *_args):
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with self.assertRaisesRegex(
                bridge.BridgeError,
                "sites_siwc_machine_auth_failed",
            ):
                bridge.request_json(
                    "GET",
                    f"http://127.0.0.1:{server.server_port}/health",
                    sites_bypass_bearer="wrong",
                )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
