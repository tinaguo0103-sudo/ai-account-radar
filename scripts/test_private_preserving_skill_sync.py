from __future__ import annotations

import contextlib
import io
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import private_preserving_skill_sync as sync
import sync_austin_scripting_skill as scripting_cli
import sync_austin_voice_scriptwriter_skill as voice_cli


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = (
    ROOT / "scripts" / "sync_austin_scripting_skill.py",
    ROOT / "scripts" / "sync_austin_voice_scriptwriter_skill.py",
)


def write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def snapshot(root: Path) -> dict[str, bytes]:
    if not root.exists():
        return {}
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


class PrivatePreservingSkillSyncTests(unittest.TestCase):
    def run_cli(
        self,
        script: Path,
        source: Path,
        active: Path,
        backups: Path,
        *extra: str,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(script),
                "--repo-skill-dir",
                str(source),
                "--global-skill-dir",
                str(active),
                "--backup-root",
                str(backups),
                *extra,
            ],
            check=False,
            capture_output=True,
            text=True,
        )

    def fixture(self, root: Path) -> tuple[Path, Path, Path]:
        source = root / "repo" / "skill"
        active = root / "global" / "skill"
        backups = root / "backups"
        write(source / "SKILL.md", b"managed-new")
        write(source / "references" / "public" / "nested.md", b"nested-new")
        write(
            source / "references" / "private" / "persona.md",
            b"repo-private-placeholder",
        )
        write(active / "SKILL.md", b"managed-old")
        write(active / "stale-managed.md", b"stale")
        write(active / "references" / "private" / "persona.md", b"private-persona")
        write(active / "examples" / "private" / "case.json", b"private-case")
        return source, active, backups

    def test_both_formal_clis_fresh_install_and_dry_run(self):
        for script in SCRIPTS:
            with self.subTest(script=script.name), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                source = root / "repo" / "skill"
                active = root / "global" / "skill"
                backups = root / "backups"
                write(source / "SKILL.md", b"managed")
                write(source / "references" / "public" / "nested.md", b"nested")
                before = snapshot(root)
                dry_run = self.run_cli(script, source, active, backups)
                self.assertEqual(dry_run.returncode, 0, dry_run.stderr)
                self.assertEqual(snapshot(root), before)
                self.assertIn("action=fresh_install", dry_run.stdout)
                self.assertIn("installed=false", dry_run.stdout)

                install = self.run_cli(
                    script, source, active, backups, "--install-public", "--yes"
                )
                self.assertEqual(install.returncode, 0, install.stderr)
                self.assertEqual(snapshot(active), snapshot(source))
                self.assertIn("action=fresh_install", install.stdout)
                self.assertIn("private_files=0", install.stdout)
                self.assertNotIn(str(root), install.stdout)

    def test_both_formal_clis_update_managed_preserve_private_and_clean_stale(self):
        for script in SCRIPTS:
            with self.subTest(script=script.name), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                source, active, backups = self.fixture(root)
                private_before = {
                    key: value
                    for key, value in snapshot(active).items()
                    if "/private/" in key
                }
                result = self.run_cli(
                    script,
                    source,
                    active,
                    backups,
                    "--install-public",
                    "--yes",
                    "--force-overwrite-existing",
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                installed = snapshot(active)
                self.assertEqual(installed["SKILL.md"], b"managed-new")
                self.assertEqual(
                    installed["references/public/nested.md"], b"nested-new"
                )
                self.assertNotIn("stale-managed.md", installed)
                self.assertEqual(
                    {
                        key: value
                        for key, value in installed.items()
                        if "/private/" in key
                    },
                    private_before,
                )
                backup_dirs = [path for path in backups.iterdir() if path.is_dir()]
                self.assertEqual(len(backup_dirs), 1)
                self.assertEqual(
                    snapshot(backup_dirs[0])["stale-managed.md"], b"stale"
                )
                self.assertIn("backup=created", result.stdout)
                self.assertNotIn("persona.md", result.stdout)
                self.assertNotIn("case.json", result.stdout)
                self.assertNotIn(str(root), result.stdout)
                leftovers = [
                    path.name
                    for path in active.parent.iterdir()
                    if path.name.startswith(f".{active.name}.")
                ]
                self.assertEqual(leftovers, [])

    def test_zero_private_files_update_normally(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            active = root / "active"
            backups = root / "backups"
            write(source / "SKILL.md", b"new")
            write(active / "SKILL.md", b"old")
            result = sync.sync_skill(
                skill_name="fixture",
                source=source,
                active=active,
                backup_root=backups,
                dry_run=False,
            )
            self.assertEqual(result.private_files, 0)
            self.assertEqual(snapshot(active), snapshot(source))

    def test_both_formal_clis_replace_failure_rolls_back_original(self):
        cli_modules = (
            ("austin-no-overtime-scripting", scripting_cli),
            ("austin-voice-scriptwriter", voice_cli),
        )
        for skill_name, cli_module in cli_modules:
            with self.subTest(skill_name=skill_name), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                source, active, backups = self.fixture(root)
                original = snapshot(active)
                calls = 0

                def fail_second_replace(src: Path, dst: Path) -> None:
                    nonlocal calls
                    calls += 1
                    if calls == 2:
                        raise OSError("injected_replace_failure")
                    Path(src).replace(dst)

                original_default = sync.sync_skill.__kwdefaults__["replace"]
                sync.sync_skill.__kwdefaults__["replace"] = fail_second_replace
                try:
                    with self.assertRaisesRegex(OSError, "injected_replace_failure"):
                        cli_module.main(
                            [
                                "--repo-skill-dir",
                                str(source),
                                "--global-skill-dir",
                                str(active),
                                "--backup-root",
                                str(backups),
                                "--install-public",
                                "--yes",
                            ]
                        )
                finally:
                    sync.sync_skill.__kwdefaults__["replace"] = original_default
                self.assertEqual(snapshot(active), original)
                self.assertEqual(len([p for p in backups.iterdir() if p.is_dir()]), 1)
                self.assertEqual(
                    [
                        p.name
                        for p in active.parent.iterdir()
                        if p.name.startswith(f".{active.name}.")
                    ],
                    [],
                )

    def test_backup_is_complete_and_can_restore_original(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, active, backups = self.fixture(root)
            original = snapshot(active)
            sync.sync_skill(
                skill_name="fixture",
                source=source,
                active=active,
                backup_root=backups,
                dry_run=False,
            )
            backup = next(path for path in backups.iterdir() if path.is_dir())
            recovered = root / "recovered"
            shutil.copytree(backup, recovered)
            self.assertEqual(snapshot(recovered), original)

    def test_no_backup_flag_fails_safe_without_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, active, backups = self.fixture(root)
            before = snapshot(root)
            result = self.run_cli(
                SCRIPTS[0],
                source,
                active,
                backups,
                "--install-public",
                "--yes",
                "--no-backup",
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("no_backup_not_supported", result.stderr)
            self.assertEqual(snapshot(root), before)

    def test_cli_output_is_private_safe(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, active, backups = self.fixture(root)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                sync.run_cli(
                    skill_name="fixture",
                    source=source,
                    active=active,
                    backup_root=backups,
                    argv=[
                        "--repo-skill-dir",
                        str(source),
                        "--global-skill-dir",
                        str(active),
                        "--backup-root",
                        str(backups),
                        "--install-public",
                        "--yes",
                    ],
                )
            text = output.getvalue()
            self.assertNotIn(str(root), text)
            self.assertNotIn("persona.md", text)
            self.assertNotIn("private-persona", text)
            self.assertIn("private_files=2", text)


if __name__ == "__main__":
    unittest.main()
