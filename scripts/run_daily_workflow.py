#!/usr/bin/env python3
"""One daily collection_enrichment -> editorial -> scripts orchestrator."""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import fcntl
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import urllib.parse
import uuid
from pathlib import Path
from typing import Any

from daily_workflow import DailyWorkflow, TERMINAL, WorkflowConflict, canonical
from daily_pipeline import current_douyin_artifact
from collected_artifact_adoption import adopt_collected_artifacts
from douyin_video_understanding_producer import (
    ProducerError,
    atomic_json,
    load_discovery_payload,
    produce,
)
from publish_website_projection import ProjectionError
from trend_hotspot_cards import (
    attach_understanding,
    build_hotspot_cards,
    complete_editorial_ledger,
    deep_read_counts,
    editorial_candidates,
    representative_candidates,
    select_representative_sources,
    validate_candidate_specific_decisions,
)
from website_publisher_client import publish_terminal
from video_runtime_readiness import RuntimeReadinessError, check_runtime_readiness
import spoken_script_runtime as script_runtime

ROOT = Path(__file__).resolve().parents[1]
ACTIVE_ROOT = Path.home() / ".codex" / "skills"
EDITORIAL_SKILL = "ai-account-editorial-director"
WRITER_SKILL = "austin-voice-scriptwriter"
MAX_FINAL_EDITORIAL_SELECT = 10
SKILLS = (EDITORIAL_SKILL, WRITER_SKILL)
WRITER_SKILLS = (WRITER_SKILL,)

REPLAY_INPUT_TARGETS = {
    "collection_checkpoint": "workflow_collection.json",
    "today_new_rows": "sources/current_run_rows.jsonl",
    "discovery_checkpoint": "video_producer/discovery.json",
    "video_packages": "video_producer/packages.json",
}
REPLAY_RESULT_ROLES = {
    "editorial_result": "editorial_result_file",
    "scripts_result": "scripts_result_file",
}


def read_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def prepare_replay_inputs(args: argparse.Namespace) -> None:
    if not args.replay_inputs:
        return
    root = Path(args.replay_inputs).resolve()
    manifest_path = root / "manifest.json"
    try:
        manifest = read_json(manifest_path)
    except (OSError, json.JSONDecodeError) as error:
        raise WorkflowConflict("replay_inputs_manifest_invalid") from error
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema_version") != 1
        or manifest.get("run_id") != args.run_id
        or manifest.get("business_date") != args.business_date
        or not isinstance(manifest.get("files"), list)
    ):
        raise WorkflowConflict("replay_inputs_manifest_invalid")
    required = set(REPLAY_INPUT_TARGETS) | set(REPLAY_RESULT_ROLES)
    by_role: dict[str, Path] = {}
    for entry in manifest["files"]:
        if not isinstance(entry, dict):
            raise WorkflowConflict("replay_inputs_manifest_invalid")
        role = str(entry.get("role") or "")
        relative = Path(str(entry.get("path") or ""))
        if role not in required or role in by_role or relative.is_absolute() or ".." in relative.parts:
            raise WorkflowConflict("replay_inputs_manifest_invalid")
        unresolved_source = root / relative
        source = unresolved_source.resolve()
        if root not in source.parents or unresolved_source.is_symlink() or not source.is_file():
            raise WorkflowConflict(f"replay_input_missing:{role}")
        actual = hashlib.sha256(source.read_bytes()).hexdigest()
        if actual != str(entry.get("sha256") or ""):
            raise WorkflowConflict(f"replay_input_hash_mismatch:{role}")
        by_role[role] = source
    missing = required - set(by_role)
    if missing:
        raise WorkflowConflict(f"replay_input_missing:{sorted(missing)[0]}")
    run_dir = Path(args.artifact_root).resolve() / args.run_id
    for role, relative in REPLAY_INPUT_TARGETS.items():
        source = by_role[role]
        target = run_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if not target.is_file() or target.read_bytes() != source.read_bytes():
                raise WorkflowConflict(f"replay_input_target_conflict:{role}")
            continue
        shutil.copyfile(source, target)
    for role, attribute in REPLAY_RESULT_ROLES.items():
        current = str(getattr(args, attribute) or "")
        expected = str(by_role[role])
        if current and Path(current).resolve() != by_role[role]:
            raise WorkflowConflict(f"replay_input_result_conflict:{role}")
        if role == "scripts_result":
            continue
        setattr(args, attribute, expected)


class WorkflowExecutionLock:
    """Process-local serialization for the one workflow authority."""

    def __init__(self, workflow_db: Path):
        self.path = workflow_db.resolve().with_name(f".{workflow_db.name}.lock")
        self.handle: Any | None = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+b")
        try:
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.handle.close()
            self.handle = None
            return False
        return True

    def release(self) -> None:
        if self.handle is None:
            return
        fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        self.handle.close()
        self.handle = None


def workflow_handoff_path(args: argparse.Namespace) -> Path:
    return Path(args.artifact_root).resolve() / args.run_id / "workflow_handoff.json"


def atomic_replace_json(path: Path, value: dict[str, Any]) -> bool:
    encoded = (canonical(value) + "\n").encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file() and path.read_bytes() == encoded:
        return False
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(temporary).unlink(missing_ok=True)
    if path.read_bytes() != encoded:
        raise WorkflowConflict("workflow_handoff_readback_unknown")
    return True


def handoff_summary(document: dict[str, Any], path: Path) -> dict[str, Any]:
    summary = {
        "ok": bool(document.get("ok", True)),
        "action": str(document.get("action") or ""),
        "run_id": str(document.get("run_id") or ""),
        "business_date": str(document.get("business_date") or ""),
        "handoff_path": str(path),
    }
    for key in (
        "stage", "status", "publish_status", "candidate_count",
        "selected_count", "script_count", "item_failure_count",
    ):
        if key in document:
            summary[key] = document[key]
    return summary


def emit_handoff(args: argparse.Namespace, value: dict[str, Any]) -> dict[str, Any]:
    path = workflow_handoff_path(args)
    value = script_runtime.sanitize_handoff(value)
    document = {
        "schema_version": 1,
        "run_id": args.run_id,
        "business_date": args.business_date,
        **value,
    }
    atomic_replace_json(path, document)
    summary = handoff_summary(document, path)
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    return summary


def emit_busy_handoff(args: argparse.Namespace) -> dict[str, Any]:
    path = workflow_handoff_path(args)
    stage = "collection_enrichment"
    if path.is_file():
        document = read_json(path)
        if (
            document.get("run_id") == args.run_id
            and document.get("business_date") == args.business_date
        ):
            stage = str(document.get("stage") or stage)
    summary = handoff_summary({
        "run_id": args.run_id,
        "business_date": args.business_date,
        "ok": True,
        "action": "waiting_stage_process",
        "stage": stage,
        "status": "waiting",
    }, path)
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    return summary


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


def source_url(row: dict[str, Any]) -> str:
    for key in ("source_url", "内容链接", "来源链接"):
        value = canonical_url(str(row.get(key) or ""))
        if value:
            return value
    return ""


def normalize_source_fields(row: dict[str, Any]) -> dict[str, Any]:
    value = source_url(row)
    if value:
        row["source_url"] = value
    match = re.search(r"douyin\.com/video/(\d+)", value)
    if match and not str(row.get("aweme_id") or "").strip():
        row["aweme_id"] = match.group(1)
    return row


def stable_item_id(row: dict[str, Any]) -> str:
    normalize_source_fields(row)
    existing = str(row.get("item_id") or "").strip()
    if existing:
        return existing
    aweme_id = str(row.get("aweme_id") or "").strip()
    if not aweme_id:
        url = source_url(row)
        match = re.search(r"douyin\.com/video/(\d+)", url)
        aweme_id = match.group(1) if match else ""
    if aweme_id:
        return f"douyin:{aweme_id}"
    external = str(row.get("external_id") or "").strip()
    if external:
        source = str(row.get("source") or row.get("平台") or "external").strip().lower()
        return f"{source}:{external}"
    url = source_url(row)
    if url:
        return f"url:{url}"
    local_id = str(row.get("local_id") or "").strip()
    if not local_id:
        local_id = f"local:{uuid.uuid4()}"
        row["local_id"] = local_id
    return local_id


def normalize_items(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    accepted: dict[str, dict[str, Any]] = {}
    failures: list[dict[str, str]] = []
    conflicted: set[str] = set()
    for raw in rows:
        row = json.loads(json.dumps(raw, ensure_ascii=False))
        normalize_source_fields(row)
        identity = stable_item_id(row)
        row["item_id"] = identity
        if identity in conflicted:
            continue
        current = accepted.get(identity)
        if current is None:
            accepted[identity] = row
        elif identity.startswith(("douyin:", "url:")):
            accepted[identity] = merge_candidate_rows(current, row)
        elif canonical(current) != canonical(row):
            accepted.pop(identity, None)
            conflicted.add(identity)
            failures.append({"item_id": identity, "reason": "stable_item_conflict"})
        else:
            accepted[identity] = current
    return [accepted[key] for key in sorted(accepted)], failures


def page_owned_items(value: Any) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    if isinstance(value, dict):
        items = value.get("item_list")
        if isinstance(items, list):
            output.extend(row for row in items if isinstance(row, dict))
        for child in value.values():
            output.extend(page_owned_items(child))
    elif isinstance(value, list):
        for child in value:
            output.extend(page_owned_items(child))
    return output


def normalize_page_owned_facts(item: dict[str, Any], raw_path: Path) -> dict[str, Any]:
    aweme_id = str(item.get("aweme_id") or "")
    statistics = item.get("statistics") if isinstance(item.get("statistics"), dict) else {}
    video = item.get("video") if isinstance(item.get("video"), dict) else {}
    play_addr = video.get("play_addr") if isinstance(video.get("play_addr"), dict) else {}
    playable_urls = play_addr.get("url_list") if isinstance(play_addr.get("url_list"), list) else []
    playable_url = next((str(value) for value in playable_urls if str(value).startswith("http")), "")
    create_time = item.get("create_time")
    published_at = ""
    if isinstance(create_time, int) and create_time > 0:
        published_at = dt.datetime.fromtimestamp(
            create_time, tz=dt.timezone.utc,
        ).isoformat().replace("+00:00", "Z")
    facts: dict[str, Any] = {}
    missing: dict[str, str] = {}
    for field, raw_field in (
        ("likes", "digg_count"),
        ("comments", "comment_count"),
        ("favorites", "collect_count"),
        ("shares", "share_count"),
    ):
        raw = statistics.get(raw_field)
        if isinstance(raw, (int, float)) and raw >= 0:
            facts[field] = int(raw)
        else:
            facts[field] = None
            missing[field] = "field_not_returned"
    if not published_at:
        missing["published_at"] = "field_not_returned"
    duration = video.get("duration")
    return {
        "aweme_id": aweme_id,
        "source_url": f"https://www.douyin.com/video/{aweme_id}" if aweme_id else "",
        "published_at": published_at,
        "duration_seconds": (
            max(1, round(float(duration) / 1000))
            if isinstance(duration, (int, float)) and duration > 0 else None
        ),
        "playable_url": playable_url,
        "media_identity": str(play_addr.get("uri") or ""),
        **facts,
        "fact_missing_reasons": missing,
        "fact_provenance": {
            "capture": "configured_account_page_owned_payload",
            "artifact": str(raw_path.name),
            "response_fields": {
                "published_at": "create_time",
                "likes": "statistics.digg_count",
                "comments": "statistics.comment_count",
                "favorites": "statistics.collect_count",
                "shares": "statistics.share_count",
                "duration_seconds": "video.duration",
            },
        },
    }


def configured_account_facts(run_dir: Path) -> dict[str, dict[str, Any]]:
    raw_root = run_dir / "sources" / "douyin" / "raw_resolver"
    output: dict[str, dict[str, Any]] = {}
    for path in sorted(raw_root.glob("*.json")) if raw_root.is_dir() else []:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for item in page_owned_items(payload):
            facts = normalize_page_owned_facts(item, path)
            if facts["source_url"]:
                current = output.get(facts["source_url"], {})
                output[facts["source_url"]] = {
                    **current,
                    **{
                        key: value for key, value in facts.items()
                        if value not in (None, "", [], {}) or key not in current
                    },
                }
    return output


def adapt_collection_rows(
    rows: list[dict[str, Any]],
    *,
    run_dir: Path,
) -> list[dict[str, Any]]:
    facts_by_url = configured_account_facts(run_dir)
    output = []
    for raw in rows:
        row = normalize_source_fields(json.loads(json.dumps(raw, ensure_ascii=False)))
        url = source_url(row)
        direct_supported = any(
            row.get(key) not in (None, "")
            for key in ("published_at", "发布时间", "likes", "comments", "favorites", "shares")
        )
        if direct_supported:
            if not row.get("published_at") and row.get("发布时间"):
                row["published_at"] = row["发布时间"]
            row.setdefault("fact_missing_reasons", {})
            row.setdefault(
                "fact_provenance",
                {"capture": "configured_account_page_owned_works_response"},
            )
        facts = None if direct_supported else facts_by_url.get(url)
        captured = facts_by_url.get(url)
        if captured:
            for key in ("playable_url", "media_identity", "duration_seconds"):
                if captured.get(key) not in (None, ""):
                    row[key] = captured[key]
        if facts:
            for key, value in facts.items():
                if key in {"source_url", "aweme_id"} or (value is not None and value != ""):
                    row[key] = value
            row["fact_missing_reasons"] = facts["fact_missing_reasons"]
            row["fact_provenance"] = facts["fact_provenance"]
        elif "douyin.com/video/" in url and not direct_supported:
            row.update({
                "discovery_source": "configured_account",
                "published_at": str(row.get("published_at") or row.get("发布时间") or ""),
                "likes": None,
                "comments": None,
                "favorites": None,
                "shares": None,
                "fact_missing_reasons": {
                    "published_at": (
                        "" if row.get("published_at") else "page_owned_payload_not_available"
                    ),
                    "likes": "page_owned_payload_not_available",
                    "comments": "page_owned_payload_not_available",
                    "favorites": "page_owned_payload_not_available",
                    "shares": "page_owned_payload_not_available",
                },
                "fact_provenance": {"capture": "configured_account_collection"},
            })
        if "douyin.com/video/" in url:
            row["discovery_source"] = "configured_account"
        output.append(row)
    return output


def merge_exact_today_new_rows(
    collection: dict[str, Any],
    *,
    run_dir: Path,
    run_id: str,
) -> dict[str, Any]:
    """Promote exact-run new works before legacy topic filtering can drop them."""
    source_path = run_dir / "sources" / "current_run_rows.jsonl"
    if not source_path.is_file():
        return collection
    page_owned_by_url = configured_account_facts(run_dir)
    content = list(collection.get("content_items") or [])
    candidates = list(collection.get("candidates") or [])
    known_content_urls = {source_url(row) for row in content if source_url(row)}
    known_candidate_urls = {source_url(row) for row in candidates if source_url(row)}
    exclusions: list[dict[str, str]] = []
    encountered = 0
    promoted = 0
    already_present = 0
    for index, line in enumerate(source_path.read_text(encoding="utf-8").splitlines()):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            exclusions.append({
                "item_id": f"today-new-row:{index}",
                "reason": "today_new_row_invalid_json",
            })
            continue
        if raw.get("候选时态") != "today_new":
            continue
        encountered += 1
        if raw.get("首次发现批次") != run_id or raw.get("运行批次") != run_id:
            exclusions.append({
                "item_id": f"today-new-row:{index}",
                "reason": "today_new_wrong_run",
            })
            continue
        row = normalize_source_fields(json.loads(json.dumps(raw, ensure_ascii=False)))
        url = source_url(row)
        if not url:
            exclusions.append({
                "item_id": f"today-new-row:{index}",
                "reason": "today_new_identity_missing",
            })
            continue
        captured = page_owned_by_url.get(url, {})
        for key in ("playable_url", "media_identity", "duration_seconds"):
            if captured.get(key) not in (None, ""):
                row[key] = captured[key]
        angle = candidate_angle(row)
        row.update({
            "run_id": run_id,
            "item_id": stable_item_id(row),
            "candidate_id": f"{stable_item_id(row)}::angle:{urllib.parse.quote(angle, safe='')}",
            "discovery_source": "configured_account",
            "today_new": True,
            "source_title": str(row.get("内容标题") or row.get("title") or ""),
            "source_summary": str(row.get("正文/字幕/简介片段") or row.get("summary") or ""),
        })
        if url not in known_content_urls:
            content.append(row)
            known_content_urls.add(url)
        if url not in known_candidate_urls:
            candidates.append(row)
            known_candidate_urls.add(url)
            promoted += 1
        else:
            already_present += 1
    output = json.loads(json.dumps(collection, ensure_ascii=False))
    output["content_items"] = content
    output["candidates"] = candidates
    output["today_new_promotion"] = {
        "source": "exact_run_current_rows",
        "encountered_count": encountered,
        "promoted_count": promoted,
        "already_present_count": already_present,
        "exclusions": exclusions,
    }
    return output


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
        value = adopt_collected_artifacts(args)
        run_dir = Path(args.adopt_collected_artifacts).resolve()
        value["content_items"] = adapt_collection_rows(
            value["content_items"], run_dir=run_dir,
        )
        value["candidates"] = adapt_collection_rows(
            value["candidates"], run_dir=run_dir,
        )
        configured_count = sum(
            str(row.get("discovery_source") or "") == "configured_account"
            for row in value["candidates"]
        )
        value.update({
            "configured_account_status": (
                "partial" if value["status"] == "completed_with_failures"
                else ("completed" if configured_count else "completed_empty")
            ),
            "configured_account_reason": (
                "account_failures_isolated"
                if value["status"] == "completed_with_failures" else ""
            ),
            "configured_account_captured_at": args.business_date,
        })
        return merge_exact_today_new_rows(
            value, run_dir=run_dir, run_id=args.run_id,
        )
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
    with (run_dir / "content_items.csv").open(encoding="utf-8-sig", newline="") as handle:
        content = adapt_collection_rows(list(csv.DictReader(handle)), run_dir=run_dir)
    with (run_dir / "today_10_topics.csv").open(encoding="utf-8-sig", newline="") as handle:
        candidates = adapt_collection_rows(list(csv.DictReader(handle)), run_dir=run_dir)
    configured_count = sum(
        str(row.get("discovery_source") or "") == "configured_account"
        for row in candidates
    )
    configured_status = (
        "partial" if str(value.get("collection_status") or "").endswith("_with_failures")
        else ("completed" if configured_count else "completed_empty")
    )
    return merge_exact_today_new_rows({
        "run_id": args.run_id, "business_date": args.business_date,
        "status": value.get("collection_status", "completed"),
        "content_items": content, "candidates": candidates,
        "source_runs": value.get("source_outcomes", []),
        "configured_account_status": configured_status,
        "configured_account_reason": (
            "account_failures_isolated" if configured_status == "partial" else ""
        ),
        "configured_account_captured_at": str(value.get("generated_at") or ""),
    }, run_dir=run_dir, run_id=args.run_id)


def candidate_angle(candidate: dict[str, Any]) -> str:
    for key in (
        "主题聚类ID", "topic_cluster_id", "我的选题标题", "可发布标题",
        "原始来源标题", "内容标题", "source_title", "title",
    ):
        value = re.sub(r"\s+", " ", str(candidate.get(key) or "")).strip()
        if value:
            return value
    return ""


def merge_candidate_rows(current: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    output = dict(current)
    for key in sorted(set(current) | set(incoming)):
        left, right = current.get(key), incoming.get(key)
        if key == "discovery_sources":
            output[key] = sorted({
                str(value) for values in (left, right)
                if isinstance(values, list) for value in values if str(value)
            })
            continue
        left_empty = left is None or left == ""
        right_empty = right is None or right == ""
        if left_empty and not right_empty:
            output[key] = right
        elif not left_empty and not right_empty and left != right:
            output[key] = min((left, right), key=lambda value: canonical(value))
    return output


def normalize_collection_candidates(
    candidates: Any,
    *,
    items: list[dict[str, Any]],
    run_id: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, str]]]:
    if not isinstance(candidates, list):
        raise WorkflowConflict("collection_candidates_invalid")
    normalized: dict[str, dict[str, Any]] = {}
    video_by_item: dict[str, dict[str, Any]] = {}
    failures: list[dict[str, str]] = []
    conflicted: set[str] = set()
    item_ids = {str(row["item_id"]) for row in items}
    item_by_url = {
        source_url(row): str(row["item_id"]) for row in items if source_url(row)
    }
    for index, raw in enumerate(candidates):
        if not isinstance(raw, dict):
            failures.append({
                "item_id": f"candidate-row:{index}",
                "reason": "collection_candidate_invalid",
            })
            continue
        candidate = normalize_source_fields(json.loads(json.dumps(raw, ensure_ascii=False)))
        url = source_url(candidate)
        explicit_candidate = str(candidate.get("candidate_id") or "")
        mapped_item = str(
            candidate.get("item_id")
            or item_by_url.get(url)
            or (explicit_candidate if explicit_candidate in item_ids else "")
        )
        try:
            if not mapped_item:
                mapped_item = stable_item_id(candidate)
        except (TypeError, ValueError) as error:
            failures.append({
                "item_id": f"candidate-row:{index}",
                "reason": f"collection_candidate_identity_missing:{type(error).__name__}",
            })
            continue
        if mapped_item not in item_ids:
            failures.append({
                "item_id": f"candidate-row:{index}",
                "reason": "collection_candidate_content_mapping_missing",
            })
            continue
        angle = candidate_angle(candidate)
        if not angle:
            failures.append({
                "item_id": mapped_item,
                "reason": "collection_candidate_angle_missing",
            })
            continue
        identity = str(
            candidate.get("candidate_id")
            or f"{mapped_item}::angle:{urllib.parse.quote(angle, safe='')}"
        )
        candidate["candidate_id"] = identity
        candidate["item_id"] = mapped_item
        candidate.setdefault("merged_input_count", 1)
        if identity in conflicted:
            continue
        current = normalized.get(identity)
        if current is not None:
            if candidate_angle(current) != angle:
                normalized.pop(identity, None)
                video_by_item.pop(mapped_item, None)
                conflicted.add(identity)
                failures.append({
                    "item_id": identity,
                    "reason": "collection_candidate_identity_conflict",
                })
                continue
            merged = merge_candidate_rows(current, candidate)
            merged["merged_input_count"] = int(current.get("merged_input_count") or 1) + 1
            normalized[identity] = merged
            continue
        normalized[identity] = candidate
        has_video_identity = bool(candidate.get("aweme_id") or candidate.get("discovery_source"))
        if has_video_identity:
            if candidate.get("run_id") not in {None, "", run_id}:
                normalized.pop(identity, None)
                failures.append({
                    "item_id": identity,
                    "reason": "collection_video_candidate_wrong_run",
                })
                continue
            candidate["run_id"] = run_id
            video_by_item.setdefault(mapped_item, candidate)
    return list(normalized.values()), list(video_by_item.values()), failures


def _compact_editorial_source(source: dict[str, Any]) -> dict[str, Any]:
    """Keep candidate-local facts without forwarding collection bookkeeping."""
    title = source.get("title")
    summary = source.get("summary")
    engagement = source.get("engagement")
    compact_engagement = {
        key: engagement.get(key)
        for key in ("likes", "comments", "favorites", "shares")
        if isinstance(engagement, dict) and engagement.get(key) is not None
    }
    keys = (
        "source_id", "url", "platform", "author", "title",
        "published_display", "recency_cohort", "signal_source",
        "source_role", "business_signal_role",
        "understanding_status", "understanding_failure",
    )
    output = {
        key: source.get(key)
        for key in keys
        if source.get(key) not in (None, "", [], {})
    }
    if compact_engagement:
        output["engagement"] = compact_engagement
    if summary not in (None, "", [], {}) and summary != title:
        output["summary"] = summary
    return output


def _compact_editorial_card(
    card: dict[str, Any],
    package: dict[str, Any] | None,
) -> dict[str, Any]:
    """Expose a readable full-pool index and candidate-local same-run evidence."""
    output = {
        key: card.get(key)
        for key in (
            "candidate_id", "run_id", "item_id", "representative_item_id",
            "trend_event_id", "title", "source_url",
            "fact_boundary", "cannot_claim", "source_count", "merged_input_count",
        )
        if card.get(key) not in (None, "", [], {})
    }
    sources = [
        _compact_editorial_source(source)
        for source in card.get("sources", [])
        if isinstance(source, dict)
    ]
    output["sources"] = sources
    output["business_signal_roles"] = sorted({
        str(source.get("business_signal_role"))
        for source in sources
        if source.get("business_signal_role")
    })
    screening = card.get("editorial_screening")
    if isinstance(screening, dict):
        # The first-pass reason can contain a screening recommendation such as
        # "先观察". Final editorial must judge the candidate itself, so expose
        # only the media request facts needed to locate candidate-local evidence.
        output["editorial_screening"] = {
            key: screening.get(key)
            for key in ("available_video_source_ids", "requested")
            if key == "available_video_source_ids"
            or screening.get(key) not in (None, "", [], {})
        }
    deep_read = card.get("deep_read")
    if isinstance(deep_read, dict):
        output["deep_read"] = {
            key: deep_read.get(key)
            for key in (
                "requested_count", "attempted_count", "completed_count",
                "failed_count", "status",
            )
            if deep_read.get(key) is not None
        }
    qualification = card.get("qualification")
    if isinstance(qualification, dict):
        output["signal_evidence"] = {
            "traffic_state": qualification.get("traffic_state"),
            "recency_cohorts": qualification.get("recency_cohorts"),
            "persona_state": qualification.get("persona_state"),
            "authenticity_state": qualification.get("authenticity_state"),
        }
        output["signal_evidence"] = {
            key: value
            for key, value in output["signal_evidence"].items()
            if value not in (None, "", [], {})
        }
    if package:
        evidence = compact_video_evidence(package)
        if evidence is not None:
            output["video_evidence"] = {
                "candidate_local": True,
                "status": package.get("status"),
                "run_id": package.get("run_id"),
                "source_url": package.get("source_url"),
                **evidence,
            }
    else:
        output["video_evidence"] = None
    return output


def editorial_handoff_candidates(collection: dict[str, Any]) -> list[dict[str, Any]]:
    """Return every trusted card with only source facts and same-run evidence."""
    packages = {
        str(row.get("candidate_id") or ""): row.get("package")
        for row in (collection.get("understanding_results") or [])
        if isinstance(row, dict) and str(row.get("candidate_id") or "")
    }
    return [
        _compact_editorial_card(row, packages.get(str(row.get("candidate_id") or "")))
        for row in (collection.get("candidates") or [])
        if isinstance(row, dict) and str(row.get("candidate_id") or "")
    ]


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
    source_local_identities: dict[str, list[str]] = {
        source: [] for source in SOURCE_LEDGER_NAMES
    }
    routes_by_identity: dict[str, set[str]] = {}
    for row in candidates:
        if str(row.get("discovery_source") or "") != "configured_account":
            continue
        identity = stable_item_id(row)
        if identity in source_local_identities["configured_account"]:
            raise WorkflowConflict("source_ledger_source_duplicate:configured_account")
        source_local_identities["configured_account"].append(identity)
        routes_by_identity.setdefault(identity, set()).add("configured_account")
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
        source = str(row["discovery_source"])
        if row["candidate_id"] in source_local_identities[source]:
            raise WorkflowConflict(f"source_ledger_source_duplicate:{source}")
        source_local_identities[source].append(row["candidate_id"])
        routes_by_identity.setdefault(row["candidate_id"], set()).add(source)
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
    for row in candidates:
        identity = stable_item_id(row)
        row["discovery_sources"] = sorted(routes_by_identity.get(identity, set()))
    ledger = [{
        "source": "configured_account",
        "attempted": True,
        "status": configured_status,
        "discovered_count": len(source_local_identities["configured_account"]),
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
    output["source_local_identities"] = {
        source: sorted(identities)
        for source, identities in source_local_identities.items()
    }
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
    supplied_identities = collection.get("source_local_identities")
    if not isinstance(supplied_identities, dict):
        raise WorkflowConflict("source_ledger_identities_missing")
    source_identities: dict[str, set[str]] = {}
    for source in SOURCE_LEDGER_NAMES:
        values = supplied_identities.get(source)
        if not isinstance(values, list) or any(not isinstance(value, str) or not value for value in values):
            raise WorkflowConflict(f"source_ledger_identities_invalid:{source}")
        if len(values) != len(set(values)):
            raise WorkflowConflict(f"source_ledger_source_duplicate:{source}")
        source_identities[source] = set(values)
    for source in SOURCE_LEDGER_NAMES:
        row = by_source.get(source)
        if row is None:
            raise WorkflowConflict(f"source_ledger_attempt_missing:{source}")
        if row["discovered_count"] != len(source_identities[source]):
            raise WorkflowConflict(f"source_ledger_count_conflict:{source}")
    global_identities = {str(row.get("item_id") or "") for row in video_candidates}
    if "" in global_identities or global_identities != set().union(*source_identities.values()):
        raise WorkflowConflict("source_ledger_global_identity_conflict")
    for row in video_candidates:
        identity = str(row["item_id"])
        expected_sources = sorted(
            source for source, identities in source_identities.items()
            if identity in identities
        )
        if row.get("discovery_sources") != expected_sources:
            raise WorkflowConflict("source_ledger_provenance_conflict")
    if not any(row["attempted"] for row in by_source.values()):
        raise WorkflowConflict("all_sources_unattempted")
    if not video_candidates:
        raise WorkflowConflict("all_sources_without_safe_candidates")
    return [by_source[source] for source in SOURCE_LEDGER_NAMES]


def enrich(
    args: argparse.Namespace,
    collection: dict[str, Any],
    *,
    requested_candidate_ids: set[str] | None = None,
    screening_rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    value = json.loads(json.dumps(collection, ensure_ascii=False))
    if value.get("run_id") != args.run_id:
        raise WorkflowConflict("collection_wrong_run")
    items, identity_failures = normalize_items(value.get("content_items", []))
    source_candidates = value.get("legacy_candidates")
    if not isinstance(source_candidates, list) or not source_candidates:
        source_candidates = value.get("candidates", [])
    legacy_candidates, video_candidates, candidate_failures = normalize_collection_candidates(
        source_candidates,
        items=items,
        run_id=args.run_id,
    )
    source_ledger = (
        normalize_source_ledger(value, video_candidates=video_candidates)
        if (
            "source_local_identities" in value
            or "source_ledger" in value
            and (bool(value.get("source_ledger")) or requested_candidate_ids is None)
        ) else []
    )
    hotspot_cards = build_hotspot_cards(
        legacy_candidates,
        items=items,
        run_id=args.run_id,
    )
    screening_reason_by_id = {
        str(row.get("candidate_id") or ""): str(row.get("reason") or "").strip()
        for row in screening_rows or []
        if isinstance(row, dict)
    }
    video_source_ids = {
        source_url(row) for row in video_candidates if source_url(row)
    }
    for card in hotspot_cards:
        candidate_id = str(card.get("candidate_id") or "")
        available_video_source_ids = sorted(
            str(source.get("source_id") or "")
            for source in card.get("sources", [])
            if str(source.get("source_id") or "") in video_source_ids
        )
        if requested_candidate_ids is not None:
            requested = candidate_id in requested_candidate_ids
            if requested and not available_video_source_ids:
                raise WorkflowConflict("editorial_screening_video_request_invalid")
            card["editorial_screening"] = {
                "requested": requested,
                "reason": screening_reason_by_id.get(candidate_id, ""),
                "available_video_source_ids": available_video_source_ids,
            }
            card["qualification"]["eligible_for_deep_read"] = requested
            card["qualification"]["status"] = (
                "requested_for_deep_read" if requested else "not_requested_by_editorial"
            )
            card["qualification"]["deep_read_eligibility_owner"] = (
                EDITORIAL_SKILL
            )
            selected_representatives = select_representative_sources(
                card.get("sources", [])
            ) if requested else []
            card["representative_source_ids"] = [
                source_id for source_id in selected_representatives
                if source_id in video_source_ids
            ]
            if requested and not card["representative_source_ids"]:
                card["representative_source_ids"] = available_video_source_ids[:1]
    representative_video_candidates = representative_candidates(
        hotspot_cards,
        video_candidates,
        requested_candidate_ids,
    )
    requested_source_ids = {
        source_url(row) for row in representative_video_candidates if source_url(row)
    }
    packages: list[dict[str, Any]]
    producer_failures: list[dict[str, Any]]
    if args.qa_frozen_packages:
        packages = read_json(args.qa_frozen_packages)
        if any(str(row.get("run_id") or "") != str(value.get("run_id") or "") for row in packages):
            raise WorkflowConflict("video_package_run_mismatch")
        if requested_candidate_ids is not None:
            packages = [
                row for row in packages
                if source_url(row) in requested_source_ids
            ]
        producer_failures = [
            {
                "item_id": f"douyin:{row.get('aweme_id')}",
                "source_url": row.get("source_url"),
                "reason": row.get("failure"),
            }
            for row in packages if row.get("status") == "failed"
        ]
    elif args.video_mode == "normal" and requested_candidate_ids is not None and not representative_video_candidates:
        packages, producer_failures = [], []
    elif args.video_mode == "normal":
        produced = produce(args, discovered_candidates=representative_video_candidates)
        packages = produced["packages"]
        producer_failures = [{
            "item_id": str(row.get("item_id") or row.get("candidate_id") or ""),
            "source_url": row.get("source_url"),
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
    hotspot_cards, understanding_results = attach_understanding(
        hotspot_cards,
        packages,
        producer_failures,
        require_viewable_keyframes=args.video_mode == "normal",
        run_id=args.run_id,
    )
    qualified_editorial_candidates = (
        hotspot_cards
        if requested_candidate_ids is not None
        else editorial_candidates(hotspot_cards)
    )
    deep_read_summary = deep_read_counts(
        hotspot_cards,
        model_owned_pool=requested_candidate_ids is not None,
    )
    value.update({
        "content_items": items,
        "legacy_candidates": legacy_candidates,
        "legacy_candidate_count": len(legacy_candidates),
        "candidates": hotspot_cards,
        "editorial_candidates": qualified_editorial_candidates,
        "editorial_candidate_count": len(qualified_editorial_candidates),
        "hotspot_cards": hotspot_cards,
        "hotspot_card_count": len(hotspot_cards),
        "representative_source_count": len(representative_video_candidates),
        "trusted_candidate_count": len(hotspot_cards),
        "screening_requested_candidate_ids": sorted(requested_candidate_ids or set()),
        "screening_requested_source_ids": sorted(requested_source_ids),
        "deep_read_summary": deep_read_summary,
        **deep_read_summary,
        "understanding_results": understanding_results,
        "item_failures": identity_failures + candidate_failures + producer_failures,
        "source_ledger": source_ledger,
        "substitute_count": 0,
    })
    return value


def validate_editorial_screening(
    run_id: str,
    result: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], set[str]]:
    """Validate the first pass without making any visible editorial decision."""
    if result.get("run_id") != run_id or not isinstance(result.get("screening"), list):
        raise WorkflowConflict("editorial_screening_result_invalid")
    allowed = {str(row.get("candidate_id") or "") for row in candidates}
    rows = result["screening"]
    identities = [str(row.get("candidate_id") or "") for row in rows if isinstance(row, dict)]
    if (
        len(rows) != len(identities)
        or set(identities) != allowed
        or len(identities) != len(set(identities))
    ):
        raise WorkflowConflict("editorial_screening_coverage_incomplete")
    by_id = {str(row.get("candidate_id") or ""): row for row in candidates}
    requested: set[str] = set()
    normalized: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            raise WorkflowConflict("editorial_screening_row_invalid")
        identity = str(row.get("candidate_id") or "")
        request = row.get("request_deep_read")
        reason = str(row.get("reason") or "").strip()
        if identity not in by_id or not isinstance(request, bool) or not reason:
            raise WorkflowConflict("editorial_screening_row_invalid")
        available = (
            by_id[identity].get("editorial_screening", {})
            .get("available_video_source_ids", [])
        )
        if request and (
            not isinstance(available, list) or not available
        ):
            raise WorkflowConflict("editorial_screening_video_request_invalid")
        if request:
            requested.add(identity)
        normalized.append({
            "candidate_id": identity,
            "request_deep_read": request,
            "reason": reason,
        })
    return normalized, requested


def validate_editorial(run_id: str, result: dict[str, Any], candidates: list[dict[str, Any]]) -> None:
    if result.get("run_id") != run_id or not isinstance(result.get("topics"), list):
        raise WorkflowConflict("editorial_result_invalid")
    allowed = {str(row.get("candidate_id")) for row in candidates}
    requires_standalone = run_id[4:12] >= "20260804"
    identities = [str(row.get("candidate_id") or "") for row in result["topics"]]
    if set(identities) != allowed or len(identities) != len(set(identities)):
        raise WorkflowConflict("editorial_result_coverage_incomplete")
    selected_count = sum(
        1 for row in result["topics"]
        if isinstance(row, dict) and row.get("decision") == "select"
    )
    if selected_count > MAX_FINAL_EDITORIAL_SELECT:
        raise WorkflowConflict("editorial_select_limit_exceeded")
    rows_by_identity = {
        str(row.get("candidate_id") or ""): row for row in result["topics"]
    }
    seen: set[str] = set()
    for row in result["topics"]:
        identity = str(row.get("candidate_id") or "")
        if identity not in allowed or identity in seen:
            raise WorkflowConflict("editorial_result_identity_conflict")
        seen.add(identity)
        if row.get("decision") not in {"select", "observe", "reject", "failed"}:
            raise WorkflowConflict("editorial_result_invalid")
        standalone = row.get("standalone_eligibility")
        if requires_standalone and (not isinstance(standalone, dict) or standalone.get("decision") not in {
            "select", "observe", "reject", "failed",
        } or not str(standalone.get("reason") or "").strip()):
            raise WorkflowConflict("editorial_standalone_eligibility_missing")
        if requires_standalone and row["decision"] != standalone["decision"]:
            if standalone["decision"] != "select" or row["decision"] not in {"observe", "reject"}:
                raise WorkflowConflict("editorial_standalone_decision_promotion")
            duplicate = row.get("duplicate_relation")
            if isinstance(duplicate, dict):
                representative_id = str(duplicate.get("duplicate_of") or "")
                representative = rows_by_identity.get(representative_id)
                representative_standalone = (
                    representative.get("standalone_eligibility")
                    if isinstance(representative, dict) else None
                )
                valid_duplicate = (
                    standalone["decision"] == "select"
                    and row["decision"] in {"observe", "reject"}
                    and representative_id in allowed
                    and representative_id != identity
                    and isinstance(representative, dict)
                    and representative.get("decision") == "select"
                    and isinstance(representative_standalone, dict)
                    and representative_standalone.get("decision") == "select"
                    and all(duplicate.get(key) is True for key in (
                        "same_user_conflict", "same_core_judgment", "same_action_or_experiment",
                    ))
                )
                if not valid_duplicate:
                    raise WorkflowConflict("editorial_standalone_decision_demotion")
        if row["decision"] in {"select", "observe", "reject"} and not str(
            row.get("selection_reason") or ""
        ).strip():
            raise WorkflowConflict("editorial_candidate_reason_missing")
        if row["decision"] in {"observe", "reject"}:
            basis = row.get("decision_basis")
            if isinstance(basis, dict) and not all(
                str(basis.get(key) or "").strip()
                for key in ("content", "persona", "differentiation")
            ):
                raise WorkflowConflict("editorial_nonselect_topic_basis_missing")
            reason = str(row.get("selection_reason") or "").lower()
            evidence_only_reasons = (
                "evidence insufficient", "insufficient evidence",
                "independent corroboration is missing", "missing independent corroboration",
                "证据不足", "缺少独立佐证", "缺少证据",
            )
            if reason.strip() in evidence_only_reasons:
                raise WorkflowConflict("editorial_nonselect_evidence_gate_forbidden")


def validate_editorial_understanding_consistency(
    run_id: str,
    result: dict[str, Any],
    collection: dict[str, Any],
) -> None:
    """Reject only contradictions between final decisions and committed media state."""
    if result.get("run_id") != run_id or not isinstance(result.get("topics"), list):
        raise WorkflowConflict("editorial_result_invalid")
    cards = {
        str(row.get("candidate_id") or ""): row
        for row in collection.get("candidates", [])
        if isinstance(row, dict) and str(row.get("candidate_id") or "")
    }
    packages = {
        str(row.get("candidate_id") or ""): row.get("package")
        for row in collection.get("understanding_results", [])
        if isinstance(row, dict) and str(row.get("candidate_id") or "")
    }
    for row in result["topics"]:
        identity = str(row.get("candidate_id") or "")
        card = cards.get(identity)
        if card is None:
            raise WorkflowConflict("editorial_result_identity_conflict")
        deep_read = card.get("deep_read") if isinstance(card.get("deep_read"), dict) else {}
        status = str(deep_read.get("status") or "")
        if status not in {"completed", "completed_with_failures"}:
            continue
        package = packages.get(identity)
        if not isinstance(package, dict) or package.get("status") not in {
            "completed", "completed_with_failures",
        }:
            raise WorkflowConflict("editorial_completed_material_conflict")
        if row.get("decision") == "failed":
            raise WorkflowConflict("editorial_completed_material_conflict")
        if row.get("decision") not in {"select", "observe", "reject"}:
            continue
        analyzed_source_ids = {
            str(source.get("source_id") or "")
            for source in card.get("sources", [])
            if isinstance(source, dict) and source.get("understanding_status") == "analyzed"
        }
        evidence_ids = {
            str(value) for value in (row.get("evidence_source_ids") or [])
        }
        if not analyzed_source_ids or not evidence_ids.intersection(analyzed_source_ids):
            raise WorkflowConflict("editorial_completed_evidence_missing")


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
    failed: set[str] = set()
    for row in result.get("failures", []):
        identity = str(row.get("topic_id") or "")
        if identity not in selected or identity in seen or identity in failed:
            raise WorkflowConflict("script_result_identity_conflict")
        failed.add(identity)
        if row.get("reason") not in {
            "material_or_angle_insufficiency", "material_insufficiency",
        } or not str(row.get("detail") or "").strip():
            raise WorkflowConflict("script_result_incomplete")
    if seen | failed != selected or seen & failed:
        raise WorkflowConflict("script_result_coverage_incomplete")


def first_context_value(rows: list[dict[str, Any]], *keys: str) -> Any:
    for row in rows:
        for key in keys:
            value = row.get(key)
            if value not in (None, "", [], {}):
                return value
    return None


def compact_video_understanding(package: dict[str, Any] | None) -> dict[str, Any] | None:
    if not package or package.get("status") not in {"completed", "completed_with_failures"}:
        return None
    representatives = package.get("representative_packages")
    if isinstance(representatives, list):
        return {
            "status": package.get("status"),
            "run_id": package.get("run_id"),
            "source_url": package.get("source_url"),
            "cluster_synthesis": package.get("cluster_synthesis") or {},
            "visual_reading": {
                "direct_view_required": True,
                "same_run_only": True,
                "source_text_context": ["asr_supplement", "screen_facts"],
                "observations_must_be_separated_from_interpretation": True,
            },
            "representative_sources": [
                compact_video_understanding(row)
                for row in representatives
                if isinstance(row, dict)
            ],
        }
    asr = package.get("asr") if isinstance(package.get("asr"), dict) else {}
    screen_rows = package.get("screen_facts")
    if not isinstance(screen_rows, list):
        screen_rows = package.get("screen_text")
    if not isinstance(screen_rows, list):
        screen_rows = []
    keyframes = package.get("keyframes")
    if not isinstance(keyframes, list):
        keyframes = []
    return {
        "status": package.get("status"),
        "run_id": package.get("run_id"),
        "source_url": package.get("source_url"),
        "caption_timeline": package.get("caption_timeline") or [],
        "asr_supplement": asr.get("text") or package.get("asr_supplement") or None,
        "screen_facts": [
            {
                key: row.get(key)
                for key in ("kind", "value", "text", "time_second", "start", "verified")
                if row.get(key) is not None
            }
            for row in screen_rows
            if isinstance(row, dict)
        ],
        "keyframes": [
            {
                key: row.get(key)
                for key in ("time_second", "start", "path", "sha256")
                if row.get(key) is not None
            }
            for row in keyframes
            if isinstance(row, dict)
        ],
    }


def compact_video_evidence(package: dict[str, Any] | None) -> dict[str, Any] | None:
    """Expose same-run observations as evidence, never as a writing outline."""
    if not package or package.get("status") not in {"completed", "completed_with_failures"}:
        return None
    asr = package.get("asr") if isinstance(package.get("asr"), dict) else {}
    screen_rows = package.get("screen_facts")
    if not isinstance(screen_rows, list):
        screen_rows = package.get("screen_text")
    if not isinstance(screen_rows, list):
        screen_rows = []
    keyframes = package.get("keyframes")
    if not isinstance(keyframes, list):
        keyframes = []
    evidence = {
        "caption_timeline": package.get("caption_timeline") or [],
        "asr_supplement": asr.get("text") or package.get("asr_supplement") or None,
        "screen_facts": [
            {
                key: row.get(key)
                for key in ("kind", "value", "text", "time_second", "start", "verified")
                if row.get(key) is not None
            }
            for row in screen_rows
            if isinstance(row, dict)
        ],
        "keyframes": [
            {
                key: row.get(key)
                for key in ("time_second", "start", "path", "sha256")
                if row.get(key) is not None
            }
            for row in keyframes
            if isinstance(row, dict)
        ],
    }
    representatives = package.get("representative_packages")
    if isinstance(representatives, list):
        evidence["representative_sources"] = [
            compact_video_evidence(row)
            for row in representatives
            if isinstance(row, dict)
        ]
    return evidence


def compact_source_facts(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Expose source-owned facts/details without forwarding editorial blueprints."""
    facts: dict[str, Any] = {}
    aliases = {
        "details": (
            "source_details", "details", "source_facts", "public_facts",
            "fact_details", "content_facts", "source_summary", "source_text",
            "description", "正文/字幕/简介片段", "内容摘要", "事实细节", "事实摘要",
        ),
        "caption": ("caption", "caption_text", "字幕", "caption_timeline"),
        "transcript": (
            "transcript", "asr_text", "正文/字幕/简介片段", "口播转写", "ASR",
        ),
        "public_claims": (
            "public_claims", "supported_claims", "公开事实", "解析说明",
        ),
    }
    for key, names in aliases.items():
        value = first_context_value(rows, *names)
        if value not in (None, "", [], {}):
            facts[key] = value
    return facts


def build_scripts_handoff(
    run_id: str,
    business_date: str,
    collection: dict[str, Any],
    editorial: dict[str, Any],
) -> dict[str, Any]:
    candidates = {
        str(row.get("candidate_id") or ""): row
        for row in collection.get("candidates", [])
        if str(row.get("candidate_id") or "")
    }
    items = {
        str(row.get("item_id") or ""): row
        for row in collection.get("content_items", [])
        if str(row.get("item_id") or "")
    }
    understanding = {
        str(row.get("candidate_id") or ""): row.get("package")
        for row in collection.get("understanding_results", [])
        if str(row.get("candidate_id") or "")
    }
    selected_topics = []
    for topic in editorial.get("topics", []):
        if topic.get("decision") != "select":
            continue
        topic_id = str(topic.get("candidate_id") or "")
        candidate = candidates.get(topic_id)
        if candidate is None:
            raise WorkflowConflict("scripts_context_candidate_missing")
        item = items.get(str(candidate.get("item_id") or topic_id), {})
        rows = [topic, candidate, item]
        source_rows = [candidate, item]
        source_evidence = {
            "source": {
                key: first_context_value(source_rows, *aliases)
                for key, aliases in {
                    "title": ("source_title", "来源标题", "title", "内容标题"),
                    "summary": ("source_summary", "来源摘要", "summary", "内容摘要", "描述"),
                    "url": ("source_url", "来源链接", "内容链接", "canonical_url"),
                    "author": ("author", "作者", "账号"),
                    "published_at": ("published_at", "发布时间"),
                    "publication_display": ("published_at_display", "发布时间展示"),
                    "recency": ("published_recency", "recency"),
                    "likes": ("likes", "点赞数"),
                    "comments": ("comments", "评论数"),
                    "favorites": ("favorites", "收藏数"),
                    "shares": ("shares", "分享数"),
                }.items()
                if first_context_value(source_rows, *aliases) is not None
            },
            "source_facts": compact_source_facts(source_rows),
            "sources": [
                {
                    key: source.get(key)
                    for key in (
                        "source_id", "url", "platform", "author", "title",
                        "published_at", "published_display", "engagement",
                        "source_role", "understanding_status",
                    )
                    if source.get(key) not in (None, "", [], {})
                }
                for source in candidate.get("sources", [])
                if isinstance(source, dict)
            ],
            "video": compact_video_evidence(understanding.get(topic_id)),
        }
        selected_topics.append({
            "topic_id": topic_id,
            "trend_event_id": candidate.get("trend_event_id") or topic_id,
            "source_evidence": source_evidence,
        })
    return {
        "ok": True,
        "action": "scripts_required",
        "run_id": run_id,
        "business_date": business_date,
        "selected_topics": selected_topics,
        "skill_names": list(WRITER_SKILLS),
        "batch_contract": {
            "deterministic_controller_owns_order_and_checkpoint": True,
            "one_automation_codex_direct_writer_stage": True,
            "one_topic_per_submission": True,
            "submit_before_next_topic": True,
        },
    }


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


def terminal_refresh_backup_root(args: argparse.Namespace) -> Path:
    return (
        Path(args.artifact_root).resolve()
        / args.run_id
        / "revision_backups"
    )


def _refresh_backup_for(
    args: argparse.Namespace, editorial_sha256: str,
) -> Path | None:
    root = terminal_refresh_backup_root(args)
    if not root.is_dir():
        return None
    for manifest_path in sorted(root.glob("revision_*/manifest.json")):
        try:
            manifest = read_json(manifest_path)
        except (OSError, json.JSONDecodeError):
            continue
        if (
            manifest.get("run_id") == args.run_id
            and manifest.get("business_date") == args.business_date
            and manifest.get("new_editorial_sha256") == editorial_sha256
        ):
            return manifest_path.parent
    return None


def _backup_file(
    temporary_root: Path,
    files: list[dict[str, Any]],
    source: Path,
    relative_target: str,
    role: str,
) -> None:
    if source.is_symlink() or not source.is_file():
        raise WorkflowConflict("terminal_refresh_backup_file_invalid")
    destination = temporary_root / "files" / relative_target
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    raw = destination.read_bytes()
    files.append({
        "path": str(destination.relative_to(temporary_root)),
        "target": relative_target,
        "role": role,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "bytes": len(raw),
    })


def _write_refresh_backup(
    args: argparse.Namespace,
    workflow: DailyWorkflow,
    editorial: dict[str, Any],
) -> Path:
    editorial_sha256 = hashlib.sha256(canonical(editorial).encode()).hexdigest()
    existing = _refresh_backup_for(args, editorial_sha256)
    if existing is not None:
        return existing
    root = terminal_refresh_backup_root(args)
    root.mkdir(parents=True, exist_ok=True)
    revision_numbers = []
    for path in root.glob("revision_*"):
        match = re.fullmatch(r"revision_(\d+)", path.name)
        if match:
            revision_numbers.append(int(match.group(1)))
    revision_id = f"revision_{max(revision_numbers, default=0) + 1:03d}"
    final_root = root / revision_id
    if final_root.exists():
        raise WorkflowConflict("terminal_refresh_backup_conflict")
    temporary_root = Path(tempfile.mkdtemp(prefix=".revision_", dir=root))
    files: list[dict[str, Any]] = []
    try:
        state = workflow.read_run(args.run_id)
        run = state["run"]
        safe_run = {
            key: run.get(key)
            for key in (
                "run_id", "business_date", "status", "publish_status",
                "publish_error", "publish_key", "created_at", "updated_at",
                "published_at",
            )
        }
        metadata = {
            "run": safe_run,
            "stage_statuses": [
                {"stage": row["stage"], "status": row["status"],
                 "committed_at": row["committed_at"]}
                for row in state["stages"]
            ],
        }
        metadata_path = temporary_root / "files" / "terminal_metadata.json"
        metadata_path.parent.mkdir(parents=True, exist_ok=True)
        metadata_path.write_text(canonical(metadata) + "\n", encoding="utf-8")
        metadata_raw = metadata_path.read_bytes()
        files.append({
            "path": str(metadata_path.relative_to(temporary_root)),
            "target": "terminal_metadata.json",
            "role": "terminal_metadata",
            "sha256": hashlib.sha256(metadata_raw).hexdigest(),
            "bytes": len(metadata_raw),
        })
        for stage in ("editorial", "scripts"):
            value = workflow.stage(args.run_id, stage)
            if value is None:
                raise WorkflowConflict("terminal_refresh_stages_incomplete")
            stage_snapshot = {
                "stage": stage,
                "status": value["status"],
                "committed_at": value["committed_at"],
                "payload": value["payload"],
            }
            relative = f"stages/{stage}.json"
            target = temporary_root / "files" / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(canonical(stage_snapshot) + "\n", encoding="utf-8")
            raw = target.read_bytes()
            files.append({
                "path": str(target.relative_to(temporary_root)),
                "target": relative,
                "role": "stage_snapshot",
                "sha256": hashlib.sha256(raw).hexdigest(),
                "bytes": len(raw),
            })
        handoff = workflow_handoff_path(args)
        if not handoff.is_file():
            raise WorkflowConflict("terminal_refresh_handoff_missing")
        handoff_value = read_json(handoff)
        if (
            handoff_value.get("run_id") != args.run_id
            or handoff_value.get("business_date") != args.business_date
        ):
            raise WorkflowConflict("terminal_refresh_handoff_identity_conflict")
        _backup_file(
            temporary_root, files, handoff, "handoff/workflow_handoff.json",
            "workflow_handoff",
        )
        artifact_root = Path(args.artifact_root).resolve() / args.run_id
        scripts_root = artifact_root / "scripts"
        if scripts_root.exists() and not scripts_root.is_dir():
            raise WorkflowConflict("terminal_refresh_script_artifact_invalid")
        if scripts_root.is_dir():
            for source in sorted(path for path in scripts_root.rglob("*") if path.is_file()):
                relative = str(source.relative_to(artifact_root))
                _backup_file(
                    temporary_root, files, source, relative,
                    "script_artifact",
                )
        manifest = {
            "schema_version": 1,
            "kind": "terminal_run_revision_backup",
            "revision_id": revision_id,
            "run_id": args.run_id,
            "business_date": args.business_date,
            "new_editorial_sha256": editorial_sha256,
            "files": files,
            "secret_material_included": False,
        }
        atomic_replace_json(temporary_root / "manifest.json", manifest)
        os.replace(temporary_root, final_root)
        return final_root
    except (OSError, sqlite3.Error) as error:
        raise WorkflowConflict("terminal_refresh_backup_failed") from error
    finally:
        if temporary_root.exists():
            shutil.rmtree(temporary_root, ignore_errors=True)


def _clear_current_script_artifacts(args: argparse.Namespace) -> None:
    scripts_root = Path(args.artifact_root).resolve() / args.run_id / "scripts"
    if not scripts_root.exists():
        return
    if not scripts_root.is_dir() or scripts_root.is_symlink():
        raise WorkflowConflict("terminal_refresh_script_artifact_invalid")
    for path in sorted(scripts_root.rglob("*")):
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise WorkflowConflict("terminal_refresh_script_artifact_invalid")
        if path.is_file():
            path.unlink()


def _restore_script_artifacts(backup: Path, args: argparse.Namespace) -> None:
    manifest = read_json(backup / "manifest.json")
    artifact_root = Path(args.artifact_root).resolve() / args.run_id
    for entry in manifest.get("files", []):
        if entry.get("role") != "script_artifact":
            continue
        source = backup / str(entry.get("path") or "")
        target = artifact_root / str(entry.get("target") or "")
        if not source.is_file():
            raise WorkflowConflict("terminal_refresh_backup_unreadable")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)


def _scripts_match_selected(
    stage: dict[str, Any] | None, selected: set[str],
) -> bool:
    if not stage or stage.get("status") not in {"completed", "completed_with_failures"}:
        return False
    payload = stage.get("payload")
    if not isinstance(payload, dict) or payload.get("failures"):
        return False
    identities = [str(row.get("topic_id") or "") for row in payload.get("scripts", [])]
    return len(identities) == len(selected) and set(identities) == selected


def _terminal_refresh_editorial(
    args: argparse.Namespace, workflow: DailyWorkflow,
) -> dict[str, Any]:
    collection_stage = workflow.stage(args.run_id, "collection_enrichment")
    if not collection_stage:
        raise WorkflowConflict("terminal_refresh_collection_missing")
    collection = collection_stage["payload"]
    candidates = editorial_handoff_candidates(collection)
    cards = collection.get("hotspot_cards") or collection.get("candidates") or []
    if not candidates or len(cards) != len(candidates):
        raise WorkflowConflict("terminal_refresh_editorial_pool_incomplete")
    submitted = read_json(args.editorial_result_file)
    if submitted.get("business_date") not in (None, "", args.business_date):
        raise WorkflowConflict("terminal_refresh_editorial_date_conflict")
    validate_editorial(args.run_id, submitted, candidates)
    validate_editorial_understanding_consistency(args.run_id, submitted, collection)
    try:
        validate_candidate_specific_decisions(submitted["topics"], candidates)
    except ValueError as error:
        raise WorkflowConflict(str(error)) from None
    topics = complete_editorial_ledger(cards, submitted["topics"])
    if len(topics) != len(candidates):
        raise WorkflowConflict("terminal_refresh_editorial_coverage_incomplete")
    return {
        **submitted,
        "run_id": args.run_id,
        "business_date": args.business_date,
        "topics": topics,
    }


def terminal_refresh(
    args: argparse.Namespace, workflow: DailyWorkflow,
) -> dict[str, Any]:
    state = workflow.read_run(args.run_id)
    run = state["run"]
    current_editorial = workflow.stage(args.run_id, "editorial")
    current_scripts = workflow.stage(args.run_id, "scripts")
    if run["status"] not in TERMINAL:
        if (
            run["status"] == "waiting"
            and current_editorial is not None
            and (current_scripts is None or current_scripts.get("status") == "in_progress")
        ):
            editorial = _terminal_refresh_editorial(args, workflow)
            editorial_sha256 = hashlib.sha256(canonical(editorial).encode()).hexdigest()
            backup = _refresh_backup_for(args, editorial_sha256)
            if (
                backup is not None
                and canonical(current_editorial.get("payload")) == canonical(editorial)
            ):
                return {
                    "refresh_action": "noop",
                    "backup_path": str(backup),
                    "selected_count": sum(
                        row.get("decision") == "select"
                        for row in editorial.get("topics", [])
                    ),
                }
        raise WorkflowConflict("terminal_refresh_run_not_terminal")
    if run["publish_status"] != "applied":
        raise WorkflowConflict("terminal_refresh_run_not_published")
    editorial = _terminal_refresh_editorial(args, workflow)
    editorial_sha256 = hashlib.sha256(canonical(editorial).encode()).hexdigest()
    backup = _refresh_backup_for(args, editorial_sha256)
    selected = {
        str(row.get("candidate_id") or "")
        for row in editorial.get("topics", [])
        if row.get("decision") == "select"
    }
    current_is_editorial = bool(
        current_editorial
        and canonical(current_editorial.get("payload")) == canonical(editorial)
    )
    already_waiting = (
        current_is_editorial
        and backup is not None
        and run["status"] == "waiting"
        and run["publish_status"] == "not_ready"
        and (current_scripts is None or current_scripts.get("status") == "in_progress")
    )
    already_published = (
        current_is_editorial
        and backup is not None
        and run["status"] in TERMINAL
        and run["publish_status"] == "applied"
        and _scripts_match_selected(current_scripts, selected)
    )
    if already_waiting or already_published:
        return {
            "refresh_action": "noop",
            "backup_path": str(backup),
            "selected_count": len(selected),
        }
    backup = _write_refresh_backup(args, workflow, editorial)
    try:
        _clear_current_script_artifacts(args)
        workflow.refresh_terminal_run(args.run_id, args.business_date, editorial)
    except Exception:
        try:
            _restore_script_artifacts(backup, args)
        except Exception as restore_error:
            raise WorkflowConflict("terminal_refresh_rollback_failed") from restore_error
        raise
    return {
        "refresh_action": "applied",
        "backup_path": str(backup),
        "selected_count": len(selected),
    }


def collection_checkpoint_path(args: argparse.Namespace) -> Path:
    return Path(args.artifact_root).resolve() / args.run_id / "workflow_collection.json"


def collection_checkpoint_reusable(args: argparse.Namespace, path: Path) -> bool:
    workflow_db = Path(args.workflow_db).resolve()
    status = DailyWorkflow.read_business_date(workflow_db, args.business_date)
    if (
        status
        and status["run"]["run_id"] == args.run_id
        and "collection_enrichment" in status["committed_stages"]
    ):
        return True
    douyin_dir = path.parent / "sources" / "douyin"
    return current_douyin_artifact(
        douyin_dir / "cdp_probe_results.json",
        douyin_dir / "content_items_manual.jsonl",
        args.run_id,
    ).get("ok") is True


def collect_with_checkpoint(args: argparse.Namespace) -> dict[str, Any]:
    path = collection_checkpoint_path(args)
    if path.is_file():
        value = read_json(path)
        if (
            value.get("run_id") != args.run_id
            or value.get("business_date") != args.business_date
        ):
            raise WorkflowConflict("collection_checkpoint_wrong_run")
        if collection_checkpoint_reusable(args, path):
            return merge_exact_today_new_rows(
                value, run_dir=path.parent, run_id=args.run_id,
            )
    value = collect(args)
    if (
        value.get("run_id") != args.run_id
        or value.get("business_date") != args.business_date
    ):
        raise WorkflowConflict("collection_checkpoint_wrong_run")
    if not path.exists():
        atomic_json(path, value)
    return value


def terminal_handoff(
    workflow: DailyWorkflow, run_id: str, business_date: str, action: str
) -> dict[str, Any]:
    row = workflow.read_run(run_id)["run"]
    collection = workflow.stage(run_id, "collection_enrichment") or {"payload": {}}
    editorial = workflow.stage(run_id, "editorial") or {"payload": {}}
    scripts = workflow.stage(run_id, "scripts") or {"payload": {}}
    return {
        "ok": True,
        "action": action,
        "run_id": run_id,
        "business_date": business_date,
        "status": row["status"],
        "publish_status": row["publish_status"],
        "candidate_count": len(collection["payload"].get("candidates", [])),
        "selected_count": sum(
            item.get("decision") == "select"
            for item in editorial["payload"].get("topics", [])
        ),
        "script_count": len(scripts["payload"].get("scripts", [])),
        "item_failure_count": (
            len(collection["payload"].get("item_failures", []))
            + len(scripts["payload"].get("failures", []))
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id")
    parser.add_argument("--business-date", required=True)
    parser.add_argument("--status-only", action="store_true")
    parser.add_argument("--terminal-refresh", action="store_true")
    parser.add_argument("--workflow-db", type=Path, default=ROOT / "output/state/daily_workflow.sqlite3")
    parser.add_argument("--source-db", default=str(ROOT / "output/state/source_control.sqlite3"))
    parser.add_argument("--artifact-root", type=Path, default=ROOT / "output/runs")
    parser.add_argument("--collection-fixture")
    parser.add_argument("--adopt-collected-artifacts")
    parser.add_argument("--adoption-log")
    parser.add_argument("--qa-frozen-packages")
    parser.add_argument("--editorial-result-file")
    parser.add_argument("--scripts-result-file")
    parser.add_argument("--script-item-file")
    parser.add_argument("--video-mode", choices=("normal", "disabled"), default="normal")
    parser.add_argument("--video-runtime-config", default="")
    parser.add_argument("--video-policy", default="")
    parser.add_argument("--discovery-fixture", default="")
    parser.add_argument("--video-discovery-checkpoint", default="")
    parser.add_argument("--replay-inputs", default="")
    parser.add_argument("--search-query", default="")
    parser.add_argument("--cdp", default="http://127.0.0.1:9333")
    args = parser.parse_args()
    workflow: DailyWorkflow | None = None
    execution_lock: WorkflowExecutionLock | None = None
    if args.terminal_refresh and args.status_only:
        print(json.dumps({
            "ok": False,
            "action": "terminal_refresh_failed",
            "error": "terminal_refresh_status_only_conflict",
        }, ensure_ascii=False))
        return 2
    if args.status_only:
        try:
            status = DailyWorkflow.read_business_date(args.workflow_db, args.business_date)
            if status is None:
                print(json.dumps({
                    "ok": True,
                    "action": "no_run_for_business_date",
                    "business_date": args.business_date,
                }, ensure_ascii=False))
                return 0
            args.run_id = status["run"]["run_id"]
            path = workflow_handoff_path(args)
            result = {
                "ok": True,
                "action": "run_status",
                "run_id": args.run_id,
                "business_date": args.business_date,
                "status": status["run"]["status"],
                "publish_status": status["run"]["publish_status"],
                "committed_stages": status["committed_stages"],
                "handoff_path": str(path),
            }
            if path.is_file():
                handoff = read_json(path)
                if (
                    handoff.get("run_id") == args.run_id
                    and handoff.get("business_date") == args.business_date
                ):
                    result["next_action"] = handoff.get("action")
            print(json.dumps(result, ensure_ascii=False))
            return 0
        except (WorkflowConflict, ValueError, OSError, json.JSONDecodeError) as error:
            print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False))
            return 2
    if not args.run_id:
        print(json.dumps({"ok": False, "error": "run_id_required"}, ensure_ascii=False))
        return 2
    if args.terminal_refresh and not args.workflow_db.is_file():
        print(json.dumps({
            "ok": False,
            "action": "terminal_refresh_failed",
            "error": "terminal_refresh_workflow_db_missing",
        }, ensure_ascii=False))
        return 2
    try:
        DailyWorkflow.validate_identity(args.run_id, args.business_date)
        if args.video_mode == "normal" and not args.terminal_refresh:
            readiness = check_runtime_readiness(args.video_runtime_config)
            args.video_runtime_config = readiness["config_path"]
            args.video_policy = readiness["policy_path"]
        if not args.terminal_refresh:
            prepare_replay_inputs(args)
        execution_lock = WorkflowExecutionLock(args.workflow_db)
        if not execution_lock.acquire():
            emit_busy_handoff(args)
            return 0
        workflow = DailyWorkflow(args.workflow_db)
        if args.terminal_refresh:
            try:
                if not args.editorial_result_file:
                    raise WorkflowConflict("terminal_refresh_editorial_result_required")
                if args.scripts_result_file or args.script_item_file:
                    raise WorkflowConflict("terminal_refresh_script_input_forbidden")
                outcome = terminal_refresh(args, workflow)
                emit_handoff(args, {
                    "ok": True,
                    "action": "scripts_required",
                    "stage": "scripts",
                    "status": "waiting",
                    "publish_status": "not_ready",
                    "terminal_refresh": True,
                    **outcome,
                })
                return 0
            except (WorkflowConflict, ProjectionError, ValueError, OSError) as error:
                print(json.dumps({
                    "ok": False,
                    "action": "terminal_refresh_failed",
                    "error": str(error),
                }, ensure_ascii=False), flush=True)
                return 2
            except Exception:
                print(json.dumps({
                    "ok": False,
                    "action": "terminal_refresh_failed",
                    "error": "terminal_refresh_unexpected_error",
                }, ensure_ascii=False), flush=True)
                return 2
        mode = workflow.begin(args.run_id, args.business_date)
        if mode == "new":
            pending = workflow.latest_pending(args.business_date)
            if pending:
                publish(workflow, args.workflow_db, pending["run_id"])
        if mode == "terminal_replay":
            row = workflow.read_run(args.run_id)["run"]
            if row["publish_status"] == "pending":
                publish(workflow, args.workflow_db, args.run_id)
                emit_handoff(
                    args,
                    terminal_handoff(
                        workflow, args.run_id, args.business_date, "completed",
                    ),
                )
            summary = handoff_summary(
                terminal_handoff(
                    workflow, args.run_id, args.business_date, "noop",
                ),
                workflow_handoff_path(args),
            )
            print(json.dumps(summary, ensure_ascii=False), flush=True)
            return 0
        model_owned_funnel = args.video_mode == "normal"
        collection_stage = workflow.stage(args.run_id, "collection_enrichment")
        if collection_stage:
            collection = collection_stage["payload"]
        else:
            workflow.mark_waiting(args.run_id)
            emit_handoff(args, {
                "ok": True,
                "action": "waiting_stage",
                "stage": "collection_enrichment",
                "status": "waiting",
            })
            collected = collect_with_checkpoint(args)
            if args.video_discovery_checkpoint:
                collected = merge_video_discovery_checkpoint(
                    collected,
                    read_json(args.video_discovery_checkpoint),
                    run_id=args.run_id,
                )
            elif args.video_mode == "normal" and not (
                args.collection_fixture
                and any(
                    str(row.get("discovery_source") or "") == "dynamic_search"
                    for row in collected.get("candidates", [])
                    if isinstance(row, dict)
                )
            ):
                collected = merge_video_discovery_checkpoint(
                    collected,
                    load_discovery_payload(args, args.run_id, Path(args.artifact_root).resolve()),
                    run_id=args.run_id,
                )
            collection = enrich(
                args,
                collected,
                requested_candidate_ids=set() if model_owned_funnel else None,
            )
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
            stage_status = (
                "in_progress"
                if model_owned_funnel and collection.get("candidates")
                else "completed_with_failures" if collection["item_failures"] else (
                    "completed" if collection["content_items"] else "completed_empty"
                )
            )
            workflow.commit_stage(args.run_id, "collection_enrichment", collection, stage_status)
            collection_stage = workflow.stage(args.run_id, "collection_enrichment")
        editorial_stage = workflow.stage(args.run_id, "editorial")
        editorial_handoff = editorial_handoff_candidates(collection)
        submitted_editorial = (
            read_json(args.editorial_result_file)
            if args.editorial_result_file else None
        )
        screening_complete = bool(
            editorial_stage
            and editorial_stage["payload"].get("phase") == "screening_complete"
        )
        legacy_direct_submission = bool(
            isinstance(submitted_editorial, dict)
            and "topics" in submitted_editorial
            and "screening" not in submitted_editorial
        )
        if not editorial_handoff:
            # In the normal model-owned path an empty trusted pool is a truthful
            # empty result; Python must not invent visible editorial decisions.
            editorial = {
                "run_id": args.run_id,
                "topics": [] if model_owned_funnel else complete_editorial_ledger(
                    collection.get("hotspot_cards", []),
                    [],
                ),
            }
            workflow.commit_stage(args.run_id, "editorial", editorial, "completed")
        elif editorial_stage and editorial_stage["status"] == "completed":
            # A later public wake may carry only a per-topic script item. Reuse
            # the committed model result instead of reopening editorial.
            editorial = editorial_stage["payload"]
        elif model_owned_funnel and not screening_complete and not legacy_direct_submission:
            if not submitted_editorial:
                workflow.mark_waiting(args.run_id)
                emit_handoff(args, {
                    "ok": True,
                    "action": "editorial_required",
                    "stage": "editorial",
                    "editorial_phase": "screening",
                    "skill_names": [EDITORIAL_SKILL],
                    "candidate_topics": editorial_handoff,
                    "screening_result_contract": {
                        "run_id": args.run_id,
                        "screening": [
                            {
                                "candidate_id": "<trusted candidate id>",
                                "request_deep_read": False,
                                "reason": "candidate-local screening reason",
                            }
                        ],
                        "coverage": "one row per trusted candidate",
                        "request_range": "0..N",
                    },
                    "stage_contract": {
                        "automation_codex_is_sole_ai_owner": True,
                        "direct_skill": EDITORIAL_SKILL,
                        "full_trusted_pool_screening": True,
                        "video_processing_is_model_requested_only": True,
                    },
                })
                return 0
            screening_rows, requested = validate_editorial_screening(
                args.run_id, submitted_editorial, editorial_handoff,
            )
            workflow.commit_stage(
                args.run_id,
                "editorial",
                {
                    "run_id": args.run_id,
                    "business_date": args.business_date,
                    "phase": "screening_complete",
                    "screening": screening_rows,
                    "requested_candidate_ids": sorted(requested),
                },
                "in_progress",
            )
            collection = enrich(
                args,
                collection,
                requested_candidate_ids=requested,
                screening_rows=screening_rows,
            )
            collection_status = (
                "completed_with_failures" if collection["item_failures"]
                else "completed"
            )
            workflow.commit_stage(
                args.run_id, "collection_enrichment", collection, collection_status,
            )
            editorial_handoff = editorial_handoff_candidates(collection)
            workflow.mark_waiting(args.run_id)
            emit_handoff(args, {
                "ok": True,
                "action": "editorial_required",
                "stage": "editorial",
                "editorial_phase": "final",
                "skill_names": [EDITORIAL_SKILL],
                "candidate_topics": editorial_handoff,
                "stage_contract": {
                    "automation_codex_is_sole_ai_owner": True,
                    "direct_skill": EDITORIAL_SKILL,
                    "full_trusted_pool_final_judgment": True,
                    "same_run_media_only": True,
                    "screening_requested_candidate_ids": sorted(requested),
                },
            })
            return 0
        elif model_owned_funnel and screening_complete and not submitted_editorial:
            workflow.mark_waiting(args.run_id)
            emit_handoff(args, {
                "ok": True,
                "action": "editorial_required",
                "stage": "editorial",
                "editorial_phase": "final",
                "skill_names": [EDITORIAL_SKILL],
                "candidate_topics": editorial_handoff,
                "stage_contract": {
                    "automation_codex_is_sole_ai_owner": True,
                    "direct_skill": EDITORIAL_SKILL,
                    "full_trusted_pool_final_judgment": True,
                    "same_run_media_only": True,
                },
            })
            return 0
        else:
            if not submitted_editorial:
                workflow.mark_waiting(args.run_id)
                emit_handoff(args, {
                    "ok": True,
                    "action": "editorial_required",
                    "stage": "editorial",
                    "skill_names": [EDITORIAL_SKILL],
                    "candidate_topics": editorial_handoff,
                    "stage_contract": {
                        "automation_codex_is_sole_ai_owner": True,
                        "direct_skill": EDITORIAL_SKILL,
                        "exact_candidate_batch_only": True,
                        "simple_result_file": True,
                    },
                })
                return 0
            judged_editorial = submitted_editorial
            validate_editorial(args.run_id, judged_editorial, editorial_handoff)
            validate_editorial_understanding_consistency(
                args.run_id, judged_editorial, collection,
            )
            if not (args.video_mode == "disabled" and not args.qa_frozen_packages):
                try:
                    validate_candidate_specific_decisions(
                        judged_editorial["topics"], editorial_handoff,
                    )
                except ValueError as error:
                    raise WorkflowConflict(str(error)) from None
            editorial = {
                **judged_editorial,
                "topics": complete_editorial_ledger(
                    collection.get("hotspot_cards", []),
                    judged_editorial["topics"],
                ),
            }
            if collection_stage and collection_stage["status"] == "in_progress":
                collection_status = (
                    "completed_with_failures" if collection["item_failures"]
                    else "completed"
                )
                workflow.commit_stage(
                    args.run_id, "collection_enrichment", collection, collection_status,
                )
            workflow.record_skill_diagnostic(
                args.run_id, "editorial", "direct", SKILLS[0],
                {
                    "provenance": skill_diagnostics()[0],
                    "execution_mode": "direct_automation_codex",
                },
            )
            workflow.commit_stage(args.run_id, "editorial", editorial, "completed")
        selected_topics = [
            row for row in editorial.get("topics", [])
            if row.get("decision") == "select"
        ]
        selected = {str(row["candidate_id"]) for row in selected_topics}
        scripts_stage = workflow.stage(args.run_id, "scripts")
        scripts_finalized = False
        if scripts_stage and scripts_stage.get("status") != "in_progress":
            scripts = scripts_stage["payload"]
            scripts_finalized = True
        elif selected_topics:
            if args.scripts_result_file:
                raise WorkflowConflict("whole_batch_scripts_submission_forbidden")
            writer_contract = script_runtime.load_writer_contract()
            all_handoff = build_scripts_handoff(
                args.run_id, args.business_date, collection, editorial,
            )
            script_topics = all_handoff["selected_topics"]
            checkpoint = script_runtime.ensure_checkpoint(
                workflow,
                args.run_id,
                args.business_date,
                script_topics,
                writer_contract,
            )
            if args.script_item_file:
                index = script_runtime.first_unfinished_index(checkpoint)
                if index >= len(script_topics):
                    raise WorkflowConflict("scripts_checkpoint_incomplete_status")
                outcome = script_runtime.submit_topic(
                    workflow,
                    args.run_id,
                    args.business_date,
                    script_topics,
                    checkpoint,
                    writer_contract,
                    read_json(args.script_item_file),
                )
                workflow.record_skill_diagnostic(
                    args.run_id,
                    "scripts",
                    script_topics[index]["topic_id"],
                    WRITER_SKILL,
                    {
                        "provenance": skill_diagnostics()[1],
                        "execution_mode": "direct_automation_codex",
                    },
                )
                if not outcome["complete"]:
                    workflow.mark_waiting(args.run_id)
                    next_handoff = outcome["handoff"]
                    next_handoff.update({
                        "skill_names": list(WRITER_SKILLS),
                        "batch_contract": all_handoff["batch_contract"],
                    })
                    emit_handoff(args, next_handoff)
                    return 0
                scripts = outcome["scripts"]
                validate_scripts(args.run_id, scripts, selected)
                for name, diagnostic in zip(WRITER_SKILLS, skill_diagnostics()[1:]):
                    workflow.record_skill_diagnostic(
                        args.run_id, "scripts", "batch", name,
                        {"provenance": diagnostic},
                    )
                write_script_artifacts(args.artifact_root, args.run_id, scripts["scripts"])
                scripts_finalized = True
            else:
                index = script_runtime.first_unfinished_index(checkpoint)
                if index >= len(script_topics):
                    raise WorkflowConflict("scripts_checkpoint_incomplete_status")
                topic_packet_value = script_runtime.topic_packet(
                    args.run_id,
                    args.business_date,
                    script_topics[index],
                    index,
                    len(script_topics),
                    len(checkpoint["completed_items"]),
                    writer_contract,
                )
                handoff = {**topic_packet_value, **{
                    "skill_names": list(WRITER_SKILLS),
                    "batch_contract": all_handoff["batch_contract"],
                }}
                workflow.mark_waiting(args.run_id)
                emit_handoff(args, handoff)
                return 0
        else:
            scripts = {
                "run_id": args.run_id, "scripts": [], "failures": [],
            }
            validate_scripts(args.run_id, scripts, selected)
            write_script_artifacts(args.artifact_root, args.run_id, scripts["scripts"])
            workflow.commit_stage(
                args.run_id, "scripts", scripts,
                "completed_with_failures" if scripts.get("failures") else "completed",
            )
            scripts_finalized = True
        failures = (
            len(collection.get("item_failures", []))
            + len(scripts.get("failures", []))
        )
        status = "completed_with_failures" if failures else (
            "completed" if collection["content_items"] else "completed_empty"
        )
        workflow.complete(args.run_id, status, f"terminal:{args.run_id}")
        publish_status = publish(workflow, args.workflow_db, args.run_id)
        emit_handoff(
            args,
            terminal_handoff(
                workflow,
                args.run_id,
                args.business_date,
                "completed" if publish_status == "applied" else "completed_publish_pending",
            ),
        )
        return 0
    except (
        WorkflowConflict, ProducerError, ProjectionError, RuntimeReadinessError,
        RuntimeError, ValueError, OSError,
    ) as error:
        if workflow is not None:
            workflow.mark_recoverable_failure(args.run_id, str(error))
            try:
                emit_handoff(args, {
                    "ok": False,
                    "action": "failed_recoverable",
                    "status": "failed_recoverable",
                    "error": str(error),
                })
            except Exception:
                print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False))
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
            try:
                emit_handoff(args, {
                    "ok": False,
                    "action": "failed_recoverable",
                    "status": "failed_recoverable",
                    "error": error,
                })
            except Exception:
                print(json.dumps({"ok": False, "error": error}, ensure_ascii=False))
        else:
            DailyWorkflow.mark_existing_recoverable_failure(
                args.workflow_db, args.run_id, args.business_date, error
            )
            print(json.dumps({"ok": False, "error": error}, ensure_ascii=False))
        return 2
    finally:
        if execution_lock is not None:
            execution_lock.release()


if __name__ == "__main__":
    raise SystemExit(main())
