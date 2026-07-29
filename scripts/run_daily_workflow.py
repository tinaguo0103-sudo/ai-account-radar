#!/usr/bin/env python3
"""One daily collection_enrichment -> editorial -> scripts orchestrator."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import urllib.parse
import uuid
from pathlib import Path
from typing import Any

from daily_workflow import DailyWorkflow, WorkflowConflict, canonical
from collected_artifact_adoption import adopt_collected_artifacts
from douyin_video_understanding_producer import ProducerError, produce
from publish_website_projection import ProjectionError
from website_publisher_client import publish_terminal
from video_runtime_readiness import RuntimeReadinessError, check_runtime_readiness

ROOT = Path(__file__).resolve().parents[1]
ACTIVE_ROOT = Path.home() / ".codex" / "skills"
SKILLS = (
    "ai-account-editorial-director",
    "austin-no-overtime-scripting",
    "austin-voice-scriptwriter",
)


def read_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def canonical_url(raw: str) -> str:
    value = raw.strip()
    if not value:
        return ""
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    query = urllib.parse.urlencode(sorted(urllib.parse.parse_qsl(parsed.query)))
    return urllib.parse.urlunsplit(
        (parsed.scheme.lower(), parsed.netloc.lower(), parsed.path.rstrip("/") or "/", query, "")
    )


def stable_item_id(row: dict[str, Any]) -> str:
    existing = str(row.get("item_id") or "").strip()
    if existing:
        return existing
    aweme_id = str(row.get("aweme_id") or "").strip()
    if not aweme_id:
        url = str(row.get("source_url") or row.get("内容链接") or "")
        match = re.search(r"douyin\.com/video/(\d+)", url)
        aweme_id = match.group(1) if match else ""
    if aweme_id:
        return f"douyin:{aweme_id}"
    external = str(row.get("external_id") or "").strip()
    if external:
        source = str(row.get("source") or row.get("平台") or "external").strip().lower()
        return f"{source}:{external}"
    url = canonical_url(str(row.get("source_url") or row.get("内容链接") or ""))
    if url:
        return f"url:{url}"
    local_id = str(row.get("local_id") or "").strip()
    if not local_id:
        local_id = f"local:{uuid.uuid4()}"
        row["local_id"] = local_id
    return local_id


def normalize_items(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    accepted: dict[str, dict[str, Any]] = {}
    conflicts: set[str] = set()
    failures: list[dict[str, str]] = []
    for raw in rows:
        row = json.loads(json.dumps(raw, ensure_ascii=False))
        identity = stable_item_id(row)
        row["item_id"] = identity
        current = accepted.get(identity)
        if current is None:
            accepted[identity] = row
        elif canonical(current) != canonical(row):
            conflicts.add(identity)
    for identity in sorted(conflicts):
        accepted.pop(identity, None)
        failures.append({"item_id": identity, "reason": "stable_item_conflict"})
    return [accepted[key] for key in sorted(accepted)], failures


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


def collect(args: argparse.Namespace) -> dict[str, Any]:
    if args.adopt_collected_artifacts:
        return adopt_collected_artifacts(args)
    if args.collection_fixture:
        value = read_json(args.collection_fixture)
        if value.get("run_id") != args.run_id:
            raise WorkflowConflict("collection_wrong_run")
        return value
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/daily_pipeline.py"),
         "--run-id", args.run_id, "--source-db", args.source_db,
         "--defer-editorial", "--no-feishu-runtime"],
        text=True, capture_output=True,
    )
    if result.returncode:
        raise RuntimeError(f"collection_failed:{result.returncode}")
    value = last_json_object(result.stdout)
    if value.get("run_id") not in {None, args.run_id}:
        raise WorkflowConflict("collection_wrong_run")
    run_dir = Path(str(value.get("run_output_dir") or "")).resolve()
    exact = Path(args.artifact_root).resolve() / args.run_id
    if run_dir != exact:
        raise WorkflowConflict("collection_run_path_mismatch")
    import csv
    with (run_dir / "content_items.csv").open(encoding="utf-8-sig", newline="") as handle:
        content = list(csv.DictReader(handle))
    with (run_dir / "today_10_topics.csv").open(encoding="utf-8-sig", newline="") as handle:
        candidates = list(csv.DictReader(handle))
    return {
        "run_id": args.run_id, "business_date": args.business_date,
        "status": value.get("collection_status", "completed"),
        "content_items": content, "candidates": candidates,
        "source_runs": value.get("source_outcomes", []),
    }


def normalize_collection_candidates(
    candidates: Any,
    *,
    item_ids: set[str],
    run_id: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not isinstance(candidates, list):
        raise WorkflowConflict("collection_candidates_invalid")
    normalized: dict[str, dict[str, Any]] = {}
    video_candidates: list[dict[str, Any]] = []
    for raw in candidates:
        if not isinstance(raw, dict):
            raise WorkflowConflict("collection_candidate_invalid")
        candidate = json.loads(json.dumps(raw, ensure_ascii=False))
        if not any(
            str(candidate.get(key) or "").strip()
            for key in (
                "candidate_id", "item_id", "aweme_id", "external_id",
                "source_url", "内容链接", "local_id",
            )
        ):
            raise WorkflowConflict("collection_candidate_identity_missing")
        try:
            identity = str(candidate.get("candidate_id") or stable_item_id(candidate))
        except (TypeError, ValueError) as error:
            raise WorkflowConflict("collection_candidate_identity_missing") from error
        if identity not in item_ids:
            raise WorkflowConflict("collection_candidate_content_mapping_missing")
        candidate["candidate_id"] = identity
        current = normalized.get(identity)
        if current is not None:
            if canonical(current) != canonical(candidate):
                raise WorkflowConflict("collection_candidate_identity_conflict")
            continue
        normalized[identity] = candidate
        has_video_identity = bool(candidate.get("aweme_id") or candidate.get("discovery_source"))
        if has_video_identity:
            if candidate.get("run_id") not in {None, "", run_id}:
                raise WorkflowConflict("collection_video_candidate_wrong_run")
            candidate["run_id"] = run_id
            video_candidates.append(candidate)
    return list(normalized.values()), video_candidates


SOURCE_LEDGER_NAMES = ("configured_account", "recommendation", "dynamic_search")


def merge_video_discovery_checkpoint(
    collection: dict[str, Any],
    checkpoint: dict[str, Any],
    *,
    run_id: str,
) -> dict[str, Any]:
    if checkpoint.get("status") not in {"completed", "completed_with_failures"}:
        raise WorkflowConflict("video_discovery_checkpoint_not_completed")
    output = json.loads(json.dumps(collection, ensure_ascii=False))
    content = list(output.get("content_items") or [])
    candidates = list(output.get("candidates") or [])
    configured = [
        row for row in candidates
        if str(row.get("discovery_source") or "") == "configured_account"
    ]
    discovered = checkpoint.get("candidates")
    if not isinstance(discovered, list):
        raise WorkflowConflict("video_discovery_checkpoint_candidates_invalid")
    for raw in discovered:
        if not isinstance(raw, dict):
            raise WorkflowConflict("video_discovery_checkpoint_candidate_invalid")
        row = json.loads(json.dumps(raw, ensure_ascii=False))
        if row.get("run_id") not in {None, "", run_id}:
            raise WorkflowConflict("video_discovery_checkpoint_wrong_run")
        if row.get("discovery_source") not in {"recommendation", "dynamic_search"}:
            raise WorkflowConflict("video_discovery_checkpoint_source_invalid")
        row["run_id"] = run_id
        row["candidate_id"] = stable_item_id(row)
        candidates.append(row)
        content.append({
            **row,
            "item_id": row["candidate_id"],
            "source": "douyin",
            "summary": row.get("title") or "",
        })
    checkpoint_ledger = checkpoint.get("source_ledger")
    if not isinstance(checkpoint_ledger, list):
        raise WorkflowConflict("video_discovery_checkpoint_ledger_invalid")
    discovery_rows = {
        str(row.get("source") or ""): json.loads(json.dumps(row, ensure_ascii=False))
        for row in checkpoint_ledger if isinstance(row, dict)
    }
    configured_status = str(output.get("configured_account_status") or "")
    if configured_status not in {"completed", "completed_empty", "partial", "failed"}:
        raise WorkflowConflict("configured_account_attempt_status_missing")
    ledger = [{
        "source": "configured_account",
        "attempted": True,
        "status": configured_status,
        "discovered_count": len(configured),
        "reason": str(output.get("configured_account_reason") or ""),
        "captured_at": str(output.get("configured_account_captured_at") or ""),
    }]
    for source in ("recommendation", "dynamic_search"):
        current = discovery_rows.get(source)
        if current is None:
            raise WorkflowConflict(f"video_discovery_checkpoint_source_missing:{source}")
        count = sum(
            str(row.get("discovery_source") or "") == source
            for row in discovered
        )
        status = str(current.get("status") or (
            "completed" if count else "completed_empty"
        ))
        reason = str(current.get("reason") or "")
        if status in {"partial", "failed"} and not reason:
            raise WorkflowConflict("source_ledger_reason_missing")
        ledger.append({
            **current,
            "source": source,
            "attempted": True,
            "status": status,
            "discovered_count": count,
            "reason": reason,
        })
    output["content_items"] = content
    output["candidates"] = candidates
    output["source_ledger"] = ledger
    return output


def normalize_source_ledger(
    collection: dict[str, Any],
    *,
    video_candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    supplied = collection.get("source_ledger") or []
    if not isinstance(supplied, list):
        raise WorkflowConflict("source_ledger_invalid")
    by_source: dict[str, dict[str, Any]] = {}
    for raw in supplied:
        if not isinstance(raw, dict):
            raise WorkflowConflict("source_ledger_invalid")
        source = str(raw.get("source") or "")
        if source not in SOURCE_LEDGER_NAMES or source in by_source:
            raise WorkflowConflict("source_ledger_identity_conflict")
        attempted = raw.get("attempted")
        status = str(raw.get("status") or "")
        count = raw.get("discovered_count")
        if attempted is not True or status not in {
            "completed", "completed_empty", "partial", "failed",
        } or not isinstance(count, int) or count < 0:
            raise WorkflowConflict("source_ledger_contract_invalid")
        if status == "completed_empty" and count != 0:
            raise WorkflowConflict("source_ledger_count_conflict")
        if status == "failed" and count != 0:
            raise WorkflowConflict("source_ledger_count_conflict")
        if status in {"partial", "failed"} and not str(raw.get("reason") or ""):
            raise WorkflowConflict("source_ledger_reason_missing")
        by_source[source] = json.loads(json.dumps(raw, ensure_ascii=False))
    actual_counts = {
        source: sum(
            str(row.get("discovery_source") or "") == source
            for row in video_candidates
        )
        for source in SOURCE_LEDGER_NAMES
    }
    for source in SOURCE_LEDGER_NAMES:
        row = by_source.get(source)
        if row is None:
            raise WorkflowConflict(f"source_ledger_attempt_missing:{source}")
        if row["discovered_count"] != actual_counts[source]:
            raise WorkflowConflict(f"source_ledger_count_conflict:{source}")
    if not any(row["attempted"] for row in by_source.values()):
        raise WorkflowConflict("all_sources_unattempted")
    if not video_candidates:
        raise WorkflowConflict("all_sources_without_safe_candidates")
    return [by_source[source] for source in SOURCE_LEDGER_NAMES]


def enrich(args: argparse.Namespace, collection: dict[str, Any]) -> dict[str, Any]:
    value = json.loads(json.dumps(collection, ensure_ascii=False))
    if value.get("run_id") != args.run_id:
        raise WorkflowConflict("collection_wrong_run")
    items, identity_failures = normalize_items(value.get("content_items", []))
    candidates, video_candidates = normalize_collection_candidates(
        value.get("candidates", []),
        item_ids={row["item_id"] for row in items},
        run_id=args.run_id,
    )
    source_ledger = (
        normalize_source_ledger(value, video_candidates=video_candidates)
        if "source_ledger" in value else []
    )
    packages: list[dict[str, Any]]
    producer_failures: list[dict[str, Any]]
    if args.qa_frozen_packages:
        packages = read_json(args.qa_frozen_packages)
        if any(str(row.get("run_id") or "") != str(value.get("run_id") or "") for row in packages):
            raise WorkflowConflict("video_package_run_mismatch")
        producer_failures = [
            {"item_id": f"douyin:{row.get('aweme_id')}", "reason": row.get("failure")}
            for row in packages if row.get("status") == "failed"
        ]
    elif args.video_mode == "normal":
        produced = produce(args, discovered_candidates=video_candidates)
        packages = produced["packages"]
        producer_failures = [{
            "item_id": str(row.get("item_id") or row.get("candidate_id") or ""),
            "reason": str(row.get("reason") or row.get("failure") or "video_understanding_failed"),
        } for row in produced["failures"]]
    else:
        packages, producer_failures = [], []
    package_by_url = {
        str(row.get("source_url") or ""): row for row in packages
        if row.get("status") in {"completed", "completed_with_failures"}
    }
    for row in items:
        package = package_by_url.get(str(row.get("source_url") or row.get("内容链接") or ""))
        if package:
            row["video_understanding"] = package
    value.update({
        "content_items": items,
        "candidates": candidates,
        "understanding_results": [
            {"candidate_id": f"douyin:{row.get('aweme_id')}", "package": row}
            for row in packages
        ],
        "item_failures": identity_failures + producer_failures,
        "source_ledger": source_ledger,
        "substitute_count": 0,
    })
    return value


def validate_editorial(run_id: str, result: dict[str, Any], candidates: list[dict[str, Any]]) -> None:
    if result.get("run_id") != run_id or not isinstance(result.get("topics"), list):
        raise WorkflowConflict("editorial_result_invalid")
    allowed = {str(row.get("candidate_id")) for row in candidates}
    seen: set[str] = set()
    for row in result["topics"]:
        identity = str(row.get("candidate_id") or "")
        if identity not in allowed or identity in seen:
            raise WorkflowConflict("editorial_result_identity_conflict")
        seen.add(identity)
        if row.get("decision") not in {"select", "observe", "reject", "failed"}:
            raise WorkflowConflict("editorial_result_invalid")
        if row["decision"] == "select" and not all(
            str(row.get(key) or "") for key in ("title", "hook", "structure", "selection_reason")
        ):
            raise WorkflowConflict("editorial_selected_incomplete")
    if seen != allowed:
        raise WorkflowConflict("editorial_result_coverage_incomplete")


def validate_scripts(run_id: str, result: dict[str, Any], selected: set[str]) -> None:
    if result.get("run_id") != run_id or not isinstance(result.get("scripts"), list):
        raise WorkflowConflict("scripts_result_invalid")
    seen: set[str] = set()
    for row in result["scripts"]:
        identity = str(row.get("topic_id") or "")
        if identity not in selected or identity in seen:
            raise WorkflowConflict("script_result_identity_conflict")
        seen.add(identity)
        if not all(str(row.get(key) or "") for key in ("title", "hook", "structure", "body")):
            raise WorkflowConflict("script_result_incomplete")


def skill_diagnostics() -> list[dict[str, str]]:
    output = []
    for name in SKILLS:
        path = ACTIVE_ROOT / name / "SKILL.md"
        value = {"name": name, "path": str(path), "available": str(path.is_file()).lower()}
        if path.is_file():
            value["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        output.append(value)
    return output


def publish(workflow: DailyWorkflow, db_path: Path, run_id: str) -> str:
    try:
        publish_terminal(db_path, run_id)
    except ProjectionError as error:
        status = "conflict" if str(error) not in {
            "publisher_config_missing", "publisher_config_incomplete",
            "website_projection_transport_unavailable",
        } else "pending"
        workflow.mark_publish(run_id, status, str(error))
        return status
    workflow.mark_publish(run_id, "applied")
    return "applied"


def write_script_artifacts(root: Path, run_id: str, scripts: list[dict[str, Any]]) -> None:
    directory = root / run_id / "scripts"
    directory.mkdir(parents=True, exist_ok=True)
    for row in scripts:
        target = directory / f"{hashlib.sha256(str(row['topic_id']).encode()).hexdigest()[:20]}.md"
        text = (
            f"# {row['title']}\n\n## 钩子\n\n{row['hook']}\n\n"
            f"## 结构\n\n{row['structure']}\n\n## 完整口播稿\n\n{row['body']}\n"
        )
        if target.exists() and target.read_text(encoding="utf-8") == text:
            continue
        if target.exists():
            raise WorkflowConflict("script_artifact_conflict")
        target.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--business-date", required=True)
    parser.add_argument("--workflow-db", type=Path, default=ROOT / "output/state/daily_workflow.sqlite3")
    parser.add_argument("--source-db", default=str(ROOT / "output/state/source_control.sqlite3"))
    parser.add_argument("--artifact-root", type=Path, default=ROOT / "output/runs")
    parser.add_argument("--collection-fixture")
    parser.add_argument("--adopt-collected-artifacts")
    parser.add_argument("--adoption-log")
    parser.add_argument("--qa-frozen-packages")
    parser.add_argument("--editorial-result-file")
    parser.add_argument("--scripts-result-file")
    parser.add_argument("--video-mode", choices=("normal", "disabled"), default="normal")
    parser.add_argument("--video-runtime-config", default="")
    parser.add_argument("--video-policy", default="")
    parser.add_argument("--discovery-fixture", default="")
    parser.add_argument("--video-discovery-checkpoint", default="")
    parser.add_argument("--cdp", default="http://127.0.0.1:9333")
    args = parser.parse_args()
    workflow: DailyWorkflow | None = None
    try:
        DailyWorkflow.validate_identity(args.run_id, args.business_date)
        if args.video_mode == "normal":
            readiness = check_runtime_readiness(args.video_runtime_config)
            args.video_runtime_config = readiness["config_path"]
            args.video_policy = readiness["policy_path"]
        workflow = DailyWorkflow(args.workflow_db)
        pending = workflow.latest_pending(args.business_date)
        if pending:
            publish(workflow, args.workflow_db, pending["run_id"])
        mode = workflow.begin(args.run_id, args.business_date)
        if mode == "terminal_replay":
            row = workflow.read_run(args.run_id)["run"]
            if row["publish_status"] == "pending":
                publish(workflow, args.workflow_db, args.run_id)
            print(json.dumps({"ok": True, "action": "noop", **workflow.read_run(args.run_id)},
                             ensure_ascii=False))
            return 0
        collection_stage = workflow.stage(args.run_id, "collection_enrichment")
        if collection_stage:
            collection = collection_stage["payload"]
        else:
            collected = collect(args)
            if args.video_discovery_checkpoint:
                collected = merge_video_discovery_checkpoint(
                    collected,
                    read_json(args.video_discovery_checkpoint),
                    run_id=args.run_id,
                )
            collection = enrich(args, collected)
            completed_item_ids = {
                str(row["item_id"]) for row in collection["content_items"]
            }
            workflow.store_items(args.run_id, [
                {"item_id": row["item_id"], "status": "completed", "failure": "", "payload": row}
                for row in collection["content_items"]
            ] + [
                {"item_id": row["item_id"], "status": "failed", "failure": row["reason"],
                 "payload": {"item_id": row["item_id"]}}
                for row in collection["item_failures"]
                if str(row["item_id"]) not in completed_item_ids
            ])
            stage_status = "completed_with_failures" if collection["item_failures"] else (
                "completed" if collection["content_items"] else "completed_empty"
            )
            workflow.commit_stage(args.run_id, "collection_enrichment", collection, stage_status)
        editorial_stage = workflow.stage(args.run_id, "editorial")
        if editorial_stage:
            editorial = editorial_stage["payload"]
        elif not collection["candidates"]:
            editorial = {"run_id": args.run_id, "topics": []}
            workflow.commit_stage(args.run_id, "editorial", editorial, "completed_empty")
        elif not args.editorial_result_file:
            print(json.dumps({
                "ok": True, "action": "editorial_required", "run_id": args.run_id,
                "business_date": args.business_date, "candidates": collection["candidates"],
                "skill_name": SKILLS[0],
            }, ensure_ascii=False))
            return 0
        else:
            editorial = read_json(args.editorial_result_file)
            validate_editorial(args.run_id, editorial, collection["candidates"])
            workflow.record_skill_diagnostic(
                args.run_id, "editorial", "daily", SKILLS[0],
                {"provenance": skill_diagnostics()[0]},
            )
            workflow.commit_stage(args.run_id, "editorial", editorial, "completed")
        selected = {
            str(row["candidate_id"]) for row in editorial.get("topics", [])
            if row.get("decision") == "select"
        }
        scripts_stage = workflow.stage(args.run_id, "scripts")
        if scripts_stage:
            scripts = scripts_stage["payload"]
        elif not args.scripts_result_file and selected:
            print(json.dumps({
                "ok": True, "action": "scripts_required", "run_id": args.run_id,
                "business_date": args.business_date,
                "selected_topics": [
                    row for row in editorial["topics"] if row.get("decision") == "select"
                ],
                "skill_names": list(SKILLS[1:]),
            }, ensure_ascii=False))
            return 0
        else:
            scripts = read_json(args.scripts_result_file) if selected else {
                "run_id": args.run_id, "scripts": [], "failures": [],
            }
            validate_scripts(args.run_id, scripts, selected)
            if selected:
                for name, diagnostic in zip(SKILLS[1:], skill_diagnostics()[1:]):
                    workflow.record_skill_diagnostic(
                        args.run_id, "scripts", "daily", name, {"provenance": diagnostic},
                    )
            write_script_artifacts(args.artifact_root, args.run_id, scripts["scripts"])
            workflow.commit_stage(
                args.run_id, "scripts", scripts,
                "completed_with_failures" if scripts.get("failures") else "completed",
            )
        failures = (
            len(collection.get("item_failures", []))
            + len(scripts.get("failures", []))
        )
        status = "completed_with_failures" if failures else (
            "completed" if collection["content_items"] else "completed_empty"
        )
        workflow.complete(args.run_id, status, f"terminal:{args.run_id}")
        publish_status = publish(workflow, args.workflow_db, args.run_id)
        print(json.dumps({
            "ok": True,
            "action": "completed" if publish_status == "applied" else "completed_publish_pending",
            **workflow.read_run(args.run_id),
        }, ensure_ascii=False))
        return 0
    except (
        WorkflowConflict, ProducerError, ProjectionError, RuntimeReadinessError,
        RuntimeError, ValueError, OSError,
    ) as error:
        if workflow is not None:
            workflow.mark_recoverable_failure(args.run_id, str(error))
        else:
            DailyWorkflow.mark_existing_recoverable_failure(
                args.workflow_db, args.run_id, args.business_date, str(error)
            )
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False))
        return 2
    except Exception:
        error = "workflow_unexpected_startup_error" if workflow is None else "workflow_unexpected_error"
        if workflow is not None:
            workflow.mark_recoverable_failure(args.run_id, error)
        else:
            DailyWorkflow.mark_existing_recoverable_failure(
                args.workflow_db, args.run_id, args.business_date, error
            )
        print(json.dumps({"ok": False, "error": error}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
