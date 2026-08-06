#!/usr/bin/env python3
from __future__ import annotations

import os
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import start_douyin_cdp_chrome as chrome


class StartDouyinCdpChromeTests(unittest.TestCase):
    def test_hidden_launch_uses_bundle_executable_without_launchservices(self) -> None:
        app_path = Path("/Applications/Google Chrome.app")
        profile = Path("/tmp/douyin-profile")

        command = chrome.chrome_app_launch_command(app_path, 9333, profile, "https://www.douyin.com/", "hidden")

        self.assertEqual(command[0], "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
        self.assertNotIn("/usr/bin/open", command)
        self.assertIn("--remote-debugging-port=9333", command)
        self.assertIn(f"--user-data-dir={profile}", command)
        self.assertIn("--start-minimized", command)

    def test_foreground_launch_command_remains_launchservices_compatible(self) -> None:
        app_path = Path("/Applications/Google Chrome.app")
        profile = Path("/tmp/douyin-profile")
        command = chrome.chrome_app_launch_command(
            app_path, 9333, profile, "https://www.douyin.com/", "foreground",
        )
        self.assertEqual(command[:3], ["/usr/bin/open", "-n", "-a"])
        self.assertEqual(command[3], str(app_path))
        self.assertEqual(command[4], "--args")
        self.assertNotIn("--start-minimized", command)

    def test_hidden_launch_spawns_direct_executable_exactly_once(self) -> None:
        calls = []
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(chrome, "ROOT", Path(tmp)), mock.patch.object(
            chrome, "chrome_app_path", return_value=Path("/Applications/Google Chrome.app"),
        ):
            proc, log_path = chrome.launch_chrome(
                9333,
                Path(tmp) / "profile",
                "https://www.douyin.com/",
                "hidden",
                popen=lambda command, **kwargs: calls.append((command, kwargs)) or SimpleNamespace(pid=42, poll=lambda: None),
            )
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0][0], "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
        self.assertEqual(calls[0][1]["cwd"], Path(tmp))
        self.assertTrue(calls[0][1]["start_new_session"])
        self.assertEqual(proc.pid, 42)
        self.assertTrue(log_path.name.endswith("hidden-9333.log"))

    def test_foreground_launch_uses_existing_run_path_once(self) -> None:
        calls = []
        completed = SimpleNamespace(returncode=0, stdout="", stderr="", pid=None)
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(chrome, "ROOT", Path(tmp)), mock.patch.object(
            chrome, "chrome_app_path", return_value=Path("/Applications/Google Chrome.app"),
        ):
            chrome.launch_chrome(
                9333,
                Path(tmp) / "profile",
                "https://www.douyin.com/",
                "foreground",
                run=lambda command, **kwargs: calls.append((command, kwargs)) or completed,
            )
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0][:3], ["/usr/bin/open", "-n", "-a"])

    def run_hidden_main(self, root: Path, launch_result, version, identity=None):
        app = root / "Google Chrome.app"
        app.mkdir()
        output = io.StringIO()
        patches = [
            mock.patch.object(chrome, "ROOT", root),
            mock.patch.object(chrome, "chrome_app_path", return_value=app),
            mock.patch.object(chrome, "configured_profile", return_value=root / "profile"),
            mock.patch.object(chrome, "launch_chrome", side_effect=launch_result if isinstance(launch_result, Exception) else None, return_value=None if isinstance(launch_result, Exception) else launch_result),
            mock.patch.object(chrome, "cdp_version", side_effect=[None, version]),
            mock.patch.object(chrome.time, "sleep"),
            mock.patch.object(sys, "argv", ["start_douyin_cdp_chrome.py", "--mode", "hidden"]),
        ]
        if identity is not None:
            patches.extend([
                mock.patch.object(chrome, "listener_pids", return_value=[42]),
                mock.patch.object(chrome, "write_identity_marker", return_value=root / "identity.json"),
                mock.patch.object(chrome, "verify_listener_identity", return_value=identity),
            ])
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
            if identity is None:
                with redirect_stdout(output):
                    code = chrome.main()
            else:
                with patches[7], patches[8], patches[9], redirect_stdout(output):
                    code = chrome.main()
        return code, json.loads(output.getvalue())

    def test_hidden_direct_launch_success_reaches_canonical_ready(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            log = root / "direct.log"
            log.write_text("spawned: true\n")
            proc = SimpleNamespace(pid=42, poll=lambda: None)
            identity = SimpleNamespace(
                ok=True,
                status="profile_identity_verified",
                actual_profile=str(root / "profile"),
                to_dict=lambda: {"ok": True, "status": "profile_identity_verified"},
            )
            code, payload = self.run_hidden_main(
                root, (proc, log), {"Browser": "Chrome", "webSocketDebuggerUrl": "ws://fixed"}, identity,
            )
        self.assertEqual(code, 0)
        self.assertEqual(payload["status"], "started")
        self.assertIsNone(payload["launch_returncode"])
        self.assertTrue(payload["profile_identity"]["ok"])

    def test_hidden_direct_launch_spawn_error_is_typed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            code, payload = self.run_hidden_main(Path(tmp), OSError("direct_exec_denied"), None)
        self.assertEqual(code, 1)
        self.assertEqual(payload["status"], "launch_failed_or_not_ready")
        self.assertIn("direct_exec_denied", payload["stderr"])

    def test_hidden_direct_launch_nonzero_not_ready_is_typed_with_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            log = root / "direct.log"
            log.write_text("direct chrome exited\n")
            proc = SimpleNamespace(pid=42, poll=lambda: 7)
            code, payload = self.run_hidden_main(root, (proc, log), None)
        self.assertEqual(code, 1)
        self.assertEqual(payload["status"], "launch_failed_or_not_ready")
        self.assertEqual(payload["launch_returncode"], 7)
        self.assertIn("direct chrome exited", payload["stderr"])

    def test_chrome_app_path_can_be_overridden_by_env(self) -> None:
        original = os.environ.get("CHROME_APP_PATH")
        with tempfile.TemporaryDirectory() as tmp:
            override = Path(tmp) / "Google Chrome.app"
            os.environ["CHROME_APP_PATH"] = str(override)
            try:
                self.assertEqual(chrome.chrome_app_path(), override)
            finally:
                if original is None:
                    os.environ.pop("CHROME_APP_PATH", None)
                else:
                    os.environ["CHROME_APP_PATH"] = original

    def test_default_profile_is_canonical_not_repo_local(self) -> None:
        self.assertIn(".codex/ai-account-radar-runtime/browser_profiles", str(chrome.DEFAULT_PROFILE))
        self.assertNotIn(".local_services", str(chrome.DEFAULT_PROFILE))

    def test_not_ready_status_does_not_dereference_missing_identity(self) -> None:
        self.assertEqual(chrome.launch_status(False, None), "launch_failed_or_not_ready")


if __name__ == "__main__":
    unittest.main()
