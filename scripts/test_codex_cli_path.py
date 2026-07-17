from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import codex_cli_path
import codex_script_package_runner as runner
import install_script_package_watcher_launch_agent as installer


class CodexCliPathTests(unittest.TestCase):
    def executable(self, root: Path, name: str) -> Path:
        path = root / name
        path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        path.chmod(0o755)
        return path

    def test_valid_configured_executable_wins(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            configured = self.executable(root, "configured")
            current = self.executable(root, "current")
            self.assertEqual(str(configured), codex_cli_path.resolve_codex_cli(str(configured), [str(current)]))

    def test_stale_old_path_falls_through_to_current_chatgpt_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            current = self.executable(Path(tmp), "chatgpt-codex")
            with mock.patch.object(codex_cli_path.shutil, "which", return_value=None):
                resolved = codex_cli_path.resolve_codex_cli(
                    "/Applications/Codex.app/Contents/Resources/codex",
                    [str(current), "/missing/legacy"],
                )
            self.assertEqual(str(current), resolved)

    def test_legacy_app_candidate_remains_supported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            legacy = self.executable(Path(tmp), "legacy-codex")
            with mock.patch.object(codex_cli_path.shutil, "which", return_value=None):
                self.assertEqual(str(legacy), codex_cli_path.resolve_codex_cli("", ["/missing/current", str(legacy)]))

    def test_path_command_override_and_path_fallback_are_supported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            binary = self.executable(Path(tmp), "codex")

            def which(name: str) -> str | None:
                return str(binary) if name in {"custom-codex", "codex"} else None

            with mock.patch.object(codex_cli_path.shutil, "which", side_effect=which):
                self.assertEqual(str(binary), codex_cli_path.resolve_codex_cli("custom-codex", []))
                self.assertEqual(str(binary), codex_cli_path.resolve_codex_cli("", ["/missing/current"]))

    def test_missing_executable_fails_before_generation_subprocess(self) -> None:
        with mock.patch.object(codex_cli_path.shutil, "which", return_value=None):
            with self.assertRaisesRegex(FileNotFoundError, "Codex CLI executable not found"):
                codex_cli_path.resolve_codex_cli("/missing/configured", ["/missing/current", "/missing/legacy"])
        with mock.patch.object(runner, "resolve_codex_cli", side_effect=FileNotFoundError("Codex CLI executable not found")), \
             mock.patch.object(runner.subprocess, "run") as subprocess_run:
            with self.assertRaisesRegex(FileNotFoundError, "Codex CLI executable not found"):
                runner.codex_bin()
        subprocess_run.assert_not_called()

    def test_installer_plist_uses_resolved_binary_and_parent_path(self) -> None:
        resolved = "/Applications/ChatGPT.app/Contents/Resources/codex"
        with mock.patch.dict(os.environ, {"CODEX_BIN": "/missing/legacy"}), \
             mock.patch.object(installer, "resolve_codex_cli", return_value=resolved):
            plist = installer.build_plist(Path("/runtime"), Path("/display"), 5, 2, 5, "/usr/bin/python3")
        environment = plist["EnvironmentVariables"]
        self.assertEqual(resolved, environment["CODEX_BIN"])
        self.assertTrue(environment["PATH"].startswith("/Applications/ChatGPT.app/Contents/Resources:"))


if __name__ == "__main__":
    unittest.main()
