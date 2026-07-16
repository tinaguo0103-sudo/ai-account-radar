#!/usr/bin/env python3
"""Validate the Git-managed editorial Skill release manifest."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST_PATH = ROOT / "config" / "ai_account_editorial_director_release_manifest.json"
DEFAULT_REPO_SKILL_DIR = ROOT / "skills" / "ai-account-editorial-director"
DEFAULT_GLOBAL_SKILL_DIR = Path.home() / ".codex" / "skills" / "ai-account-editorial-director"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def discover_repo_managed_files(skill_dir: Path) -> list[str]:
    if not skill_dir.exists():
        return []
    return sorted(
        str(path.relative_to(skill_dir))
        for path in skill_dir.rglob("*")
        if path.is_file()
    )


def load_manifest(path: Path = DEFAULT_MANIFEST_PATH) -> tuple[dict[str, Any], list[str]]:
    if not path.is_file():
        return {}, ["missing_manifest"]
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}, ["malformed_manifest"]
    if not isinstance(payload, dict):
        return {}, ["malformed_manifest"]
    return payload, []


def manifest_files(manifest: dict[str, Any]) -> tuple[dict[str, str], list[str]]:
    failures: list[str] = []
    files = manifest.get("managed_files")
    if not isinstance(files, list) or not files:
        return {}, ["manifest_missing_managed_files"]
    output: dict[str, str] = {}
    for index, item in enumerate(files):
        if not isinstance(item, dict):
            failures.append(f"managed_file_{index}_malformed")
            continue
        relpath = str(item.get("path") or "").strip()
        expected_hash = str(item.get("sha256") or "").strip()
        if not relpath or relpath.startswith("/") or ".." in Path(relpath).parts:
            failures.append(f"managed_file_{index}_unsafe_path")
            continue
        if len(expected_hash) != 64 or any(ch not in "0123456789abcdef" for ch in expected_hash):
            failures.append(f"managed_file_{relpath}_bad_hash")
            continue
        output[relpath] = expected_hash
    return output, failures


def verify_skill_release_manifest(
    manifest_path: Path = DEFAULT_MANIFEST_PATH,
    repo_skill_dir: Path = DEFAULT_REPO_SKILL_DIR,
    global_skill_dir: Path = DEFAULT_GLOBAL_SKILL_DIR,
) -> dict[str, Any]:
    manifest, failures = load_manifest(manifest_path)
    files, file_failures = manifest_files(manifest) if manifest else ({}, [])
    failures.extend(file_failures)

    if manifest and manifest.get("schema_version") != 1:
        failures.append("unsupported_manifest_schema_version")
    if manifest and manifest.get("skill_name") != "ai-account-editorial-director":
        failures.append("unexpected_skill_name")

    repo_managed_files = discover_repo_managed_files(repo_skill_dir)
    manifest_file_names = sorted(files)
    if repo_managed_files and manifest_file_names and repo_managed_files != manifest_file_names:
        failures.append("repo_managed_file_set_mismatch")

    file_results: list[dict[str, Any]] = []
    for relpath, expected_hash in files.items():
        repo_path = repo_skill_dir / relpath
        global_path = global_skill_dir / relpath
        repo_hash = sha256_file(repo_path) if repo_path.is_file() else ""
        global_hash = sha256_file(global_path) if global_path.is_file() else ""
        status = "pass"
        if not repo_path.is_file():
            failures.append(f"missing_repo_file:{relpath}")
            status = "fail"
        if not global_path.is_file():
            failures.append(f"missing_global_file:{relpath}")
            status = "fail"
        if repo_hash and repo_hash != expected_hash:
            failures.append(f"repo_manifest_hash_mismatch:{relpath}")
            status = "fail"
        if global_hash and global_hash != expected_hash:
            failures.append(f"global_manifest_hash_mismatch:{relpath}")
            status = "fail"
        if repo_hash and global_hash and repo_hash != global_hash:
            failures.append(f"repo_global_hash_mismatch:{relpath}")
            status = "fail"
        file_results.append({
            "path": relpath,
            "manifest_sha256": expected_hash,
            "repo_sha256": repo_hash,
            "global_sha256": global_hash,
            "status": status,
        })

    return {
        "ok": not failures,
        "manifest_verified": not failures,
        "manifest_path": str(manifest_path),
        "repo_skill_dir": str(repo_skill_dir),
        "global_skill_dir": str(global_skill_dir),
        "schema_version": manifest.get("schema_version") if manifest else None,
        "skill_name": manifest.get("skill_name", "") if manifest else "",
        "source_release_commit": manifest.get("source_release_commit", "") if manifest else "",
        "managed_file_count": len(files),
        "repo_managed_file_count": len(repo_managed_files),
        "files": file_results,
        "failures": failures,
        "writes_feishu": False,
        "syncs_global_skill": False,
        "contains_private_persona": False,
    }


def main() -> int:
    result = verify_skill_release_manifest()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
