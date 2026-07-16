#!/usr/bin/env python3
"""Validate the Git-managed editorial Skill release manifest."""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST_PATH = ROOT / "config" / "ai_account_editorial_director_release_manifest.json"
DEFAULT_REPO_SKILL_DIR = ROOT / "skills" / "ai-account-editorial-director"
DEFAULT_GLOBAL_SKILL_DIR = Path.home() / ".codex" / "skills" / "ai-account-editorial-director"
DEFAULT_GIT_ROOT = ROOT
SOURCE_SKILL_PREFIX = "skills/ai-account-editorial-director"
FULL_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")


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


def git_bytes(git_root: Path, args: list[str]) -> tuple[int, bytes, bytes]:
    try:
        result = subprocess.run(
            ["git", "-C", str(git_root), *args],
            check=False,
            capture_output=True,
        )
    except OSError as exc:
        return 127, b"", str(exc).encode("utf-8", errors="replace")
    return result.returncode, result.stdout, result.stderr


def source_release_commit(manifest: dict[str, Any]) -> tuple[str, list[str]]:
    if "source_release_commit" not in manifest:
        return "", ["missing_source_release_commit"]
    raw = manifest.get("source_release_commit")
    if not isinstance(raw, str):
        return "", ["source_release_commit_nonstring"]
    commit = raw.strip()
    if not commit:
        return "", ["missing_source_release_commit"]
    if not FULL_COMMIT_RE.fullmatch(commit):
        return commit, ["invalid_source_release_commit"]
    return commit, []


def verify_source_release_tree(
    git_root: Path,
    commit: str,
    manifest_hashes: dict[str, str],
) -> tuple[list[dict[str, Any]], list[str], bool, int]:
    failures: list[str] = []
    source_results: list[dict[str, Any]] = []
    if not commit or not FULL_COMMIT_RE.fullmatch(commit):
        return source_results, failures, False, 0

    rc, _stdout, stderr = git_bytes(git_root, ["cat-file", "-e", f"{commit}^{{commit}}"])
    if rc == 127:
        failures.append("git_read_error:cat_file_commit")
        return source_results, failures, False, 0
    if rc != 0:
        failures.append("unknown_source_release_commit")
        return source_results, failures, False, 0

    rc, stdout, stderr = git_bytes(git_root, ["ls-tree", "-r", "--name-only", commit, "--", SOURCE_SKILL_PREFIX])
    if rc == 127:
        failures.append("git_read_error:ls_tree_source")
        return source_results, failures, False, 0
    if rc != 0:
        failures.append("source_release_tree_read_failed")
        return source_results, failures, False, 0

    prefix = SOURCE_SKILL_PREFIX + "/"
    source_paths = sorted(
        line.decode("utf-8", errors="replace")[len(prefix):]
        for line in stdout.splitlines()
        if line.decode("utf-8", errors="replace").startswith(prefix)
    )
    manifest_paths = sorted(manifest_hashes)
    if source_paths != manifest_paths:
        failures.append("source_managed_file_set_mismatch")

    source_path_set = set(source_paths)
    for relpath, expected_hash in manifest_hashes.items():
        source_hash = ""
        status = "pass"
        if relpath not in source_path_set:
            failures.append(f"missing_source_file:{relpath}")
            status = "fail"
        else:
            rc, blob, stderr = git_bytes(git_root, ["cat-file", "blob", f"{commit}:{SOURCE_SKILL_PREFIX}/{relpath}"])
            if rc == 127:
                failures.append(f"git_read_error:source_blob:{relpath}")
                status = "fail"
            elif rc != 0:
                failures.append(f"source_blob_read_failed:{relpath}")
                status = "fail"
            else:
                source_hash = hashlib.sha256(blob).hexdigest()
                if source_hash != expected_hash:
                    failures.append(f"source_manifest_hash_mismatch:{relpath}")
                    status = "fail"
        source_results.append({
            "path": relpath,
            "source_blob_sha256": source_hash,
            "status": status,
        })
    return source_results, failures, not failures, len(source_paths)


def verify_skill_release_manifest(
    manifest_path: Path = DEFAULT_MANIFEST_PATH,
    repo_skill_dir: Path = DEFAULT_REPO_SKILL_DIR,
    global_skill_dir: Path = DEFAULT_GLOBAL_SKILL_DIR,
    git_root: Path = DEFAULT_GIT_ROOT,
) -> dict[str, Any]:
    manifest, failures = load_manifest(manifest_path)
    files, file_failures = manifest_files(manifest) if manifest else ({}, [])
    failures.extend(file_failures)
    source_commit, source_failures = source_release_commit(manifest) if manifest else ("", [])
    failures.extend(source_failures)

    if manifest and manifest.get("schema_version") != 1:
        failures.append("unsupported_manifest_schema_version")
    if manifest and manifest.get("skill_name") != "ai-account-editorial-director":
        failures.append("unexpected_skill_name")

    repo_managed_files = discover_repo_managed_files(repo_skill_dir)
    manifest_file_names = sorted(files)
    if repo_managed_files and manifest_file_names and repo_managed_files != manifest_file_names:
        failures.append("repo_managed_file_set_mismatch")

    source_results, source_tree_failures, source_identity_verified, source_managed_file_count = verify_source_release_tree(
        git_root,
        source_commit,
        files,
    ) if manifest and files else ([], [], False, 0)
    failures.extend(source_tree_failures)

    file_results: list[dict[str, Any]] = []
    source_result_by_path = {item["path"]: item for item in source_results}
    for relpath, expected_hash in files.items():
        repo_path = repo_skill_dir / relpath
        global_path = global_skill_dir / relpath
        repo_hash = sha256_file(repo_path) if repo_path.is_file() else ""
        global_hash = sha256_file(global_path) if global_path.is_file() else ""
        source_result = source_result_by_path.get(relpath, {})
        source_hash = str(source_result.get("source_blob_sha256") or "")
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
        if source_result and source_result.get("status") != "pass":
            status = "fail"
        if source_hash and source_hash != expected_hash:
            status = "fail"
        file_results.append({
            "path": relpath,
            "manifest_sha256": expected_hash,
            "repo_sha256": repo_hash,
            "global_sha256": global_hash,
            "source_blob_sha256": source_hash,
            "status": status,
        })

    manifest_verified = not failures and source_identity_verified
    return {
        "ok": manifest_verified,
        "manifest_verified": manifest_verified,
        "source_identity_verified": source_identity_verified,
        "manifest_path": str(manifest_path),
        "repo_skill_dir": str(repo_skill_dir),
        "global_skill_dir": str(global_skill_dir),
        "git_root": str(git_root),
        "schema_version": manifest.get("schema_version") if manifest else None,
        "skill_name": manifest.get("skill_name", "") if manifest else "",
        "source_release_commit": source_commit,
        "managed_file_count": len(files),
        "repo_managed_file_count": len(repo_managed_files),
        "source_managed_file_count": source_managed_file_count,
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
