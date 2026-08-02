#!/usr/bin/env python3
"""Internal terminal projection builder and authenticated HTTP transport."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

class ProjectionError(RuntimeError):
    pass


def stable_id(kind: str, run_id: str, identity: str) -> str:
    return f"{kind}_{hashlib.sha256(f'{run_id}|{identity}'.encode()).hexdigest()[:24]}"


def source_name(platform: str) -> str:
    text = platform.lower()
    if "抖音" in platform or "douyin" in text:
        return "douyin"
    if "公众号" in platform or "微信" in platform or "wechat" in text:
        return "wechat"
    return "aihot"


def normalize_video_understanding(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    normalized = dict(value)
    if "keyframes" in value:
        normalized["keyframes"] = [
            {**row, "start": row.get("start", row.get("time_second"))}
            for row in value.get("keyframes", [])
            if isinstance(row, dict)
        ]
    if "screen_text" in value:
        normalized["screen_text"] = [
            {
                **row,
                "text": row.get("text", row.get("value", "")),
                "start": row.get("start", row.get("time_second")),
            }
            for row in value.get("screen_text", [])
            if isinstance(row, dict)
        ]
    return normalized


def build_workflow_projection(
    db_path: Path, run_id: str, authority_identity: str,
    scripts_override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    database = sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True)
    database.row_factory = sqlite3.Row
    run = database.execute("SELECT * FROM daily_runs WHERE run_id=?", (run_id,)).fetchone()
    rows = database.execute(
        """SELECT * FROM stage_results WHERE run_id=?
           ORDER BY CASE stage WHEN 'collection_enrichment' THEN 1
           WHEN 'editorial' THEN 2 ELSE 3 END""", (run_id,),
    ).fetchall()
    if not run or len(rows) != 3 or rows[-1]["stage"] != "scripts":
        raise ProjectionError("workflow_terminal_not_committed")
    payloads = {row["stage"]: json.loads(row["payload_json"]) for row in rows}
    collection = payloads.get("collection_enrichment", {})
    editorial = payloads.get("editorial", {})
    scripts_stage = scripts_override if scripts_override is not None else payloads.get("scripts", {})
    understanding_by_url = {
        str(result.get("package", {}).get("source_url") or ""): result.get("package")
        for result in collection.get("understanding_results", [])
    }
    content: list[dict[str, Any]] = []
    by_identity: dict[str, dict[str, Any]] = {}
    for row in collection.get("content_items", []):
        identity = str(row.get("item_id") or row.get("id") or "")
        if not identity or identity in by_identity:
            raise ProjectionError("stable_item_identity_conflict")
        item = {
            "id": stable_id("content", run_id, identity), "run_id": run_id,
            # Website retains this legacy column name internally. It stores the
            # stable item ID and is not a Radar runtime fingerprint contract.
            "content_fingerprint": identity, "source": source_name(str(row.get("source") or row.get("平台") or "")),
            "account": str(row.get("account") or row.get("账号名/公众号名") or ""),
            "title": str(row.get("title") or row.get("内容标题") or ""),
            "summary": str(row.get("summary") or row.get("正文/字幕/简介片段") or "")[:360],
            "body": str(row.get("body") or row.get("正文/字幕/简介片段") or ""),
            "source_url": str(row.get("source_url") or row.get("内容链接") or ""),
            "published_at": str(row.get("published_at") or row.get("发布时间") or ""),
            "collected_at": str(row.get("collected_at") or run["updated_at"]),
            "video_understanding": normalize_video_understanding(
                understanding_by_url.get(
                    str(row.get("source_url") or row.get("内容链接") or "")
                )
            ),
        }
        content.append(item)
        by_identity[identity] = item
    topics: list[dict[str, Any]] = []
    candidates_by_identity = {
        str(row.get("candidate_id") or ""): row
        for row in (collection.get("hotspot_cards") or collection.get("candidates", []))
        if str(row.get("candidate_id") or "")
    }
    content_by_url = {
        str(row.get("source_url") or ""): row for row in content
        if str(row.get("source_url") or "")
    }
    for row in editorial.get("topics", []):
        identity = str(row.get("candidate_id") or "")
        candidate = candidates_by_identity.get(identity, {})
        decision = str(row.get("decision") or "")
        if decision not in {"select", "observe", "reject", "failed", "signal"}:
            raise ProjectionError("topic_decision_invalid")
        differentiation = json.loads(json.dumps(
            row.get("differentiation") or candidate.get("differentiation") or {}
        ))
        cluster_synthesis = json.loads(json.dumps(
            row.get("cluster_synthesis") or candidate.get("cluster_synthesis") or {}
        ))
        cluster_synthesis["review_stage"] = str(
            row.get("review_stage") or candidate.get("review_stage") or ""
        )
        primary_angle = str(
            row.get("unique_judgment")
            or differentiation.get("primary_angle")
            or cluster_synthesis.get("primary_angle")
            or ""
        ).strip()
        if decision in {"select", "observe", "reject"} and primary_angle:
            differentiation["primary_angle"] = primary_angle
            cluster_synthesis["primary_angle"] = primary_angle
        representative_item_id = str(
            candidate.get("representative_item_id")
            or candidate.get("item_id")
            or identity
        )
        item = by_identity.get(representative_item_id)
        if not item:
            raise ProjectionError("topic_content_mapping_missing")
        sources = json.loads(json.dumps(candidate.get("sources") or []))
        for source in sources:
            source_item = content_by_url.get(str(source.get("url") or ""))
            if source_item:
                source["content_id"] = source_item["id"]
        topics.append({
            "id": stable_id("topic", run_id, identity), "run_id": run_id,
            "content_id": item["id"], "title": str(
                row.get("title") or candidate.get("event_name") or candidate.get("title") or item["title"]
            ),
            "source": item["source"], "brief": item["summary"],
            "reason": str(row.get("selection_reason") or ""), "status": decision,
            "updated_at": run["updated_at"], "selection_reason": str(row.get("selection_reason") or ""),
            "hook": str(row.get("hook") or ""), "content_structure": str(row.get("structure") or ""),
            "source_url": item["source_url"],
            "generation_status": "not_generated" if decision == "select" else "not_applicable",
            "generation_error": "",
            "trend_event_id": str(candidate.get("trend_event_id") or identity),
            "sources": sources,
            "cluster_synthesis": cluster_synthesis,
            "traffic_opportunity": candidate.get("traffic_opportunity") or {},
            "persona_stability": candidate.get("persona_stability") or {},
            "differentiation": differentiation,
        })
    topic_by_identity = {
        str(row.get("candidate_id")): topic
        for row, topic in zip(editorial.get("topics", []), topics)
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
    content_source_counts: dict[str, int] = {}
    for item in content:
        source = str(item["source"])
        content_source_counts[source] = content_source_counts.get(source, 0) + 1
    source_rows = collection.get("source_runs", [])
    if not source_rows:
        source_rows = collection.get("source_ledger", [])
    for row in source_rows:
        raw_source = str(row.get("source") or "")
        source = raw_source if raw_source in {
            "configured_account", "recommendation", "dynamic_search",
        } else source_name(raw_source)
        counts = row.get("counts") if isinstance(row.get("counts"), dict) else {}
        item_count = int(
            row.get("item_count")
            if row.get("item_count") is not None
            else row.get("discovered_count", content_source_counts.get(source, 0))
        )
        succeeded_count = int(row.get("succeeded_count") or counts.get("new") or item_count)
        failed_count = int(
            row.get("failed_count") or counts.get("failed")
            or (1 if row.get("status") == "failed" else 0)
        )
        source_runs.append({
            "id": str(row.get("id") or f"{run_id}:{row.get('source')}"),
            "run_id": run_id, "source": source,
            "status": str(row.get("status") or "completed"),
            "planned_count": int(row.get("planned_count") or succeeded_count + failed_count),
            "succeeded_count": succeeded_count,
            "failed_count": failed_count,
            "item_count": item_count,
            "error_summary": str(row.get("error_summary") or row.get("reason") or ""),
            "completed_at": str(row.get("completed_at") or run["updated_at"]),
        })
    payload = {
        "run_id": run_id, "business_date": run["business_date"], "revision": 1,
        "stage": "scripts", "authority_identity": authority_identity, "updated_at": run["updated_at"],
        "run": {
            "status": run["status"],
            "candidate_count": len(collection.get("hotspot_cards") or collection.get("candidates", [])),
        },
        "source_runs": source_runs, "collected_items": content, "topics": topics, "scripts": scripts,
    }
    database.close()
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
