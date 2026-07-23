#!/usr/bin/env python3
"""Run one project-locked WeWe refresh and read the live SQLite result."""
from __future__ import annotations

import argparse
import json
import os
import socket
import sqlite3
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]
PROVIDER_URL = "http://127.0.0.1:4000"
CANONICAL_DATA_DIR = Path.home() / ".codex" / "ai-account-radar-runtime" / "providers" / "wewe-rss" / "data"
PROJECT_REFRESH_STATE_DIR = ROOT / "output" / "state" / "wewe-refresh"
PROJECT_REFRESH_LOCK_PATH = PROJECT_REFRESH_STATE_DIR / "refresh.lock"
LOCK_TTL_MS = 10 * 60 * 1000


class RefreshError(RuntimeError):
    pass


def configured_data_dir() -> Path:
    return Path(os.getenv("WEWE_RSS_DATA_DIR", str(CANONICAL_DATA_DIR))).expanduser().resolve()


def read_snapshot(database: Path) -> dict[str, Any]:
    if not database.is_file():
        raise RefreshError("provider_database_missing")
    try:
        connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True, timeout=0.5)
        accounts = int(connection.execute("select count(*) from accounts where status=1").fetchone()[0])
        rows = connection.execute("select id, sync_time, updated_at from feeds where status=1 order by id").fetchall()
        feeds = []
        for feed_id, sync_time, updated_at in rows:
            count, latest = connection.execute(
                "select count(*), coalesce(max(publish_time),0) from articles where mp_id=?", (feed_id,)
            ).fetchone()
            feeds.append({
                "feed_id": str(feed_id),
                "sync_time": int(sync_time or 0),
                "updated_at_ms": int(updated_at or 0),
                "article_count": int(count),
                "max_publish_time": int(latest or 0),
            })
        connection.close()
    except sqlite3.Error as exc:
        raise RefreshError("sqlite_busy_or_unreadable") from exc
    if accounts < 1:
        raise RefreshError("login_required")
    if not feeds:
        raise RefreshError("active_feed_set_empty")
    return {"active_account_count": accounts, "feeds": feeds}


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


def project_lock(path: Path, project_root: Path | None = None) -> Path:
    root = (project_root or ROOT).resolve()
    resolved = path.expanduser().resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise RefreshError("refresh_lock_not_project_owned") from exc
    return resolved


def acquire_lock(path: Path, now_ms: int) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    owner = {
        "pid": os.getpid(), "host": socket.gethostname(),
        "created_at_ms": now_ms, "expires_at_ms": now_ms + LOCK_TTL_MS,
    }
    while True:
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            try:
                existing = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, TypeError):
                raise RefreshError("refresh_lock_malformed")
            if int(existing.get("expires_at_ms") or 0) >= now_ms or owner_alive(
                int(existing.get("pid") or 0), str(existing.get("host") or "")
            ):
                raise RefreshError("refresh_in_progress")
            path.unlink()
            continue
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(owner, handle, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        return owner


def release_lock(path: Path, owner: dict[str, Any]) -> None:
    try:
        current = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RefreshError("refresh_lock_release_failed") from exc
    if current != owner:
        raise RefreshError("refresh_lock_owner_mismatch")
    path.unlink()


def default_request(timeout: float) -> int:
    auth_code = os.environ.get("WEWE_RSS_AUTH_CODE", "")
    if not auth_code:
        raise RefreshError("provider_auth_code_missing")
    body = json.dumps({"json": {}}, separators=(",", ":")).encode()
    request = urllib.request.Request(
        f"{PROVIDER_URL}/trpc/feed.refreshAllArticles",
        data=body,
        headers={"Content-Type": "application/json", "Authorization": auth_code},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
        if isinstance(payload, dict) and payload.get("error"):
            raise RefreshError("provider_refresh_error")
        return int(response.status)


def completion(before: dict[str, Any], after: dict[str, Any], requested_at_ms: int) -> tuple[bool, int]:
    old = {row["feed_id"]: row for row in before["feeds"]}
    new = {row["feed_id"]: row for row in after["feeds"]}
    if list(old) != list(new):
        raise RefreshError("active_feed_set_drift")
    added = 0
    complete = True
    for feed_id, previous in old.items():
        current = new[feed_id]
        if current["article_count"] < previous["article_count"] or current["max_publish_time"] < previous["max_publish_time"]:
            raise RefreshError("article_aggregate_rollback")
        complete = complete and current["sync_time"] > previous["sync_time"] and current["sync_time"] >= requested_at_ms // 1000
        added += current["article_count"] - previous["article_count"]
    return complete, added


def run_refresh(
    run_id: str, run_started_at_ms: int, *, data_dir: Path | None = None,
    lock_path: Path | None = None, project_root: Path | None = None,
    request_fn: Callable[[float], int] = default_request,
    snapshot_fn: Callable[[Path], dict[str, Any]] = read_snapshot,
    clock_ms: Callable[[], int] = lambda: int(time.time() * 1000),
    sleep_fn: Callable[[float], None] = time.sleep,
    deadline_ms: int = 120000, poll_interval_ms: int = 500,
) -> dict[str, Any]:
    if not run_id or "/" in run_id:
        raise RefreshError("refresh_run_id_invalid")
    database = (data_dir or configured_data_dir()) / "wewe-rss.db"
    resolved_lock = project_lock(lock_path or PROJECT_REFRESH_LOCK_PATH, project_root)
    owner = acquire_lock(resolved_lock, clock_ms())
    try:
        before = snapshot_fn(database)
        requested_at_ms = clock_ms()
        status = int(request_fn(15.0))
        if not 200 <= status < 300:
            raise RefreshError("refresh_request_rejected")
        deadline = requested_at_ms + deadline_ms
        while clock_ms() <= deadline:
            after = snapshot_fn(database)
            complete, new_count = completion(before, after, requested_at_ms)
            if complete:
                return {
                    "ok": True, "status": "success", "run_id": run_id,
                    "run_started_at_ms": run_started_at_ms,
                    "requested_at_ms": requested_at_ms,
                    "completed_at_ms": clock_ms(),
                    "provider_request_count": 1,
                    "new_item_count": new_count,
                    "before": before, "after": after,
                    "secret_material_read": False, "secrets_exposed": False,
                }
            sleep_fn(poll_interval_ms / 1000)
        raise RefreshError("refresh_completion_timeout")
    finally:
        release_lock(resolved_lock, owner)


def check_only_plan(
    data_dir: Path | None = None, *, lock_path: Path | None = None,
    project_root: Path | None = None,
) -> dict[str, Any]:
    root = (project_root or ROOT).resolve()
    resolved = project_lock(lock_path or root / "output" / "state" / "wewe-refresh" / "refresh.lock", root)
    owner = acquire_lock(resolved, int(time.time() * 1000))
    acquired = resolved.is_file()
    release_lock(resolved, owner)
    snapshot = read_snapshot((data_dir or configured_data_dir()) / "wewe-rss.db")
    return {
        "ok": acquired and not resolved.exists(), "status": "refresh_required",
        "check_only": True, "lock_path": str(resolved), "lock_acquired": acquired,
        "lock_released": not resolved.exists(), "provider_request_count": 0,
        "secret_material_read": False, "active_account_count": snapshot["active_account_count"],
        "feed_count": len(snapshot["feeds"]),
    }


def write_result(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--run-started-at-ms", type=int, required=True)
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    try:
        result = check_only_plan() if args.check_only else run_refresh(args.run_id, args.run_started_at_ms)
    except (OSError, RefreshError, urllib.error.URLError, json.JSONDecodeError) as exc:
        result = {
            "ok": False, "status": "provider_failed", "reason": str(exc),
            "run_id": args.run_id, "provider_request_count": 1 if not args.check_only else 0,
            "secret_material_read": False, "secrets_exposed": False,
        }
    write_result(Path(args.out).expanduser().resolve(), result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 4


if __name__ == "__main__":
    raise SystemExit(main())
