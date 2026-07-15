from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pre_merge_check as gate


def command_result(stdout: str = "", returncode: int = 0, stderr: str = "") -> dict:
    return {"command": [], "cwd": "", "returncode": returncode, "stdout": stdout, "stderr": stderr}


class ProductionReleaseGateTests(unittest.TestCase):
    def test_git_contract_accepts_exact_clean_production_main(self) -> None:
        head = "a" * 40
        outputs = [
            (0, "main", ""),
            (0, head, ""),
            (0, head, ""),
            (0, "", ""),
        ]
        with patch.object(gate, "configured_production_root", return_value=gate.ROOT.resolve()), patch.object(gate, "git_text", side_effect=outputs):
            result = gate.check_git_production_release(head)
        self.assertTrue(result["ok"])
        self.assertEqual(result["local_head"], head)

    def test_git_contract_rejects_missing_or_mismatched_head_and_wrong_context(self) -> None:
        head = "a" * 40
        other = "b" * 40
        cases = [
            ("", gate.ROOT.resolve(), "main", head, head, "", "missing_expected_head"),
            ("abc", gate.ROOT.resolve(), "main", head, head, "", "invalid_expected_head"),
            (other, gate.ROOT.resolve(), "main", head, head, "", "head_mismatch"),
            (head, Path("/tmp/not-production"), "main", head, head, "", "not_configured_production_root"),
            (head, gate.ROOT.resolve(), "release/test", head, head, "", "not_main_branch"),
            (head, gate.ROOT.resolve(), "main", head, head, " M file", "dirty_worktree"),
        ]
        for expected, root, branch, local, remote, status, reason in cases:
            outputs = [(0, branch, ""), (0, local, ""), (0, remote, ""), (0, status, "")]
            with self.subTest(reason=reason), patch.object(gate, "configured_production_root", return_value=root), patch.object(gate, "git_text", side_effect=outputs):
                result = gate.check_git_production_release(expected)
                self.assertFalse(result["ok"])
                self.assertIn(reason, result["reasons"])

    def test_check_only_accepts_safe_skip_and_preserves_artifacts(self) -> None:
        payload = {"ok": True, "check_only": True, "sent": False, "reason": "no_today_candidates"}
        def fake_run(command, **_kwargs):
            return {**command_result(json.dumps(payload)), "command": command}
        with patch.object(gate, "run", side_effect=fake_run), patch.object(gate, "topic_card_artifact_snapshot", side_effect=[{"a": "1"}, {"a": "1"}]):
            result = gate.check_topic_card_production_release()
        self.assertTrue(result["ok"])
        self.assertIn("--check-only", result["command"])
        self.assertIn("--no-notify", result["command"])

    def test_check_only_rejects_malformed_sent_or_artifact_mutation(self) -> None:
        cases = [
            ("not-json", [{}, {}]),
            (json.dumps({"check_only": True, "sent": True}), [{}, {}]),
            (json.dumps({"check_only": False, "sent": False}), [{}, {}]),
            (json.dumps({"check_only": True, "sent": False, "writes_feishu": True}), [{}, {}]),
            (json.dumps({"check_only": True, "sent": False, "notification_sent": True}), [{}, {}]),
            (json.dumps({"check_only": True, "sent": False}), [{"a": "1"}, {"a": "2"}]),
        ]
        for stdout, snapshots in cases:
            with self.subTest(stdout=stdout), patch.object(gate, "run", return_value=command_result(stdout)), patch.object(gate, "topic_card_artifact_snapshot", side_effect=snapshots):
                self.assertFalse(gate.check_topic_card_production_release()["ok"])

    def test_default_dev_guard_semantics_remain(self) -> None:
        blocked = command_result(json.dumps({"check_only": True, "sent": False, "reason": "running_from_unexpected_directory"}), returncode=2)
        with patch.object(gate, "run", return_value=blocked), patch.object(gate, "ROOT", Path("/tmp/release-worktree")), patch.object(gate, "PRODUCTION_ROOT", Path("/tmp/production")):
            self.assertTrue(gate.check_topic_card_guard()["ok"])

    def test_default_gate_accepts_rc2_but_not_main(self) -> None:
        with patch.object(gate, "run", return_value=command_result("## release/ar020e-rc2-20260715...origin/main\n")):
            self.assertTrue(gate.check_git_dev()["ok"])
        with patch.object(gate, "run", return_value=command_result("## main...origin/main\n")):
            self.assertFalse(gate.check_git_dev()["ok"])

    def test_real_temporary_git_fixture_enforces_main_clean_and_remote_head(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            import subprocess
            base = Path(temp)
            remote = base / "origin.git"
            seed = base / "seed"
            root = base / "production"
            subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
            subprocess.run(["git", "init", "-b", "main", str(seed)], check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "fixture@example.invalid"], cwd=seed, check=True)
            subprocess.run(["git", "config", "user.name", "Fixture"], cwd=seed, check=True)
            (seed / "file.txt").write_text("ok\n", encoding="utf-8")
            subprocess.run(["git", "add", "file.txt"], cwd=seed, check=True)
            subprocess.run(["git", "commit", "-m", "fixture"], cwd=seed, check=True, capture_output=True)
            subprocess.run(["git", "remote", "add", "origin", str(remote)], cwd=seed, check=True)
            subprocess.run(["git", "push", "-u", "origin", "main"], cwd=seed, check=True, capture_output=True)
            subprocess.run(["git", "clone", "--branch", "main", str(remote), str(root)], check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "fixture@example.invalid"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "Fixture"], cwd=root, check=True)
            head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, check=True, text=True, capture_output=True).stdout.strip()
            with patch.object(gate, "ROOT", root), patch.object(gate, "configured_production_root", return_value=root.resolve()):
                clean_result = gate.check_git_production_release(head)
                self.assertTrue(clean_result["ok"], clean_result)
                (root / "file.txt").write_text("dirty\n", encoding="utf-8")
                result = gate.check_git_production_release(head)
                self.assertFalse(result["ok"])
                self.assertIn("dirty_worktree", result["reasons"])


if __name__ == "__main__":
    unittest.main()
