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
from daily_workflow import DailyWorkflow, LEGACY_STAGES, STAGES, WorkflowConflict, digest
from douyin_video_understanding import materialize as materialize_video_understanding
from douyin_video_understanding import merge_candidates as merge_video_candidates
from douyin_video_understanding_producer import (
    produce as produce_video_understanding,
    validate_runtime as validate_video_runtime,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "output/state/daily_workflow.sqlite3"
ACTIVE_SKILLS = (
    "ai-account-editorial-director",
    "austin-no-overtime-scripting",
    "austin-voice-scriptwriter",
)
CURRENT_WORKFLOW: DailyWorkflow | None = None
CURRENT_RUN_ID = ""
CURRENT_STAGE = ""


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def last_json_object(text: str) -> dict[str, Any]:
    decoder = json.JSONDecoder()
    parsed: list[dict[str, Any]] = []
    cursor = 0
    while cursor < len(text):
        index = text.find("{", cursor)
        if index < 0:
            break
        try:
            value, end = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            cursor = index + 1
            continue
        if isinstance(value, dict):
            parsed.append(value)
        cursor = index + end
    if not parsed:
        raise RuntimeError("collection_result_json_missing")
    return parsed[-1]


def collection_from_run_dir(
    args: argparse.Namespace, run_dir: Path, payload: dict[str, Any],
) -> dict[str, Any]:
    exact_root = Path(args.artifact_root).resolve()
    exact_dir = run_dir.resolve()
    if exact_dir != exact_root / args.run_id:
        raise RuntimeError("collection_run_output_path_mismatch")
    content_path = exact_dir / "content_items.csv"
    candidates_path = exact_dir / "today_10_topics.csv"
    if not content_path.is_file() or not candidates_path.is_file():
        raise RuntimeError("collection_required_artifact_missing")
    content_items = read_csv(content_path)
    candidates = read_csv(candidates_path)
    if not content_items:
        raise RuntimeError("collection_content_artifact_empty")
    return {
        "run_id": args.run_id,
        "business_date": args.business_date,
        "status": payload.get("collection_status", "completed"),
        "content_items": content_items,
        "candidates": candidates,
        "source_runs": payload.get("source_outcomes", payload.get("isolated_source_failures", [])),
        "recovered_from_exact_artifacts": bool(args.recover_daily_log),
    }


def recover_collection(args: argparse.Namespace) -> dict[str, Any]:
    payload = json.loads(Path(args.recover_daily_log).resolve().read_text(encoding="utf-8"))
    if payload.get("run_id") != args.run_id:
        raise RuntimeError("recovery_wrong_run")
    if payload.get("collection_status") not in {"completed", "completed_with_failures"}:
        raise RuntimeError("recovery_collection_not_completed")
    if payload.get("downstream_usable") is not True:
        raise RuntimeError("recovery_downstream_not_usable")
    return collection_from_run_dir(
        args, Path(str(payload.get("run_output_dir") or "")), payload,
    )


def run_collection(args: argparse.Namespace) -> dict[str, Any]:
    modes = [
        bool(args.collection_fixture),
        bool(args.collection_stdout_fixture),
        bool(args.recover_daily_log),
    ]
    if sum(modes) > 1:
        raise RuntimeError("collection_input_mode_conflict")
    if args.recover_daily_log:
        return recover_collection(args)
    if args.collection_stdout_fixture:
        payload = last_json_object(
            Path(args.collection_stdout_fixture).read_text(encoding="utf-8")
        )
        if payload.get("run_id") not in {None, args.run_id}:
            raise RuntimeError("collection_result_wrong_run")
        return collection_from_run_dir(
            args, Path(str(payload.get("run_output_dir") or "")), payload,
        )
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
    if payload.get("run_id") not in {None, args.run_id}:
        raise RuntimeError("collection_result_wrong_run")
    return collection_from_run_dir(
        args, Path(str(payload.get("run_output_dir") or "")), payload,
    )


def editorial_input(
    collection: dict[str, Any], understanding: dict[str, Any] | None = None,
) -> dict[str, Any]:
    bounded_results = []
    for row in (understanding or {}).get("understanding_results", []):
        package = row.get("package") or {}
        caption_text = "\n".join(
            str(item.get("text") or "")
            for item in package.get("caption_timeline") or []
        )
        asr_text = str((package.get("asr") or {}).get("text") or "")
        bounded_results.append({
            "candidate_id": row.get("candidate_id"),
            "trigger": row.get("trigger"),
            "status": package.get("status"),
            "title": package.get("title"),
            "author": package.get("author"),
            "source_url": package.get("source_url"),
            "published_at": package.get("published_at"),
            "public_engagement": package.get("public_engagement"),
            "caption_text": caption_text[:12_000],
            "caption_text_truncated": len(caption_text) > 12_000,
            "asr_text": asr_text[:12_000],
            "asr_text_truncated": len(asr_text) > 12_000,
            "screen_text": (package.get("screen_text") or [])[:100],
            "unresolved_terms": package.get("unresolved_terms") or [],
            "failures": package.get("failures") or [],
            "package_sha256": package.get("package_sha256"),
        })
    value = {
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
    if understanding is not None:
        value["video_understanding"] = {
            "run_id": understanding.get("run_id"),
            "business_date": understanding.get("business_date"),
            "status": understanding.get("status"),
            "completed_count": understanding.get("completed_count"),
            "failed_count": understanding.get("failed_count"),
            "substitute_count": understanding.get("substitute_count"),
            "understanding_failures": understanding.get("understanding_failures") or [],
            "understanding_results": bounded_results,
        }
    return value


def merge_discovered_collection(
    collection: dict[str, Any], producer_state: dict[str, Any],
) -> dict[str, Any]:
    value = json.loads(json.dumps(collection, ensure_ascii=False))
    content = {
        str(row.get("content_fingerprint") or row.get("id") or ""): row
        for row in value.get("content_items", [])
    }
    candidates = {
        str(row.get("candidate_id") or row.get("content_fingerprint") or row.get("id") or ""): row
        for row in value.get("candidates", [])
    }
    packages = {
        f"douyin:{row.get('aweme_id')}": row
        for row in producer_state.get("packages", [])
        if row.get("status") in {"completed", "completed_with_failures"}
    }
    for row in producer_state.get("raw_candidates", []):
        identity = f"douyin:{row['aweme_id']}"
        package = packages.get(identity, {})
        asr_text = str(package.get("asr", {}).get("text") or "")
        caption_text = "\n".join(
            str(item.get("text") or "") for item in package.get("caption_timeline") or []
        )
        body = asr_text or caption_text
        content.setdefault(identity, {
            "id": identity,
            "content_fingerprint": identity,
            "source": "Douyin",
            "account": str(row.get("author") or ""),
            "title": str(row.get("title") or ""),
            "summary": body[:300],
            "body": body,
            "source_url": str(row.get("source_url") or ""),
            "published_at": str(row.get("published_at") or ""),
            "collected_at": f"{value['business_date']}T08:00:00+08:00",
        })
        candidates.setdefault(identity, {
            "candidate_id": identity,
            "content_fingerprint": identity,
            "title": str(row.get("title") or ""),
            "source_url": str(row.get("source_url") or ""),
            "source": "Douyin",
        })
    value["content_items"] = list(content.values())
    value["candidates"] = list(candidates.values())
    return value


def run_video_understanding(
    args: argparse.Namespace, on_demand_ids: set[str] | None = None,
    producer_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if args.video_mode == "normal":
        if producer_state is None:
            produced = produce_video_understanding(args)
        elif not on_demand_ids:
            produced = producer_state
        else:
            incremental = produce_video_understanding(
                args,
                on_demand_ids=on_demand_ids or set(),
                discovered_candidates=producer_state["raw_candidates"],
                include_automatic=False,
            )
            produced = {
                **producer_state,
                "packages": producer_state["packages"] + incremental["packages"],
                "failures": producer_state["failures"] + incremental["failures"],
            }
        policy = json.loads(Path(args.video_policy).read_text())
        result = materialize_video_understanding(
            run_id=args.run_id, business_date=args.business_date,
            candidates=produced["candidates"], decisions=produced["decisions"],
            packages=produced["packages"], policy=policy,
            output_root=Path(args.artifact_root), on_demand_ids=on_demand_ids or set(),
        )
        result["_producer_state"] = produced
        result["status"] = (
            "completed_with_failures" if result["failed_count"]
            else "completed" if result["completed_count"] else "completed_empty"
        )
        return result
    if args.video_mode == "disabled":
        return {
            "run_id": args.run_id, "business_date": args.business_date,
            "status": "completed_empty", "reason": "video_understanding_disabled",
            "understanding_results": [], "understanding_failures": [],
            "completed_count": 0, "failed_count": 0, "substitute_count": 0,
        }
    if args.video_mode not in {"qa-fixture", "offline-recovery"}:
        raise RuntimeError("video_understanding_mode_invalid")
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


def load_committed_producer_state(args: argparse.Namespace) -> dict[str, Any]:
    root = Path(args.artifact_root) / args.run_id / "video_producer"
    required = {
        name: root / name
        for name in ("discovery.json", "candidates.json", "decisions.json", "packages.json")
    }
    if any(not path.is_file() for path in required.values()):
        raise RuntimeError("video_producer_recovery_artifact_missing")
    discovery = json.loads(required["discovery.json"].read_text())
    if discovery.get("status") != "completed":
        raise RuntimeError("video_producer_recovery_discovery_invalid")
    candidate_batches = json.loads(required["candidates.json"].read_text())
    decisions = json.loads(required["decisions.json"].read_text())
    packages = json.loads(required["packages.json"].read_text())
    if not isinstance(candidate_batches, list):
        raise RuntimeError("video_producer_recovery_candidates_invalid")
    raw_candidates = [
        row for batch in candidate_batches for row in batch
        if isinstance(batch, list)
    ]
    identities = {str(row.get("aweme_id") or "") for row in raw_candidates}
    if not identities or any(str(row.get("run_id") or args.run_id) != args.run_id for row in raw_candidates):
        raise RuntimeError("video_producer_recovery_wrong_run")
    if any(str(row.get("aweme_id") or "") not in identities for row in packages):
        raise RuntimeError("video_producer_recovery_package_identity_invalid")
    return {
        "candidates": merge_video_candidates(candidate_batches, args.run_id),
        "raw_candidates": raw_candidates,
        "decisions": decisions,
        "packages": packages,
        "failures": [
            {
                "candidate_id": f"douyin:{row.get('aweme_id')}",
                "failure": row.get("failure"),
            }
            for row in packages if row.get("status") == "failed"
        ],
    }


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
    stdout_fixture_hash = ""
    if args.collection_stdout_fixture:
        path = Path(args.collection_stdout_fixture).resolve()
        stdout_fixture_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    recovery_hash = ""
    if args.recover_daily_log:
        path = Path(args.recover_daily_log).resolve()
        recovery_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    video_inputs = {}
    if not args.recover_daily_log:
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
        "collection_stdout_fixture_sha256": stdout_fixture_hash,
        "recovery_daily_log_sha256": recovery_hash,
        "publisher_url": args.publisher_url.rstrip("/"),
        "publisher_identity": args.publisher_identity,
        "video_mode": "legacy-exact-artifact-recovery" if args.recover_daily_log else args.video_mode,
        "video_runtime_config_sha256": (
            file_hash(Path(args.video_runtime_config).resolve())
            if args.video_runtime_config and not args.recover_daily_log else ""
        ),
        "video_inputs": video_inputs,
        "video_on_demand": sorted(args.video_on_demand),
        "stage_plan": list(LEGACY_STAGES if args.recover_daily_log else STAGES),
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
    if getattr(args, "recover_daily_log", ""):
        if any((args.video_candidates, args.video_decisions, args.video_packages,
                args.discovery_fixture, args.video_on_demand)):
            raise RuntimeError("historical_recovery_video_input_forbidden")
        return
    supplied = any((args.video_candidates, args.video_decisions, args.video_packages))
    if args.video_mode == "normal":
        if supplied:
            raise RuntimeError("normal_video_fixture_input_forbidden")
        if not args.video_runtime_config:
            raise RuntimeError("video_runtime_config_missing")
        validate_video_runtime(json.loads(Path(args.video_runtime_config).read_text()))
    elif args.video_mode in {"qa-fixture", "offline-recovery"}:
        if not all((args.video_candidates, args.video_decisions, args.video_packages)):
            raise RuntimeError("video_understanding_binding_incomplete")


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


def mark_projection_terminal(
    workflow: DailyWorkflow, run_id: str, unresolved: list[str],
) -> None:
    statuses = []
    for stage in unresolved:
        committed = workflow.stage(run_id, stage)
        if committed:
            receipt = workflow.projection(run_id, stage, int(committed["revision"]))
            statuses.append(str((receipt or {}).get("status") or "unknown"))
    conflict = any(status == "conflict" for status in statuses)
    workflow.fail(
        run_id, "publisher",
        ("projection_conflict:" if conflict else "projection_unresolved:") + ",".join(unresolved),
        status="failed" if conflict else "projection_pending",
    )


def run_legacy_recovery(
    args: argparse.Namespace, workflow: DailyWorkflow,
) -> int:
    global CURRENT_STAGE
    existing_collection = workflow.stage(args.run_id, "collection")
    CURRENT_STAGE = "collection"
    if existing_collection:
        collection = existing_collection["payload"]
        collection_commit = existing_collection
    else:
        collection = run_collection(args)
        collection_commit = workflow.commit_stage(
            args.run_id, "collection", digest({"source_revision": args.source_revision}),
            collection, collection.get("status", "completed"),
        )
    CURRENT_STAGE = "publisher:collection"
    projection_green = publish_committed_stage(
        args, workflow, "collection", collection, collection_commit,
    )

    existing_editorial = workflow.stage(args.run_id, "editorial")
    CURRENT_STAGE = "editorial"
    if existing_editorial:
        editorial_payload = existing_editorial["payload"]
        editorial_commit = existing_editorial
    else:
        editorial_request = editorial_input(collection)
        editorial_payload, editorial_skills = invoke(
            ["ai-account-editorial-director"], editorial_request,
        )
        selected = [
            row for row in editorial_payload.get("topics", [])
            if row.get("decision") == "select"
        ]
        for identity in editorial_skills:
            workflow.record_skill(
                run_id=args.run_id, stage="editorial", unit_id="daily",
                attempt=1, skill_name=identity["name"], skill_path=identity["path"],
                skill_hash=identity["sha256"], input_hash=digest(editorial_request),
                output_hash=digest(editorial_payload), status="completed",
            )
        editorial_payload["selected_count"] = len(selected)
        editorial_commit = workflow.commit_stage(
            args.run_id, "editorial", collection_commit["output_hash"],
            editorial_payload, "completed",
        )
    CURRENT_STAGE = "publisher:editorial"
    editorial_projection_green = publish_committed_stage(
        args, workflow, "editorial", editorial_payload, editorial_commit,
    )

    selected = [
        row for row in editorial_payload.get("topics", [])
        if row.get("decision") == "select"
    ]
    existing_scripts = workflow.stage(args.run_id, "scripts")
    CURRENT_STAGE = "scripts"
    if existing_scripts:
        scripts_payload = existing_scripts["payload"]
        scripts_commit = existing_scripts
    else:
        contents = {
            str(row.get("content_fingerprint") or row.get("id")): row
            for row in collection["content_items"]
        }
        scripts, failures = [], []
        for topic in selected:
            unit = str(topic["candidate_id"])
            source = contents.get(unit, {})
            try:
                request = script_input(args.run_id, topic, source)
                payload, identities = invoke(
                    ["austin-no-overtime-scripting", "austin-voice-scriptwriter"],
                    request,
                )
                payload = write_script_artifact(
                    Path(args.artifact_root), args.run_id, payload,
                )
                scripts.append(payload)
                for identity in identities:
                    workflow.record_skill(
                        run_id=args.run_id, stage="scripts", unit_id=unit, attempt=1,
                        skill_name=identity["name"], skill_path=identity["path"],
                        skill_hash=identity["sha256"], input_hash=digest(request),
                        output_hash=digest(payload), status="completed",
                    )
            except Exception as error:
                failures.append({"topic_id": unit, "reason": str(error)})
        scripts_payload = {
            "run_id": args.run_id, "scripts": scripts, "failures": failures,
        }
        scripts_commit = workflow.commit_stage(
            args.run_id, "scripts", editorial_commit["output_hash"], scripts_payload,
            "completed_with_failures" if failures else "completed",
        )
    CURRENT_STAGE = "publisher:scripts"
    scripts_projection_green = publish_committed_stage(
        args, workflow, "scripts", scripts_payload, scripts_commit,
    )
    unresolved = [
        stage for stage, green in (
            ("collection", projection_green),
            ("editorial", editorial_projection_green),
            ("scripts", scripts_projection_green),
        ) if not green
    ]
    if unresolved:
        mark_projection_terminal(workflow, args.run_id, unresolved)
    else:
        workflow.complete(args.run_id, str(scripts_commit["status"]))
    result = workflow.read_run(args.run_id)
    result["action"] = "completed" if not unresolved else "projection_unresolved"
    result["unresolved_stages"] = unresolved
    print(json.dumps(result, ensure_ascii=False))
    return 0 if not unresolved else 2


def main() -> int:
    global CURRENT_WORKFLOW, CURRENT_RUN_ID, CURRENT_STAGE
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--business-date", required=True)
    parser.add_argument("--source-db", default=str(ROOT / "output/state/source_control.sqlite3"))
    parser.add_argument("--workflow-db", default=str(DEFAULT_DB))
    parser.add_argument("--source-revision", type=int, required=True)
    parser.add_argument("--collection-fixture", default="")
    parser.add_argument("--collection-stdout-fixture", default="")
    parser.add_argument("--recover-daily-log", default="")
    parser.add_argument("--publisher-url", default="")
    parser.add_argument("--publisher-identity", default="")
    parser.add_argument("--artifact-root", default=str(ROOT / "output/runs"))
    parser.add_argument("--video-candidates", default="")
    parser.add_argument("--video-decisions", default="")
    parser.add_argument("--video-packages", default="")
    parser.add_argument(
        "--video-policy",
        default=str(ROOT / "config/douyin_video_understanding_policy.json"),
    )
    parser.add_argument("--video-on-demand", action="append", default=[])
    parser.add_argument(
        "--video-mode",
        choices=("normal", "qa-fixture", "offline-recovery", "disabled"),
        default="normal",
    )
    parser.add_argument("--video-runtime-config", default=os.environ.get("DOUYIN_VIDEO_RUNTIME_CONFIG", ""))
    parser.add_argument("--discovery-fixture", default="")
    parser.add_argument("--cdp", default="http://127.0.0.1:9333")
    parser.add_argument("--search-query", default="")
    args = parser.parse_args()
    DailyWorkflow.validate_identity(args.run_id, args.business_date)
    validate_runtime(args)
    exact_contract = contract_identity(args)
    workflow = DailyWorkflow(args.workflow_db)
    CURRENT_WORKFLOW = workflow
    CURRENT_RUN_ID = args.run_id
    stage_plan = LEGACY_STAGES if args.recover_daily_log else STAGES
    if args.recover_daily_log:
        workflow.reconcile_empty_legacy_recovery_contract(
            args.run_id, args.business_date, args.source_revision, exact_contract,
        )
    begin_action = workflow.begin(
        args.run_id, args.business_date, args.source_revision, exact_contract,
        stage_plan=stage_plan,
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
        if unresolved:
            mark_projection_terminal(workflow, args.run_id, unresolved)
            result = workflow.read_run(args.run_id)
            result["action"] = "projection_unresolved"
            result["unresolved_stages"] = unresolved
        print(json.dumps(result, ensure_ascii=False))
        return 0 if not unresolved else 2
    if begin_action == "resume":
        workflow.resume(args.run_id)
    if args.recover_daily_log:
        return run_legacy_recovery(args, workflow)

    CURRENT_STAGE = "collection"
    existing_collection = workflow.stage(args.run_id, "collection")
    precomputed_understanding = None
    precomputed_producer_state = None
    if existing_collection:
        collection = existing_collection["payload"]
        collection_commit = existing_collection
        if args.video_mode == "normal":
            CURRENT_STAGE = "video_understanding"
            precomputed_producer_state = load_committed_producer_state(args)
            precomputed_understanding = run_video_understanding(
                args, producer_state=precomputed_producer_state,
            )
    else:
        collection = run_collection(args)
        if args.video_mode == "normal":
            CURRENT_STAGE = "video_understanding"
            precomputed_understanding = run_video_understanding(args)
            precomputed_producer_state = precomputed_understanding.pop("_producer_state")
            collection = merge_discovered_collection(collection, precomputed_producer_state)
        CURRENT_STAGE = "collection"
        collection_commit = workflow.commit_stage(
            args.run_id, "collection", digest({"source_revision": args.source_revision}),
            collection, "completed_empty" if not collection["content_items"] else collection.get("status", "completed"),
        )
    CURRENT_STAGE = "publisher:collection"
    projection_green = publish_committed_stage(
        args, workflow, "collection", collection, collection_commit,
    )
    if not collection["content_items"]:
        if not projection_green:
            mark_projection_terminal(workflow, args.run_id, ["collection"])
        else:
            workflow.complete(args.run_id, "completed_empty")
        result = workflow.read_run(args.run_id)
        result["action"] = "completed_empty" if projection_green else "projection_unresolved"
        print(json.dumps(result, ensure_ascii=False))
        return 0 if projection_green else 2

    existing_understanding = workflow.stage(args.run_id, "video_understanding")
    existing_editorial = workflow.stage(args.run_id, "editorial")
    CURRENT_STAGE = "video_understanding"
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
            else precomputed_understanding if precomputed_understanding is not None
            else run_video_understanding(args)
        )
        producer_state = (
            precomputed_producer_state
            or automatic_understanding.pop("_producer_state", None)
        )
        CURRENT_STAGE = "editorial"
        editorial_payload, editorial_skills = invoke(
            ["ai-account-editorial-director"],
            editorial_input(collection, automatic_understanding),
        )
        selected = [row for row in editorial_payload.get("topics", []) if row.get("decision") == "select"]
        editorial_on_demand = editorial_on_demand_ids(
            selected,
            automatic_understanding,
            (
                [producer_state["raw_candidates"]]
                if producer_state else
                json.loads(Path(args.video_candidates).read_text())
                if args.video_candidates else []
            ),
        )
        requested_on_demand = set(args.video_on_demand)
        if existing_understanding:
            understanding = existing_understanding["payload"]
            understanding_commit = existing_understanding
        else:
            understanding = run_video_understanding(
                args, editorial_on_demand | requested_on_demand, producer_state,
            )
            understanding.pop("_producer_state", None)
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
    CURRENT_STAGE = "publisher:video_understanding"
    understanding_projection_green = publish_committed_stage(
        args, workflow, "video_understanding", understanding, understanding_commit,
    )
    selected = [row for row in editorial_payload.get("topics", []) if row.get("decision") == "select"]
    CURRENT_STAGE = "publisher:editorial"
    editorial_projection_green = publish_committed_stage(
        args, workflow, "editorial", editorial_payload, editorial_commit,
    )

    existing_scripts = workflow.stage(args.run_id, "scripts")
    CURRENT_STAGE = "scripts"
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
    CURRENT_STAGE = "publisher:scripts"
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
    if unresolved:
        mark_projection_terminal(workflow, args.run_id, unresolved)
        result = workflow.read_run(args.run_id)
        result["action"] = "projection_unresolved"
        result["unresolved_stages"] = unresolved
    else:
        workflow.complete(args.run_id, str(script_commit["status"]))
        result = workflow.read_run(args.run_id)
        result["action"] = "completed"
        result["unresolved_stages"] = []
    print(json.dumps(result, ensure_ascii=False))
    return 0 if not unresolved else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        if CURRENT_WORKFLOW is not None and CURRENT_RUN_ID:
            CURRENT_WORKFLOW.fail(
                CURRENT_RUN_ID, CURRENT_STAGE or "workflow", "workflow_interrupted",
            )
        print(json.dumps({"ok": False, "error": "workflow_interrupted"}, sort_keys=True))
        raise SystemExit(130)
    except (RuntimeError, WorkflowConflict, ValueError, json.JSONDecodeError) as error:
        if CURRENT_WORKFLOW is not None and CURRENT_RUN_ID:
            CURRENT_WORKFLOW.fail(
                CURRENT_RUN_ID, CURRENT_STAGE or "workflow",
                f"{type(error).__name__}:{error}",
            )
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False, sort_keys=True))
        raise SystemExit(2)
