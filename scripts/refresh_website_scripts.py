#!/usr/bin/env python3
"""Conditionally refresh one terminal run's scripts without mutating local authority."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import urllib.parse
from pathlib import Path
from typing import Any, Callable, Optional

from daily_workflow import DailyWorkflow, TERMINAL
from publish_website_projection import ProjectionError, build_workflow_projection, request_json
from website_publisher_client import load_config

SCRIPT_KEYS = {"topic_id", "title", "hook", "structure", "body"}
FAILURE_KEYS = {"topic_id", "reason"}
CONTENT_BUSINESS_KEYS = (
    "id", "run_id", "source", "account", "title", "summary", "source_url",
    "published_at", "collected_at", "selected", "topic_id", "script_id",
)
TOPIC_BUSINESS_KEYS = (
    "id", "run_id", "content_id", "title", "source", "brief", "status",
    "selection_reason", "hook", "content_structure", "source_url",
    "generation_status", "generation_error", "trend_event_id", "sources",
    "cluster_synthesis", "review_stage", "traffic_opportunity", "persona_stability",
    "differentiation", "script_id", "source_title", "source_body", "business_date",
)
RequestFn = Callable[[str, str, Optional[dict[str, Any]]], dict[str, Any]]


class ScriptRefreshPostWriteError(ProjectionError):
    """A successful POST was followed by an untrusted authoritative readback."""

    def __init__(self, reason: str, ledger: dict[str, int]):
        super().__init__(f"script_refresh_post_write_unknown:{reason}")
        self.request_ledger = dict(ledger)


class ScriptRefreshPreconditionError(ProjectionError):
    """A Website precondition failed before any POST was attempted."""

    def __init__(self, reason: str, ledger: dict[str, int]):
        super().__init__(reason)
        self.write_state = "not_attempted"
        self.request_ledger = dict(ledger)


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ProjectionError("script_refresh_artifact_invalid") from error
    if not isinstance(value, dict):
        raise ProjectionError("script_refresh_artifact_invalid")
    return value


def authority_snapshot(db_path: Path, run_id: str, business_date: str) -> dict[str, Any]:
    DailyWorkflow.validate_identity(run_id, business_date)
    if not db_path.is_file():
        raise ProjectionError("script_refresh_authority_missing")
    try:
        database = sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True)
        database.row_factory = sqlite3.Row
        run = database.execute("SELECT * FROM daily_runs WHERE run_id=?", (run_id,)).fetchone()
        if not run:
            raise ProjectionError("script_refresh_run_missing")
        if run["business_date"] != business_date:
            raise ProjectionError("script_refresh_run_date_conflict")
        if run["status"] not in TERMINAL:
            raise ProjectionError("script_refresh_run_not_terminal")
        rows = database.execute(
            "SELECT stage,payload_json FROM stage_results WHERE run_id=? ORDER BY stage", (run_id,),
        ).fetchall()
        payloads = {row["stage"]: json.loads(row["payload_json"]) for row in rows}
    except (sqlite3.Error, json.JSONDecodeError) as error:
        raise ProjectionError("script_refresh_authority_unreadable") from error
    finally:
        if "database" in locals():
            database.close()
    if set(payloads) != {"collection_enrichment", "editorial", "scripts"}:
        raise ProjectionError("script_refresh_terminal_stages_incomplete")
    topics = payloads["editorial"].get("topics")
    if not isinstance(topics, list):
        raise ProjectionError("script_refresh_editorial_invalid")
    selected = [str(row.get("candidate_id") or "") for row in topics if row.get("decision") == "select"]
    if any(not value for value in selected) or len(set(selected)) != len(selected):
        raise ProjectionError("script_refresh_selected_identity_conflict")
    return {"run": dict(run), "selected": selected, "payloads": payloads}


def validate_override(
    artifact: dict[str, Any], run_id: str, selected: list[str],
) -> dict[str, Any]:
    if set(artifact) != {"run_id", "scripts", "failures"} or artifact.get("run_id") != run_id:
        raise ProjectionError("script_refresh_artifact_run_conflict")
    scripts = artifact.get("scripts")
    failures = artifact.get("failures")
    if not isinstance(scripts, list) or not isinstance(failures, list):
        raise ProjectionError("script_refresh_artifact_schema_invalid")
    seen: set[str] = set()
    for row in scripts:
        if not isinstance(row, dict) or set(row) != SCRIPT_KEYS:
            raise ProjectionError("script_refresh_artifact_schema_invalid")
        identity = str(row.get("topic_id") or "")
        if not identity or identity in seen or any(not str(row.get(key) or "") for key in SCRIPT_KEYS):
            raise ProjectionError("script_refresh_script_identity_conflict")
        seen.add(identity)
    failed: set[str] = set()
    for row in failures:
        if not isinstance(row, dict) or set(row) != FAILURE_KEYS:
            raise ProjectionError("script_refresh_artifact_schema_invalid")
        identity = str(row.get("topic_id") or "")
        if not identity or identity in seen or identity in failed or not str(row.get("reason") or ""):
            raise ProjectionError("script_refresh_script_identity_conflict")
        failed.add(identity)
    if seen | failed != set(selected):
        raise ProjectionError("script_refresh_selected_coverage_conflict")
    return {"run_id": run_id, "scripts": scripts, "failures": failures}


def canonical_rows(rows: list[dict[str, Any]]) -> str:
    return json.dumps(sorted(rows, key=lambda row: (str(row.get("run_id") or ""), str(row.get("id") or ""))),
                      ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def read_pages(
    base_url: str, resource: str, request_fn: RequestFn, *, run_id: str | None = None,
    max_pages: int = 100,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for page in range(1, max_pages + 1):
        query = {"page": str(page)}
        if run_id:
            query["run_id"] = run_id
        else:
            query["range"] = "all"
        if resource == "topics":
            query["view"] = "ledger"
        value = request_fn("GET", f"{base_url}/api/{resource}?{urllib.parse.urlencode(query)}", None)
        key = "items" if resource == "content" else resource
        batch = value.get(key)
        meta = value.get("page")
        if not isinstance(batch, list) or not isinstance(meta, dict):
            raise ProjectionError("script_refresh_business_readback_invalid")
        rows.extend(batch)
        total_pages = int(meta.get("total_pages") or 0)
        if total_pages < 1 or page > total_pages:
            raise ProjectionError("script_refresh_business_readback_invalid")
        if page == total_pages:
            if len(rows) != int(meta.get("total") or -1):
                raise ProjectionError("script_refresh_business_readback_invalid")
            return rows
    raise ProjectionError("script_refresh_business_readback_bound_exceeded")


def expected_script_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return [{
        "id": row["id"], "run_id": row["run_id"], "topic_id": row["topic_id"],
        "script_version": row["script_version"], "title": row["title"], "hook": row["hook"],
        "content_structure": row["content_structure"], "body": row["body"],
        "current_revision_number": row["current_revision_number"], "saved_at": row["saved_at"],
    } for row in payload["scripts"]]


def script_semantics(rows: list[dict[str, Any]]) -> str:
    keys = ("id", "run_id", "topic_id", "script_version", "title", "hook", "content_structure",
            "body", "current_revision_number", "saved_at")
    return canonical_rows([{key: row.get(key) for key in keys} for row in rows])


def normalize_semantic(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: normalize_semantic(item) for key, item in value.items()}
    if isinstance(value, list):
        return [normalize_semantic(item) for item in value]
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def semantic_rows(rows: list[dict[str, Any]], keys: tuple[str, ...]) -> str:
    return canonical_rows([
        normalize_semantic({key: row.get(key) for key in keys}) for row in rows
    ])


def expected_business_rows(payload: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    selected_topics = {
        str(row["content_id"]): row for row in payload["topics"]
        if row.get("status") in {"select", "selected"}
    }
    scripts_by_topic = {str(row["topic_id"]): row for row in payload["scripts"]}
    content = []
    for row in payload["collected_items"]:
        topic = selected_topics.get(str(row["id"]))
        script = scripts_by_topic.get(str(topic["id"])) if topic else None
        content.append({
            **{key: row.get(key) for key in CONTENT_BUSINESS_KEYS},
            "selected": 1 if topic else 0,
            "topic_id": topic.get("id") if topic else None,
            "script_id": script.get("id") if script else None,
        })
    content_by_id = {str(row["id"]): row for row in payload["collected_items"]}
    topics = []
    for row in payload["topics"]:
        source = content_by_id.get(str(row["content_id"]), {})
        script = scripts_by_topic.get(str(row["id"]))
        cluster = row.get("cluster_synthesis") if isinstance(row.get("cluster_synthesis"), dict) else {}
        topics.append({
            **{key: row.get(key) for key in TOPIC_BUSINESS_KEYS},
            "review_stage": cluster.get("review_stage") or None,
            "script_id": script.get("id") if script else None,
            "source_title": source.get("title"),
            "source_body": source.get("body"),
            "business_date": payload["business_date"],
        })
    return {"content": content, "topics": topics}


def assert_projection_precondition(
    payload: dict[str, Any], target: dict[str, list[dict[str, Any]]],
) -> None:
    expected = expected_business_rows(payload)
    expected_content = {str(row["id"]) for row in payload["collected_items"]}
    actual_content = {str(row.get("id") or "") for row in target["content"]}
    if len(actual_content) != len(target["content"]) or actual_content != expected_content:
        raise ProjectionError("script_refresh_content_precondition_drift")
    if semantic_rows(target["content"], CONTENT_BUSINESS_KEYS) != semantic_rows(
        expected["content"], CONTENT_BUSINESS_KEYS,
    ):
        raise ProjectionError("script_refresh_content_precondition_drift")
    expected_topics = {str(row["id"]) for row in payload["topics"]}
    actual_topics = {str(row.get("id") or "") for row in target["topics"]}
    if len(actual_topics) != len(target["topics"]) or actual_topics != expected_topics:
        raise ProjectionError("script_refresh_topic_precondition_drift")
    if semantic_rows(target["topics"], TOPIC_BUSINESS_KEYS) != semantic_rows(
        expected["topics"], TOPIC_BUSINESS_KEYS,
    ):
        raise ProjectionError("script_refresh_topic_precondition_drift")


def run_refresh(
    db_path: Path, run_id: str, business_date: str, scripts_path: Path,
    *, config: dict[str, str] | None = None, request_fn: RequestFn = request_json,
) -> dict[str, Any]:
    authority = authority_snapshot(db_path, run_id, business_date)
    override = validate_override(read_json(scripts_path), run_id, authority["selected"])
    settings = config or load_config()
    payload = build_workflow_projection(
        db_path, run_id, settings["authority_identity"], scripts_override=override,
    )
    base = settings["website_url"].rstrip("/")
    endpoint = base + "/api/business-projection"
    previous_app = os.environ.get("WEBSITE_PROJECTION_BEARER")
    previous_sites = os.environ.get("WEBSITE_PROJECTION_SIWC_BYPASS_BEARER")
    os.environ["WEBSITE_PROJECTION_BEARER"] = settings["app_bearer"]
    os.environ["WEBSITE_PROJECTION_SIWC_BYPASS_BEARER"] = settings["sites_bearer"]
    ledger = {"precondition_get": 0, "business_get": 0, "terminal_post": 0, "readback_get": 0}
    try:
        ledger["precondition_get"] += 1
        existing = request_fn("GET", f"{endpoint}?run_id={run_id}", None)
        expected_counts = {"content": len(payload["collected_items"]), "topics": len(payload["topics"]),
                           "scripts": len(payload["scripts"])}
        if (existing.get("run_id") != run_id or existing.get("business_date") != business_date
                or existing.get("run_status") != payload["run"]["status"]
                or existing.get("authority_identity") != settings["authority_identity"]
                or existing.get("counts") != expected_counts):
            raise ScriptRefreshPreconditionError(
                "script_refresh_projection_precondition_mismatch", ledger,
            )
        before = {name: read_pages(base, name, request_fn) for name in ("content", "topics", "scripts")}
        ledger["business_get"] += 3
        target_before = {name: [row for row in rows if row.get("run_id") == run_id] for name, rows in before.items()}
        history_before = {name: [row for row in rows if row.get("run_id") != run_id] for name, rows in before.items()}
        try:
            assert_projection_precondition(payload, target_before)
        except ProjectionError as error:
            raise ScriptRefreshPreconditionError(str(error), ledger) from error
        if script_semantics(target_before["scripts"]) == script_semantics(expected_script_rows(payload)):
            return {"action": "noop", "request_ledger": ledger, "readback": existing, "payload": payload}
        payload["refresh_precondition"] = {
            "business_date": existing.get("business_date"),
            "authority_identity": existing.get("authority_identity"),
            "projected_at": existing.get("projected_at"),
        }
        ledger["terminal_post"] += 1
        result = request_fn("POST", endpoint, payload)
        ledger["readback_get"] += 1
        readback = request_fn("GET", f"{endpoint}?run_id={run_id}", None)
        after = {name: read_pages(base, name, request_fn) for name in ("content", "topics", "scripts")}
        ledger["business_get"] += 3
    finally:
        if previous_app is None:
            os.environ.pop("WEBSITE_PROJECTION_BEARER", None)
        else:
            os.environ["WEBSITE_PROJECTION_BEARER"] = previous_app
        if previous_sites is None:
            os.environ.pop("WEBSITE_PROJECTION_SIWC_BYPASS_BEARER", None)
        else:
            os.environ["WEBSITE_PROJECTION_SIWC_BYPASS_BEARER"] = previous_sites
    if (readback.get("run_id") != run_id or readback.get("business_date") != business_date
            or readback.get("run_status") != payload["run"]["status"]
            or readback.get("counts") != expected_counts):
        raise ScriptRefreshPostWriteError("projection_readback_mismatch", ledger)
    target_after = {name: [row for row in rows if row.get("run_id") == run_id] for name, rows in after.items()}
    history_after = {name: [row for row in rows if row.get("run_id") != run_id] for name, rows in after.items()}
    if canonical_rows(target_before["content"]) != canonical_rows(target_after["content"]):
        raise ScriptRefreshPostWriteError("content_mismatch", ledger)
    if canonical_rows(target_before["topics"]) != canonical_rows(target_after["topics"]):
        raise ScriptRefreshPostWriteError("topic_mismatch", ledger)
    if any(canonical_rows(history_before[name]) != canonical_rows(history_after[name]) for name in history_before):
        raise ScriptRefreshPostWriteError("historical_run_mismatch", ledger)
    if script_semantics(target_after["scripts"]) != script_semantics(expected_script_rows(payload)):
        raise ScriptRefreshPostWriteError("script_readback_mismatch", ledger)
    return {"action": "refreshed", "result": result, "readback": readback,
            "request_ledger": ledger, "payload": payload}


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description="Refresh scripts for one exact terminal Website projection.")
    value.add_argument("--workflow-db", required=True, type=Path)
    value.add_argument("--run-id", required=True)
    value.add_argument("--business-date", required=True)
    value.add_argument("--scripts-result", required=True, type=Path)
    return value


def main() -> int:
    args = parser().parse_args()
    try:
        result = run_refresh(args.workflow_db, args.run_id, args.business_date, args.scripts_result)
    except (ProjectionError, ValueError) as error:
        output: dict[str, Any] = {"ok": False, "error": str(error)}
        if isinstance(error, ScriptRefreshPostWriteError):
            output["write_state"] = "unknown"
            output["request_ledger"] = error.request_ledger
        elif isinstance(error, ScriptRefreshPreconditionError):
            output["write_state"] = error.write_state
            output["request_ledger"] = error.request_ledger
        print(json.dumps(output, ensure_ascii=False))
        return 1
    print(json.dumps({"ok": True, "action": result["action"],
                      "request_ledger": result["request_ledger"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
