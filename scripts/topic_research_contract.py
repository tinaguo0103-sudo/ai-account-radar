#!/usr/bin/env python3
"""Fail-closed source-open, web-research, hook and claim provenance contract."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import trusted_exact_source_adapter as source_adapter


VERSION = "ar020d_research_grounded_v1"
TRACKING_KEYS = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "spm", "from"}
ACCOUNT_PATH_MARKERS = {"user", "profile", "account", "channel", "home", "search"}
MATERIAL_CONCEPTS = {"\u8fd4\u4fee", "\u9a8c\u6536", "\u4ea4\u4ed8", "\u5546\u4e1a\u53ef\u7528", "\u6548\u7387\u63d0\u5347", "\u66ff\u4ee3\u5c97\u4f4d"}


class ContractError(RuntimeError):
    pass


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def hash_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def canonical_url(value: str) -> str:
    parts = urlsplit(str(value or "").strip())
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise ContractError("Exact source URL must be an absolute http(s) URL")
    query = urlencode([(key, val) for key, val in parse_qsl(parts.query, keep_blank_values=True) if key.lower() not in TRACKING_KEYS])
    path = re.sub(r"/{2,}", "/", parts.path or "/")
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path.rstrip("/") or "/", query, ""))


def is_concrete_source_url(value: str) -> bool:
    try:
        parts = urlsplit(canonical_url(value))
    except ContractError:
        return False
    segments = [segment.lower() for segment in parts.path.split("/") if segment]
    if not segments or segments[-1] in ACCOUNT_PATH_MARKERS:
        return False
    if any(marker in segments for marker in ACCOUNT_PATH_MARKERS) and not any(segment.isdigit() or len(segment) > 12 for segment in segments):
        return False
    return True


def evidence_ids(dossier: dict[str, Any]) -> set[str]:
    values = set()
    for item in dossier.get("source", {}).get("content_evidence", []):
        if item.get("evidence_id"):
            values.add(str(item["evidence_id"]))
    for item in dossier.get("results", dossier.get("research", {}).get("results", [])):
        if item.get("evidence_id"):
            values.add(str(item["evidence_id"]))
    return values


def parse_evidence_ids(value: Any) -> set[str]:
    if isinstance(value, list):
        return {str(item).strip() for item in value if str(item).strip()}
    return {item for item in re.split(r"[,，、\s]+", str(value or "")) if item}


def validate_claim_trace(decision: dict[str, Any], dossier: dict[str, Any]) -> None:
    known_ids = evidence_ids(dossier)
    evidence = parse_evidence_ids(decision.get("research_evidence_ids")) | parse_evidence_ids(
        decision.get("hook_evidence_ids")
    )
    text = "\n".join(str(decision.get(field) or "") for field in [
        "audience_hook", "natural_austin_angle", "selected_visible_title", "public_decision_summary",
    ])
    assert_claim_trace(text, sorted(evidence), known_ids, hypothesis=False)


def validate_recommendation_research_eligibility(
    decision: dict[str, Any], dossier: dict[str, Any]
) -> None:
    """Fail closed when a producible decision lacks freshly opened web context."""
    if str(decision.get("decision") or "") != "select" and str(
        decision.get("recommendation_status") or ""
    ) != "生成脚本包":
        return
    opened_ids = {
        str(item.get("evidence_id"))
        for item in dossier.get("results", [])
        if item.get("open_status") == "opened"
        and item.get("evidence_id")
        and item.get("url")
        and item.get("captured_content_hash")
        and item.get("dom_text_path")
        and item.get("opened_at")
    }
    used_ids = parse_evidence_ids(decision.get("research_evidence_ids")) | parse_evidence_ids(
        decision.get("hook_evidence_ids")
    )
    if not opened_ids:
        raise ContractError("Recommended candidate has no freshly opened external research evidence")
    if not (opened_ids & used_ids):
        raise ContractError("Recommended candidate does not cite opened external research evidence")


def validate_source_open(candidate: dict[str, Any], output: dict[str, Any]) -> dict[str, Any]:
    try:
        source_adapter.validate_primary_adapter(candidate, output)
    except source_adapter.AdapterContractError as exc:
        raise ContractError(str(exc)) from exc
    expected = canonical_url(candidate.get("exact_url", ""))
    status = str(output.get("open_status") or "")
    if status != "opened":
        return {**output, "open_status": "failed", "eligible": False, "failure_reason": output.get("failure_reason") or "exact_source_not_opened"}
    exact = canonical_url(output.get("exact_url", ""))
    final = canonical_url(output.get("final_url", ""))
    if exact != expected or final != expected:
        raise ContractError("Opened URL does not match the exact shortlisted source URL")
    if not is_concrete_source_url(final):
        raise ContractError("Source URL is not a concrete article/video page")
    required = ["platform", "author", "opened_at", "captured_content_hash", "source_type", "source_summary"]
    missing = [field for field in required if not str(output.get(field) or "").strip()]
    if missing:
        raise ContractError(f"Source-open output missing fields: {', '.join(missing)}")
    if output.get("independent_title_verified"):
        if not str(output.get("exact_title") or "").strip():
            raise ContractError("Verified independent title is empty")
    elif not str(output.get("caption_body") or "").strip():
        raise ContractError("Platform without an independent title requires caption/post text")
    if not re.fullmatch(r"[0-9a-f]{64}", str(output.get("captured_content_hash"))):
        raise ContractError("captured_content_hash must be SHA256")
    if not output.get("content_evidence"):
        raise ContractError("Opened source has no content evidence")
    if "douyin.com" in expected:
        if output.get("retrieval_surface") != "dedicated_local_chrome_cdp":
            raise ContractError("Douyin exact source must use the dedicated local Chrome CDP")
        if output.get("identity_match") is not True or output.get("input_video_id") != output.get("final_video_id"):
            raise ContractError("Douyin exact video identity is unverified")
        if output.get("title_verification_state") != "visible_prefix_match":
            raise ContractError("Douyin exact title was not verified against visible page content")
        screenshot_path = str(output.get("screenshot_path") or "")
        if not screenshot_path:
            raise ContractError("Douyin exact source has no screenshot evidence path")
    return {**output, "exact_url": exact, "final_url": final, "eligible": True, "failure_reason": ""}


def validate_research_dossier(candidate: dict[str, Any], source: dict[str, Any], dossier: dict[str, Any]) -> dict[str, Any]:
    if source.get("open_status") != "opened" or not source.get("eligible"):
        raise ContractError("Research cannot substitute for an unopened exact source")
    if dossier.get("source_content_hash") != source.get("captured_content_hash"):
        raise ContractError("Research source hash mismatch")
    if dossier.get("status") != "completed":
        return {**dossier, "eligible": False, "failure_reason": dossier.get("failure_reason") or "research_not_completed"}
    queries = dossier.get("queries") or []
    results = dossier.get("results") or []
    if not queries:
        raise ContractError("Research query ledger is empty")
    exact_url = canonical_url(candidate.get("exact_url", "")) if candidate.get("exact_url") else ""
    normalized_queries = [str(item.get("query") if isinstance(item, dict) else item).strip() for item in queries]
    if exact_url and not any(query and query != exact_url and exact_url not in query for query in normalized_queries):
        raise ContractError("Research requires a topical/entity/claim query beyond the exact source URL")
    opened = [item for item in results if item.get("open_status") == "opened" and item.get("url") and item.get("evidence_id")]
    corroboration_state = str(dossier.get("external_corroboration_state") or "")
    if not opened and corroboration_state != "no_accessible_corroboration":
        raise ContractError("Research has no opened supporting result and no explicit no-corroboration state")
    if not opened and str(dossier.get("confidence") or "").lower() not in {"low", "弱"}:
        raise ContractError("Source-only research must use low confidence")
    if not opened and not str(dossier.get("corroboration_gap") or "").strip():
        raise ContractError("Source-only research must explain the corroboration gap")
    for item in opened:
        required_result_fields = (
            "title", "publisher", "opened_at", "captured_content_hash",
            "dom_text_path", "source_class", "supported_claim",
        )
        missing_result = [name for name in required_result_fields if not str(item.get(name) or "").strip()]
        if missing_result:
            raise ContractError(
                "Opened research result missing fields: " + ", ".join(missing_result)
            )
        if item.get("evidence_surface") in {"search_snippet", "model_memory", "prior_dossier"}:
            raise ContractError("Search snippets, model memory, and prior dossiers are not research evidence")
        dom_path = Path(str(item["dom_text_path"]))
        if not dom_path.is_file() or not dom_path.read_text(encoding="utf-8").strip():
            raise ContractError("Opened research result has no readable DOM text artifact")
        if hashlib.sha256(dom_path.read_bytes()).hexdigest() != item["captured_content_hash"]:
            raise ContractError("Opened research result DOM hash mismatch")
    ids = {str(item.get("evidence_id")) for item in source.get("content_evidence", []) if item.get("evidence_id")}
    ids.update(str(item.get("evidence_id")) for item in opened)
    hook = dossier.get("hook_analysis") or {}
    hook_ids = {str(value) for value in hook.get("hook_evidence_ids") or []}
    if not hook.get("audience_hook") or not hook.get("why_unfamiliar_audience_clicks"):
        raise ContractError("Research dossier has no public audience hook")
    if not hook_ids or not hook_ids.issubset(ids):
        raise ContractError("Hook evidence IDs are missing or unknown")
    if hook.get("product_name_is_not_hook") is not True:
        raise ContractError("Product/entity name must not be treated as a self-explanatory hook")
    claims = dossier.get("claim_evidence") or []
    for claim in claims:
        claim_ids = {str(value) for value in claim.get("evidence_ids") or []}
        if not claim_ids or not claim_ids.issubset(ids):
            raise ContractError("Material claim has missing or unknown evidence IDs")
        if claim.get("persona_only"):
            raise ContractError("Persona-only claim cannot enter source/research evidence")
    clean = {key: value for key, value in dossier.items() if key != "dossier_hash"}
    expected_hash = hash_json(clean)
    if dossier.get("dossier_hash") != expected_hash:
        raise ContractError("Research dossier hash mismatch")
    return {**dossier, "eligible": True, "failure_reason": ""}


def assert_claim_trace(text: str, evidence: list[str], known_ids: set[str], *, hypothesis: bool = False) -> None:
    used = {str(value) for value in evidence}
    if used and not used.issubset(known_ids):
        raise ContractError("Editorial claim uses unknown evidence IDs")
    if any(term in str(text or "") for term in MATERIAL_CONCEPTS) and not used and not hypothesis:
        raise ContractError("Strong content concept lacks source/research evidence IDs")


def cache_ttl_hours(source_type: str) -> int:
    normalized = str(source_type or "").lower()
    if any(term in normalized for term in ["news", "event", "\u65b0\u95fb", "\u70ed\u70b9"]):
        return 24
    if any(term in normalized for term in ["product", "update", "tool", "\u4ea7\u54c1", "\u66f4\u65b0", "\u5de5\u5177"]):
        return 72
    return 24 * 7


def cache_valid(dossier: dict[str, Any], *, source_hash: str, now: datetime | None = None) -> bool:
    if dossier.get("source_content_hash") != source_hash:
        return False
    try:
        completed = datetime.fromisoformat(str(dossier.get("completed_at")).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return False
    current = now or datetime.now(timezone.utc)
    if completed.tzinfo is None:
        completed = completed.replace(tzinfo=timezone.utc)
    age_hours = (current - completed).total_seconds() / 3600
    return 0 <= age_hours <= cache_ttl_hours(str(dossier.get("source_type") or ""))
