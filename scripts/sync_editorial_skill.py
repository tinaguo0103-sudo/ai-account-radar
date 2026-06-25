#!/usr/bin/env python3
"""Install the public-safe repository copy of ai-account-editorial-director.

The production workflow can use a richer private global Skill in ~/.codex.
This script exists for portability: it copies the sanitized repo version into
the global Codex skills folder when explicitly requested.
It refuses to overwrite an existing global Skill unless explicitly forced.
"""
from __future__ import annotations

import argparse
import shutil
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPO_SKILL_DIR = ROOT / "skills" / "ai-account-editorial-director"
GLOBAL_SKILL_DIR = Path.home() / ".codex" / "skills" / "ai-account-editorial-director"
BACKUP_ROOT = Path.home() / ".codex" / "skills" / ".backups"


def copy_tree(src: Path, dst: Path) -> None:
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--install-public", action="store_true", help="Copy the sanitized repo Skill into ~/.codex/skills.")
    parser.add_argument("--yes", action="store_true", help="Confirm replacing the global Skill copy.")
    parser.add_argument("--no-backup", action="store_true", help="Do not back up the current global Skill before replacement.")
    parser.add_argument("--force-overwrite-existing", action="store_true", help="Explicitly allow replacing an existing global private Skill.")
    args = parser.parse_args()

    if not REPO_SKILL_DIR.exists():
        raise SystemExit(f"Missing repo Skill: {REPO_SKILL_DIR}")

    if not args.install_public:
        print(f"repo_skill={REPO_SKILL_DIR}")
        print(f"global_skill={GLOBAL_SKILL_DIR}")
        print("dry_run=true")
        print("Use --install-public --yes only for first-time bootstrap.")
        print("If a global Skill already exists, add --force-overwrite-existing only when you intentionally want to replace it.")
        return 0

    if not args.yes:
        raise SystemExit(
            "Refusing to replace the global Skill without --yes. "
            "This installs the public-safe repo copy and may overwrite a richer private local Skill."
        )
    if GLOBAL_SKILL_DIR.exists() and not args.force_overwrite_existing:
        raise SystemExit(
            f"Refusing to overwrite existing global Skill: {GLOBAL_SKILL_DIR}. "
            "Production uses the global private Skill. The repo copy is only for sync/bootstrap/testing. "
            "Pass --force-overwrite-existing only when you intentionally want to replace the global copy."
        )

    if GLOBAL_SKILL_DIR.exists() and not args.no_backup:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = BACKUP_ROOT / f"ai-account-editorial-director.{stamp}"
        BACKUP_ROOT.mkdir(parents=True, exist_ok=True)
        copy_tree(GLOBAL_SKILL_DIR, backup)
        print(f"backup={backup}")

    GLOBAL_SKILL_DIR.parent.mkdir(parents=True, exist_ok=True)
    copy_tree(REPO_SKILL_DIR, GLOBAL_SKILL_DIR)
    print(f"installed={GLOBAL_SKILL_DIR}")
    print("source=public-safe repo copy")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
