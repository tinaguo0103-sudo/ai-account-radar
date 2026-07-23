#!/usr/bin/env python3
"""Receipt-capable refresh adapter for the single fixed local WeWe RSS provider."""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import socket
import sqlite3
import stat
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Callable

PROVIDER_URL = "http://127.0.0.1:4000"
ROOT = Path(__file__).resolve().parents[1]
CANONICAL_DATA_DIR = Path.home() / ".codex" / "ai-account-radar-runtime" / "providers" / "wewe-rss" / "data"
LEGACY_HEALTH_DIR = CANONICAL_DATA_DIR.parent / "health"
PROJECT_REFRESH_STATE_DIR = ROOT / "output" / "state" / "wewe-refresh"
HEALTH_DIR = PROJECT_REFRESH_STATE_DIR
LEGACY_REFRESH_LOCK_PATH = LEGACY_HEALTH_DIR / "refresh.lock"
PROJECT_REFRESH_LOCK_PATH = PROJECT_REFRESH_STATE_DIR / "refresh.lock"
RECEIPT_DIR = HEALTH_DIR / "receipts"
ATTEMPT_DIR = HEALTH_DIR / "attempts"
ATTESTATION_KEY_PATH = CANONICAL_DATA_DIR.parent / "secrets" / "wewe-refresh-attestation.key"
LEASE_TTL_MS = 10 * 60 * 1000
RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
ATTEMPT_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")


class RefreshError(RuntimeError):
    pass


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def load_attestation_key(path: Path = ATTESTATION_KEY_PATH) -> bytes:
    path = Path(path).expanduser()
    parent = path.parent
    if not path.is_absolute() or parent.is_symlink() or parent.resolve() != parent:
        raise RefreshError("refresh_attestation_parent_unsafe")
    directory_fd = -1
    key_fd = -1
    try:
        directory_fd = os.open(parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0))
        parent_info = os.fstat(directory_fd)
        if not stat.S_ISDIR(parent_info.st_mode) or parent_info.st_uid != os.getuid() or parent_info.st_mode & 0o022:
            raise RefreshError("refresh_attestation_parent_unsafe")
        key_fd = os.open(path.name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=directory_fd)
        info = os.fstat(key_fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise RefreshError("refresh_attestation_key_unsafe")
        chunks = []
        while True:
            chunk = os.read(key_fd, 4096)
            if not chunk:
                break
            chunks.append(chunk)
        key = b"".join(chunks)
    except OSError as exc:
        raise RefreshError("refresh_attestation_key_unavailable") from exc
    finally:
        if key_fd >= 0:
            os.close(key_fd)
        if directory_fd >= 0:
            os.close(directory_fd)
    if len(key) < 32:
        raise RefreshError("refresh_attestation_key_invalid")
    return key


def attestation_key_id(key: bytes) -> str:
    return sha256_bytes(key)[:16]


def sign_payload(payload: dict[str, Any], key: bytes) -> str:
    unsigned = {name: value for name, value in payload.items() if name != "attestation_signature"}
    return hmac.new(key, canonical_json(unsigned), hashlib.sha256).hexdigest()


def seal_payload(payload: dict[str, Any], key: bytes) -> dict[str, Any]:
    sealed = dict(payload)
    sealed["attestation_key_id"] = attestation_key_id(key)
    sealed["attestation_signature"] = sign_payload(sealed, key)
    return sealed


def database_identity(database: Path) -> dict[str, Any]:
    resolved = database.resolve(strict=True)
    stat = resolved.stat()
    return {"path": str(resolved), "device": stat.st_dev, "inode": stat.st_ino}


def read_snapshot(database: Path, *, busy_timeout_ms: int = 500) -> dict[str, Any]:
    identity = database_identity(database)
    try:
        connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True, timeout=busy_timeout_ms / 1000)
        connection.execute(f"pragma busy_timeout={busy_timeout_ms}")
        accounts = int(connection.execute("select count(*) from accounts where status=1").fetchone()[0])
        feed_rows = connection.execute("select id, sync_time, updated_at from feeds where status=1 order by id").fetchall()
        if len({str(row[0]) for row in feed_rows}) != len(feed_rows):
            raise RefreshError("duplicate_active_feed_id")
        feeds = []
        for feed_id, sync_time, updated_at in feed_rows:
            count, latest = connection.execute(
                "select count(*), coalesce(max(publish_time),0) from articles where mp_id=?", (feed_id,)
            ).fetchone()
            feeds.append({"feed_id": str(feed_id), "sync_time": int(sync_time or 0), "updated_at_ms": int(updated_at or 0), "article_count": int(count), "max_publish_time": int(latest or 0)})
        connection.close()
    except sqlite3.OperationalError as exc:
        raise RefreshError("sqlite_busy_or_unreadable") from exc
    if accounts < 1:
        raise RefreshError("login_required")
    if not feeds:
        raise RefreshError("active_feed_set_empty")
    payload = {"database_identity": identity, "active_account_count": accounts, "feeds": feeds}
    payload["snapshot_sha256"] = sha256_bytes(canonical_json(payload))
    return payload


def owner_alive(pid: int, host: str) -> bool:
    if host != socket.gethostname() or pid <= 0:
        return True
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def project_owned_lock_path(path: Path, project_root: Path | None = None) -> Path:
    root = (project_root or ROOT).resolve()
    resolved = path.expanduser().resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise RefreshError("refresh_lock_not_project_owned") from exc
    if resolved == LEGACY_REFRESH_LOCK_PATH.resolve():
        raise RefreshError("refresh_lock_legacy_runtime_path_forbidden")
    return resolved


def atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    temporary.write_bytes(canonical_json(payload) + b"\n")
    os.replace(temporary, path)


def exclusive_write(path: Path, payload: dict[str, Any]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = canonical_json(payload) + b"\n"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(raw)
        handle.flush(); os.fsync(handle.fileno())
    return sha256_bytes(raw)


def acquire_lease(path: Path, payload: dict[str, Any], now_ms: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError):
            raise RefreshError("refresh_lease_malformed")
        if int(existing.get("expires_at_ms") or 0) >= now_ms or owner_alive(int(existing.get("pid") or 0), str(existing.get("host") or "")):
            raise RefreshError("refresh_in_progress")
        recovery = path.parent / "lease_recovery.jsonl"
        with recovery.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"recovered_at_ms": now_ms, "stale_attempt_id": existing.get("attempt_id"), "stale_pid": existing.get("pid")}, ensure_ascii=False) + "\n")
        stale = path.with_suffix(f".stale.{now_ms}")
        os.replace(path, stale)
        return acquire_lease(path, payload, now_ms)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        handle.flush(); os.fsync(handle.fileno())


def release_lease(path: Path, attempt_id: str) -> None:
    try:
        current = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if str(current.get("attempt_id") or "") != attempt_id:
        raise RefreshError("lease_owner_mismatch")
    path.unlink()


def default_request(feed_id: str, timeout: float) -> int:
    auth_code = os.environ.get("WEWE_RSS_AUTH_CODE", "")
    if not auth_code:
        raise RefreshError("provider_auth_code_missing")
    url = f"{PROVIDER_URL}/trpc/feed.refreshArticles"
    body = canonical_json({"json": {"mpId": feed_id}})
    request = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json", "Authorization": auth_code}, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
        if isinstance(payload, dict) and payload.get("error"):
            raise RefreshError("provider_refresh_error")
        return int(response.status)


def validate_completion(before: dict[str, Any], after: dict[str, Any], request_started_ms: int) -> tuple[bool, list[dict[str, Any]], int]:
    if after["database_identity"] != before["database_identity"]:
        raise RefreshError("database_identity_drift")
    if after["active_account_count"] < 1:
        raise RefreshError("login_required")
    before_rows = {row["feed_id"]: row for row in before["feeds"]}
    after_rows = {row["feed_id"]: row for row in after["feeds"]}
    if list(before_rows) != list(after_rows):
        raise RefreshError("active_feed_set_drift")
    statuses = []
    total_new = 0
    complete = True
    for feed_id, old in before_rows.items():
        new = after_rows[feed_id]
        advanced = new["sync_time"] > old["sync_time"] and new["sync_time"] >= request_started_ms // 1000
        if new["article_count"] < old["article_count"] or new["max_publish_time"] < old["max_publish_time"]:
            raise RefreshError("article_aggregate_rollback")
        added = new["article_count"] - old["article_count"]
        total_new += added
        complete = complete and advanced
        statuses.append({"feed_id": feed_id, "before_sync_time": old["sync_time"], "after_sync_time": new["sync_time"], "completion_advanced": advanced, "new_item_count": added})
    return complete, statuses, total_new


def run_refresh(
    run_id: str, run_started_at_ms: int, *, data_dir: Path = CANONICAL_DATA_DIR,
    health_dir: Path = HEALTH_DIR, request_fn: Callable[[str, float], int] = default_request,
    snapshot_fn: Callable[[Path], dict[str, Any]] = read_snapshot,
    clock_ms: Callable[[], int] = lambda: int(time.time() * 1000),
    sleep_fn: Callable[[float], None] = time.sleep, deadline_ms: int = 120000, poll_interval_ms: int = 500,
    signing_key: bytes | None = None, lock_path: Path | None = None, project_root: Path | None = None,
) -> dict[str, Any]:
    if not RUN_ID_PATTERN.fullmatch(run_id):
        raise RefreshError("refresh_run_id_invalid")
    database = data_dir.resolve() / "wewe-rss.db"
    key = signing_key if signing_key is not None else load_attestation_key()
    if len(key) < 32:
        raise RefreshError("refresh_attestation_key_invalid")
    attempt_id = uuid.uuid4().hex
    started = clock_ms()
    lease_path = project_owned_lock_path(lock_path or PROJECT_REFRESH_LOCK_PATH, project_root)
    lease = seal_payload({"schema_version": 1, "attempt_id": attempt_id, "run_id": run_id, "pid": os.getpid(), "host": socket.gethostname(), "started_at_ms": started, "expires_at_ms": started + LEASE_TTL_MS, "provider_url": PROVIDER_URL, "data_identity": database_identity(database)}, key)
    acquire_lease(lease_path, lease, started)
    try:
        lease_record_path = health_dir / "leases" / f"{run_id}_{attempt_id}.json"
        lease_hash = exclusive_write(lease_record_path, lease)
        before = snapshot_fn(database)
        requested_at = clock_ms()
        requested_ids = [row["feed_id"] for row in before["feeds"]]
        lineage = seal_payload({
            "schema_version": 1, "attempt_id": attempt_id, "run_id": run_id,
            "provider_url": PROVIDER_URL, "database_identity": before["database_identity"],
            "feed_ids": requested_ids, "before_snapshot_sha256": before["snapshot_sha256"],
            "lease_sha256": lease_hash, "pid": os.getpid(),
            "host": socket.gethostname(), "started_at_ms": started, "requested_at_ms": requested_at,
            "status": "requesting",
        }, key)
        lineage_path = health_dir / "attempts" / f"{run_id}_{attempt_id}.json"
        lineage_hash = exclusive_write(lineage_path, lineage)
        accepted = []
        for feed_id in requested_ids:
            try:
                status = int(request_fn(feed_id, 15.0))
            except (OSError, TypeError, ValueError) as exc:
                raise RefreshError("refresh_request_failed") from exc
            if status < 200 or status >= 300:
                raise RefreshError("refresh_request_rejected")
            accepted.append(feed_id)
        if accepted != requested_ids:
            raise RefreshError("refresh_request_coverage_mismatch")
        deadline = started + deadline_ms
        last_error = ""
        while clock_ms() <= deadline:
            try:
                after = snapshot_fn(database)
                complete, per_feed, new_count = validate_completion(before, after, requested_at)
            except RefreshError as exc:
                if str(exc) != "sqlite_busy_or_unreadable":
                    raise
                last_error = str(exc); sleep_fn(poll_interval_ms / 1000); continue
            if complete:
                completed = clock_ms()
                receipt = seal_payload({"schema_version": 1, "attempt_id": attempt_id, "run_id": run_id, "provider_url": PROVIDER_URL, "database_identity": before["database_identity"], "feed_ids": requested_ids, "before_snapshot_sha256": before["snapshot_sha256"], "after_snapshot_sha256": after["snapshot_sha256"], "attempt_lineage_sha256": lineage_hash, "before": before, "after": after, "per_feed": per_feed, "started_at_ms": started, "requested_at_ms": requested_at, "completed_at_ms": completed, "new_item_count": new_count, "refresh_revision": max(row["after_sync_time"] for row in per_feed), "refreshed_at_ms": max(row["updated_at_ms"] for row in after["feeds"]), "status": "success"}, key)
                receipt_path = health_dir / "receipts" / f"{run_id}_{attempt_id}.json"
                atomic_write(receipt_path, receipt)
                receipt_hash = sha256_bytes(receipt_path.read_bytes())
                return {"ok": True, "status": "success", "run_id": run_id, "attempt_id": attempt_id, "receipt_path": str(receipt_path.resolve()), "receipt_sha256": receipt_hash, "feed_count": len(requested_ids), "new_item_count": new_count, "starts_browser": False, "starts_provider": False, "secret_material_read": True, "secrets_exposed": False}
            sleep_fn(poll_interval_ms / 1000)
        raise RefreshError(last_error or "refresh_completion_timeout")
    finally:
        release_lease(lease_path, attempt_id)


def probe_project_lock(
    lock_path: Path | None = None, *, project_root: Path | None = None,
    clock_ms: Callable[[], int] = lambda: int(time.time() * 1000),
) -> dict[str, Any]:
    resolved = project_owned_lock_path(lock_path or PROJECT_REFRESH_LOCK_PATH, project_root)
    attempt_id = uuid.uuid4().hex
    now_ms = clock_ms()
    payload = {
        "schema_version": 1,
        "attempt_id": attempt_id,
        "run_id": "check-only",
        "pid": os.getpid(),
        "host": socket.gethostname(),
        "started_at_ms": now_ms,
        "expires_at_ms": now_ms + LEASE_TTL_MS,
        "provider_url": PROVIDER_URL,
        "data_identity": {},
    }
    acquire_lease(resolved, payload, now_ms)
    released = False
    try:
        acquired = resolved.is_file()
    finally:
        release_lease(resolved, attempt_id)
        released = not resolved.exists()
    if not acquired or not released:
        raise RefreshError("refresh_lock_probe_failed")
    return {
        "lock_path": str(resolved),
        "lock_path_project_owned": True,
        "lock_acquired": acquired,
        "lock_released": released,
        "legacy_lock_path_written": False,
        "provider_requests": 0,
    }


def probe_project_state(
    state_dir: Path = PROJECT_REFRESH_STATE_DIR, *, project_root: Path | None = None,
    clock_ms: Callable[[], int] = lambda: int(time.time() * 1000),
) -> dict[str, Any]:
    root = (project_root or ROOT).resolve()
    resolved = state_dir.expanduser().resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise RefreshError("refresh_state_not_project_owned") from exc

    lock_proof = probe_project_lock(resolved / "refresh.lock", project_root=root, clock_ms=clock_ms)
    probe_id = uuid.uuid4().hex
    probes = {
        "lease": resolved / "leases" / f"check-only_{probe_id}.json",
        "attempt": resolved / "attempts" / f"check-only_{probe_id}.json",
        "receipt": resolved / "receipts" / f"check-only_{probe_id}.json",
        "watermark": resolved / f"last_success.check-only.{probe_id}.json",
    }
    payload = {"check_only": True, "probe_id": probe_id}
    created: list[Path] = []
    try:
        for path in probes.values():
            atomic_write(path, payload)
            created.append(path)
            if json.loads(path.read_text(encoding="utf-8")) != payload:
                raise RefreshError("refresh_state_probe_readback_failed")
    finally:
        for path in created:
            path.unlink(missing_ok=True)
    if any(path.exists() for path in probes.values()):
        raise RefreshError("refresh_state_probe_cleanup_failed")
    return {
        **lock_proof,
        "state_root": str(resolved),
        "state_root_project_owned": True,
        "mutable_paths_probed": ["refresh.lock", "leases", "attempts", "receipts", "last_success.json"],
        "probe_files_final_absent": True,
        "provider_requests": 0,
        "secret_material_read": False,
    }


def check_only_plan(
    data_dir: Path = CANONICAL_DATA_DIR, *, lock_path: Path | None = None,
    project_root: Path | None = None,
) -> dict[str, Any]:
    effective_root = (project_root or (data_dir.parent if data_dir != CANONICAL_DATA_DIR else ROOT)).resolve()
    effective_state = lock_path.parent if lock_path else effective_root / "output" / "state" / "wewe-refresh"
    try:
        state_proof = probe_project_state(
            effective_state, project_root=effective_root,
        )
    except (OSError, RefreshError) as exc:
        return {"ok": False, "status": str(exc), "check_only": True, "refresh_requested": False, "provider_requests": 0, "secret_material_read": False, "secrets_exposed": False}
    try:
        snapshot = read_snapshot(data_dir.resolve() / "wewe-rss.db")
    except (OSError, RefreshError) as exc:
        return {"ok": False, "status": str(exc), "check_only": True, "refresh_requested": False, **state_proof, "secret_material_read": False, "secrets_exposed": False}
    return {"ok": True, "status": "refresh_required", "check_only": True, "refresh_requested": False, "provider_url": PROVIDER_URL, "feed_ids": [row["feed_id"] for row in snapshot["feeds"]], "active_account_count": snapshot["active_account_count"], "database_identity": snapshot["database_identity"], "starts_browser": False, "starts_provider": False, **state_proof, "secret_material_read": False, "secrets_exposed": False}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--run-started-at-ms", type=int, required=True)
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    if args.check_only:
        result = check_only_plan()
    else:
        secret_material_read = False
        try:
            key = load_attestation_key()
            secret_material_read = True
            result = run_refresh(args.run_id, args.run_started_at_ms, signing_key=key)
        except (OSError, RefreshError, urllib.error.URLError) as exc:
            result = {"ok": False, "status": "provider_failed", "reason": str(exc), "run_id": args.run_id, "refresh_requested": True, "receipt_written": False, "secret_material_read": secret_material_read, "secrets_exposed": False}
    atomic_write(Path(args.out).expanduser().resolve(), result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 4


if __name__ == "__main__":
    raise SystemExit(main())
