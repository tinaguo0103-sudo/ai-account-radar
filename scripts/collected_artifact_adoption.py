#!/usr/bin/env python3
"""Exact-run adapter for adopting a completed collection checkpoint."""
from __future__ import annotations

import csv
import json
from argparse import Namespace
from pathlib import Path
from typing import Any

from daily_workflow import WorkflowConflict


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def adopt_collected_artifacts(args: Namespace) -> dict[str, Any]:
    run_dir = Path(args.adopt_collected_artifacts).resolve()
    exact = Path(args.artifact_root).resolve() / args.run_id
    if run_dir != exact or run_dir.name != args.run_id:
        raise WorkflowConflict("adoption_run_path_mismatch")
    log_raw = str(args.adoption_log or "").strip()
    if not log_raw:
        raise WorkflowConflict("adoption_log_missing")
    try:
        log = read_json(Path(log_raw).resolve())
    except (OSError, ValueError, json.JSONDecodeError) as error:
        raise WorkflowConflict("adoption_log_invalid") from error
    if log.get("run_id") != args.run_id:
        raise WorkflowConflict("adoption_log_wrong_run")
    logged_dir = Path(str(log.get("run_output_dir") or "")).resolve()
    if logged_dir.name != args.run_id:
        raise WorkflowConflict("adoption_log_path_mismatch")
    status = str(log.get("collection_status") or "")
    if status not in {"completed", "completed_with_failures"}:
        raise WorkflowConflict("adoption_collection_not_completed")
    if log.get("downstream_usable") is not True:
        raise WorkflowConflict("adoption_downstream_not_usable")
    required = ("content_items.csv", "content_breakdowns.csv", "today_10_topics.csv")
    for name in required:
        path = run_dir / name
        if not path.is_file() or path.stat().st_size == 0:
            raise WorkflowConflict(f"adoption_required_csv_missing:{name}")
    rows: dict[str, list[dict[str, str]]] = {}
    for name in required:
        with (run_dir / name).open(encoding="utf-8-sig", newline="") as handle:
            rows[name] = list(csv.DictReader(handle))
    content = rows["content_items.csv"]
    breakdowns = rows["content_breakdowns.csv"]
    candidates = rows["today_10_topics.csv"]
    if not content or len(content) != len(breakdowns):
        raise WorkflowConflict("adoption_csv_identity_conflict")
    content_by_artifact_identity: dict[str, str] = {}
    for row in content:
        artifact_identity = str(row.get("内容指纹") or "").strip()
        if not artifact_identity or artifact_identity in content_by_artifact_identity:
            raise WorkflowConflict("adoption_content_identity_conflict")
        row["source_url"] = str(row.get("内容链接") or "")
        row["title"] = str(row.get("内容标题") or "")
        row["source"] = str(row.get("平台") or row.get("来源类型") or "")
        row["item_id"] = f"adopted:{artifact_identity}"
        content_by_artifact_identity[artifact_identity] = row["item_id"]
    for row in candidates:
        artifact_identity = str(row.get("内容指纹") or "").strip()
        candidate_id = content_by_artifact_identity.get(artifact_identity)
        if not candidate_id:
            raise WorkflowConflict("adoption_candidate_content_mapping_missing")
        row["source_url"] = str(row.get("来源链接") or "")
        row["title"] = str(row.get("可发布标题") or row.get("我的选题标题") or "")
        row["source"] = str(row.get("平台") or row.get("来源类型") or "")
        row["candidate_id"] = candidate_id
    return {
        "run_id": args.run_id,
        "business_date": args.business_date,
        "status": status,
        "content_items": content,
        "candidates": candidates,
        "source_runs": log.get("source_outcomes", []),
        "adoption": {
            "content_count": len(content),
            "breakdown_count": len(breakdowns),
            "candidate_count": len(candidates),
            "collection_calls": 0,
        },
    }
