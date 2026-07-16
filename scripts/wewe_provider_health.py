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


def classify_snapshot(snapshot: dict[str, Any], *, now_ms: int, previous_success_revision: int = 0) -> dict[str, Any]:
    if not snapshot.get("provider_reachable") or not snapshot.get("database_readable"):
        state = "provider_failed"
    elif int(snapshot.get("active_account_count") or 0) < 1:
        state = "login_required"
    else:
        revision = int(snapshot.get("refresh_revision") or 0)
        refreshed_at = int(snapshot.get("refreshed_at_ms") or 0)
        refresh_age_ms = now_ms - refreshed_at
        if previous_success_revision <= 0 or revision <= 0 or refreshed_at <= 0 or refresh_age_ms < 0 or refresh_age_ms > 24 * 3600 * 1000:
            state = "stale_cache"
        elif revision > previous_success_revision and int(snapshot.get("new_item_count") or 0) > 0:
            state = "updated_with_new_items"
        else:
            state = "updated_no_new_items"
    return {"ok": state in {"updated_with_new_items", "updated_no_new_items"}, "state": state, **snapshot}


def load_success_watermark(path: Path) -> dict[str, int]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return {
            "refresh_revision": int(payload["refresh_revision"]),
            "article_publish_watermark": int(payload["article_publish_watermark"]),
        }
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return {"refresh_revision": 0, "article_publish_watermark": 0}


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
    args = parser.parse_args()
    data_dir = configured_data_dir()
    state_path = Path(args.state_path).expanduser().resolve()
    watermark = load_success_watermark(state_path)
    previous_revision = args.previous_success_revision if args.previous_success_revision is not None else watermark["refresh_revision"]
    article_watermark = args.article_publish_watermark if args.article_publish_watermark is not None else watermark["article_publish_watermark"]
    now_ms = args.now_ms or int(datetime.now(timezone.utc).timestamp() * 1000)
    result = classify_snapshot(
        sqlite_snapshot(data_dir, article_watermark),
        now_ms=now_ms,
        previous_success_revision=previous_revision,
    )
    result["previous_success_revision"] = previous_revision
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
