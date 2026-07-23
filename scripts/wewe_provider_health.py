#!/usr/bin/env python3
"""Read-only freshness and account-health gate for the canonical wewe-rss runtime."""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import sqlite3
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from wewe_provider_refresh import ATTEMPT_ID_PATTERN, HEALTH_DIR, LEGACY_HEALTH_DIR, PROVIDER_URL, RUN_ID_PATTERN, RefreshError, attestation_key_id, canonical_json, load_attestation_key, read_snapshot, sha256_bytes, sign_payload

CANONICAL_DATA_DIR = Path.home() / ".codex" / "ai-account-radar-runtime" / "providers" / "wewe-rss" / "data"
CANONICAL_STATE_PATH = HEALTH_DIR / "last_success.json"
LEGACY_CANONICAL_STATE_PATH = LEGACY_HEALTH_DIR / "last_success.json"
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


def validate_refresh_receipt(
    receipt_path: Path, receipt_sha256: str, *, run_id: str, attempt_id: str,
    data_dir: Path = CANONICAL_DATA_DIR, health_dir: Path = HEALTH_DIR,
    now_ms: int, run_started_at_ms: int = 0, previous_attempt_id: str = "",
    signing_key: bytes | None = None,
) -> dict[str, Any]:
    try:
        key = signing_key if signing_key is not None else load_attestation_key()
    except RefreshError as exc:
        raise ValueError(str(exc)) from exc
    expected_key_id = attestation_key_id(key)
    if not isinstance(run_id, str) or not RUN_ID_PATTERN.fullmatch(run_id) or not isinstance(attempt_id, str) or not ATTEMPT_ID_PATTERN.fullmatch(attempt_id):
        raise ValueError("refresh_receipt_identity_format_invalid")
    root = health_dir.resolve()
    expected = root / "receipts" / f"{run_id}_{attempt_id}.json"
    lineage_path = root / "attempts" / f"{run_id}_{attempt_id}.json"
    lease_record_path = root / "leases" / f"{run_id}_{attempt_id}.json"
    supplied = Path(receipt_path)
    if not supplied.is_absolute() or supplied != expected or supplied.parent.resolve() != (root / "receipts").resolve():
        raise ValueError("refresh_receipt_path_not_owned")
    for path, reason in ((expected, "refresh_receipt_path_not_owned"), (lineage_path, "refresh_attempt_lineage_missing"), (lease_record_path, "refresh_lease_lineage_missing")):
        try:
            info = path.lstat()
        except OSError as exc:
            raise ValueError(reason) from exc
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or path.is_symlink():
            raise ValueError(reason)
    try:
        raw = expected.read_bytes()
        receipt = json.loads(raw)
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        raise ValueError("refresh_receipt_unreadable") from exc
    if sha256_bytes(raw) != receipt_sha256:
        raise ValueError("refresh_receipt_hash_mismatch")
    required = {"schema_version", "attempt_id", "run_id", "provider_url", "database_identity", "feed_ids", "before_snapshot_sha256", "after_snapshot_sha256", "attempt_lineage_sha256", "before", "after", "per_feed", "started_at_ms", "requested_at_ms", "completed_at_ms", "new_item_count", "refresh_revision", "refreshed_at_ms", "status", "attestation_key_id", "attestation_signature"}
    if not isinstance(receipt, dict) or set(receipt) != required:
        raise ValueError("refresh_receipt_schema_invalid")
    if receipt["schema_version"] != 1 or receipt["status"] != "success" or receipt["run_id"] != run_id or receipt["attempt_id"] != attempt_id:
        raise ValueError("refresh_receipt_identity_mismatch")
    if receipt["attestation_key_id"] != expected_key_id or not hmac.compare_digest(str(receipt["attestation_signature"]), sign_payload(receipt, key)):
        raise ValueError("refresh_receipt_attestation_invalid")
    if previous_attempt_id and receipt["attempt_id"] == previous_attempt_id:
        raise ValueError("refresh_receipt_replayed")
    if receipt["provider_url"] != PROVIDER_URL:
        raise ValueError("refresh_receipt_provider_mismatch")
    integer_fields = ("schema_version", "started_at_ms", "requested_at_ms", "completed_at_ms", "new_item_count", "refresh_revision", "refreshed_at_ms")
    if any(type(receipt.get(key)) is not int for key in integer_fields):
        raise ValueError("refresh_receipt_type_invalid")
    started, requested, completed = (receipt[key] for key in ("started_at_ms", "requested_at_ms", "completed_at_ms"))
    if not (0 < run_started_at_ms <= started <= requested <= completed <= now_ms):
        raise ValueError("refresh_receipt_time_invalid")
    try:
        lineage_raw = lineage_path.read_bytes(); lineage = json.loads(lineage_raw)
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        raise ValueError("refresh_attempt_lineage_unreadable") from exc
    lineage_keys = {"schema_version", "attempt_id", "run_id", "provider_url", "database_identity", "feed_ids", "before_snapshot_sha256", "lease_sha256", "pid", "host", "started_at_ms", "requested_at_ms", "status", "attestation_key_id", "attestation_signature"}
    if not isinstance(lineage, dict) or set(lineage) != lineage_keys or sha256_bytes(lineage_raw) != receipt["attempt_lineage_sha256"]:
        raise ValueError("refresh_attempt_lineage_invalid")
    if any(type(lineage.get(key)) is not int for key in ("schema_version", "pid", "started_at_ms", "requested_at_ms")):
        raise ValueError("refresh_attempt_lineage_type_invalid")
    if lineage["attestation_key_id"] != expected_key_id or not hmac.compare_digest(str(lineage["attestation_signature"]), sign_payload(lineage, key)):
        raise ValueError("refresh_attempt_attestation_invalid")
    if lineage["status"] != "requesting" or lineage["run_id"] != run_id or lineage["attempt_id"] != attempt_id or lineage["provider_url"] != PROVIDER_URL or lineage["started_at_ms"] != started or lineage["requested_at_ms"] != requested:
        raise ValueError("refresh_attempt_lineage_identity_mismatch")
    try:
        lease_raw = lease_record_path.read_bytes(); lease = json.loads(lease_raw)
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        raise ValueError("refresh_lease_lineage_unreadable") from exc
    lease_keys = {"schema_version", "attempt_id", "run_id", "pid", "host", "started_at_ms", "expires_at_ms", "provider_url", "data_identity", "attestation_key_id", "attestation_signature"}
    if not isinstance(lease, dict) or set(lease) != lease_keys or sha256_bytes(lease_raw) != lineage["lease_sha256"]:
        raise ValueError("refresh_lease_lineage_invalid")
    if lease["attestation_key_id"] != expected_key_id or not hmac.compare_digest(str(lease["attestation_signature"]), sign_payload(lease, key)):
        raise ValueError("refresh_lease_attestation_invalid")
    if lease["run_id"] != run_id or lease["attempt_id"] != attempt_id or lease["provider_url"] != PROVIDER_URL or lease["data_identity"] != receipt["database_identity"] or lease["started_at_ms"] != started or lease["pid"] != lineage["pid"] or lease["host"] != lineage["host"]:
        raise ValueError("refresh_lease_lineage_identity_mismatch")
    snapshot_keys = {"database_identity", "active_account_count", "feeds", "snapshot_sha256"}
    identity_keys = {"path", "device", "inode"}
    feed_keys = {"feed_id", "sync_time", "updated_at_ms", "article_count", "max_publish_time"}
    per_feed_keys = {"feed_id", "before_sync_time", "after_sync_time", "completion_advanced", "new_item_count"}
    before, after = receipt["before"], receipt["after"]
    if not isinstance(before, dict) or not isinstance(after, dict) or set(before) != snapshot_keys or set(after) != snapshot_keys:
        raise ValueError("refresh_receipt_snapshot_schema_invalid")
    hash_fields = ("before_snapshot_sha256", "after_snapshot_sha256", "attempt_lineage_sha256")
    if any(not isinstance(receipt.get(key), str) or not re.fullmatch(r"[0-9a-f]{64}", receipt[key]) for key in hash_fields):
        raise ValueError("refresh_receipt_hash_type_invalid")
    if receipt["before_snapshot_sha256"] != sha256_bytes(canonical_json({key: value for key, value in before.items() if key != "snapshot_sha256"})) or before["snapshot_sha256"] != receipt["before_snapshot_sha256"]:
        raise ValueError("refresh_receipt_before_hash_mismatch")
    if receipt["after_snapshot_sha256"] != sha256_bytes(canonical_json({key: value for key, value in after.items() if key != "snapshot_sha256"})) or after["snapshot_sha256"] != receipt["after_snapshot_sha256"]:
        raise ValueError("refresh_receipt_after_hash_mismatch")
    if not isinstance(receipt["database_identity"], dict) or set(receipt["database_identity"]) != identity_keys or before["database_identity"] != receipt["database_identity"] or after["database_identity"] != receipt["database_identity"] or lineage["database_identity"] != receipt["database_identity"]:
        raise ValueError("refresh_receipt_database_identity_mismatch")
    if type(before["active_account_count"]) is not int or type(after["active_account_count"]) is not int or before["active_account_count"] < 1 or after["active_account_count"] < 1:
        raise ValueError("refresh_receipt_account_state_invalid")
    if not isinstance(before["feeds"], list) or not isinstance(after["feeds"], list) or not isinstance(receipt["per_feed"], list) or not isinstance(receipt["feed_ids"], list) or not receipt["feed_ids"]:
        raise ValueError("refresh_receipt_feed_schema_invalid")
    if any(not isinstance(row, dict) or set(row) != feed_keys for row in before["feeds"] + after["feeds"]) or any(not isinstance(row, dict) or set(row) != per_feed_keys for row in receipt["per_feed"]):
        raise ValueError("refresh_receipt_feed_schema_invalid")
    before_ids = [row["feed_id"] for row in before["feeds"]]; after_ids = [row["feed_id"] for row in after["feeds"]]
    if any(not isinstance(value, str) or not value for value in receipt["feed_ids"] + before_ids + after_ids) or len(set(receipt["feed_ids"])) != len(receipt["feed_ids"]):
        raise ValueError("refresh_receipt_feed_id_invalid")
    if before_ids != receipt["feed_ids"] or after_ids != receipt["feed_ids"] or lineage["feed_ids"] != receipt["feed_ids"] or len(receipt["per_feed"]) != len(receipt["feed_ids"]):
        raise ValueError("refresh_receipt_feed_set_mismatch")
    expected_per_feed = []
    total_new = 0
    for old, new in zip(before["feeds"], after["feeds"]):
        numeric = [old[key] for key in feed_keys - {"feed_id"}] + [new[key] for key in feed_keys - {"feed_id"}]
        if any(type(value) is not int for value in numeric):
            raise ValueError("refresh_receipt_feed_type_invalid")
        if new["article_count"] < old["article_count"] or new["max_publish_time"] < old["max_publish_time"]:
            raise ValueError("refresh_receipt_article_aggregate_rollback")
        if not (requested <= new["updated_at_ms"] <= completed):
            raise ValueError("refresh_receipt_completion_time_drift")
        advanced = new["sync_time"] > old["sync_time"] and new["sync_time"] >= requested // 1000
        added = new["article_count"] - old["article_count"]
        expected_per_feed.append({"feed_id": old["feed_id"], "before_sync_time": old["sync_time"], "after_sync_time": new["sync_time"], "completion_advanced": advanced, "new_item_count": added})
        total_new += added
    if receipt["per_feed"] != expected_per_feed or not all(row["completion_advanced"] for row in expected_per_feed):
        raise ValueError("refresh_receipt_per_feed_mismatch")
    if receipt["new_item_count"] != total_new or receipt["refresh_revision"] != max(row["sync_time"] for row in after["feeds"]) or receipt["refreshed_at_ms"] != max(row["updated_at_ms"] for row in after["feeds"]):
        raise ValueError("refresh_receipt_aggregate_mismatch")
    if lineage["feed_ids"] != before_ids or lineage["before_snapshot_sha256"] != receipt["before_snapshot_sha256"]:
        raise ValueError("refresh_attempt_lineage_snapshot_mismatch")
    live = read_snapshot(data_dir.resolve() / "wewe-rss.db")
    if live["database_identity"] != receipt["database_identity"] or live["snapshot_sha256"] != receipt["after_snapshot_sha256"]:
        raise ValueError("refresh_receipt_live_state_drift")
    return receipt


def load_success_watermark(path: Path, legacy_path: Path | None = None) -> dict[str, Any]:
    source = path
    if not source.exists() and legacy_path is not None and legacy_path.exists():
        source = legacy_path
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
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
        row = connection.execute("select coalesce(max(sync_time),0), coalesce(max(updated_at),0) from feeds where status=1").fetchone()
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
    parser.add_argument("--refresh-result", required=True)
    args = parser.parse_args()
    data_dir = configured_data_dir()
    state_path = Path(args.state_path).expanduser().resolve()
    legacy_state_path = LEGACY_CANONICAL_STATE_PATH if state_path == CANONICAL_STATE_PATH.resolve() else None
    watermark = load_success_watermark(state_path, legacy_state_path)
    watermark_source = "project" if state_path.exists() else ("legacy_read_only" if legacy_state_path and legacy_state_path.exists() else "empty")
    if args.previous_success_revision is not None:
        watermark["refresh_revision"] = args.previous_success_revision
    article_watermark = args.article_publish_watermark if args.article_publish_watermark is not None else watermark["article_publish_watermark"]
    now_ms = args.now_ms or int(datetime.now(timezone.utc).timestamp() * 1000)
    receipt_error = ""
    secret_material_read = False
    try:
        key = load_attestation_key()
        secret_material_read = True
        adapter_result = json.loads(Path(args.refresh_result).read_text(encoding="utf-8"))
        receipt = validate_refresh_receipt(Path(adapter_result["receipt_path"]), str(adapter_result["receipt_sha256"]), run_id=args.run_id, attempt_id=str(adapter_result["attempt_id"]), now_ms=now_ms, run_started_at_ms=args.run_started_at_ms, previous_attempt_id=str(watermark.get("refresh_attempt_id") or ""), signing_key=key)
    except (OSError, KeyError, ValueError, RefreshError, json.JSONDecodeError, TypeError) as exc:
        receipt_error = str(exc) or type(exc).__name__
        receipt = {}
    refresh_attempt = {
        "run_id": receipt.get("run_id"), "status": receipt.get("status"), "attempt_id": receipt.get("attempt_id"),
        "started_at_ms": receipt.get("started_at_ms"), "completed_at_ms": receipt.get("completed_at_ms"),
        "refresh_revision": receipt.get("refresh_revision"), "refreshed_at_ms": receipt.get("refreshed_at_ms"),
    }
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
    result["receipt_validation_error"] = receipt_error
    result["run_id"] = args.run_id
    result["article_publish_watermark"] = article_watermark
    result["state_path"] = str(state_path)
    result["watermark_source"] = watermark_source
    result.update({
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "data_dir": str(data_dir),
        "data_dir_identity_hash": identity_hash(data_dir),
        "check_only": True,
        "starts_browser": False,
        "starts_provider": False,
        "secret_material_read": secret_material_read,
        "secrets_exposed": False,
    })
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 4


if __name__ == "__main__":
    raise SystemExit(main())
