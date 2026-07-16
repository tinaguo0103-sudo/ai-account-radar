#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import skill_release_manifest as manifest


class SkillReleaseManifestTests(unittest.TestCase):
    def make_skill(self, root: Path, text: str = "skill") -> dict[str, str]:
        files = {
            "SKILL.md": text,
            "agents/openai.yaml": "agent",
            "references/persona-and-cases.md": "cases",
            "references/persona-brief.md": "brief",
        }
        for relpath, body in files.items():
            path = root / relpath
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body, encoding="utf-8")
        return {relpath: manifest.sha256_file(root / relpath) for relpath in files}

    def write_manifest(self, path: Path, hashes: dict[str, str]) -> None:
        path.write_text(json.dumps({
            "schema_version": 1,
            "skill_name": "ai-account-editorial-director",
            "source_release_commit": "test",
            "managed_files": [
                {"path": relpath, "sha256": digest}
                for relpath, digest in sorted(hashes.items())
            ],
        }), encoding="utf-8")

    def test_manifest_repo_global_exact_pass(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            global_skill = root / "global"
            hashes = self.make_skill(repo)
            self.make_skill(global_skill)
            manifest_path = root / "manifest.json"
            self.write_manifest(manifest_path, hashes)
            result = manifest.verify_skill_release_manifest(manifest_path, repo, global_skill)
        self.assertTrue(result["ok"])
        self.assertTrue(result["manifest_verified"])
        self.assertEqual(result["managed_file_count"], 4)

    def test_missing_malformed_unknown_and_hash_drift_fail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            global_skill = root / "global"
            hashes = self.make_skill(repo)
            self.make_skill(global_skill, text="different")
            manifest_path = root / "manifest.json"
            self.write_manifest(manifest_path, {**hashes, "unknown.txt": "0" * 64})
            result = manifest.verify_skill_release_manifest(manifest_path, repo, global_skill)
            missing = manifest.verify_skill_release_manifest(root / "missing.json", repo, global_skill)
            malformed_path = root / "malformed.json"
            malformed_path.write_text("{", encoding="utf-8")
            malformed = manifest.verify_skill_release_manifest(malformed_path, repo, global_skill)
        self.assertFalse(result["ok"])
        self.assertIn("repo_managed_file_set_mismatch", result["failures"])
        self.assertTrue(any("global_manifest_hash_mismatch:SKILL.md" == item for item in result["failures"]))
        self.assertIn("missing_manifest", missing["failures"])
        self.assertIn("malformed_manifest", malformed["failures"])


if __name__ == "__main__":
    unittest.main()
