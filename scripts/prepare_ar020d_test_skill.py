#!/usr/bin/env python3
"""Prepare an isolated AR-020D test Skill directory.

The repo mirror is the Git-managed Skill contract. The private global Skill
holds the full persona/style references. This script combines them into a local
test-only Skill directory so replay can prove that the full style reference is
embedded without modifying the production global Skill or committing private
material.
"""
from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime
from pathlib import Path

import editorial_skill_runner as runner


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DEST = Path("/private/tmp/ai-account-editorial-director-ar020d-test")


def copy_file(src: Path, dest: Path) -> dict[str, str | int]:
    if not src.exists():
        raise FileNotFoundError(f"Missing source file: {src}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    return {
        "source": str(src),
        "dest": str(dest),
        "sha256": runner.file_sha256(dest),
        "bytes": dest.stat().st_size,
    }


def prepare(dest: Path, *, overwrite: bool) -> dict[str, object]:
    if dest.exists() and overwrite:
        shutil.rmtree(dest)
    if dest.exists() and any(dest.iterdir()) and not overwrite:
        raise FileExistsError(f"Destination already exists: {dest}. Pass --overwrite to replace it.")

    repo_skill = runner.REPO_SKILL_DIR / "SKILL.md"
    private_refs = runner.GLOBAL_SKILL_DIR / "references"
    manifest = {
        "ok": True,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "dest": str(dest),
        "purpose": "AR-020D isolated test private Skill path",
        "production_global_skill_modified": False,
        "git_committed_private_material": False,
        "files": {
            "skill_md": copy_file(repo_skill, dest / "SKILL.md"),
            "persona_brief": copy_file(private_refs / "persona-brief.md", dest / "references" / "persona-brief.md"),
            "persona_style": copy_file(private_refs / "persona-and-cases.md", dest / "references" / "persona-and-cases.md"),
        },
        "use_env": f"EDITORIAL_SKILL_DIR={dest}",
    }
    (dest / "ar020d_test_skill_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare isolated AR-020D test Skill directory.")
    parser.add_argument("--dest", default=str(DEFAULT_DEST))
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    manifest = prepare(Path(args.dest).expanduser(), overwrite=args.overwrite)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
