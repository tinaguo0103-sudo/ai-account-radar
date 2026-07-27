#!/usr/bin/env python3
"""SQLite authority for the single daily collection/editorial/scripts workflow."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 2
STAGES = ("collection", "video_understanding", "editorial", "scripts")


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


class WorkflowConflict(RuntimeError):
    pass


class DailyWorkflow:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self._migrate()

    def _migrate(self) -> None:
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS workflow_meta(
          key TEXT PRIMARY KEY, value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS runs(
          run_id TEXT PRIMARY KEY, business_date TEXT NOT NULL,
          status TEXT NOT NULL, source_revision INTEGER NOT NULL,
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
          contract_hash TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS stages(
          run_id TEXT NOT NULL REFERENCES runs(run_id), stage TEXT NOT NULL,
          revision INTEGER NOT NULL, status TEXT NOT NULL,
          input_hash TEXT NOT NULL, output_hash TEXT NOT NULL,
          payload_json TEXT NOT NULL, committed_at TEXT NOT NULL,
          PRIMARY KEY(run_id, stage)
        );
        CREATE TABLE IF NOT EXISTS skill_attempts(
          run_id TEXT NOT NULL, stage TEXT NOT NULL, unit_id TEXT NOT NULL,
          attempt INTEGER NOT NULL, skill_name TEXT NOT NULL,
          skill_path TEXT NOT NULL, skill_hash TEXT NOT NULL,
          input_hash TEXT NOT NULL, output_hash TEXT NOT NULL,
          status TEXT NOT NULL, fallback_used INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL,
          PRIMARY KEY(run_id, stage, unit_id, attempt, skill_name)
        );
        CREATE TABLE IF NOT EXISTS projection_receipts(
          run_id TEXT NOT NULL, stage TEXT NOT NULL, revision INTEGER NOT NULL,
          payload_hash TEXT NOT NULL, status TEXT NOT NULL,
          detail TEXT NOT NULL, updated_at TEXT NOT NULL,
          PRIMARY KEY(run_id, stage, revision)
        );
        """)
        version = self.db.execute(
            "SELECT value FROM workflow_meta WHERE key='schema_version'"
        ).fetchone()
        if not version:
            self.db.execute(
                "INSERT INTO workflow_meta(key,value) VALUES('schema_version',?)",
                (str(SCHEMA_VERSION),),
            )
        elif version["value"] not in {"1", str(SCHEMA_VERSION)}:
            raise WorkflowConflict("workflow_schema_version_conflict")
        elif version["value"] == "1":
            # Existing v1 runs keep their committed three-stage revisions. Stage
            # revisions are run-scoped, so only future runs adopt the four-stage order.
            self.db.execute(
                "UPDATE workflow_meta SET value=? WHERE key='schema_version'", (str(SCHEMA_VERSION),)
            )
        columns = {row["name"] for row in self.db.execute("PRAGMA table_info(runs)")}
        if "contract_hash" not in columns:
            self.db.execute("ALTER TABLE runs ADD COLUMN contract_hash TEXT NOT NULL DEFAULT ''")
        self.db.commit()

    @staticmethod
    def validate_identity(run_id: str, business_date: str) -> None:
        if len(run_id) != 19 or not run_id.startswith("run_"):
            raise ValueError("wrong_run")
        compact = run_id[4:12]
        if business_date != f"{compact[:4]}-{compact[4:6]}-{compact[6:]}":
            raise ValueError("wrong_business_date")

    def begin(self, run_id: str, business_date: str, source_revision: int,
              contract_hash: str) -> str:
        self.validate_identity(run_id, business_date)
        row = self.db.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if row:
            if (row["business_date"] != business_date
                    or row["source_revision"] != source_revision
                    or row["contract_hash"] != contract_hash):
                raise WorkflowConflict("run_identity_conflict")
            return "completed_replay" if row["status"] in {
                "completed", "completed_with_failures", "completed_empty"
            } else "resume"
        now = datetime.now(timezone.utc).isoformat()
        self.db.execute(
            "INSERT INTO runs VALUES(?,?,?,?,?,?,?)",
            (run_id, business_date, "running", source_revision, now, now, contract_hash),
        )
        self.db.commit()
        return "new"

    def commit_stage(self, run_id: str, stage: str, input_hash: str,
                     payload: dict[str, Any], status: str) -> dict[str, Any]:
        if stage not in STAGES:
            raise ValueError("invalid_stage")
        position = STAGES.index(stage)
        if position:
            prior = self.db.execute(
                "SELECT status FROM stages WHERE run_id=? AND stage=?",
                (run_id, STAGES[position - 1]),
            ).fetchone()
            if not prior or prior["status"] not in {"completed", "completed_with_failures", "completed_empty"}:
                raise WorkflowConflict("prior_stage_not_committed")
        output_hash = digest(payload)
        existing = self.db.execute(
            "SELECT * FROM stages WHERE run_id=? AND stage=?", (run_id, stage)
        ).fetchone()
        if existing:
            if existing["input_hash"] == input_hash and existing["output_hash"] == output_hash:
                return {"action": "noop", **dict(existing)}
            raise WorkflowConflict("stage_input_or_output_conflict")
        now = datetime.now(timezone.utc).isoformat()
        revision = position + 1
        with self.db:
            self.db.execute(
                "INSERT INTO stages VALUES(?,?,?,?,?,?,?,?)",
                (run_id, stage, revision, status, input_hash, output_hash,
                 canonical(payload), now),
            )
            self.db.execute(
                "UPDATE runs SET status=?,updated_at=? WHERE run_id=?",
                (status if stage == "scripts" or (stage == "collection" and status == "completed_empty")
                 else "running", now, run_id),
            )
        readback = self.db.execute(
            "SELECT * FROM stages WHERE run_id=? AND stage=?", (run_id, stage)
        ).fetchone()
        if not readback or readback["output_hash"] != output_hash:
            raise RuntimeError("stage_readback_unknown")
        return {"action": "committed", **dict(readback)}

    def record_skill(self, *, run_id: str, stage: str, unit_id: str,
                     attempt: int, skill_name: str, skill_path: str,
                     skill_hash: str, input_hash: str, output_hash: str,
                     status: str) -> None:
        self.db.execute(
            """INSERT OR REPLACE INTO skill_attempts
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (run_id, stage, unit_id, attempt, skill_name, skill_path, skill_hash,
             input_hash, output_hash, status, 0,
             datetime.now(timezone.utc).isoformat()),
        )
        self.db.commit()

    def record_projection(self, run_id: str, stage: str, revision: int,
                          payload_hash: str, status: str, detail: str) -> None:
        existing = self.db.execute(
            "SELECT * FROM projection_receipts WHERE run_id=? AND stage=? AND revision=?",
            (run_id, stage, revision),
        ).fetchone()
        if existing and existing["status"] == "applied":
            if existing["payload_hash"] != payload_hash:
                raise WorkflowConflict("applied_projection_payload_conflict")
            return
        self.db.execute(
            """INSERT OR REPLACE INTO projection_receipts VALUES(?,?,?,?,?,?,?)""",
            (run_id, stage, revision, payload_hash, status, detail,
             datetime.now(timezone.utc).isoformat()),
        )
        self.db.commit()

    def projection(self, run_id: str, stage: str, revision: int) -> dict[str, Any] | None:
        row = self.db.execute(
            "SELECT * FROM projection_receipts WHERE run_id=? AND stage=? AND revision=?",
            (run_id, stage, revision),
        ).fetchone()
        return dict(row) if row else None

    def stage(self, run_id: str, stage: str) -> dict[str, Any] | None:
        row = self.db.execute(
            "SELECT * FROM stages WHERE run_id=? AND stage=?", (run_id, stage)
        ).fetchone()
        if not row:
            return None
        value = dict(row)
        value["payload"] = json.loads(value.pop("payload_json"))
        return value

    def read_run(self, run_id: str) -> dict[str, Any]:
        run = self.db.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if not run:
            raise KeyError("run_not_found")
        stages = [dict(row) for row in self.db.execute(
            "SELECT * FROM stages WHERE run_id=? ORDER BY revision", (run_id,)
        )]
        skills = [dict(row) for row in self.db.execute(
            "SELECT * FROM skill_attempts WHERE run_id=? ORDER BY stage,unit_id,attempt", (run_id,)
        )]
        receipts = [dict(row) for row in self.db.execute(
            "SELECT * FROM projection_receipts WHERE run_id=? ORDER BY revision", (run_id,)
        )]
        return {"run": dict(run), "stages": stages, "skill_attempts": skills,
                "projection_receipts": receipts}
