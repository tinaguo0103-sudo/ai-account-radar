"""Validate declared source dependencies at existing Writer submission gates.

The validator checks evidence identity, paragraph coverage and allowed claim
scope. It cannot decide whether a paraphrase is semantically accurate.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from typing import Any
from urllib.parse import urlsplit

from daily_workflow import WorkflowConflict


RESEARCH_MATERIAL_KEYS = (
    "run_id", "business_date", "topic_id", "source_url", "title", "excerpt",
)
CLAIM_SCOPES = frozenset({
    "source_quote", "source_context", "interpretation",
    "motion_observation", "external_fact",
})


def normalized_known_name(value: str) -> str:
    """Normalize Unicode dash variants only while comparing known names."""
    dashes = "\u2010\u2011\u2012\u2013\u2014\u2015\u2212\ufe58\ufe63\uff0d"
    return unicodedata.normalize("NFC", value).translate(str.maketrans({char: "-" for char in dashes}))


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def evidence_digest(evidence: dict[str, Any]) -> str:
    return digest({key: value for key, value in evidence.items() if key != "claim_contract"})


def research_evidence_id(material: dict[str, Any]) -> str:
    """Derive a source identity from the exact current-topic material record."""
    record = {key: material[key] for key in RESEARCH_MATERIAL_KEYS}
    return "public-research:" + digest(record)


def _research_binding(evidence: dict[str, Any], topic_id: str) -> dict[str, str]:
    source_material = evidence.get("source_material")
    source_material = source_material if isinstance(source_material, dict) else {}
    same_run = source_material.get("same_run")
    same_run = same_run if isinstance(same_run, dict) else {}
    source_record = source_material.get("source_record")
    source_record = source_record if isinstance(source_record, dict) else {}
    run_id = same_run.get("run_id")
    business_date = same_run.get("business_date")
    if (
        not isinstance(run_id, str) or not run_id
        or not isinstance(business_date, str) or not business_date
        or not topic_id
        or source_record.get("candidate_id") != topic_id
    ):
        raise WorkflowConflict("script_claim_research_material_identity_conflict")
    return {
        "run_id": run_id,
        "business_date": business_date,
        "topic_id": topic_id,
    }


def validate_research_materials(
    evidence: dict[str, Any], topic_id: str, materials: Any,
) -> list[dict[str, str]]:
    """Validate traceable source records without asserting source truth."""
    if not isinstance(materials, list):
        raise WorkflowConflict("script_claim_research_material_schema_invalid")
    if not materials:
        return []
    binding = _research_binding(evidence, topic_id)
    validated: list[dict[str, str]] = []
    seen: set[str] = set()
    for material in materials:
        if not isinstance(material, dict) or set(material) != set(RESEARCH_MATERIAL_KEYS):
            raise WorkflowConflict("script_claim_research_material_schema_invalid")
        if any(
            not isinstance(material.get(key), str)
            or not material[key]
            or material[key] != material[key].strip()
            for key in RESEARCH_MATERIAL_KEYS
        ):
            raise WorkflowConflict("script_claim_research_material_schema_invalid")
        if any(material[key] != value for key, value in binding.items()):
            raise WorkflowConflict("script_claim_research_material_identity_conflict")
        url = material["source_url"]
        if any(char.isspace() for char in url):
            raise WorkflowConflict("script_claim_research_material_schema_invalid")
        try:
            parsed = urlsplit(url)
        except ValueError as exc:
            raise WorkflowConflict("script_claim_research_material_schema_invalid") from exc
        if (
            parsed.scheme.lower() not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise WorkflowConflict("script_claim_research_material_schema_invalid")
        identity = research_evidence_id(material)
        if identity in seen:
            raise WorkflowConflict("script_claim_research_material_duplicate")
        seen.add(identity)
        validated.append({key: material[key] for key in RESEARCH_MATERIAL_KEYS})
    return validated


def claim_contract(
    evidence: dict[str, Any], *,
    research_materials: Any = None,
    topic_id: str = "",
) -> dict[str, Any]:
    anchors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    def add(source: str, kind: str, text: Any, scopes: list[str], **details: Any) -> None:
        if not text:
            return
        row = {"source_url": source, "kind": kind, "text": str(text), "allowed_scopes": scopes, **details}
        row["evidence_id"] = digest(row)
        if row not in anchors:
            anchors.append(row)

    def add_video(node: Any) -> None:
        if not isinstance(node, dict):
            return
        nested = [*(node.get("representative_sources") or []), *(node.get("additional_sources") or [])]
        if nested:
            for child in nested:
                add_video(child)
            return
        source_url = str(node.get("source_url") or "")
        quality = node.get("evidence_quality") if isinstance(node.get("evidence_quality"), dict) else {}
        add(source_url, "recognized_audio", node.get("asr_supplement"), ["source_quote", "interpretation"])
        for row in node.get("caption_timeline", []) or []:
            if isinstance(row, dict):
                add(
                    source_url, "recognized_screen_text", row.get("text"), ["source_quote", "interpretation"],
                    start=row.get("start"), end=row.get("end"), frame_sha256=row.get("frame_sha256"),
                )
        for row in node.get("screen_facts", []) or []:
            if isinstance(row, dict):
                add(
                    source_url, "recognized_screen_text",
                    row.get("text") or row.get("value"),
                    ["source_quote", "interpretation"],
                    start=row.get("start") if row.get("start") is not None else row.get("time_second"),
                    frame_sha256=row.get("frame_sha256"),
                )
        for row in quality.get("reviewed_claims", []) or []:
            if isinstance(row, dict) and row.get("text") and row.get("reviewed") is True:
                add(source_url, "reviewed_source_context", row["text"], ["source_context", "interpretation"], review_ref=row.get("review_ref"))
        for row in quality.get("reviewed_motion_observations", []) or []:
            if isinstance(row, dict) and row.get("continuous_motion_reviewed") is True:
                add(
                    source_url, "reviewed_continuous_motion", row.get("text"), ["motion_observation", "interpretation"],
                    start=row.get("start"), end=row.get("end"), evidence_ref=row.get("evidence_ref"),
                )
        for row in quality.get("external_fact_evidence", []) or []:
            if isinstance(row, dict) and row.get("verified") is True and row.get("url") and row.get("quote"):
                add(source_url, "external_fact_document", row["quote"], ["external_fact", "interpretation"], url=row["url"])
        for warning in node.get("unresolved_terms", []) or []:
            item = dict(warning) if isinstance(warning, dict) else {"term": str(warning)}
            item.setdefault("source_url", source_url)
            item["warning_id"] = digest(item)
            if item not in warnings:
                warnings.append(item)

    add_video(evidence.get("video"))
    source = evidence.get("source") if isinstance(evidence.get("source"), dict) else {}
    source_url = str(source.get("url") or "")
    add(source_url, "source_summary", source.get("summary"), ["source_context", "interpretation"])
    facts = evidence.get("source_facts") if isinstance(evidence.get("source_facts"), dict) else {}
    for key in ("details", "caption", "transcript", "public_claims"):
        if isinstance(facts.get(key), str):
            add(source_url, f"source_{key}", facts[key], ["source_quote", "interpretation"])

    materials = validate_research_materials(
        evidence, topic_id, [] if research_materials is None else research_materials,
    )
    for material in materials:
        row = {
            "source_url": material["source_url"],
            "kind": "public_research_material",
            "text": material["excerpt"],
            "allowed_scopes": ["source_quote", "source_context", "interpretation"],
            "title": material["title"],
            "run_id": material["run_id"],
            "business_date": material["business_date"],
            "topic_id": material["topic_id"],
        }
        row["evidence_id"] = research_evidence_id(material)
        if row not in anchors:
            anchors.append(row)

    source_material = evidence.get("source_material")
    source_material = source_material if isinstance(source_material, dict) else {}
    same_run = source_material.get("same_run")
    same_run = same_run if isinstance(same_run, dict) else {}
    source_record = source_material.get("source_record")
    source_record = source_record if isinstance(source_record, dict) else {}
    research_binding = None
    if (
        same_run.get("run_id")
        and same_run.get("business_date")
        and source_record.get("candidate_id")
    ):
        research_binding = {
            "run_id": str(same_run["run_id"]),
            "business_date": str(same_run["business_date"]),
            "topic_id": str(source_record["candidate_id"]),
        }

    return {
        "evidence_sha256": evidence_digest(evidence),
        "anchors": anchors,
        "warnings": warnings,
        "research_materials": materials,
        "research_material_binding": research_binding,
        "research_material_keys": list(RESEARCH_MATERIAL_KEYS),
        "research_evidence_id_rule": (
            "public-research:<sha256 of canonical JSON record with ensure_ascii=False, "
            "sort_keys=True and compact separators>"
        ),
        "research_truth_verified": False,
        # Keep legacy and evidence-free topics on the existing writer envelope.
        # Once source evidence or an unresolved warning is present, its declared
        # dependencies become part of both article and spoken submission gates.
        "required": bool(anchors or warnings),
        "paragraph_coverage_required": True,
        "source_scoped_claims_require_evidence": True,
        "interpretation_may_be_unanchored": True,
        "still_frames_do_not_verify_motion": True,
        "claim_review_keys": [
            "evidence_sha256", "content_sha256", "excluded_warning_ids",
            "research_materials", "claims",
        ],
        "claim_keys": ["field", "text", "scope", "evidence_ids"],
        "content_hash_fields": ["title", "hook", "structure", "body"],
        "content_hash_rule": (
            "SHA256 of available content_hash_fields as UTF-8 JSON; ensure_ascii=False, "
            "sort_keys=True, separators=(comma,colon). Claims cover each field paragraph in field order."
        ),
        "review_limits": "Dependency and scope checks do not replace semantic review of paraphrases.",
    }


def validate_claim_dependencies(
    content: dict[str, Any],
    evidence: dict[str, Any] | None,
    review: Any,
    *,
    frozen_research_materials: Any = None,
) -> None:
    if not isinstance(evidence, dict):
        raise WorkflowConflict("script_claim_authority_missing")
    if not isinstance(review, dict):
        raise WorkflowConflict("script_claim_dependencies_missing")
    topic_id = str(content.get("topic_id") or "")
    research_materials = review.get("research_materials", [])
    contract = claim_contract(
        evidence,
        research_materials=research_materials,
        topic_id=topic_id,
    )
    if (
        frozen_research_materials is not None
        and research_materials != frozen_research_materials
    ):
        raise WorkflowConflict("script_claim_research_material_conflict")
    if not isinstance(review, dict) or review.get("evidence_sha256") != contract["evidence_sha256"]:
        raise WorkflowConflict("script_claim_dependencies_missing")
    bound = {key: str(content[key]) for key in ("title", "hook", "structure", "body") if key in content}
    if review.get("content_sha256") != digest(bound):
        raise WorkflowConflict("script_claim_content_conflict")
    exclusions = set(review.get("excluded_warning_ids") or [])
    if exclusions != {row["warning_id"] for row in contract["warnings"]}:
        raise WorkflowConflict("script_claim_warning_disposition_missing")
    full_text = normalized_known_name("\n".join(bound.values()))
    for warning in contract["warnings"]:
        term = str(warning.get("term") or "")
        if term == "english_proper_noun":
            continue
        for raw_name in term.split(" / ") + list(warning.get("aliases") or []):
            name = normalized_known_name(str(raw_name))
            if name and re.search(r"(?<![A-Za-z0-9_])" + re.escape(name) + r"(?![A-Za-z0-9_])", full_text, re.I):
                raise WorkflowConflict("script_claim_uses_unconfirmed_term")
    claims = review.get("claims")
    if not isinstance(claims, list):
        raise WorkflowConflict("script_claim_dependencies_missing")
    expected = [
        (field, paragraph.strip())
        for field, text in bound.items()
        for paragraph in re.split(r"\n\s*\n", text)
        if paragraph.strip()
    ]
    observed = [(row.get("field"), row.get("text")) for row in claims if isinstance(row, dict)]
    if observed != expected:
        raise WorkflowConflict("script_claim_paragraph_coverage_incomplete")
    anchors = {row["evidence_id"]: row for row in contract["anchors"]}
    for claim in claims:
        if not isinstance(claim, dict):
            raise WorkflowConflict("script_claim_paragraph_coverage_incomplete")
        scope = claim.get("scope")
        if not isinstance(scope, str) or scope not in CLAIM_SCOPES:
            raise WorkflowConflict("script_claim_evidence_scope_conflict")
        refs = claim.get("evidence_ids")
        if not isinstance(refs, list):
            raise WorkflowConflict("script_claim_evidence_missing")
        if any(not isinstance(reference, str) for reference in refs):
            raise WorkflowConflict("script_claim_evidence_identity_conflict")
        if not refs and scope != "interpretation":
            raise WorkflowConflict("script_claim_evidence_missing")
        for reference in refs:
            if reference not in anchors:
                raise WorkflowConflict("script_claim_evidence_identity_conflict")
            if scope not in anchors[reference]["allowed_scopes"]:
                raise WorkflowConflict("script_claim_evidence_scope_conflict")
