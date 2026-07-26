#!/usr/bin/env python3
"""Public single-schedule daily workflow entrypoint."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from active_skill_executor import invoke
from daily_workflow import DailyWorkflow, digest

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "output/state/daily_workflow.sqlite3"


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def last_json_object(text: str) -> dict[str, Any]:
    decoder = json.JSONDecoder()
    parsed: list[dict[str, Any]] = []
    for index, character in enumerate(text):
        if character != "{":
            continue
        try:
            value, _ = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            parsed.append(value)
    if not parsed:
        raise RuntimeError("collection_result_json_missing")
    return parsed[-1]


def run_collection(args: argparse.Namespace) -> dict[str, Any]:
    if args.collection_fixture:
        payload = json.loads(Path(args.collection_fixture).read_text(encoding="utf-8"))
        if payload.get("run_id") != args.run_id:
            raise RuntimeError("collection_fixture_wrong_run")
        return payload
    command = [
        sys.executable, str(ROOT / "scripts/daily_pipeline.py"),
        "--run-id", args.run_id, "--source-db", args.source_db,
        "--defer-editorial", "--no-feishu-runtime",
    ]
    result = subprocess.run(command, text=True, capture_output=True)
    if result.returncode != 0:
        raise RuntimeError(f"collection_failed:{result.returncode}")
    payload = last_json_object(result.stdout)
    run_dir = Path(payload["run_output_dir"])
    return {
        "run_id": args.run_id,
        "business_date": args.business_date,
        "status": payload.get("collection_status", "completed"),
        "content_items": read_csv(run_dir / "content_items.csv"),
        "candidates": read_csv(run_dir / "today_10_topics.csv"),
        "source_runs": payload.get("source_outcomes", []),
    }


def editorial_input(collection: dict[str, Any]) -> dict[str, Any]:
    return {
        "task": "editorial_selection",
        "run_id": collection["run_id"],
        "business_date": collection["business_date"],
        "candidates": collection["candidates"],
        "output_contract": {
            "run_id": "exact input run_id",
            "topics": [{"candidate_id": "stable candidate identity", "decision": "select|observe|reject",
                        "title": "string", "hook": "string", "structure": "string",
                        "selection_reason": "string"}],
        },
    }


def script_input(run_id: str, topic: dict[str, Any], content: dict[str, Any]) -> dict[str, Any]:
    return {
        "task": "complete_spoken_script",
        "run_id": run_id, "topic": topic, "source_content": content,
        "output_contract": {"run_id": run_id, "topic_id": topic["candidate_id"],
                            "title": "string", "hook": "string", "structure": "string",
                            "body": "complete spoken Chinese script"},
    }


def write_script_artifact(root: Path, run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    topic_id = str(payload["topic_id"])
    safe_id = hashlib.sha256(topic_id.encode()).hexdigest()[:20]
    target = root / run_id / "scripts" / f"{safe_id}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    text = (
        f"# {payload.get('title', '')}\n\n"
        f"## 钩子\n\n{payload.get('hook', '')}\n\n"
        f"## 结构\n\n{payload.get('structure', '')}\n\n"
        f"## 完整口播稿\n\n{payload.get('body', '')}\n"
    )
    temporary = target.with_suffix(".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(target)
    value = dict(payload)
    value["artifact_path"] = str(target)
    value["artifact_sha256"] = hashlib.sha256(target.read_bytes()).hexdigest()
    return value


def publish(args: argparse.Namespace, stage_payload: dict[str, Any], revision: int) -> tuple[str, str]:
    if not args.publisher_url:
        return "pending", "publisher_not_configured"
    env = os.environ.copy()
    command = [
        sys.executable, str(ROOT / "scripts/publish_website_projection.py"),
        "--repo", str(ROOT), "--run-id", args.run_id, "--stage", stage_payload["stage"],
        "--revision", str(revision), "--website-url", args.publisher_url,
        "--workflow-db", args.workflow_db,
        "--authority-identity", f"daily_workflow.sqlite3:{args.source_revision}",
    ]
    result = subprocess.run(command, text=True, capture_output=True, env=env)
    return ("applied", result.stdout[-500:]) if result.returncode == 0 else ("pending", f"publisher_exit_{result.returncode}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--business-date", required=True)
    parser.add_argument("--source-db", default=str(ROOT / "output/state/source_control.sqlite3"))
    parser.add_argument("--workflow-db", default=str(DEFAULT_DB))
    parser.add_argument("--source-revision", type=int, required=True)
    parser.add_argument("--collection-fixture", default="")
    parser.add_argument("--publisher-url", default="")
    parser.add_argument("--artifact-root", default=str(ROOT / "output/runs"))
    args = parser.parse_args()
    DailyWorkflow.validate_identity(args.run_id, args.business_date)
    workflow = DailyWorkflow(args.workflow_db)
    workflow.begin(args.run_id, args.business_date, args.source_revision)

    collection = run_collection(args)
    collection_commit = workflow.commit_stage(
        args.run_id, "collection", digest({"source_revision": args.source_revision}),
        collection, "completed_empty" if not collection["content_items"] else collection.get("status", "completed"),
    )
    projection_status, projection_detail = publish(
        args, {"stage": "collection", **collection}, int(collection_commit["revision"])
    )
    workflow.record_projection(
        args.run_id, "collection", int(collection_commit["revision"]),
        collection_commit["output_hash"], projection_status, projection_detail,
    )
    if not collection["content_items"]:
        print(json.dumps(workflow.read_run(args.run_id), ensure_ascii=False))
        return 0

    existing_editorial = workflow.stage(args.run_id, "editorial")
    if existing_editorial:
        editorial_payload = existing_editorial["payload"]
        editorial_commit = existing_editorial
    else:
        editorial_payload, editorial_skills = invoke(
            ["ai-account-editorial-director"], editorial_input(collection)
        )
        selected = [row for row in editorial_payload.get("topics", []) if row.get("decision") == "select"]
        for identity in editorial_skills:
            workflow.record_skill(
                run_id=args.run_id, stage="editorial", unit_id="daily",
                attempt=1, skill_name=identity["name"], skill_path=identity["path"],
                skill_hash=identity["sha256"], input_hash=digest(editorial_input(collection)),
                output_hash=digest(editorial_payload), status="completed",
            )
        editorial_payload["selected_count"] = len(selected)
        editorial_commit = workflow.commit_stage(
            args.run_id, "editorial", collection_commit["output_hash"], editorial_payload, "completed",
        )
    selected = [row for row in editorial_payload.get("topics", []) if row.get("decision") == "select"]
    projection_status, projection_detail = publish(
        args, {"stage": "editorial", **editorial_payload}, int(editorial_commit["revision"])
    )
    workflow.record_projection(
        args.run_id, "editorial", int(editorial_commit["revision"]),
        editorial_commit["output_hash"], projection_status, projection_detail,
    )

    existing_scripts = workflow.stage(args.run_id, "scripts")
    if existing_scripts:
        scripts_payload = existing_scripts["payload"]
        script_commit = existing_scripts
    else:
        contents = {str(row.get("content_fingerprint") or row.get("id")): row for row in collection["content_items"]}
        scripts, failures = [], []
        for topic in selected:
            unit = str(topic["candidate_id"])
            source = contents.get(unit, {})
            try:
                payload, identities = invoke(
                    ["austin-no-overtime-scripting", "austin-voice-scriptwriter"],
                    script_input(args.run_id, topic, source),
                )
                payload = write_script_artifact(Path(args.artifact_root), args.run_id, payload)
                scripts.append(payload)
                for identity in identities:
                    workflow.record_skill(
                        run_id=args.run_id, stage="scripts", unit_id=unit, attempt=1,
                        skill_name=identity["name"], skill_path=identity["path"],
                        skill_hash=identity["sha256"], input_hash=digest(script_input(args.run_id, topic, source)),
                        output_hash=digest(payload), status="completed",
                    )
            except Exception as error:
                failures.append({"topic_id": unit, "reason": str(error)})
        scripts_payload = {"run_id": args.run_id, "scripts": scripts, "failures": failures}
        script_commit = workflow.commit_stage(
            args.run_id, "scripts", editorial_commit["output_hash"], scripts_payload,
            "completed_with_failures" if failures else "completed",
        )
    projection_status, projection_detail = publish(
        args, {"stage": "scripts", **scripts_payload}, int(script_commit["revision"])
    )
    workflow.record_projection(
        args.run_id, "scripts", int(script_commit["revision"]),
        script_commit["output_hash"], projection_status, projection_detail,
    )
    print(json.dumps(workflow.read_run(args.run_id), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
