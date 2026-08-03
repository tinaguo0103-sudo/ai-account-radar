#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import check_douyin_session as session
import douyin_chrome_runtime as runtime


class DouyinChromeRuntimeTests(unittest.TestCase):
    @staticmethod
    def open_files(profile: Path) -> list[str]:
        profile = profile.resolve()
        return [str(profile / "BrowserMetrics" / "BrowserMetrics-1.pma"), str(profile / "Default" / "History")]

    def test_canonical_profile_is_worktree_independent(self) -> None:
        self.assertNotIn("ai_account_radar", str(runtime.CANONICAL_PROFILE))
        self.assertTrue(str(runtime.CANONICAL_PROFILE).endswith("browser_profiles/douyin-chrome-profile"))

    def test_correct_process_identity_passes(self) -> None:
        profile = Path("/tmp/canonical").resolve()
        version = {"webSocketDebuggerUrl": "ws://127.0.0.1:9333/devtools/browser/session-a"}
        marker = {
            "marker_version": 1, "port": 9333, "pid": 42, "profile": str(profile),
            "profile_identity_hash": runtime.profile_identity_hash(profile),
            "browser_websocket_identity": version["webSocketDebuggerUrl"],
            "created_by": "start_douyin_cdp_chrome.py",
        }
        result = runtime.verify_listener_identity(
            9333, profile, version,
            pid_reader=lambda _port: [42],
            marker_reader=lambda _path: marker,
            open_file_reader=lambda _pid: self.open_files(profile),
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.pid, 42)

    def test_wrong_worktree_profile_fails_closed(self) -> None:
        expected = Path("/tmp/canonical").resolve()
        actual = Path("/tmp/ai_account_radar_rc/.local_services/douyin-chrome-profile").resolve()
        result = runtime.verify_listener_identity(
            9333, expected, {"webSocketDebuggerUrl": "ws://session"},
            pid_reader=lambda _port: [17170],
            marker_reader=lambda _path: {
                "marker_version": 1, "port": 9333, "pid": 17170, "profile": str(actual),
                "profile_identity_hash": runtime.profile_identity_hash(actual),
                "browser_websocket_identity": "ws://session", "created_by": "start_douyin_cdp_chrome.py",
            },
            open_file_reader=lambda _pid: self.open_files(actual),
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "profile_identity_mismatch")
        self.assertEqual(result.actual_profile, str(actual))

    def test_missing_marker_fails_closed(self) -> None:
        result = runtime.verify_listener_identity(
            9333, Path("/tmp/canonical"), {"webSocketDebuggerUrl": "ws://session"},
            pid_reader=lambda _port: [9],
            marker_reader=lambda _path: (_ for _ in ()).throw(FileNotFoundError("missing")),
            open_file_reader=lambda _pid: self.open_files(Path("/tmp/canonical")),
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "identity_marker_missing_or_invalid")

    def test_stale_pid_and_websocket_fail_closed(self) -> None:
        profile = Path("/tmp/canonical").resolve()
        base = {
            "marker_version": 1, "port": 9333, "pid": 8, "profile": str(profile),
            "profile_identity_hash": runtime.profile_identity_hash(profile),
            "browser_websocket_identity": "ws://old", "created_by": "start_douyin_cdp_chrome.py",
        }
        result = runtime.verify_listener_identity(
            9333, profile, {"webSocketDebuggerUrl": "ws://new"},
            pid_reader=lambda _port: [9], marker_reader=lambda _path: base,
            open_file_reader=lambda _pid: self.open_files(profile),
        )
        self.assertFalse(result.ok)
        self.assertIn("pid", result.error)
        self.assertIn("browser_websocket_identity", result.error)

    def test_marker_is_written_atomically_with_runtime_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            profile = Path(tmp) / "profiles" / "douyin"
            marker = runtime.write_identity_marker(9333, profile, 42, {
                "Browser": "Chrome/Test", "webSocketDebuggerUrl": "ws://session-a",
            }, open_file_reader=lambda _pid: self.open_files(profile))
            payload = json.loads(marker.read_text(encoding="utf-8"))
        self.assertEqual(payload["pid"], 42)
        self.assertEqual(payload["profile"], str(profile.resolve()))
        self.assertEqual(payload["browser_websocket_identity"], "ws://session-a")

    def test_wrong_listener_race_cannot_receive_marker(self) -> None:
        expected = Path("/tmp/canonical").resolve()
        wrong = Path("/tmp/racing-listener").resolve()
        with self.assertRaisesRegex(RuntimeError, "profile_open_file_proof_failed"):
            runtime.write_identity_marker(
                9333, expected, 42,
                {"Browser": "Chrome/Test", "webSocketDebuggerUrl": "ws://session-a"},
                open_file_reader=lambda _pid: self.open_files(wrong),
            )

    def test_forged_marker_cannot_override_wrong_lsof_profile(self) -> None:
        expected = Path("/tmp/canonical").resolve()
        actual = Path("/tmp/wrong-profile").resolve()
        marker = {
            "marker_version": 1, "port": 9333, "pid": 42, "profile": str(expected),
            "profile_identity_hash": runtime.profile_identity_hash(expected),
            "browser_websocket_identity": "ws://session", "created_by": "start_douyin_cdp_chrome.py",
        }
        result = runtime.verify_listener_identity(
            9333, expected, {"webSocketDebuggerUrl": "ws://session"},
            pid_reader=lambda _port: [42], marker_reader=lambda _path: marker,
            open_file_reader=lambda _pid: self.open_files(actual),
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.actual_profile, str(actual))
        self.assertFalse(result.profile_open_file_proof)

    def test_lsof_unavailable_fails_closed(self) -> None:
        result = runtime.verify_listener_identity(
            9333, Path("/tmp/canonical"), {"webSocketDebuggerUrl": "ws://session"},
            pid_reader=lambda _port: [42], marker_reader=lambda _path: {},
            open_file_reader=lambda _pid: (_ for _ in ()).throw(PermissionError("blocked")),
        )
        self.assertFalse(result.ok)
        self.assertIn("blocked", result.error)

    @mock.patch.object(session, "verify_listener_identity")
    def test_logged_in_probe_passes_without_secrets(self, identity_mock) -> None:
        identity_mock.return_value = runtime.ProcessIdentity(
            True, "profile_identity_verified", 9333, 42, "/tmp/p", "/tmp/p", 9333, "hash"
        )
        completed = subprocess.CompletedProcess([], 0, json.dumps({
            "state": "logged_in", "url": "https://www.douyin.com/", "title": "抖音",
            "markers": {"loggedInAvatar": True},
        }), "")
        code, payload = session.preflight(9333, Path("/tmp/p"), runner=lambda *a, **k: completed)
        self.assertEqual(code, 0)
        self.assertTrue(payload["ok"])
        serialized = json.dumps(payload)
        for secret_name in ("cookie", "token", "localStorage"):
            self.assertNotIn(secret_name, serialized)

    @mock.patch.object(session, "verify_listener_identity")
    def test_non_logged_in_states_fail(self, identity_mock) -> None:
        identity_mock.return_value = runtime.ProcessIdentity(
            True, "profile_identity_verified", 9333, 42, "/tmp/p", "/tmp/p", 9333, "hash"
        )
        for state in ("logged_out", "verification_required", "indeterminate"):
            completed = subprocess.CompletedProcess([], 4, json.dumps({"state": state, "markers": {}}), "")
            code, payload = session.preflight(9333, Path("/tmp/p"), runner=lambda *a, **k: completed)
            self.assertNotEqual(code, 0)
            self.assertEqual(payload["login_state"], state)
            self.assertEqual(payload["status"], {
                "logged_out": "browser_session_logged_out",
                "verification_required": "verification_required",
                "indeterminate": "browser_readiness_inconclusive",
            }[state])

    @mock.patch.object(session, "verify_listener_identity")
    def test_empty_and_malformed_dom_probe_output_fail_typed(self, identity_mock) -> None:
        identity_mock.return_value = runtime.ProcessIdentity(
            True, "profile_identity_verified", 9333, 42, "/tmp/p", "/tmp/p", 9333, "hash"
        )
        for stdout, expected in (("", "empty_dom_probe_output"), ("not-json", "malformed_dom_probe_output"), ("[]", "malformed_dom_probe_output")):
            completed = subprocess.CompletedProcess([], 0, stdout, "")
            code, payload = session.preflight(9333, Path("/tmp/p"), runner=lambda *a, **k: completed)
            self.assertEqual(code, 4)
            self.assertFalse(payload["ok"])
            self.assertEqual(payload["status"], "browser_readiness_inconclusive")
            self.assertEqual(payload["error"], expected)

    @mock.patch.object(session, "verify_listener_identity")
    def test_logged_in_payload_with_wrong_exit_fails_closed(self, identity_mock) -> None:
        identity_mock.return_value = runtime.ProcessIdentity(
            True, "profile_identity_verified", 9333, 42, "/tmp/p", "/tmp/p", 9333, "hash"
        )
        completed = subprocess.CompletedProcess([], 4, json.dumps({"state": "logged_in", "markers": {}}), "")
        code, payload = session.preflight(9333, Path("/tmp/p"), runner=lambda *a, **k: completed)
        self.assertEqual(code, 4)
        self.assertFalse(payload["ok"])
        self.assertIn("unexpected_dom_probe_exit", payload["error"])

    @mock.patch.object(session, "verify_listener_identity")
    def test_dom_probe_schema_mutations_fail_typed_without_exceptions(self, identity_mock) -> None:
        identity_mock.return_value = runtime.ProcessIdentity(
            True, "profile_identity_verified", 9333, 42, "/tmp/p", "/tmp/p", 9333, "hash"
        )
        mutations = {
            "markers_string": {"state": "logged_in", "markers": "yes"},
            "markers_list": {"state": "logged_in", "markers": []},
            "markers_null": {"state": "logged_in", "markers": None},
            "unknown_state": {"state": "authenticated", "markers": {}},
            "non_string_state": {"state": 1, "markers": {}},
            "null_state": {"state": None, "markers": {}},
            "marker_string_value": {"state": "logged_in", "markers": {"headerSelfLink": "true"}},
            "marker_integer_value": {"state": "logged_in", "markers": {"headerSelfLink": 1}},
            "url_wrong_type": {"state": "logged_in", "markers": {}, "url": []},
            "title_wrong_type": {"state": "logged_in", "markers": {}, "title": 42},
            "error_wrong_type": {"state": "indeterminate", "markers": {}, "error": {}},
            "visibility_wrong_type": {"state": "logged_in", "markers": {}, "visibility": []},
            "visibility_extra_field": {
                "state": "logged_in", "markers": {},
                "visibility": {
                    "viewport": {"width": 1280, "height": 720},
                    "visibleHeaderSelfMarkerCount": 2,
                    "visibleLoginMarkerCount": 0,
                    "visibleVerificationMarkerCount": 0,
                    "verificationIframeRects": [],
                    "accountIdentity": "must-not-pass",
                },
            },
            "visibility_count_bool": {
                "state": "logged_in", "markers": {},
                "visibility": {
                    "viewport": {"width": 1280, "height": 720},
                    "visibleHeaderSelfMarkerCount": True,
                    "visibleLoginMarkerCount": 0,
                    "visibleVerificationMarkerCount": 0,
                    "verificationIframeRects": [],
                },
            },
        }
        for name, mutation in mutations.items():
            with self.subTest(name=name):
                completed = subprocess.CompletedProcess([], 0, json.dumps(mutation), "")
                code, payload = session.preflight(9333, Path("/tmp/p"), runner=lambda *a, **k: completed)
                self.assertEqual(code, 4)
                self.assertFalse(payload["ok"])
                self.assertEqual(payload["status"], "browser_readiness_inconclusive")
                self.assertEqual(payload["login_state"], "indeterminate")
                self.assertEqual(payload["error"], "malformed_dom_probe_output")

    def test_dom_probe_schema_accepts_nullable_optional_strings(self) -> None:
        payload, error = session.parse_dom_probe_output(json.dumps({
            "state": "indeterminate",
            "markers": {"loginButton": False},
            "url": None,
            "title": "",
            "error": None,
        }))
        self.assertEqual(error, "")
        self.assertEqual(payload["state"], "indeterminate")

    def test_dom_probe_schema_accepts_sanitized_visibility_diagnostics(self) -> None:
        visibility = {
            "viewport": {"width": 1280, "height": 720},
            "visibleHeaderSelfMarkerCount": 2,
            "visibleLoginMarkerCount": 0,
            "visibleVerificationMarkerCount": 0,
            "verificationIframeRects": [{"width": 0, "height": 0, "visible": False}],
        }
        payload, error = session.parse_dom_probe_output(json.dumps({
            "state": "logged_in",
            "markers": {"multipleHeaderSelfMarkers": True},
            "visibility": visibility,
        }))
        self.assertEqual(error, "")
        self.assertEqual(payload["visibility"], visibility)


if __name__ == "__main__":
    unittest.main()
