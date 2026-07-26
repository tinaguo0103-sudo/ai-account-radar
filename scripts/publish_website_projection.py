#!/usr/bin/env python3
"""Publish one exact Radar run to the website business projection endpoint."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sqlite3
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any

RUN_RE = re.compile(r"run_(\d{4})(\d{2})(\d{2})_\d{6}")


class ProjectionError(RuntimeError):
    pass


def canonical(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: canonical(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [canonical(item) for item in value]
    return value


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(canonical(value), ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


def stable_id(kind: str, run_id: str, identity: str) -> str:
    return f"{kind}_{hashlib.sha256(f'{run_id}|{identity}'.encode()).hexdigest()[:24]}"


def source_name(platform: str) -> str:
    text = platform.lower()
    if "抖音" in platform or "douyin" in text:
        return "douyin"
    if "公众号" in platform or "微信" in platform or "wechat" in text:
        return "wechat"
    return "aihot"


def load_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file() or path.stat().st_size == 0:
        raise ProjectionError(f"required_artifact_missing:{path.name}")
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def build_projection(repo: Path, run_id: str, revision: int, authority_identity: str) -> dict[str, Any]:
    match = RUN_RE.fullmatch(run_id)
    if not match:
        raise ProjectionError("wrong_run")
    business_date = "-".join(match.groups())
    run_root = (repo / "output" / "runs" / run_id).resolve()
    expected_root = (repo / "output" / "runs").resolve()
    if run_root.parent != expected_root:
        raise ProjectionError("run_path_escape")
    content_rows = load_csv(run_root / "content_items.csv")
    editorial_rows = load_csv(run_root / "today_10_topics.csv")
    selected_rows = [row for row in editorial_rows if row.get("今日建议级别") == "推荐制作"]
    log_path = repo / "output" / "logs" / f"daily_pipeline_{business_date}.json"
    log = json.loads(log_path.read_text())
    if log.get("run_id") != run_id:
        raise ProjectionError("daily_log_run_mismatch")

    fingerprints: set[str] = set()
    content: list[dict[str, Any]] = []
    content_by_fingerprint: dict[str, dict[str, Any]] = {}
    source_counts: Counter[str] = Counter()
    updated_at = str(log.get("generated_at") or "")
    for row in content_rows:
        fingerprint = str(row.get("内容指纹") or "").strip()
        if not fingerprint or fingerprint in fingerprints:
            raise ProjectionError("content_fingerprint_conflict")
        fingerprints.add(fingerprint)
        source = source_name(str(row.get("平台") or row.get("来源类型") or ""))
        source_counts[source] += 1
        item = {
            "id": stable_id("content", run_id, fingerprint),
            "run_id": run_id,
            "content_fingerprint": fingerprint,
            "source": source,
            "account": str(row.get("账号名/公众号名") or ""),
            "title": str(row.get("内容标题") or ""),
            "summary": str(row.get("正文/字幕/简介片段") or "")[:360],
            "body": str(row.get("正文/字幕/简介片段") or ""),
            "source_url": str(row.get("内容链接") or ""),
            "published_at": str(row.get("发布时间") or ""),
            "collected_at": updated_at,
        }
        content.append(item)
        content_by_fingerprint[fingerprint] = item

    topics: list[dict[str, Any]] = []
    for row in selected_rows:
        fingerprint = str(row.get("内容指纹") or "").strip()
        item = content_by_fingerprint.get(fingerprint)
        if not item:
            raise ProjectionError("topic_content_mapping_missing")
        topics.append({
            "id": stable_id("topic", run_id, fingerprint),
            "run_id": run_id,
            "content_id": item["id"],
            "title": str(row.get("可发布标题") or row.get("我的选题标题") or ""),
            "source": item["source"],
            "brief": str(row.get("一句话Brief") or row.get("来源内容") or "")[:500],
            "reason": str(row.get("推荐理由") or row.get("主编判断") or ""),
            "status": "selected",
            "updated_at": updated_at,
            "selection_reason": str(row.get("推荐理由") or row.get("为什么今天值得做") or ""),
            "hook": str(row.get("热点切入方式") or row.get("我的蹭热点角度") or ""),
            "content_structure": str(row.get("验证方式") or row.get("我要做的实验") or ""),
            "source_url": str(row.get("来源链接") or item["source_url"]),
            "generation_status": "not_generated",
            "generation_error": "",
        })

    failed_accounts = list(log.get("isolated_failed_accounts") or [])
    sources = []
    for source in ("wechat", "douyin", "aihot"):
        source_failures = [row for row in failed_accounts if source in str(row).lower()]
        sources.append({
            "id": f"{run_id}:{source}",
            "run_id": run_id,
            "source": source,
            "status": "completed_with_failures" if source_failures else "completed",
            "planned_count": source_counts[source] + len(source_failures),
            "succeeded_count": source_counts[source],
            "failed_count": len(source_failures),
            "item_count": source_counts[source],
            "error_summary": "；".join(map(str, source_failures))[:500],
            "completed_at": updated_at,
        })
    payload: dict[str, Any] = {
        "run_id": run_id,
        "business_date": business_date,
        "revision": revision,
        "stage": "editorial",
        "authority_identity": authority_identity,
        "updated_at": updated_at,
        "run": {
            "status": str(log.get("collection_status") or "completed"),
            "downstream_usable": bool(log.get("downstream_usable")),
            "candidate_count": len(editorial_rows),
        },
        "source_runs": sources,
        "collected_items": content,
        "topics": topics,
        "scripts": [],
    }
    payload["payload_sha256"] = digest(payload)
    return payload


def build_workflow_projection(db_path: Path, run_id: str, stage: str,
                              revision: int, authority_identity: str) -> dict[str, Any]:
    database = sqlite3.connect(db_path)
    database.row_factory = sqlite3.Row
    run = database.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
    rows = database.execute(
        "SELECT * FROM stages WHERE run_id=? AND revision<=? ORDER BY revision",
        (run_id, revision),
    ).fetchall()
    if not run or not rows or rows[-1]["stage"] != stage:
        raise ProjectionError("workflow_stage_not_committed")
    payloads = {row["stage"]: json.loads(row["payload_json"]) for row in rows}
    collection = payloads.get("collection", {})
    editorial = payloads.get("editorial", {})
    scripts_stage = payloads.get("scripts", {})
    content: list[dict[str, Any]] = []
    by_identity: dict[str, dict[str, Any]] = {}
    for row in collection.get("content_items", []):
        identity = str(row.get("content_fingerprint") or row.get("id") or "")
        if not identity or identity in by_identity:
            raise ProjectionError("content_fingerprint_conflict")
        item = {
            "id": stable_id("content", run_id, identity), "run_id": run_id,
            "content_fingerprint": identity, "source": source_name(str(row.get("source") or row.get("平台") or "")),
            "account": str(row.get("account") or row.get("账号名/公众号名") or ""),
            "title": str(row.get("title") or row.get("内容标题") or ""),
            "summary": str(row.get("summary") or row.get("正文/字幕/简介片段") or "")[:360],
            "body": str(row.get("body") or row.get("正文/字幕/简介片段") or ""),
            "source_url": str(row.get("source_url") or row.get("内容链接") or ""),
            "published_at": str(row.get("published_at") or row.get("发布时间") or ""),
            "collected_at": str(row.get("collected_at") or run["updated_at"]),
        }
        content.append(item)
        by_identity[identity] = item
    topics: list[dict[str, Any]] = []
    for row in editorial.get("topics", []):
        if row.get("decision") != "select":
            continue
        identity = str(row.get("candidate_id") or "")
        item = by_identity.get(identity)
        if not item:
            raise ProjectionError("topic_content_mapping_missing")
        topics.append({
            "id": stable_id("topic", run_id, identity), "run_id": run_id,
            "content_id": item["id"], "title": str(row.get("title") or ""),
            "source": item["source"], "brief": item["summary"],
            "reason": str(row.get("selection_reason") or ""), "status": "selected",
            "updated_at": run["updated_at"], "selection_reason": str(row.get("selection_reason") or ""),
            "hook": str(row.get("hook") or ""), "content_structure": str(row.get("structure") or ""),
            "source_url": item["source_url"], "generation_status": "not_generated",
            "generation_error": "",
        })
    topic_by_identity = {
        str(row.get("candidate_id")): topic
        for row, topic in zip(
            [row for row in editorial.get("topics", []) if row.get("decision") == "select"], topics
        )
    }
    scripts: list[dict[str, Any]] = []
    for row in scripts_stage.get("scripts", []):
        identity = str(row.get("topic_id") or "")
        topic = topic_by_identity.get(identity)
        if not topic:
            raise ProjectionError("script_topic_mapping_missing")
        topic["generation_status"] = "generated"
        scripts.append({
            "id": stable_id("script", run_id, identity), "run_id": run_id,
            "topic_id": topic["id"], "script_version": 1,
            "title": str(row.get("title") or ""), "hook": str(row.get("hook") or ""),
            "content_structure": str(row.get("structure") or ""), "body": str(row.get("body") or ""),
            "updated_at": run["updated_at"], "current_revision_number": 1, "saved_at": run["updated_at"],
        })
    for failure in scripts_stage.get("failures", []):
        topic = topic_by_identity.get(str(failure.get("topic_id") or ""))
        if topic:
            topic["generation_status"] = "failed"
            topic["generation_error"] = str(failure.get("reason") or "script_generation_failed")
    source_runs = []
    for row in collection.get("source_runs", []):
        source_runs.append({
            "id": str(row.get("id") or f"{run_id}:{row.get('source')}"),
            "run_id": run_id, "source": source_name(str(row.get("source") or "")),
            "status": str(row.get("status") or "completed"), "planned_count": int(row.get("planned_count") or row.get("item_count") or 0),
            "succeeded_count": int(row.get("succeeded_count") or row.get("item_count") or 0),
            "failed_count": int(row.get("failed_count") or (1 if row.get("status") == "failed" else 0)),
            "item_count": int(row.get("item_count") or 0), "error_summary": str(row.get("error_summary") or ""),
            "completed_at": str(row.get("completed_at") or run["updated_at"]),
        })
    payload = {
        "run_id": run_id, "business_date": run["business_date"], "revision": revision,
        "stage": stage, "authority_identity": authority_identity, "updated_at": run["updated_at"],
        "run": {"status": rows[-1]["status"], "candidate_count": len(collection.get("candidates", []))},
        "source_runs": source_runs, "collected_items": content, "topics": topics, "scripts": scripts,
    }
    payload["payload_sha256"] = digest(payload)
    return payload


def request_json(method: str, url: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    app_bearer = os.environ.get("WEBSITE_PROJECTION_BEARER", "").strip()
    if not app_bearer:
        raise ProjectionError("website_projection_bearer_missing")
    headers = {"Authorization": f"Bearer {app_bearer}", "Content-Type": "application/json"}
    sites_bearer = os.environ.get("WEBSITE_PROJECTION_SIWC_BYPASS_BEARER", "").strip()
    if sites_bearer:
        headers["OAI-Sites-Authorization"] = f"Bearer {sites_bearer}"
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode()
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as error:
        try:
            reason = json.loads(error.read()).get("error")
        except Exception:
            reason = f"http_{error.code}"
        raise ProjectionError(str(reason)) from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise ProjectionError("website_projection_transport_unavailable") from None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--revision", type=int, required=True)
    parser.add_argument("--authority-identity", required=True)
    parser.add_argument("--website-url", required=True)
    parser.add_argument("--workflow-db", type=Path)
    parser.add_argument("--stage", choices=("collection", "editorial", "scripts"))
    parser.add_argument("--payload-out", type=Path)
    parser.add_argument("--build-only", action="store_true")
    args = parser.parse_args()
    try:
        if args.workflow_db:
            if not args.stage:
                raise ProjectionError("workflow_stage_required")
            payload = build_workflow_projection(
                args.workflow_db.resolve(), args.run_id, args.stage, args.revision,
                args.authority_identity,
            )
        else:
            payload = build_projection(args.repo.resolve(), args.run_id, args.revision, args.authority_identity)
        if args.payload_out:
            args.payload_out.parent.mkdir(parents=True, exist_ok=True)
            args.payload_out.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
        if args.build_only:
            result = {"ok": True, "status": "built", "run_id": args.run_id,
                      "payload_sha256": payload["payload_sha256"],
                      "counts": {"content": len(payload["collected_items"]),
                                 "topics": len(payload["topics"]), "scripts": len(payload["scripts"])}}
        else:
            endpoint = args.website_url.rstrip("/") + "/api/business-projection"
            try:
                result = request_json("POST", endpoint, payload)
            except ProjectionError as error:
                if str(error) != "business_projection_conflict":
                    raise
                readback = request_json("GET", f"{endpoint}?run_id={args.run_id}")
                expected_counts = {
                    "content": len(payload["collected_items"]),
                    "topics": len(payload["topics"]),
                    "scripts": len(payload["scripts"]),
                }
                if (readback.get("revision") != args.revision
                        or readback.get("payload_sha256") != payload["payload_sha256"]
                        or readback.get("authority_identity") != args.authority_identity
                        or readback.get("counts") != expected_counts):
                    raise
                result = {"ok": True, "status": "reconciled"}
            readback = request_json("GET", f"{endpoint}?run_id={args.run_id}")
            if (readback.get("revision") != args.revision
                    or readback.get("payload_sha256") != payload["payload_sha256"]
                    or readback.get("authority_identity") != args.authority_identity):
                raise ProjectionError("business_projection_readback_mismatch")
            result["readback"] = readback
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except (ProjectionError, ValueError, json.JSONDecodeError) as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
