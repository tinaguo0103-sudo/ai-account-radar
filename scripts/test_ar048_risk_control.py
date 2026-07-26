#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import source_control_http


class Result:
    def __init__(self, returncode: int, payload: dict | None = None):
        self.returncode = returncode
        self.stdout = json.dumps(payload or {})
        self.stderr = ""


class RiskControlTest(unittest.TestCase):
    def test_preflight_matrix_is_typed_and_only_logged_in_is_green(self):
        matrix = [
            (0, {"ok": True, "status": "session_verified", "login_state": "logged_in"}, True),
            (4, {"ok": False, "status": "login_preflight_failed", "login_state": "logged_out"}, False),
            (4, {"ok": False, "status": "login_preflight_failed", "login_state": "verification_required"}, False),
            (4, {"ok": False, "status": "login_preflight_failed", "login_state": "indeterminate"}, False),
        ]
        for returncode, payload, expected in matrix:
            with self.subTest(payload=payload), mock.patch.object(
                source_control_http.subprocess, "run", return_value=Result(returncode, payload)
            ):
                self.assertEqual(source_control_http.run_preflight()["ok"], expected)

    def test_foreground_action_uses_exact_fixed_profile_command(self):
        with mock.patch.object(
            source_control_http.subprocess, "run", return_value=Result(0)
        ) as runner:
            result = source_control_http.open_fixed_profile_foreground()
        self.assertTrue(result["ok"])
        command = runner.call_args.args[0]
        self.assertEqual(command[-3:], ["--port", "9333", "--foreground"])
        self.assertNotIn("cookie", str(command).lower())
        self.assertEqual(result["profile_identity"], "fixed_douyin_profile_9333")

    def test_resume_uses_exact_run_and_database_without_shell(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            run_id = "run_20260726_080000"
            run_root = source_control_http.ROOT / "output" / "runs" / run_id / "sources" / "douyin"
            with mock.patch.object(Path, "is_dir", return_value=True), mock.patch.object(
                Path, "open", mock.mock_open()
            ), mock.patch.object(source_control_http.subprocess, "Popen") as popen:
                popen.return_value.pid = 4321
                result = source_control_http.start_exact_resume(run_id, Path(tmpdir) / "source.sqlite3")
        self.assertEqual(result["status"], "resuming")
        kwargs = popen.call_args.kwargs
        command = popen.call_args.args[0]
        self.assertFalse(kwargs.get("shell", False))
        self.assertIn(run_id, command)
        self.assertIn("resume_douyin_risk_run.py", " ".join(command))


if __name__ == "__main__":
    unittest.main()
