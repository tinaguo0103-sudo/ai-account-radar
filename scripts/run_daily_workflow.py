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

from active_skill_executor import ACTIVE_ROOT, file_hash, invoke
from daily_workflow import DailyWorkflow, WorkflowConflict, digest
from douyin_video_understanding import materialize as materialize_video_understanding
from douyin_video_understanding import merge_candidates as merge_video_candidates

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "output/state/daily_workflow.sqlite3"
ACTIVE_SKILLS = (
    "ai-account-editorial-director",
    "austin-no-overtime-scripting",
    "austin-voice-scriptwriter",
)


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


def editorial_input(collection: dict[str, Any], understanding: dict[str, Any]) -> dict[str, Any]:
    return {
        "task": "editorial_selection",
        "run_id": collection["run_id"],
        "business_date": collection["business_date"],
        "candidates": collection["candidates"],
        "video_understanding": understanding,
        "output_contract": {
            "run_id": "exact input run_id",
            "topics": [{"candidate_id": "stable candidate identity", "decision": "select|observe|reject",
                        "title": "string", "hook": "string", "structure": "string",
                        "selection_reason": "string"}],
        },
    }


def run_video_understanding(
    args: argparse.Namespace, on_demand_ids: set[str] | None = None,
) -> dict[str, Any]:
    bindings = [args.video_candidates, args.video_decisions, args.video_packages, args.video_policy]
    if not any(bindings):
        return {
            "run_id": args.run_id, "business_date": args.business_date,
            "status": "completed_empty", "reason": "no_douyin_video_understanding_input",
            "understanding_results": [], "understanding_failures": [],
            "completed_count": 0, "failed_count": 0, "substitute_count": 0,
        }
    if not all(bindings):
        raise RuntimeError("video_understanding_binding_incomplete")
    policy = json.loads(Path(args.video_policy).read_text())
    candidates = merge_video_candidates(
        json.loads(Path(args.video_candidates).read_text()), args.run_id
    )
    result = materialize_video_understanding(
        run_id=args.run_id, business_date=args.business_date,
        candidates=candidates,
        decisions=json.loads(Path(args.video_decisions).read_text()),
        packages=json.loads(Path(args.video_packages).read_text()),
        policy=policy, output_root=Path(args.artifact_root),
        on_demand_ids=on_demand_ids or set(),
    )
    result["status"] = (
        "completed_with_failures" if result["failed_count"]
        else "completed" if result["completed_count"] else "completed_empty"
    )
    return result


def editorial_on_demand_ids(
    selected: list[dict[str, Any]],
    automatic_understanding: dict[str, Any],
    candidate_batches: list[list[dict[str, Any]]],
) -> set[str]:
    automatic_ids = {
        str(row.get("candidate_id") or "")
        for row in automatic_understanding.get("understanding_results", [])
    }
    candidate_ids = {
        f"douyin:{row.get('aweme_id')}"
        for batch in candidate_batches for row in batch
    }
    return {
        str(row.get("candidate_id") or "") for row in selected
        if str(row.get("candidate_id") or "") in candidate_ids
        and str(row.get("candidate_id") or "") not in automatic_ids
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


def contract_identity(args: argparse.Namespace) -> str:
    skills = []
    for name in ACTIVE_SKILLS:
        path = ACTIVE_ROOT / name / "SKILL.md"
        if not path.is_file():
            raise RuntimeError(f"active_skill_missing:{name}")
        skills.append({"name": name, "path": str(path), "sha256": file_hash(path)})
    fixture_hash = ""
    if args.collection_fixture:
        fixture = Path(args.collection_fixture).resolve()
        fixture_hash = hashlib.sha256(fixture.read_bytes()).hexdigest()
    video_inputs = {}
    for key in ("video_candidates", "video_decisions", "video_packages", "video_policy"):
        value = getattr(args, key, "")
        if value:
            path = Path(value).resolve()
            video_inputs[key] = hashlib.sha256(path.read_bytes()).hexdigest()
    return digest({
        "run_id": args.run_id, "business_date": args.business_date,
        "source_revision": args.source_revision,
        "source_db": str(Path(args.source_db).resolve()),
        "collection_fixture_sha256": fixture_hash,
        "publisher_url": args.publisher_url.rstrip("/"),
        "publisher_identity": args.publisher_identity,
        "video_inputs": video_inputs,
        "video_on_demand": sorted(args.video_on_demand),
        "skills": skills,
    })


def validate_runtime(args: argparse.Namespace) -> None:
    if not args.publisher_url:
        raise RuntimeError("publisher_url_missing")
    if not args.publisher_identity:
        raise RuntimeError("publisher_identity_missing")
    if not os.environ.get("WEBSITE_PROJECTION_BEARER", "").strip():
        raise RuntimeError("website_projection_bearer_missing")
    if not os.environ.get("WEBSITE_PROJECTION_SIWC_BYPASS_BEARER", "").strip():
        raise RuntimeError("website_projection_machine_access_bearer_missing")


def publish(args: argparse.Namespace, stage_payload: dict[str, Any], revision: int) -> tuple[str, str]:
    env = os.environ.copy()
    command = [
        sys.executable, str(ROOT / "scripts/publish_website_projection.py"),
        "--repo", str(ROOT), "--run-id", args.run_id, "--stage", stage_payload["stage"],
        "--revision", str(revision), "--website-url", args.publisher_url,
        "--workflow-db", args.workflow_db,
        "--authority-identity", args.publisher_identity,
    ]
    result = subprocess.run(command, text=True, capture_output=True, env=env)
    try:
        detail = last_json_object(result.stdout)
    except RuntimeError:
        detail = {"ok": False, "error": f"publisher_exit_{result.returncode}"}
    if result.returncode == 0 and detail.get("ok", True):
        return "applied", json.dumps(detail, ensure_ascii=False, sort_keys=True)
    error = str(detail.get("error") or f"publisher_exit_{result.returncode}")
    return ("pending" if error == "website_projection_transport_unavailable" else "conflict",
            json.dumps(detail, ensure_ascii=False, sort_keys=True))


def publish_committed_stage(args: argparse.Namespace, workflow: DailyWorkflow,
                            stage: str, payload: dict[str, Any],
                            committed: dict[str, Any]) -> bool:
    revision = int(committed["revision"])
    prior = workflow.projection(args.run_id, stage, revision)
    if prior and prior["status"] == "applied":
        return True
    status, detail = publish(args, {"stage": stage, **payload}, revision)
    workflow.record_projection(
        args.run_id, stage, revision, committed["output_hash"], status, detail,
    )
    return status == "applied"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--business-date", required=True)
    parser.add_argument("--source-db", default=str(ROOT / "output/state/source_control.sqlite3"))
    parser.add_argument("--workflow-db", default=str(DEFAULT_DB))
    parser.add_argument("--source-revision", type=int, required=True)
    parser.add_argument("--collection-fixture", default="")
    parser.add_argument("--publisher-url", default="")
    parser.add_argument("--publisher-identity", default="")
    parser.add_argument("--artifact-root", default=str(ROOT / "output/runs"))
    parser.add_argument("--video-candidates", default="")
    parser.add_argument("--video-decisions", default="")
    parser.add_argument("--video-packages", default="")
    parser.add_argument("--video-policy", default="")
    parser.add_argument("--video-on-demand", action="append", default=[])
    args = parser.parse_args()
    DailyWorkflow.validate_identity(args.run_id, args.business_date)
    validate_runtime(args)
    exact_contract = contract_identity(args)
    workflow = DailyWorkflow(args.workflow_db)
    begin_action = workflow.begin(
        args.run_id, args.business_date, args.source_revision, exact_contract,
    )
    if begin_action == "completed_replay":
        completed = workflow.read_run(args.run_id)
        unresolved = []
        for stage_row in completed["stages"]:
            stage = str(stage_row["stage"])
            payload = json.loads(stage_row["payload_json"])
            if not publish_committed_stage(args, workflow, stage, payload, stage_row):
                unresolved.append(stage)
        result = workflow.read_run(args.run_id)
        result["action"] = "noop" if not unresolved else "projection_unresolved"
        result["unresolved_stages"] = unresolved
        print(json.dumps(result, ensure_ascii=False))
        return 0 if not unresolved else 2

    collection = run_collection(args)
    collection_commit = workflow.commit_stage(
        args.run_id, "collection", digest({"source_revision": args.source_revision}),
        collection, "completed_empty" if not collection["content_items"] else collection.get("status", "completed"),
    )
    projection_green = publish_committed_stage(
        args, workflow, "collection", collection, collection_commit,
    )
    if not collection["content_items"]:
        result = workflow.read_run(args.run_id)
        result["action"] = "completed_empty" if projection_green else "projection_unresolved"
        print(json.dumps(result, ensure_ascii=False))
        return 0 if projection_green else 2

    existing_understanding = workflow.stage(args.run_id, "video_understanding")
    existing_editorial = workflow.stage(args.run_id, "editorial")
    if existing_editorial:
        if not existing_understanding:
            raise RuntimeError("editorial_without_video_understanding")
        understanding = existing_understanding["payload"]
        understanding_commit = existing_understanding
        editorial_payload = existing_editorial["payload"]
        editorial_commit = existing_editorial
    else:
        automatic_understanding = (
            existing_understanding["payload"] if existing_understanding
            else run_video_understanding(args)
        )
        editorial_payload, editorial_skills = invoke(
            ["ai-account-editorial-director"],
            editorial_input(collection, automatic_understanding),
        )
        selected = [row for row in editorial_payload.get("topics", []) if row.get("decision") == "select"]
        editorial_on_demand = editorial_on_demand_ids(
            selected,
            automatic_understanding,
            json.loads(Path(args.video_candidates).read_text())
            if args.video_candidates else [],
        )
        requested_on_demand = set(args.video_on_demand)
        if existing_understanding:
            understanding = existing_understanding["payload"]
            understanding_commit = existing_understanding
        else:
            understanding = run_video_understanding(
                args, editorial_on_demand | requested_on_demand,
            )
            understanding["editorial_on_demand_ids"] = sorted(editorial_on_demand)
            understanding_commit = workflow.commit_stage(
                args.run_id, "video_understanding", collection_commit["output_hash"],
                understanding, understanding["status"],
            )
        for identity in editorial_skills:
            workflow.record_skill(
                run_id=args.run_id, stage="editorial", unit_id="daily",
                attempt=1, skill_name=identity["name"], skill_path=identity["path"],
                skill_hash=identity["sha256"],
                input_hash=digest(editorial_input(collection, understanding)),
                output_hash=digest(editorial_payload), status="completed",
            )
        editorial_payload["selected_count"] = len(selected)
        editorial_commit = workflow.commit_stage(
            args.run_id, "editorial", understanding_commit["output_hash"],
            editorial_payload, "completed",
        )
    understanding_projection_green = publish_committed_stage(
        args, workflow, "video_understanding", understanding, understanding_commit,
    )
    selected = [row for row in editorial_payload.get("topics", []) if row.get("decision") == "select"]
    editorial_projection_green = publish_committed_stage(
        args, workflow, "editorial", editorial_payload, editorial_commit,
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
    scripts_projection_green = publish_committed_stage(
        args, workflow, "scripts", scripts_payload, script_commit,
    )
    result = workflow.read_run(args.run_id)
    unresolved = [
        stage for stage, green in (
            ("collection", projection_green),
            ("video_understanding", understanding_projection_green),
            ("editorial", editorial_projection_green),
            ("scripts", scripts_projection_green),
        ) if not green
    ]
    result["action"] = "completed" if not unresolved else "projection_unresolved"
    result["unresolved_stages"] = unresolved
    print(json.dumps(result, ensure_ascii=False))
    return 0 if not unresolved else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, WorkflowConflict, ValueError, json.JSONDecodeError) as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False, sort_keys=True))
        raise SystemExit(2)
