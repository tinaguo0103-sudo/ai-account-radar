#!/usr/bin/env python3
"""Single SQLite authority for source configuration and run health."""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "output" / "state" / "source_control.sqlite3"
RUN_RE = re.compile(r"^run_(\d{8})_\d{6}(?:_[A-Za-z0-9_-]+)?$")
DOUYIN_HOSTS = {"douyin.com", "www.douyin.com"}
SUCCESS_OUTCOMES = {"success", "updated_no_new_items"}
TRANSIENT_FAILURES = {"douyin_works_response_timeout", "timeout", "network_timeout"}

SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS schema_meta (
  schema_version INTEGER PRIMARY KEY,
  applied_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS config_revisions (
  revision INTEGER PRIMARY KEY,
  parent_revision INTEGER,
  source TEXT NOT NULL,
  actor_label TEXT NOT NULL,
  command_id TEXT UNIQUE,
  payload_sha256 TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS channels (
  channel_id TEXT PRIMARY KEY,
  platform TEXT NOT NULL,
  provider_key TEXT NOT NULL,
  enabled INTEGER NOT NULL,
  priority INTEGER NOT NULL,
  config_revision INTEGER NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS source_accounts (
  source_id TEXT PRIMARY KEY,
  channel_id TEXT NOT NULL REFERENCES channels(channel_id),
  display_name TEXT NOT NULL,
  configured_identity TEXT NOT NULL,
  verified_identity TEXT NOT NULL,
  homepage_url TEXT NOT NULL,
  enabled INTEGER NOT NULL,
  participates_sampling INTEGER NOT NULL,
  priority TEXT NOT NULL,
  fetch_method TEXT NOT NULL,
  sample_frequency TEXT NOT NULL,
  source_role TEXT NOT NULL,
  learn_focus TEXT NOT NULL,
  remarks TEXT NOT NULL,
  config_revision INTEGER NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(channel_id, verified_identity)
);
CREATE TABLE IF NOT EXISTS source_run_events (
  run_id TEXT NOT NULL,
  source_id TEXT NOT NULL REFERENCES source_accounts(source_id),
  attempted_at TEXT NOT NULL,
  outcome TEXT NOT NULL,
  failure_class TEXT NOT NULL,
  artifact_count INTEGER NOT NULL,
  verified_identity TEXT NOT NULL,
  substitute_count INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY(run_id, source_id)
);
CREATE TABLE IF NOT EXISTS account_health_current (
  source_id TEXT PRIMARY KEY REFERENCES source_accounts(source_id),
  last_attempt TEXT NOT NULL,
  last_success TEXT NOT NULL,
  current_outcome TEXT NOT NULL,
  failure_class TEXT NOT NULL,
  consecutive_failures INTEGER NOT NULL,
  rolling_success_json TEXT NOT NULL,
  action_required INTEGER NOT NULL,
  derived_through_run_id TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS applied_commands (
  command_id TEXT PRIMARY KEY,
  expected_revision INTEGER NOT NULL,
  applied_revision INTEGER,
  status TEXT NOT NULL,
  error_code TEXT,
  result_sha256 TEXT,
  created_at TEXT NOT NULL,
  completed_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS source_accounts_plan_idx
  ON source_accounts(enabled, participates_sampling, channel_id, priority);
CREATE INDEX IF NOT EXISTS source_events_account_idx
  ON source_run_events(source_id, attempted_at);
"""


class SourceControlError(RuntimeError):
    pass


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def source_id(record_id: str, name: str, url: str) -> str:
    if record_id:
        return f"feishu_{record_id}"
    return "source_" + hashlib.sha256(f"{name}|{url}".encode()).hexdigest()[:20]


def douyin_identity(url: str) -> str:
    parsed = urlparse(url)
    parts = [part for part in parsed.path.split("/") if part]
    if parsed.hostname not in DOUYIN_HOSTS or len(parts) != 2 or parts[0] != "user":
        return ""
    return parts[1]


def normalized_account(row: dict[str, Any]) -> dict[str, Any]:
    name = str(row.get("display_name") or row.get("account_name") or "").strip()
    url = str(row.get("homepage_url") or row.get("url") or "").strip()
    platform = str(row.get("platform") or "").strip()
    channel_id = str(row.get("channel_id") or platform.lower() or "unknown").strip()
    configured = str(row.get("configured_identity") or "").strip()
    verified = str(row.get("verified_identity") or "").strip()
    if platform == "抖音":
        verified = verified or douyin_identity(url)
        configured = configured or verified
    else:
        verified = verified or url or name
        configured = configured or verified
    if not name or not channel_id or not verified:
        raise SourceControlError("source_identity_incomplete")
    return {
        "source_id": source_id(str(row.get("record_id") or ""), name, url),
        "channel_id": channel_id,
        "platform": platform,
        "provider_key": str(row.get("provider_key") or row.get("fetch_method") or "direct"),
        "display_name": name,
        "configured_identity": configured,
        "verified_identity": verified,
        "homepage_url": url,
        "enabled": bool(row.get("enabled", row.get("default_enabled", True))),
        "participates_sampling": bool(row.get("participates_sampling", row.get("participates_main_sampling", True))),
        "priority": str(row.get("priority") or "medium"),
        "fetch_method": str(row.get("fetch_method") or ""),
        "sample_frequency": str(row.get("sample_frequency") or "daily_or_when_updated"),
        "source_role": str(row.get("source_role") or row.get("source_group") or ""),
        "learn_focus": str(row.get("learn_focus") or ""),
        "remarks": str(row.get("remarks") or ""),
    }


class SourceControl:
    def __init__(self, path: Path | str = DEFAULT_DB):
        self.path = Path(path).resolve()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys=ON")
            yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connect() as db:
            db.executescript(SCHEMA)
            db.execute(
                "INSERT OR IGNORE INTO schema_meta(schema_version, applied_at) VALUES(1, ?)",
                (now(),),
            )
            db.commit()

    def current_revision(self, db: sqlite3.Connection | None = None) -> int:
        if db is not None:
            row = db.execute("SELECT COALESCE(MAX(revision), 0) AS revision FROM config_revisions").fetchone()
            return int(row["revision"])
        with self.connect() as connection:
            return self.current_revision(connection)

    def import_accounts(self, rows: list[dict[str, Any]], *, actor: str = "feishu_read_only_migration") -> dict[str, Any]:
        accounts = [normalized_account(row) for row in rows]
        if not accounts:
            raise SourceControlError("source_import_empty")
        ids = [row["source_id"] for row in accounts]
        identities = [(row["channel_id"], row["verified_identity"]) for row in accounts]
        if len(ids) != len(set(ids)) or len(identities) != len(set(identities)):
            raise SourceControlError("source_identity_collision")
        payload = {"accounts": sorted(accounts, key=lambda row: row["source_id"])}
        timestamp = now()
        self.initialize()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if self.current_revision(db):
                raise SourceControlError("source_import_requires_fresh_database")
            revision = 1
            db.execute(
                """INSERT INTO config_revisions
                (revision,parent_revision,source,actor_label,command_id,payload_sha256,payload_json,created_at)
                VALUES(1,NULL,'migration',?,NULL,?,?,?)""",
                (actor, digest(payload), canonical_json(payload), timestamp),
            )
            for account in accounts:
                db.execute(
                    """INSERT OR IGNORE INTO channels
                    (channel_id,platform,provider_key,enabled,priority,config_revision,updated_at)
                    VALUES(?,?,?,?,?,?,?)""",
                    (account["channel_id"], account["platform"], account["provider_key"], 1, 0, revision, timestamp),
                )
                db.execute(
                    """INSERT INTO source_accounts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        account["source_id"], account["channel_id"], account["display_name"],
                        account["configured_identity"], account["verified_identity"], account["homepage_url"],
                        int(account["enabled"]), int(account["participates_sampling"]), account["priority"],
                        account["fetch_method"], account["sample_frequency"], account["source_role"],
                        account["learn_focus"], account["remarks"], revision, timestamp,
                    ),
                )
            db.commit()
        return self.get_source_snapshot()

    def get_source_snapshot(self) -> dict[str, Any]:
        self.initialize()
        with self.connect() as db:
            revision = self.current_revision(db)
            rows = [dict(row) for row in db.execute(
                """SELECT a.*,c.platform,c.provider_key,
                h.last_attempt,h.last_success,h.current_outcome,h.failure_class,
                h.consecutive_failures,h.rolling_success_json,h.action_required,h.derived_through_run_id
                FROM source_accounts a JOIN channels c USING(channel_id)
                LEFT JOIN account_health_current h USING(source_id)
                ORDER BY c.platform,a.priority,a.display_name"""
            )]
        return {"ok": True, "schema_version": 1, "revision": revision, "count": len(rows), "accounts": rows}

    def build_collection_plan(self) -> dict[str, Any]:
        snapshot = self.get_source_snapshot()
        active = [
            row for row in snapshot["accounts"]
            if row["enabled"] and row["participates_sampling"]
            and row["source_role"] in {"current_main_competitor", "current_aux_competitor"}
        ]
        invalid: list[dict[str, str]] = []
        seen: dict[str, str] = {}
        for row in active:
            if row["platform"] != "抖音":
                continue
            identity = douyin_identity(row["homepage_url"])
            if not identity or identity != row["verified_identity"]:
                invalid.append({"source_id": row["source_id"], "name": row["display_name"], "reason": "douyin_identity_invalid"})
            elif identity in seen:
                invalid.append({"source_id": row["source_id"], "name": row["display_name"], "reason": "douyin_identity_duplicate"})
            else:
                seen[identity] = row["source_id"]
        executable = [row for row in active if row["source_id"] not in {item["source_id"] for item in invalid}]
        return {
            "ok": bool(executable) and not invalid,
            "plan_ready": bool(executable) and not invalid,
            "revision": snapshot["revision"],
            "planned_accounts": len(active),
            "planned_douyin_accounts": sum(row["platform"] == "抖音" for row in active),
            "executable_douyin_accounts": sum(row["platform"] == "抖音" for row in executable),
            "invalid_accounts": invalid,
            "accounts": executable,
            "feishu_runtime_calls": 0,
        }

    def export_runtime_config(self) -> dict[str, Any]:
        snapshot = self.get_source_snapshot()
        return {
            "schema_version": 1,
            "source_authority": "source_control_sqlite",
            "config_revision": snapshot["revision"],
            "sources": [
                {
                    "id": row["source_id"],
                    "source_group": row["source_role"],
                    "source_role": row["source_role"],
                    "participates_main_sampling": bool(row["participates_sampling"]),
                    "default_enabled": bool(row["enabled"]),
                    "platform": row["platform"],
                    "account_name": row["display_name"],
                    "url": row["homepage_url"],
                    "configured_identity": row["configured_identity"],
                    "verified_identity": row["verified_identity"],
                    "priority": row["priority"],
                    "fetch_method": row["fetch_method"],
                    "sample_frequency": row["sample_frequency"],
                    "learn_focus": row["learn_focus"],
                    "remarks": row["remarks"],
                }
                for row in snapshot["accounts"]
            ],
        }

    def _write_revision(
        self,
        db: sqlite3.Connection,
        *,
        revision: int,
        parent: int,
        source: str,
        actor: str,
        command_id: str | None,
        accounts: list[dict[str, Any]],
    ) -> None:
        payload = {"accounts": sorted(accounts, key=lambda row: row["source_id"])}
        db.execute(
            """INSERT INTO config_revisions
            (revision,parent_revision,source,actor_label,command_id,payload_sha256,payload_json,created_at)
            VALUES(?,?,?,?,?,?,?,?)""",
            (revision, parent, source, actor, command_id, digest(payload), canonical_json(payload), now()),
        )

    def apply_config_command(
        self, command_id: str, expected_revision: int, operations: list[dict[str, Any]], *, actor: str = "source_ui"
    ) -> dict[str, Any]:
        if not command_id or not operations:
            raise SourceControlError("command_invalid")
        self.initialize()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT * FROM applied_commands WHERE command_id=?", (command_id,)).fetchone()
            if existing:
                db.rollback()
                return dict(existing)
            current = self.current_revision(db)
            timestamp = now()
            if expected_revision != current:
                db.execute(
                    "INSERT INTO applied_commands VALUES(?,?,?,?,?,?,?,?)",
                    (command_id, expected_revision, None, "conflict", "stale_revision", None, timestamp, timestamp),
                )
                db.commit()
                return self.get_command_result(command_id)
            current_rows = [dict(row) for row in db.execute("SELECT * FROM source_accounts ORDER BY source_id")]
            by_id = {row["source_id"]: row for row in current_rows}
            for operation in operations:
                target = by_id.get(str(operation.get("source_id") or ""))
                if not target:
                    raise SourceControlError("source_not_found")
                changes = operation.get("changes")
                if not isinstance(changes, dict):
                    raise SourceControlError("command_changes_invalid")
                for key in ("display_name", "configured_identity", "verified_identity", "homepage_url", "enabled", "participates_sampling", "priority"):
                    if key in changes:
                        target[key] = changes[key]
                if target["channel_id"] == "抖音" or target.get("platform") == "抖音":
                    identity = douyin_identity(str(target["homepage_url"]))
                    if not identity or identity != str(target["verified_identity"]):
                        raise SourceControlError("douyin_identity_invalid")
            identities = [(row["channel_id"], row["verified_identity"]) for row in current_rows]
            if len(identities) != len(set(identities)):
                raise SourceControlError("source_identity_collision")
            revision = current + 1
            for row in current_rows:
                db.execute(
                    """UPDATE source_accounts SET display_name=?,configured_identity=?,verified_identity=?,
                    homepage_url=?,enabled=?,participates_sampling=?,priority=?,config_revision=?,updated_at=?
                    WHERE source_id=?""",
                    (
                        row["display_name"], row["configured_identity"], row["verified_identity"], row["homepage_url"],
                        int(row["enabled"]), int(row["participates_sampling"]), row["priority"], revision, timestamp,
                        row["source_id"],
                    ),
                )
            revised_rows = [dict(row) for row in db.execute("SELECT * FROM source_accounts ORDER BY source_id")]
            self._write_revision(db, revision=revision, parent=current, source="command", actor=actor, command_id=command_id, accounts=revised_rows)
            result_hash = digest({"revision": revision, "accounts": revised_rows})
            db.execute(
                "INSERT INTO applied_commands VALUES(?,?,?,?,?,?,?,?)",
                (command_id, expected_revision, revision, "applied", None, result_hash, timestamp, timestamp),
            )
            db.commit()
        return self.get_command_result(command_id)

    def rollback_to_revision(self, command_id: str, expected_revision: int, target_revision: int) -> dict[str, Any]:
        self.initialize()
        with self.connect() as db:
            row = db.execute("SELECT payload_json FROM config_revisions WHERE revision=?", (target_revision,)).fetchone()
            if not row:
                raise SourceControlError("rollback_revision_not_found")
            target_accounts = json.loads(row["payload_json"])["accounts"]
        operations = []
        current = {row["source_id"]: row for row in self.get_source_snapshot()["accounts"]}
        for target in target_accounts:
            if target["source_id"] not in current:
                raise SourceControlError("rollback_source_set_mismatch")
            operations.append({
                "source_id": target["source_id"],
                "changes": {
                    key: target[key] for key in (
                        "display_name", "configured_identity", "verified_identity", "homepage_url",
                        "enabled", "participates_sampling", "priority",
                    )
                },
            })
        return self.apply_config_command(command_id, expected_revision, operations, actor=f"rollback:{target_revision}")

    def get_command_result(self, command_id: str) -> dict[str, Any]:
        with self.connect() as db:
            row = db.execute("SELECT * FROM applied_commands WHERE command_id=?", (command_id,)).fetchone()
        if not row:
            raise SourceControlError("command_not_found")
        return dict(row)

    def record_run_outcomes(self, run_id: str, outcomes: list[dict[str, Any]]) -> dict[str, Any]:
        if not RUN_RE.fullmatch(run_id):
            raise SourceControlError("wrong_run")
        self.initialize()
        timestamp = now()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            for outcome in outcomes:
                sid = str(outcome.get("source_id") or "")
                account = db.execute("SELECT verified_identity FROM source_accounts WHERE source_id=?", (sid,)).fetchone()
                if not account:
                    raise SourceControlError("source_not_found")
                verified = str(outcome.get("verified_identity") or "")
                if verified != account["verified_identity"]:
                    raise SourceControlError("run_event_identity_mismatch")
                substitute = int(outcome.get("substitute_count") or 0)
                if substitute:
                    raise SourceControlError("run_event_substitute_forbidden")
                values = (
                    run_id, sid, str(outcome.get("attempted_at") or timestamp),
                    str(outcome.get("outcome") or "failed"), str(outcome.get("failure_class") or ""),
                    int(outcome.get("artifact_count") or 0), verified, 0,
                )
                existing = db.execute(
                    "SELECT * FROM source_run_events WHERE run_id=? AND source_id=?", (run_id, sid)
                ).fetchone()
                if existing:
                    comparable = tuple(existing[key] for key in (
                        "run_id", "source_id", "attempted_at", "outcome", "failure_class",
                        "artifact_count", "verified_identity", "substitute_count",
                    ))
                    if comparable != values:
                        raise SourceControlError("same_run_event_conflict")
                    continue
                db.execute("INSERT INTO source_run_events VALUES(?,?,?,?,?,?,?,?)", values)
                prior = db.execute("SELECT * FROM account_health_current WHERE source_id=?", (sid,)).fetchone()
                state = values[3]
                success = state in SUCCESS_OUTCOMES
                consecutive = 0 if success else int(prior["consecutive_failures"] if prior else 0) + 1
                recent = [row["outcome"] for row in db.execute(
                    "SELECT outcome FROM source_run_events WHERE source_id=? ORDER BY attempted_at DESC LIMIT 10", (sid,)
                )]
                action_required = int(
                    (not success and values[4] not in TRANSIENT_FAILURES)
                    or (not success and values[4] in TRANSIENT_FAILURES and consecutive >= 3)
                )
                db.execute(
                    """INSERT INTO account_health_current VALUES(?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(source_id) DO UPDATE SET
                    last_attempt=excluded.last_attempt,last_success=excluded.last_success,
                    current_outcome=excluded.current_outcome,failure_class=excluded.failure_class,
                    consecutive_failures=excluded.consecutive_failures,rolling_success_json=excluded.rolling_success_json,
                    action_required=excluded.action_required,derived_through_run_id=excluded.derived_through_run_id,
                    updated_at=excluded.updated_at""",
                    (
                        sid, values[2], values[2] if success else str(prior["last_success"] if prior else ""),
                        state, values[4], consecutive, canonical_json({"window": len(recent), "success": sum(x in SUCCESS_OUTCOMES for x in recent)}),
                        action_required, run_id, timestamp,
                    ),
                )
            db.commit()
        return {"ok": True, "run_id": run_id, "event_count": len(outcomes), "snapshot": self.get_source_snapshot()}
