#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import skill_release_manifest as manifest


class SkillReleaseManifestTests(unittest.TestCase):
    def git(self, root: Path, *args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(root), *args],
            check=True,
            text=True,
            capture_output=True,
        ).stdout.strip()

    def init_git(self, root: Path) -> None:
        subprocess.run(["git", "init", str(root)], check=True, capture_output=True)
        self.git(root, "checkout", "-b", "main")
        self.git(root, "config", "user.email", "fixture@example.invalid")
        self.git(root, "config", "user.name", "Fixture")

    def commit_all(self, root: Path, message: str) -> str:
        self.git(root, "add", ".")
        self.git(root, "commit", "-m", message)
        return self.git(root, "rev-parse", "HEAD")

    def make_skill(self, root: Path, text: str = "skill", extra_files: dict[str, str] | None = None) -> dict[str, str]:
        files = {
            "SKILL.md": text,
            "agents/openai.yaml": "agent",
            "references/persona-and-cases.md": "cases",
            "references/persona-brief.md": "brief",
        }
        if extra_files:
            files.update(extra_files)
        for relpath, body in files.items():
            path = root / relpath
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body, encoding="utf-8")
        return {relpath: manifest.sha256_file(root / relpath) for relpath in files}

    def write_manifest(self, path: Path, hashes: dict[str, str], source_commit: object) -> None:
        payload = {
            "schema_version": 1,
            "skill_name": "ai-account-editorial-director",
            "source_release_commit": source_commit,
            "managed_files": [
                {"path": relpath, "sha256": digest}
                for relpath, digest in sorted(hashes.items())
            ],
        }
        path.write_text(json.dumps(payload), encoding="utf-8")

    def prepare_exact_fixture(self, root: Path) -> tuple[Path, Path, Path, dict[str, str], str]:
        git_root = root / "repo"
        self.init_git(git_root)
        repo_skill = git_root / "skills" / "ai-account-editorial-director"
        hashes = self.make_skill(repo_skill)
        commit = self.commit_all(git_root, "skill release")
        global_skill = root / "global"
        self.make_skill(global_skill)
        manifest_path = root / "manifest.json"
        self.write_manifest(manifest_path, hashes, commit)
        return git_root, repo_skill, global_skill, hashes, commit

    def verify(self, manifest_path: Path, repo_skill: Path, global_skill: Path, git_root: Path) -> dict:
        return manifest.verify_skill_release_manifest(manifest_path, repo_skill, global_skill, git_root)

    def test_manifest_repo_global_source_exact_pass(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            git_root, repo_skill, global_skill, hashes, commit = self.prepare_exact_fixture(Path(tmp))
            result = self.verify(Path(tmp) / "manifest.json", repo_skill, global_skill, git_root)
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["manifest_verified"])
        self.assertTrue(result["source_identity_verified"])
        self.assertEqual(result["source_release_commit"], commit)
        self.assertEqual(result["managed_file_count"], 4)
        self.assertEqual(result["source_managed_file_count"], 4)
        self.assertTrue(all(row["source_blob_sha256"] == row["manifest_sha256"] for row in result["files"]))

    def test_missing_malformed_unknown_and_hash_drift_fail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            git_root, repo_skill, global_skill, hashes, commit = self.prepare_exact_fixture(root)
            manifest_path = root / "manifest.json"
            self.write_manifest(manifest_path, {**hashes, "unknown.txt": "0" * 64}, commit)
            result = self.verify(manifest_path, repo_skill, global_skill, git_root)
            missing = self.verify(root / "missing.json", repo_skill, global_skill, git_root)
            malformed_path = root / "malformed.json"
            malformed_path.write_text("{", encoding="utf-8")
            malformed = self.verify(malformed_path, repo_skill, global_skill, git_root)
        self.assertFalse(result["ok"])
        self.assertIn("repo_managed_file_set_mismatch", result["failures"])
        self.assertIn("source_managed_file_set_mismatch", result["failures"])
        self.assertIn("missing_source_file:unknown.txt", result["failures"])
        self.assertIn("missing_manifest", missing["failures"])
        self.assertIn("malformed_manifest", malformed["failures"])

    def test_invalid_source_release_commit_shapes_fail(self) -> None:
        cases = [
            (None, "missing_source_release_commit"),
            (123, "source_release_commit_nonstring"),
            ("abc123", "invalid_source_release_commit"),
            ("g" * 40, "invalid_source_release_commit"),
        ]
        for source_commit, expected_failure in cases:
            with self.subTest(source_commit=source_commit), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                git_root, repo_skill, global_skill, hashes, _commit = self.prepare_exact_fixture(root)
                manifest_path = root / "manifest.json"
                payload = {
                    "schema_version": 1,
                    "skill_name": "ai-account-editorial-director",
                    "managed_files": [
                        {"path": relpath, "sha256": digest}
                        for relpath, digest in sorted(hashes.items())
                    ],
                }
                if source_commit is not None:
                    payload["source_release_commit"] = source_commit
                manifest_path.write_text(json.dumps(payload), encoding="utf-8")
                result = self.verify(manifest_path, repo_skill, global_skill, git_root)
                self.assertFalse(result["ok"])
                self.assertFalse(result["source_identity_verified"])
                self.assertIn(expected_failure, result["failures"])

    def test_arbitrary_valid_unknown_source_identity_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            git_root, repo_skill, global_skill, hashes, _commit = self.prepare_exact_fixture(root)
            manifest_path = root / "manifest.json"
            self.write_manifest(manifest_path, hashes, "0" * 40)
            result = self.verify(manifest_path, repo_skill, global_skill, git_root)
        self.assertFalse(result["ok"])
        self.assertFalse(result["source_identity_verified"])
        self.assertIn("unknown_source_release_commit", result["failures"])

    def test_known_commit_before_skill_exists_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            git_root = root / "repo"
            self.init_git(git_root)
            (git_root / "README.md").write_text("before skill\n", encoding="utf-8")
            before_skill = self.commit_all(git_root, "before skill")
            repo_skill = git_root / "skills" / "ai-account-editorial-director"
            hashes = self.make_skill(repo_skill)
            self.commit_all(git_root, "add skill")
            global_skill = root / "global"
            self.make_skill(global_skill)
            manifest_path = root / "manifest.json"
            self.write_manifest(manifest_path, hashes, before_skill)
            result = self.verify(manifest_path, repo_skill, global_skill, git_root)
        self.assertFalse(result["ok"])
        self.assertIn("source_managed_file_set_mismatch", result["failures"])
        self.assertIn("missing_source_file:SKILL.md", result["failures"])

    def test_known_commit_with_changed_blob_fails_even_when_repo_and_global_match_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            git_root, repo_skill, global_skill, hashes, first_commit = self.prepare_exact_fixture(root)
            (repo_skill / "SKILL.md").write_text("changed in source commit", encoding="utf-8")
            changed_commit = self.commit_all(git_root, "change skill")
            self.git(git_root, "checkout", first_commit, "--", "skills/ai-account-editorial-director/SKILL.md")
            manifest_path = root / "manifest.json"
            self.write_manifest(manifest_path, hashes, changed_commit)
            result = self.verify(manifest_path, repo_skill, global_skill, git_root)
        self.assertFalse(result["ok"])
        self.assertIn("source_manifest_hash_mismatch:SKILL.md", result["failures"])
        self.assertFalse(result["source_identity_verified"])

    def test_source_path_missing_fails_when_current_repo_and_global_include_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            git_root, repo_skill, global_skill, hashes, first_commit = self.prepare_exact_fixture(root)
            extra = {"references/new-release-note.md": "new managed file"}
            hashes = self.make_skill(repo_skill, extra_files=extra)
            self.make_skill(global_skill, extra_files=extra)
            manifest_path = root / "manifest.json"
            self.write_manifest(manifest_path, hashes, first_commit)
            result = self.verify(manifest_path, repo_skill, global_skill, git_root)
        self.assertFalse(result["ok"])
        self.assertIn("source_managed_file_set_mismatch", result["failures"])
        self.assertIn("missing_source_file:references/new-release-note.md", result["failures"])

    def test_source_commit_extra_managed_path_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            git_root, repo_skill, global_skill, hashes, _first_commit = self.prepare_exact_fixture(root)
            extra_path = repo_skill / "references" / "extra-source-only.md"
            extra_path.write_text("source-only managed file", encoding="utf-8")
            extra_commit = self.commit_all(git_root, "add source extra")
            extra_path.unlink()
            self.git(git_root, "add", str(extra_path.relative_to(git_root)))
            self.git(git_root, "commit", "-m", "remove source extra")
            manifest_path = root / "manifest.json"
            self.write_manifest(manifest_path, hashes, extra_commit)
            result = self.verify(manifest_path, repo_skill, global_skill, git_root)
        self.assertFalse(result["ok"])
        self.assertIn("source_managed_file_set_mismatch", result["failures"])

    def test_git_read_error_fails_closed_before_manifest_verified(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            git_root, repo_skill, global_skill, hashes, commit = self.prepare_exact_fixture(root)
            manifest_path = root / "manifest.json"
            self.write_manifest(manifest_path, hashes, commit)
            with mock.patch.object(manifest, "git_bytes", return_value=(127, b"", b"permission denied")):
                result = self.verify(manifest_path, repo_skill, global_skill, git_root)
        self.assertFalse(result["ok"])
        self.assertFalse(result["source_identity_verified"])
        self.assertIn("git_read_error:cat_file_commit", result["failures"])


if __name__ == "__main__":
    unittest.main()
