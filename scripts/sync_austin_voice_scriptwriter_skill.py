#!/usr/bin/env python3
"""Private-preserving installer for the Austin voice Skill."""
from __future__ import annotations

from pathlib import Path

from private_preserving_skill_sync import run_cli


ROOT = Path(__file__).resolve().parents[1]
REPO_SKILL_DIR = ROOT / "skills" / "austin-voice-scriptwriter"
GLOBAL_SKILL_DIR = Path.home() / ".codex" / "skills" / "austin-voice-scriptwriter"
BACKUP_ROOT = Path.home() / ".codex" / "skills" / ".backups"


def main(argv: list[str] | None = None) -> int:
    return run_cli(
        skill_name="austin-voice-scriptwriter",
        source=REPO_SKILL_DIR,
        active=GLOBAL_SKILL_DIR,
        backup_root=BACKUP_ROOT,
        argv=argv,
    )


if __name__ == "__main__":
    raise SystemExit(main())
