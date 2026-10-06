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

from daily_workflow import WorkflowConflict
from spoken_script_runtime import read_article_artifact

class ProjectionError(RuntimeError):
    pass


SOURCE_ID_TO_KEY = {
    "feishu_recvl4uBak1aBQ": "aihot_selected",
    "feishu_recvl4uBakCutL": "aihot_daily",
    "feishu_recvl4uBak96UM": "openai_news",
    "feishu_recvl4uBakM7LI": "anthropic_newsroom",
    "feishu_recvl4uBakDz73": "product_hunt_ai",
    "feishu_recvl4uBakYAzP": "hn_ai_discussion",
    "feishu_recvl4uBakTUDT": "x",
    "feishu_recvlm0QvTIDMe": "wechat",
}
SOURCE_RUN_KEYS = frozenset({
    "aihot_selected", "aihot_daily", "openai_news", "anthropic_newsroom",
    "product_hunt_ai", "hn_ai_discussion", "douyin", "wechat", "x",
})


def stable_id(kind: str, run_id: str, identity: str) -> str:
    return f"{kind}_{hashlib.sha256(f'{run_id}|{identity}'.encode()).hexdigest()[:24]}"


def source_name(platform: str, source_id: str = "", source_key: str = "") -> str:
    if source_id in SOURCE_ID_TO_KEY:
        key = SOURCE_ID_TO_KEY[source_id]
        if key.startswith("aihot_"):
            return "aihot"
        if key in {"openai_news", "anthropic_newsroom", "product_hunt_ai", "hn_ai_discussion"}:
            return "public_web"
        return key
    if source_key in SOURCE_RUN_KEYS:
        if source_key.startswith("aihot_"):
            return "aihot"
        if source_key in {"openai_news", "anthropic_newsroom", "product_hunt_ai", "hn_ai_discussion"}:
            return "public_web"
        return source_key
    text = platform.lower().strip()
    if "抖音" in platform or "douyin" in text:
        return "douyin"
    if "公众号" in platform or "微信" in platform or "wechat" in text:
        return "wechat"
    if "x.com" in text or text == "x" or "twitter" in text:
        return "x"
    if any(value in text for value in ("openai", "anthropic", "product hunt", "hacker news", "hn ai")):
        return "public_web"
    if "aihot" in text:
        return "aihot"
    raise ProjectionError("unknown_source_platform")


def normalize_provenance(value: Any, *, item_source: str) -> list[dict[str, Any]]:
    if isinstance(value, str):
        if not value.strip():
            value = []
        else:
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                raise ProjectionError("source_provenance_invalid") from None
    if value is None:
        return []
    if not isinstance(value, list):
        raise ProjectionError("source_provenance_invalid")
    rows: dict[str, dict[str, Any]] = {}
    for raw in value:
        if not isinstance(raw, dict):
            raise ProjectionError("source_provenance_invalid")
        source_id = str(raw.get("source_id") or "")
        if not source_id:
            raise ProjectionError("source_provenance_identity_missing")
        row = dict(raw)
        row["source_id"] = source_id
        for local_path_key in ("raw_artifact_path", "raw_path", "original_payload_path"):
            row.pop(local_path_key, None)
        rows[json.dumps(row, ensure_ascii=False, sort_keys=True)] = row
    if not rows and item_source not in {"douyin", "wechat", "aihot", "public_web", "x"}:
        raise ProjectionError("source_provenance_identity_missing")
    return [rows[key] for key in sorted(rows)]


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
    artifact_root: Path | str | None = None,
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
    checkpoint_scripts = payloads.get("scripts", {})
    scripts_stage = scripts_override if scripts_override is not None else checkpoint_scripts
    understanding_by_url = {
        str(result.get("package", {}).get("source_url") or ""): result.get("package")
        for result in collection.get("understanding_results", [])
    }
    runtime_sources: dict[str, dict[str, Any]] = {}
    run_artifacts = Path(artifact_root).resolve() / run_id if artifact_root is not None else None
    if run_artifacts is not None:
        try:
            config = json.loads((run_artifacts / "sources" / "douyin" / "source_plan_config.json").read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            config = {}
        runtime_sources = {
            str(source.get("account_name") or source.get("name") or ""): source
            for source in config.get("sources", []) if isinstance(source, dict)
        }
    content: list[dict[str, Any]] = []
    by_identity: dict[str, dict[str, Any]] = {}
    for row in collection.get("content_items", []):
        identity = str(row.get("item_id") or row.get("id") or "")
        if not identity or identity in by_identity:
            raise ProjectionError("stable_item_identity_conflict")
        raw_platform = str(row.get("source") or row.get("平台") or "")
        raw_source_ids = row.get("source_ids") if isinstance(row.get("source_ids"), list) else []
        source_id = str(row.get("source_id") or (raw_source_ids[0] if raw_source_ids else ""))
        source_key = str(row.get("source_key") or "")
        account = str(row.get("account") or row.get("账号名/公众号名") or "")
        if not source_id and account in runtime_sources:
            source_id = str(runtime_sources[account].get("id") or runtime_sources[account].get("source_id") or "")
        content_source = source_name(raw_platform, source_id, source_key)
        provenance = normalize_provenance(row.get("source_provenance"), item_source=content_source)
        if not provenance and source_id:
            provenance = [{
                "source_id": source_id, "source": source_key or content_source,
                "platform": raw_platform, "account": account,
                "title": str(row.get("title") or row.get("内容标题") or ""),
                "url": str(row.get("source_url") or row.get("内容链接") or ""),
                "published_at": str(row.get("published_at") or row.get("发布时间") or ""),
            }]
        item = {
            "id": stable_id("content", run_id, identity), "run_id": run_id,
            # Website retains this legacy column name internally. It stores the
            # stable item ID and is not a Radar runtime fingerprint contract.
            "content_fingerprint": identity, "source": content_source,
            "source_provenance": provenance,
            "account": account,
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
    resolved_artifact_root = (
        Path(artifact_root).resolve() if artifact_root is not None
        else Path(db_path).resolve().parent.parent / "runs"
    )
    article_rows: list[dict[str, Any]] = []
    article_metadata_rows = checkpoint_scripts.get("article_artifacts", [])
    if not isinstance(article_metadata_rows, list):
        raise ProjectionError("article_checkpoint_metadata_invalid")
    seen_article_topics: set[str] = set()
    for metadata in article_metadata_rows:
        if not isinstance(metadata, dict):
            raise ProjectionError("article_checkpoint_metadata_invalid")
        identity = str(metadata.get("topic_id") or "")
        topic = topic_by_identity.get(identity)
        if not identity or identity in seen_article_topics:
            raise ProjectionError("article_checkpoint_identity_conflict")
        if not topic or topic.get("status") not in {"select", "selected"}:
            raise ProjectionError("article_topic_mapping_missing")
        expected_relative_path = (
            Path("articles")
            / f"{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:20]}.md"
        ).as_posix()
        if (
            metadata.get("run_id") != run_id
            or metadata.get("business_date") != run["business_date"]
            or metadata.get("relative_path") != expected_relative_path
            or not str(metadata.get("sha256") or "")
            or not str(metadata.get("artifact_sha256") or "")
            or not str(metadata.get("title") or "")
        ):
            raise ProjectionError("article_checkpoint_identity_conflict")
        try:
            article = read_article_artifact(
                metadata, run_id=run_id, business_date=str(run["business_date"]),
                topic_id=identity, artifact_root=resolved_artifact_root,
            )
        except WorkflowConflict as error:
            reason = str(error) if str(error) else "article_artifact_identity_conflict"
            raise ProjectionError(reason) from None
        if article.get("title") != metadata.get("title"):
            raise ProjectionError("article_artifact_identity_conflict")
        article_row = {
            "id": stable_id("article", run_id, identity),
            "run_id": run_id,
            "topic_id": topic["id"],
            "source_topic_id": identity,
            "title": article["title"],
            "body": article["body"],
            "artifact_path": expected_relative_path,
            "article_sha256": metadata["sha256"],
            "artifact_sha256": metadata["artifact_sha256"],
            "claim_review": article.get("claim_review"),
        }
        if "claim_review" not in article:
            article_row.pop("claim_review")
        article_rows.append(article_row)
        seen_article_topics.add(identity)
    source_runs = []
    content_source_counts: dict[str, int] = {}
    for item in content:
        source = str(item["source"])
        content_source_counts[source] = content_source_counts.get(source, 0) + 1
    source_rows = list(collection.get("source_runs", []) or [])
    if not source_rows:
        source_rows = list(collection.get("source_ledger", []) or [])
    grouped_source_rows: dict[str, list[dict[str, Any]]] = {}
    for row in source_rows:
        raw_source = str(row.get("source") or row.get("source_key") or "")
        source_id = str(row.get("source_id") or "")
        if raw_source in {"configured_account", "recommendation", "dynamic_search"}:
            source = "douyin"
        elif raw_source in SOURCE_RUN_KEYS:
            source = raw_source
        else:
            mapped = source_name(str(row.get("platform") or raw_source), source_id, raw_source)
            source = "aihot_selected" if mapped == "aihot" and "selected" in raw_source else (
                "aihot_daily" if mapped == "aihot" and "daily" in raw_source else mapped
            )
        if source not in SOURCE_RUN_KEYS:
            raise ProjectionError("unknown_source_run_identity")
        grouped_source_rows.setdefault(source, []).append(row)
    for source, rows_for_source in sorted(grouped_source_rows.items()):
        row = rows_for_source[0]
        counts = row.get("counts") if isinstance(row.get("counts"), dict) else {}
        item_count = sum(int(
            candidate.get("item_count") if candidate.get("item_count") is not None
            else candidate.get("discovered_count", content_source_counts.get(source, 0))
        ) for candidate in rows_for_source)
        succeeded_count = sum(int(
            candidate.get("succeeded_count") if candidate.get("succeeded_count") is not None
            else counts["new"] if "new" in counts
            else int(str(candidate.get("status") or "") in {"completed", "completed_empty", "partial"})
        ) for candidate in rows_for_source)
        failed_count = sum(int(
            candidate.get("failed_count") if candidate.get("failed_count") is not None
            else counts["failed"] if "failed" in counts
            else int(str(candidate.get("status") or "") in {"failed", "partial", "blocked"})
        ) for candidate in rows_for_source)
        statuses = {str(candidate.get("status") or "") for candidate in rows_for_source}
        if statuses <= {"not_attempted", "blocked"}:
            status = "not_attempted" if statuses == {"not_attempted"} else "blocked"
        elif failed_count and succeeded_count:
            status = "partial"
        elif failed_count:
            status = "failed"
        elif statuses == {"completed_empty"}:
            status = "completed_empty"
        elif "partial" in statuses:
            status = "partial"
        else:
            status = "completed"
        planned_count = sum(int(candidate.get("planned_count") or 0) for candidate in rows_for_source)
        if planned_count == 0:
            planned_count = sum(int(
                candidate.get("attempt_count") if candidate.get("attempt_count") is not None
                else bool(candidate.get("attempted")) if candidate.get("attempted") is not None
                else str(candidate.get("status") or "") in {
                    "completed", "completed_empty", "partial", "failed", "blocked",
                }
            ) for candidate in rows_for_source)
        errors = sorted({str(candidate.get("error_summary") or candidate.get("reason") or "") for candidate in rows_for_source if candidate.get("error_summary") or candidate.get("reason")})
        source_runs.append({
            "id": f"{run_id}:{source}",
            "run_id": run_id, "source": source,
            "status": status,
            "planned_count": planned_count or succeeded_count + failed_count,
            "succeeded_count": succeeded_count,
            "failed_count": failed_count,
            "item_count": item_count,
            "error_summary": ";".join(errors)[:500],
            "completed_at": str(max((candidate.get("completed_at") or candidate.get("attempted_at") or "" for candidate in rows_for_source), default="") or run["updated_at"]),
        })
    payload = {
        "run_id": run_id, "business_date": run["business_date"], "revision": 1,
        "stage": "scripts", "authority_identity": authority_identity, "updated_at": run["updated_at"],
        "run": {
            "status": run["status"],
            "candidate_count": len(collection.get("hotspot_cards") or collection.get("candidates", [])),
        },
        "source_runs": source_runs, "collected_items": content, "topics": topics,
        "scripts": scripts, "articles": article_rows,
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
