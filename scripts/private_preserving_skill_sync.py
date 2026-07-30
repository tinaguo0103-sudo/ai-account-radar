#!/usr/bin/env python3
"""Install Git-owned Skill files while preserving approved private namespaces."""
from __future__ import annotations

import argparse
import os
import shutil
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable


PRIVATE_NAMESPACES = (Path("references/private"), Path("examples/private"))


@dataclass(frozen=True)
class SyncResult:
    action: str
    managed_files: int
    private_files: int
    backup_created: bool
    installed: bool


def _is_private(relative_path: Path) -> bool:
    return any(
        relative_path == namespace or namespace in relative_path.parents
        for namespace in PRIVATE_NAMESPACES
    )


def _files(root: Path) -> dict[Path, bytes]:
    if not root.exists():
        return {}
    return {
        path.relative_to(root): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _copy_private_files(active: Path, staging: Path) -> int:
    count = 0
    for relative_path, content in _files(active).items():
        if not _is_private(relative_path):
            continue
        destination = staging / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
        shutil.copystat(active / relative_path, destination)
        count += 1
    return count


def _validate_staging(source: Path, active: Path | None, staging: Path) -> tuple[int, int]:
    source_files = _files(source)
    staging_files = _files(staging)
    managed_source = {
        path: content for path, content in source_files.items() if not _is_private(path)
    }
    managed_staging = {
        path: content for path, content in staging_files.items() if not _is_private(path)
    }
    if managed_staging != managed_source:
        raise RuntimeError("managed_staging_mismatch")

    private_active = {
        path: content
        for path, content in _files(active).items()
        if _is_private(path)
    } if active else {}
    for path, content in private_active.items():
        if staging_files.get(path) != content:
            raise RuntimeError("private_staging_mismatch")
    return len(managed_source), len(private_active)


def _remove_tree(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _backup_active(active: Path, backup_root: Path, skill_name: str) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    backup = backup_root / f"{skill_name}.{stamp}.{uuid.uuid4().hex[:8]}"
    backup_root.mkdir(parents=True, exist_ok=True)
    shutil.copytree(active, backup, symlinks=True)
    if _files(backup) != _files(active):
        _remove_tree(backup)
        raise RuntimeError("backup_readback_mismatch")
    return backup


def sync_skill(
    *,
    skill_name: str,
    source: Path,
    active: Path,
    backup_root: Path,
    dry_run: bool,
    replace: Callable[[Path, Path], None] = os.replace,
) -> SyncResult:
    if not source.is_dir():
        raise RuntimeError("source_skill_missing")

    source_files = _files(source)
    managed_count = sum(not _is_private(path) for path in source_files)
    active_files = _files(active)
    private_count = sum(_is_private(path) for path in active_files)
    action = "fresh_install" if not active.exists() else "managed_update"
    if dry_run:
        return SyncResult(action, managed_count, private_count, False, False)

    active.parent.mkdir(parents=True, exist_ok=True)
    operation_id = uuid.uuid4().hex
    staging = active.parent / f".{active.name}.staging.{operation_id}"
    rollback = active.parent / f".{active.name}.rollback.{operation_id}"
    failed = active.parent / f".{active.name}.failed.{operation_id}"
    backup: Path | None = None
    active_moved = False
    installed = False
    try:
        shutil.copytree(source, staging, symlinks=True)
        if active.exists():
            _copy_private_files(active, staging)
        managed_count, private_count = _validate_staging(
            source, active if active.exists() else None, staging
        )

        if active.exists():
            backup = _backup_active(active, backup_root, skill_name)
            replace(active, rollback)
            active_moved = True
        replace(staging, active)
        installed = True
        _fsync_directory(active.parent)
        _validate_staging(source, rollback if active_moved else None, active)
        if active_moved:
            _remove_tree(rollback)
            active_moved = False
    except Exception:
        if active_moved:
            if active.exists():
                replace(active, failed)
            if rollback.exists():
                replace(rollback, active)
                _fsync_directory(active.parent)
            _remove_tree(failed)
        elif installed:
            _remove_tree(active)
            _fsync_directory(active.parent)
        raise
    finally:
        _remove_tree(staging)
        _remove_tree(rollback)
        _remove_tree(failed)

    return SyncResult(action, managed_count, private_count, backup is not None, True)


def add_sync_arguments(
    parser: argparse.ArgumentParser,
    *,
    source: Path,
    active: Path,
    backup_root: Path,
) -> None:
    parser.add_argument(
        "--install-public",
        action="store_true",
        help="Install or update Git-owned files while preserving approved private namespaces.",
    )
    parser.add_argument("--yes", action="store_true", help="Confirm the managed Skill sync.")
    parser.add_argument(
        "--force-overwrite-existing",
        action="store_true",
        help="Deprecated compatibility flag; existing Skills are always updated private-safely.",
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="Deprecated unsafe flag; existing Skills always require a backup.",
    )
    parser.add_argument("--repo-skill-dir", type=Path, default=source)
    parser.add_argument("--global-skill-dir", type=Path, default=active)
    parser.add_argument("--backup-root", type=Path, default=backup_root)


def run_cli(
    *,
    skill_name: str,
    source: Path,
    active: Path,
    backup_root: Path,
    argv: list[str] | None = None,
) -> int:
    parser = argparse.ArgumentParser()
    add_sync_arguments(
        parser, source=source, active=active, backup_root=backup_root
    )
    args = parser.parse_args(argv)
    if args.no_backup:
        parser.error("no_backup_not_supported")
    if args.install_public and not args.yes:
        parser.error("confirmation_required")

    result = sync_skill(
        skill_name=skill_name,
        source=args.repo_skill_dir,
        active=args.global_skill_dir,
        backup_root=args.backup_root,
        dry_run=not args.install_public,
    )
    print(f"action={result.action}")
    print(f"managed_files={result.managed_files}")
    print(f"private_files={result.private_files}")
    print(f"backup={'created' if result.backup_created else 'not_created'}")
    print(f"installed={'true' if result.installed else 'false'}")
    print(f"dry_run={'false' if result.installed else 'true'}")
    return 0
