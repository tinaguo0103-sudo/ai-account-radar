#!/usr/bin/env python3
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

import source_command_bridge as bridge


class SourceCommandBridgeTest(unittest.TestCase):
    def env(self):
        return patch.dict(os.environ, {
            "SOURCE_BRIDGE_URL": "http://127.0.0.1:4274",
            "SOURCE_BRIDGE_BEARER": "runtime-only",
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
            calls.append((method, url))
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
        self.assertEqual([url.rsplit("/", 1)[-1] for _, url in calls], [
            "identity", "claim", "sourcecmd_abcdefgh", "commands", "sources", "complete",
        ])
        self.assertNotIn("private-token", str(result))

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


if __name__ == "__main__":
    unittest.main()
