#!/usr/bin/env python3
"""Minimal SQLite authority for the daily personal content workflow."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 5
STAGES = ("collection_enrichment", "editorial", "scripts")
TERMINAL = {"completed", "completed_with_failures", "completed_empty"}
RECOVERABLE_FAILURE = "failed_recoverable"
WAITING = "waiting"


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class WorkflowConflict(RuntimeError):
    pass


class DailyWorkflow:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self._migrate()

    def _migrate(self) -> None:
        # New installations contain only the V2 authority. Old, unused tables or
        # columns may remain in an upgraded production database, but V2 never
        # reads or writes them.
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS workflow_meta(
          key TEXT PRIMARY KEY, value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS daily_runs(
          run_id TEXT PRIMARY KEY, business_date TEXT NOT NULL,
          status TEXT NOT NULL, publish_status TEXT NOT NULL DEFAULT 'not_ready',
          publish_error TEXT NOT NULL DEFAULT '', publish_key TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL, published_at TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS stage_results(
          run_id TEXT NOT NULL, stage TEXT NOT NULL, status TEXT NOT NULL,
          payload_json TEXT NOT NULL, committed_at TEXT NOT NULL,
          PRIMARY KEY(run_id, stage)
        );
        CREATE TABLE IF NOT EXISTS workflow_items(
          run_id TEXT NOT NULL, item_id TEXT NOT NULL, status TEXT NOT NULL,
          failure TEXT NOT NULL, payload_json TEXT NOT NULL,
          PRIMARY KEY(run_id, item_id)
        );
        CREATE TABLE IF NOT EXISTS skill_diagnostics(
          run_id TEXT NOT NULL, stage TEXT NOT NULL, unit_id TEXT NOT NULL,
          skill_name TEXT NOT NULL, details_json TEXT NOT NULL, created_at TEXT NOT NULL,
          PRIMARY KEY(run_id, stage, unit_id, skill_name)
        );
        """)
        columns = {row["name"] for row in self.db.execute("PRAGMA table_info(daily_runs)")}
        additions = {
            "publish_status": "TEXT NOT NULL DEFAULT 'not_ready'",
            "publish_error": "TEXT NOT NULL DEFAULT ''",
            "publish_key": "TEXT NOT NULL DEFAULT ''",
            "published_at": "TEXT NOT NULL DEFAULT ''",
        }
        for name, definition in additions.items():
            if name not in columns:
                self.db.execute(f"ALTER TABLE daily_runs ADD COLUMN {name} {definition}")
        self.db.execute(
            """INSERT INTO workflow_meta(key,value) VALUES('schema_version',?)
               ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
            (str(SCHEMA_VERSION),),
        )
        self.db.commit()

    @staticmethod
    def validate_identity(run_id: str, business_date: str) -> None:
        if len(run_id) != 19 or not run_id.startswith("run_"):
            raise ValueError("wrong_run")
        compact = run_id[4:12]
        if business_date != f"{compact[:4]}-{compact[4:6]}-{compact[6:]}":
            raise ValueError("wrong_business_date")

    def begin(self, run_id: str, business_date: str) -> str:
        self.validate_identity(run_id, business_date)
        row = self.db.execute("SELECT * FROM daily_runs WHERE run_id=?", (run_id,)).fetchone()
        if row:
            if row["business_date"] != business_date:
                raise WorkflowConflict("run_identity_conflict")
            if row["status"] in TERMINAL:
                return "terminal_replay"
            if row["status"] == RECOVERABLE_FAILURE:
                now = datetime.now(timezone.utc).isoformat()
                self.db.execute(
                    """UPDATE daily_runs SET status=?,publish_error='',
                       updated_at=? WHERE run_id=?""",
                    (WAITING, now, run_id),
                )
                self.db.commit()
            return "resume"
        now = datetime.now(timezone.utc).isoformat()
        self.db.execute(
            """INSERT INTO daily_runs(
              run_id,business_date,status,publish_status,publish_error,publish_key,
              created_at,updated_at,published_at
            ) VALUES(?,?,?,'not_ready','','',?,?, '')""",
            (run_id, business_date, WAITING, now, now),
        )
        self.db.commit()
        return "new"

    @classmethod
    def read_business_date(
        cls, path: Path | str, business_date: str
    ) -> dict[str, Any] | None:
        """Read the unique exact-date run without creating or migrating a database."""
        target = Path(path)
        if not target.is_file():
            return None
        try:
            datetime.fromisoformat(business_date)
        except ValueError:
            raise ValueError("wrong_business_date") from None
        db = sqlite3.connect(f"file:{target.resolve()}?mode=ro", uri=True)
        db.row_factory = sqlite3.Row
        try:
            table = db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='daily_runs'"
            ).fetchone()
            if not table:
                return None
            rows = db.execute(
                "SELECT * FROM daily_runs WHERE business_date=? ORDER BY run_id",
                (business_date,),
            ).fetchall()
            if len(rows) > 1:
                raise WorkflowConflict("multiple_runs_for_business_date")
            if not rows:
                return None
            run = dict(rows[0])
            stages = [
                row["stage"] for row in db.execute(
                    """SELECT stage FROM stage_results WHERE run_id=?
                       ORDER BY CASE stage WHEN 'collection_enrichment' THEN 1
                       WHEN 'editorial' THEN 2 ELSE 3 END""",
                    (run["run_id"],),
                )
            ]
            return {"run": run, "committed_stages": stages}
        finally:
            db.close()

    @classmethod
    def mark_existing_recoverable_failure(
        cls, path: Path | str, run_id: str, business_date: str, error: str
    ) -> bool:
        """Terminalize an existing same-run row without creating a database."""
        target = Path(path)
        if not target.is_file():
            return False
        cls.validate_identity(run_id, business_date)
        db = sqlite3.connect(target)
        db.row_factory = sqlite3.Row
        try:
            table = db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='daily_runs'"
            ).fetchone()
            if not table:
                return False
            row = db.execute(
                "SELECT business_date,status FROM daily_runs WHERE run_id=?", (run_id,)
            ).fetchone()
            if not row:
                return False
            if row["business_date"] != business_date:
                raise WorkflowConflict("run_identity_conflict")
            if row["status"] in TERMINAL:
                return False
            now = datetime.now(timezone.utc).isoformat()
            db.execute(
                """UPDATE daily_runs SET status=?,publish_status='not_ready',
                   publish_error=?,updated_at=? WHERE run_id=?""",
                (RECOVERABLE_FAILURE, error[:500], now, run_id),
            )
            db.commit()
            return True
        finally:
            db.close()

    def mark_recoverable_failure(self, run_id: str, error: str) -> None:
        row = self.db.execute(
            "SELECT status FROM daily_runs WHERE run_id=?", (run_id,)
        ).fetchone()
        if not row or row["status"] in TERMINAL:
            return
        now = datetime.now(timezone.utc).isoformat()
        self.db.execute(
            """UPDATE daily_runs SET status=?,publish_status='not_ready',
               publish_error=?,updated_at=? WHERE run_id=?""",
            (RECOVERABLE_FAILURE, error[:500], now, run_id),
        )
        self.db.commit()

    def mark_waiting(self, run_id: str) -> None:
        row = self.db.execute(
            "SELECT status FROM daily_runs WHERE run_id=?", (run_id,)
        ).fetchone()
        if not row or row["status"] in TERMINAL:
            return
        if row["status"] == WAITING:
            return
        now = datetime.now(timezone.utc).isoformat()
        self.db.execute(
            """UPDATE daily_runs SET status=?,publish_status='not_ready',
               publish_error='',updated_at=? WHERE run_id=?""",
            (WAITING, now, run_id),
        )
        self.db.commit()

    def stage(self, run_id: str, stage: str) -> dict[str, Any] | None:
        row = self.db.execute(
            "SELECT * FROM stage_results WHERE run_id=? AND stage=?", (run_id, stage)
        ).fetchone()
        if not row:
            return None
        value = dict(row)
        value["payload"] = json.loads(value.pop("payload_json"))
        return value

    def commit_stage(
        self, run_id: str, stage: str, payload: dict[str, Any], status: str
    ) -> dict[str, Any]:
        if stage not in STAGES:
            raise ValueError("invalid_stage")
        position = STAGES.index(stage)
        if position and not self.stage(run_id, STAGES[position - 1]):
            raise WorkflowConflict("prior_stage_not_committed")
        encoded = canonical(payload)
        existing = self.db.execute(
            "SELECT * FROM stage_results WHERE run_id=? AND stage=?", (run_id, stage)
        ).fetchone()
        if existing:
            if existing["status"] == status and existing["payload_json"] == encoded:
                return {"action": "noop", **dict(existing)}
            raise WorkflowConflict("stage_result_conflict")
        now = datetime.now(timezone.utc).isoformat()
        with self.db:
            self.db.execute(
                "INSERT INTO stage_results VALUES(?,?,?,?,?)",
                (run_id, stage, status, encoded, now),
            )
            self.db.execute(
                "UPDATE daily_runs SET status=?,updated_at=? WHERE run_id=?",
                (WAITING, now, run_id),
            )
        return {"action": "committed", **dict(self.db.execute(
            "SELECT * FROM stage_results WHERE run_id=? AND stage=?", (run_id, stage)
        ).fetchone())}

    def store_items(self, run_id: str, rows: list[dict[str, Any]]) -> None:
        with self.db:
            for row in rows:
                existing = self.db.execute(
                    "SELECT * FROM workflow_items WHERE run_id=? AND item_id=?",
                    (run_id, row["item_id"]),
                ).fetchone()
                encoded = canonical(row["payload"])
                values = (row["status"], row.get("failure", ""), encoded)
                if existing:
                    if tuple(existing[key] for key in ("status", "failure", "payload_json")) != values:
                        raise WorkflowConflict("item_write_conflict")
                    continue
                self.db.execute(
                    "INSERT INTO workflow_items VALUES(?,?,?,?,?)",
                    (run_id, row["item_id"], *values),
                )

    def record_skill_diagnostic(
        self, run_id: str, stage: str, unit_id: str, skill_name: str,
        details: dict[str, Any],
    ) -> None:
        existing = self.db.execute(
            """SELECT * FROM skill_diagnostics
               WHERE run_id=? AND stage=? AND unit_id=? AND skill_name=?""",
            (run_id, stage, unit_id, skill_name),
        ).fetchone()
        if existing:
            return
        self.db.execute(
            "INSERT INTO skill_diagnostics VALUES(?,?,?,?,?,?)",
            (run_id, stage, unit_id, skill_name, canonical(details),
             datetime.now(timezone.utc).isoformat()),
        )
        self.db.commit()

    def complete(self, run_id: str, status: str, publish_key: str) -> None:
        if status not in TERMINAL:
            raise ValueError("invalid_terminal_status")
        row = self.db.execute("SELECT * FROM daily_runs WHERE run_id=?", (run_id,)).fetchone()
        if row["status"] == status and row["publish_key"] == publish_key:
            return
        now = datetime.now(timezone.utc).isoformat()
        self.db.execute(
            """UPDATE daily_runs SET status=?,publish_status='pending',publish_error='',
               publish_key=?,updated_at=? WHERE run_id=?""",
            (status, publish_key, now, run_id),
        )
        self.db.commit()

    def mark_publish(self, run_id: str, status: str, error: str = "") -> None:
        if status not in {"pending", "applied", "conflict"}:
            raise ValueError("invalid_publish_status")
        row = self.db.execute("SELECT publish_status FROM daily_runs WHERE run_id=?", (run_id,)).fetchone()
        if row and row["publish_status"] == "applied":
            return
        now = datetime.now(timezone.utc).isoformat()
        self.db.execute(
            """UPDATE daily_runs SET publish_status=?,publish_error=?,published_at=?,
               updated_at=CASE WHEN ?='applied' THEN updated_at ELSE updated_at END
               WHERE run_id=?""",
            (status, error[:500], now if status == "applied" else "", status, run_id),
        )
        self.db.commit()

    def latest_pending(self, before_date: str | None = None) -> dict[str, Any] | None:
        clause = "AND business_date<?" if before_date else ""
        params: tuple[Any, ...] = ("pending", before_date) if before_date else ("pending",)
        row = self.db.execute(
            f"""SELECT * FROM daily_runs WHERE publish_status=? AND status IN
                ('completed','completed_with_failures','completed_empty') {clause}
                ORDER BY business_date DESC,run_id DESC LIMIT 1""",
            params,
        ).fetchone()
        return dict(row) if row else None

    def read_run(self, run_id: str) -> dict[str, Any]:
        run = self.db.execute("SELECT * FROM daily_runs WHERE run_id=?", (run_id,)).fetchone()
        if not run:
            raise KeyError("run_not_found")
        stages = [dict(row) for row in self.db.execute(
            """SELECT * FROM stage_results WHERE run_id=?
               ORDER BY CASE stage WHEN 'collection_enrichment' THEN 1
               WHEN 'editorial' THEN 2 ELSE 3 END""", (run_id,)
        )]
        items = [dict(row) for row in self.db.execute(
            "SELECT * FROM workflow_items WHERE run_id=? ORDER BY item_id", (run_id,)
        )]
        skills = [dict(row) for row in self.db.execute(
            "SELECT * FROM skill_diagnostics WHERE run_id=? ORDER BY stage,unit_id,skill_name",
            (run_id,),
        )]
        return {"run": dict(run), "stages": stages, "items": items,
                "skill_diagnostics": skills}
