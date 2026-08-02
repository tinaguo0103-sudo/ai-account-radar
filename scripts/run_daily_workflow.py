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
import subprocess
import sys
import tempfile
import urllib.parse
import uuid
from pathlib import Path
from typing import Any

from daily_workflow import DailyWorkflow, WorkflowConflict, canonical
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
    validate_candidate_specific_decisions,
)
from website_publisher_client import publish_terminal
from video_runtime_readiness import RuntimeReadinessError, check_runtime_readiness

ROOT = Path(__file__).resolve().parents[1]
ACTIVE_ROOT = Path.home() / ".codex" / "skills"
SKILLS = (
    "ai-account-editorial-director",
    "austin-no-overtime-scripting",
    "austin-voice-scriptwriter",
)

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


def enrich(args: argparse.Namespace, collection: dict[str, Any]) -> dict[str, Any]:
    value = json.loads(json.dumps(collection, ensure_ascii=False))
    if value.get("run_id") != args.run_id:
        raise WorkflowConflict("collection_wrong_run")
    items, identity_failures = normalize_items(value.get("content_items", []))
    legacy_candidates, video_candidates, candidate_failures = normalize_collection_candidates(
        value.get("candidates", []),
        items=items,
        run_id=args.run_id,
    )
    source_ledger = (
        normalize_source_ledger(value, video_candidates=video_candidates)
        if "source_ledger" in value else []
    )
    hotspot_cards = build_hotspot_cards(
        legacy_candidates,
        items=items,
        run_id=args.run_id,
    )
    representative_video_candidates = representative_candidates(
        hotspot_cards,
        video_candidates,
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
        produced = produce(args, discovered_candidates=representative_video_candidates)
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
    hotspot_cards, understanding_results = attach_understanding(
        hotspot_cards,
        packages,
        producer_failures,
    )
    qualified_editorial_candidates = editorial_candidates(hotspot_cards)
    deep_read_summary = deep_read_counts(hotspot_cards)
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
        "deep_read_summary": deep_read_summary,
        **deep_read_summary,
        "understanding_results": understanding_results,
        "item_failures": identity_failures + candidate_failures + producer_failures,
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
    failed: set[str] = set()
    for row in result.get("failures", []):
        identity = str(row.get("topic_id") or "")
        if identity not in selected or identity in seen or identity in failed:
            raise WorkflowConflict("script_result_identity_conflict")
        failed.add(identity)
        if not str(row.get("reason") or ""):
            raise WorkflowConflict("script_result_incomplete")
    if seen | failed != selected:
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
            "cluster_synthesis": package.get("cluster_synthesis") or {},
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
                for key in ("time_second", "start", "sha256")
                if row.get(key) is not None
            }
            for row in keyframes
            if isinstance(row, dict)
        ],
        "unresolved": package.get("unresolved_terms") or package.get("unresolved") or [],
    }


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
        selected_topics.append({
            "topic_id": topic_id,
            "trend_event_id": candidate.get("trend_event_id") or topic_id,
            "title": topic.get("title"),
            "hook": topic.get("hook"),
            "structure": topic.get("structure"),
            "selection_reason": topic.get("selection_reason"),
            "unique_judgment": first_context_value(
                rows, "unique_judgment", "我的独家判断", "我的思考点", "主编判断摘要",
            ),
            "persona_fit": first_context_value(
                rows, "persona_fit", "persona_reason", "我的账号为什么能讲", "人设匹配",
            ),
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
                    "provenance": ("fact_provenance", "provenance", "事实来源"),
                    "missing_reasons": ("fact_missing_reasons", "missing_reasons", "事实缺失原因"),
                }.items()
                if first_context_value(source_rows, *aliases) is not None
            },
            "workflow_context": {
                key: first_context_value(rows, *aliases)
                for key, aliases in {
                    "pain": ("pain", "我的工作流痛点", "痛点"),
                    "old_workflow": ("old_workflow", "旧流程痛点", "旧流程"),
                    "ai_intervention": ("ai_intervention", "AI介入点"),
                    "experiment": ("experiment", "我要做的实验"),
                    "validation": ("validation", "验证方式"),
                    "available_evidence": ("available_evidence", "可展示证据", "市场验证依据"),
                    "missing_evidence": ("missing_evidence", "需要补的证据", "证据缺口"),
                }.items()
                if first_context_value(rows, *aliases) is not None
            },
            "fact_boundary": first_context_value(
                rows, "fact_boundary", "fact_boundary_note", "事实边界",
            ),
            "cannot_claim": first_context_value(
                rows, "cannot_claim", "cannot_claim_notes", "不能声称的部分",
            ),
            "traffic_opportunity": candidate.get("traffic_opportunity"),
            "persona_stability": candidate.get("persona_stability"),
            "differentiation": candidate.get("differentiation"),
            "cluster_synthesis": candidate.get("cluster_synthesis"),
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
            "video_understanding": compact_video_understanding(understanding.get(topic_id)),
        })
    return {
        "ok": True,
        "action": "scripts_required",
        "run_id": run_id,
        "business_date": business_date,
        "selected_topics": selected_topics,
        "skill_names": list(SKILLS[1:]),
        "batch_contract": {
            "one_outer_ai_owner": True,
            "one_batch_invocation_per_skill": True,
            "independent_body_per_topic": True,
            "missing_optional_context_must_not_be_fabricated": True,
            "human_supplement_excluded": True,
            "production_direction_excluded": True,
            "fact_boundaries_are_silent_generation_context": True,
            "plausible_hypothetical_or_composite_scenes_allowed": True,
            "illustrative_experiment_data_allowed": True,
            "fabricated_actual_client_team_or_measured_results_forbidden": True,
            "defensive_disclaimer_pattern_forbidden": True,
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


def collection_checkpoint_path(args: argparse.Namespace) -> Path:
    return Path(args.artifact_root).resolve() / args.run_id / "workflow_collection.json"


def collect_with_checkpoint(args: argparse.Namespace) -> dict[str, Any]:
    path = collection_checkpoint_path(args)
    if path.is_file():
        value = read_json(path)
        if (
            value.get("run_id") != args.run_id
            or value.get("business_date") != args.business_date
        ):
            raise WorkflowConflict("collection_checkpoint_wrong_run")
        return merge_exact_today_new_rows(
            value, run_dir=path.parent, run_id=args.run_id,
        )
    value = collect(args)
    if (
        value.get("run_id") != args.run_id
        or value.get("business_date") != args.business_date
    ):
        raise WorkflowConflict("collection_checkpoint_wrong_run")
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
    parser.add_argument("--replay-inputs", default="")
    parser.add_argument("--search-query", default="")
    parser.add_argument("--cdp", default="http://127.0.0.1:9333")
    args = parser.parse_args()
    workflow: DailyWorkflow | None = None
    execution_lock: WorkflowExecutionLock | None = None
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
    try:
        DailyWorkflow.validate_identity(args.run_id, args.business_date)
        if args.video_mode == "normal":
            readiness = check_runtime_readiness(args.video_runtime_config)
            args.video_runtime_config = readiness["config_path"]
            args.video_policy = readiness["policy_path"]
        prepare_replay_inputs(args)
        execution_lock = WorkflowExecutionLock(args.workflow_db)
        if not execution_lock.acquire():
            emit_busy_handoff(args)
            return 0
        workflow = DailyWorkflow(args.workflow_db)
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
            elif args.video_mode == "normal":
                collected = merge_video_discovery_checkpoint(
                    collected,
                    load_discovery_payload(args, args.run_id, Path(args.artifact_root).resolve()),
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
        editorial_handoff = collection.get("editorial_candidates", [])
        if args.video_mode == "disabled" and not args.qa_frozen_packages:
            # Explicit three-stage recovery/test mode has no video-enrichment
            # stage. Production normal mode never takes this path.
            editorial_handoff = collection.get("candidates", [])
        if editorial_stage:
            editorial = editorial_stage["payload"]
        elif not editorial_handoff:
            editorial = {
                "run_id": args.run_id,
                "topics": complete_editorial_ledger(
                    collection.get("hotspot_cards", []),
                    [],
                ),
            }
            workflow.commit_stage(args.run_id, "editorial", editorial, "completed")
        elif not args.editorial_result_file:
            workflow.mark_waiting(args.run_id)
            emit_handoff(args, {
                "ok": True, "action": "editorial_required", "run_id": args.run_id,
                "business_date": args.business_date, "candidates": editorial_handoff,
                "candidate_count": len(editorial_handoff), "skill_name": SKILLS[0],
                "complete_hotspot_card_count": len(collection.get("hotspot_cards", [])),
                "deep_read_summary": collection.get("deep_read_summary", {}),
                "required_output_contract": {
                    "decisions": ["select", "observe", "reject", "failed"],
                    "candidate_specific_fields": [
                        "selection_reason", "evidence_source_ids", "decision_basis",
                    ],
                    "judged_requires": ["unique_judgment"],
                    "primary_angle_contract": {
                        "applies_to": ["select", "observe", "reject"],
                        "must_cover": [
                            "concrete_conflict", "affected_party",
                            "action_or_experiment", "consequence",
                        ],
                        "selection_reason_is_separate": True,
                    },
                    "select_requires": ["title", "hook", "structure"],
                },
                "stage": "editorial", "status": "waiting",
            })
            return 0
        else:
            judged_editorial = read_json(args.editorial_result_file)
            validate_editorial(args.run_id, judged_editorial, editorial_handoff)
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
            workflow.mark_waiting(args.run_id)
            handoff = build_scripts_handoff(
                args.run_id, args.business_date, collection, editorial,
            )
            handoff.update({
                "selected_count": len(selected),
                "stage": "scripts",
                "status": "waiting",
            })
            emit_handoff(args, handoff)
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
