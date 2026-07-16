#!/usr/bin/env python3
"""Read-only freshness and account-health gate for the canonical wewe-rss runtime."""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CANONICAL_DATA_DIR = Path.home() / ".codex" / "ai-account-radar-runtime" / "providers" / "wewe-rss" / "data"
CANONICAL_STATE_PATH = CANONICAL_DATA_DIR.parent / "health" / "last_success.json"
ALLOWED_STATES = {"updated_with_new_items", "updated_no_new_items", "stale_cache", "login_required", "provider_failed"}


def configured_data_dir() -> Path:
    return CANONICAL_DATA_DIR.resolve()


def identity_hash(path: Path) -> str:
    return hashlib.sha256(str(path.resolve()).encode()).hexdigest()


def classify_snapshot(
    snapshot: dict[str, Any], *, now_ms: int, previous_watermark: dict[str, Any] | None = None,
    run_id: str = "", run_started_at_ms: int = 0, refresh_attempt: dict[str, Any] | None = None,
    previous_success_revision: int | None = None,
) -> dict[str, Any]:
    previous_watermark = dict(previous_watermark or {})
    refresh_attempt = dict(refresh_attempt or {})
    if previous_success_revision is not None:
        previous_watermark.setdefault("refresh_revision", previous_success_revision)
    if not snapshot.get("provider_reachable") or not snapshot.get("database_readable"):
        state = "provider_failed"
    elif int(snapshot.get("active_account_count") or 0) < 1:
        state = "login_required"
    else:
        revision = int(snapshot.get("refresh_revision") or 0)
        refreshed_at = int(snapshot.get("refreshed_at_ms") or 0)
        previous_revision = int(previous_watermark.get("refresh_revision") or 0)
        previous_refreshed_at = int(previous_watermark.get("refreshed_at_ms") or 0)
        attempt_ok = (
            isinstance(refresh_attempt, dict)
            and str(refresh_attempt.get("run_id") or "") == run_id
            and str(refresh_attempt.get("status") or "") == "success"
            and bool(refresh_attempt.get("attempt_id"))
            and int(refresh_attempt.get("started_at_ms") or 0) >= run_started_at_ms
            and int(refresh_attempt.get("completed_at_ms") or 0) >= int(refresh_attempt.get("started_at_ms") or 0)
            and int(refresh_attempt.get("completed_at_ms") or 0) <= now_ms
            and int(refresh_attempt.get("refresh_revision") or 0) == revision
            and int(refresh_attempt.get("refreshed_at_ms") or 0) == refreshed_at
        )
        freshness_ok = (
            revision > 0 and refreshed_at > previous_refreshed_at
            and refreshed_at > run_started_at_ms and refreshed_at <= now_ms
            and (revision > previous_revision or attempt_ok)
        )
        if previous_revision <= 0 or previous_refreshed_at <= 0 or not attempt_ok or not freshness_ok:
            state = "stale_cache"
        elif int(snapshot.get("new_item_count") or 0) > 0:
            state = "updated_with_new_items"
        else:
            state = "updated_no_new_items"
    return {"ok": state in {"updated_with_new_items", "updated_no_new_items"}, "state": state, **snapshot}


def load_success_watermark(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return {
            "refresh_revision": int(payload["refresh_revision"]),
            "refreshed_at_ms": int(payload["refreshed_at_ms"]),
            "article_publish_watermark": int(payload["article_publish_watermark"]),
            "refresh_attempt_id": str(payload["refresh_attempt_id"]),
            "accepted_run_id": str(payload["accepted_run_id"]),
        }
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return {"refresh_revision": 0, "refreshed_at_ms": 0, "article_publish_watermark": 0, "refresh_attempt_id": "", "accepted_run_id": ""}


def sqlite_snapshot(data_dir: Path, article_publish_watermark: int = 0) -> dict[str, Any]:
    database = data_dir / "wewe-rss.db"
    if not database.exists():
        return {"provider_reachable": False, "database_readable": False, "active_account_count": 0}
    try:
        connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
        active_accounts = int(connection.execute("select count(*) from accounts where status=1").fetchone()[0])
        feed_count = int(connection.execute("select count(*) from feeds where status=1").fetchone()[0])
        row = connection.execute("select coalesce(max(update_time),0), coalesce(max(updated_at),0) from feeds where status=1").fetchone()
        article_count = int(connection.execute("select count(*) from articles").fetchone()[0])
        new_item_count = int(connection.execute(
            "select count(*) from articles where publish_time > ?", (article_publish_watermark,)
        ).fetchone()[0])
        latest_publish_time = int(connection.execute("select coalesce(max(publish_time),0) from articles").fetchone()[0])
        connection.close()
        revision = int(row[0] or 0)
        refreshed_at = int(row[1] or 0)
        return {
            "provider_reachable": True,
            "database_readable": True,
            "active_account_count": active_accounts,
            "active_source_count": feed_count,
            "refresh_revision": revision,
            "refreshed_at_ms": refreshed_at,
            "article_count": article_count,
            "new_item_count": new_item_count,
            "latest_article_publish_time": latest_publish_time,
        }
    except (sqlite3.Error, OSError, ValueError):
        return {"provider_reachable": True, "database_readable": False, "active_account_count": 0}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check-only", action="store_true", required=True)
    parser.add_argument("--state-path", default=str(CANONICAL_STATE_PATH))
    parser.add_argument("--previous-success-revision", type=int, default=None)
    parser.add_argument("--article-publish-watermark", type=int, default=None)
    parser.add_argument("--now-ms", type=int, default=0)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--run-started-at-ms", type=int, required=True)
    parser.add_argument("--refresh-attempt", required=True)
    args = parser.parse_args()
    data_dir = configured_data_dir()
    state_path = Path(args.state_path).expanduser().resolve()
    watermark = load_success_watermark(state_path)
    if args.previous_success_revision is not None:
        watermark["refresh_revision"] = args.previous_success_revision
    article_watermark = args.article_publish_watermark if args.article_publish_watermark is not None else watermark["article_publish_watermark"]
    now_ms = args.now_ms or int(datetime.now(timezone.utc).timestamp() * 1000)
    try:
        refresh_attempt = json.loads(Path(args.refresh_attempt).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        refresh_attempt = {}
    result = classify_snapshot(
        sqlite_snapshot(data_dir, article_watermark),
        now_ms=now_ms,
        previous_watermark=watermark,
        run_id=args.run_id,
        run_started_at_ms=args.run_started_at_ms,
        refresh_attempt=refresh_attempt,
    )
    result["previous_success_revision"] = watermark["refresh_revision"]
    result["previous_refreshed_at_ms"] = watermark["refreshed_at_ms"]
    result["refresh_attempt_id"] = str(refresh_attempt.get("attempt_id") or "")
    result["run_id"] = args.run_id
    result["article_publish_watermark"] = article_watermark
    result["state_path"] = str(state_path)
    result.update({
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "data_dir": str(data_dir),
        "data_dir_identity_hash": identity_hash(data_dir),
        "check_only": True,
        "starts_browser": False,
        "starts_provider": False,
        "secrets_read": False,
    })
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 4


if __name__ == "__main__":
    raise SystemExit(main())
