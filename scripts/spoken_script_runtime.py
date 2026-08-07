#!/usr/bin/env python3
"""Public per-topic spoken-script checkpoint helpers."""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

from daily_workflow import WorkflowConflict, canonical


RUNTIME_SCHEMA_VERSION = 1
DEFAULT_VOICE_PACK = (
    Path(__file__).resolve().parents[1]
    / "config"
    / "web010_austin_voice_pack.json"
)
PRIVATE_STYLE_SOURCE_SPECS = (
    (
        "production_context",
        Path(".codex/skills/austin-no-overtime-scripting/references/private/production_context.md"),
    ),
    (
        "private_runtime",
        Path(".codex/skills/austin-no-overtime-scripting/references/private/private_runtime.json"),
    ),
    (
        "evidence_playbook",
        Path(".codex/skills/austin-no-overtime-scripting/references/private/evidence_playbook.md"),
    ),
    (
        "private_topic_cards",
        Path(".codex/skills/austin-no-overtime-scripting/examples/private/full_topic_cards.json"),
    ),
    (
        "voice_prd",
        Path("00_资料库/01_项目PRD/Austin不加班脚本Skill_PRD.md"),
    ),
)


def _private_style_source_paths(
    source_paths: dict[str, str | Path] | None = None,
) -> list[tuple[str, Path]]:
    if source_paths is not None:
        return [(role, Path(path)) for role, path in source_paths.items()]
    home = Path(os.environ.get("HOME") or Path.home())
    project_root = Path(__file__).resolve().parents[1]
    paths: list[tuple[str, Path]] = []
    for role, relative in PRIVATE_STYLE_SOURCE_SPECS:
        if str(relative).startswith(".codex/"):
            base = home
        elif (project_root / relative).exists():
            base = project_root
        else:
            base = project_root.parent
        paths.append((role, base / relative))
    return paths


def _read_private_sources(
    source_paths: dict[str, str | Path] | None = None,
) -> dict[str, dict[str, Any]]:
    sources: dict[str, dict[str, Any]] = {}
    for role, path in _private_style_source_paths(source_paths):
        try:
            raw = path.read_bytes()
            text = raw.decode("utf-8")
        except (OSError, UnicodeError):
            continue
        if text.strip():
            sources[role] = {
                "role": role,
                "text": text,
                "sha256": hashlib.sha256(raw).hexdigest(),
                "content_bytes": len(raw),
            }
    return sources


def _catalog_from_sources(sources: dict[str, dict[str, Any]]) -> dict[str, Any]:
    rows = [
        {
            "role": role,
            "sha256": value["sha256"],
            "content_bytes": value["content_bytes"],
        }
        for role, value in sorted(sources.items())
    ]
    encoded = canonical(rows).encode("utf-8")
    return {
        "contract": "per_topic_reference_catalog_v2",
        "loaded": len(sources) == len(PRIVATE_STYLE_SOURCE_SPECS),
        "loaded_source_count": len(sources),
        "expected_source_count": len(PRIVATE_STYLE_SOURCE_SPECS),
        "source_roles": [row["role"] for row in rows],
        "source_hashes": {row["role"]: row["sha256"] for row in rows},
        "catalog_sha256": hashlib.sha256(encoded).hexdigest(),
        "case_matching": "topic_specific_transient",
        "current_fact_source": False,
        "raw_content_embedded": False,
    }


def load_private_style_context(
    source_paths: dict[str, str | Path] | None = None,
) -> dict[str, Any]:
    """Return only safe catalog metadata; raw private text stays in memory."""
    return _catalog_from_sources(_read_private_sources(source_paths))


def _matching_terms(text: str, terms: list[str]) -> list[str]:
    lowered = text.casefold()
    matches: list[str] = []
    for term in terms:
        value = str(term or "").strip()
        if value and value.casefold() in lowered and value not in matches:
            matches.append(value)
    return matches


def _private_card_terms(card: dict[str, Any]) -> list[str]:
    title = str(card.get("topic_title") or "")
    # The terms come from the private case title itself, not from the current topic.
    terms = re.findall(r"[A-Za-z0-9+#.-]+|[\u4e00-\u9fff]{2}", title)
    stop = {
        "不是", "真正", "最难", "一个", "怎么", "以后", "需要", "工作",
        "流程", "内容", "系统", "自动", "人工", "可以", "应该", "开始",
    }
    return [term for term in terms if term not in stop]


def _private_case_excerpt(card: dict[str, Any]) -> str:
    fields = (
        ("题目", "topic_title"),
        ("核心判断", "core_thesis"),
        ("痛点", "pain_point"),
        ("旧流程", "old_workflow"),
        ("AI介入", "ai_intervention"),
        ("独有判断", "unique_judgment"),
    )
    return "\n".join(
        f"{label}：{card[key]}"
        for label, key in fields
        if str(card.get(key) or "").strip()
    )


def load_private_reference_library(
    source_paths: dict[str, str | Path] | None = None,
) -> dict[str, Any]:
    """Load private cases/persona excerpts for transient, topic-local use only."""
    sources = _read_private_sources(source_paths)
    catalog = _catalog_from_sources(sources)
    cases: list[dict[str, Any]] = []
    runtime: dict[str, Any] = {}
    cards: list[dict[str, Any]] = []
    try:
        runtime = json.loads(sources.get("private_runtime", {}).get("text", "{}"))
    except (json.JSONDecodeError, TypeError):
        runtime = {}
    try:
        raw_cards = json.loads(sources.get("private_topic_cards", {}).get("text", "[]"))
        cards = raw_cards if isinstance(raw_cards, list) else []
    except (json.JSONDecodeError, TypeError):
        cards = []
    anchors = runtime.get("case_anchors", []) if isinstance(runtime, dict) else []
    for card in cards:
        if not isinstance(card, dict) or not str(card.get("topic_id") or ""):
            continue
        reference_id = f"private_case:{card['topic_id']}"
        terms = _private_card_terms(card)
        for anchor in anchors if isinstance(anchors, list) else []:
            if not isinstance(anchor, dict):
                continue
            anchor_terms = [str(term) for term in anchor.get("keywords", [])]
            if str(anchor.get("name") or "") and any(
                term.casefold() in str(card.get("topic_title") or "").casefold()
                for term in anchor_terms if term
            ):
                terms.extend(anchor_terms)
        cases.append({
            "reference_id": reference_id,
            "role": "private_case",
            "source_role": "private_topic_cards",
            "source_sha256": sources.get("private_topic_cards", {}).get("sha256", ""),
            "match_terms": sorted(set(terms)),
            "text": _private_case_excerpt(card),
        })

    persona_text = sources.get("production_context", {}).get("text", "")
    paragraphs = [part.strip() for part in persona_text.split("\n\n") if part.strip()]
    persona_candidates = [
        part for part in paragraphs
        if any(term in part for term in ("AI业务系统导演", "真人轨", "录屏轨", "人工判断"))
    ]
    if persona_candidates:
        cases.append({
            "reference_id": "private_persona:production_context:director",
            "role": "private_persona",
            "source_role": "production_context",
            "source_sha256": sources.get("production_context", {}).get("sha256", ""),
            "match_terms": ["AI", "导演", "工作流", "判断", "交付"],
            "text": persona_candidates[0],
        })
    return {
        "catalog": catalog,
        "references": cases,
    }


def load_voice_pack(path: str | Path = DEFAULT_VOICE_PACK) -> dict[str, Any]:
    source = Path(path)
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise WorkflowConflict("voice_pack_unreadable") from error
    exemplars = value.get("exemplars") if isinstance(value, dict) else None
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != RUNTIME_SCHEMA_VERSION
        or not isinstance(exemplars, list)
    ):
        raise WorkflowConflict("voice_pack_schema_invalid")
    if len(exemplars) != 2:
        raise WorkflowConflict("voice_pack_count_invalid")
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for exemplar in exemplars:
        if not isinstance(exemplar, dict):
            raise WorkflowConflict("voice_pack_exemplar_invalid")
        exemplar_id = str(exemplar.get("exemplar_id") or "")
        title = str(exemplar.get("title") or "")
        raw_body = exemplar.get("body")
        body = "\n\n".join(str(part) for part in raw_body) if isinstance(raw_body, list) else str(raw_body or "")
        style_focus = str(exemplar.get("style_focus") or "")
        match_terms = exemplar.get("match_terms", [])
        if not isinstance(match_terms, list):
            raise WorkflowConflict("voice_pack_exemplar_invalid")
        if not exemplar_id or exemplar_id in seen or not title or not body or not style_focus:
            raise WorkflowConflict("voice_pack_exemplar_invalid")
        seen.add(exemplar_id)
        normalized.append({
            "exemplar_id": exemplar_id,
            "title": title,
            "body": body,
            "style_focus": style_focus,
            "match_terms": [str(term) for term in match_terms if str(term).strip()],
            "source_role": "style_only",
        })
    # Match terms are retrieval metadata. Keep the established library digest based
    # on the approved body/title/style fields so existing checkpoints remain valid.
    digest_exemplars = [
        {key: row[key] for key in ("exemplar_id", "title", "body", "style_focus", "source_role")}
        for row in normalized
    ]
    pack_payload = {
        "schema_version": RUNTIME_SCHEMA_VERSION,
        "exemplars": digest_exemplars,
    }
    encoded = canonical(pack_payload).encode("utf-8")
    return {
        "schema_version": pack_payload["schema_version"],
        "exemplars": normalized,
        "sha256": hashlib.sha256(encoded).hexdigest(),
        "content_bytes": len(encoded),
    }


def selected_topic_ids(selected_topics: list[dict[str, Any]]) -> list[str]:
    identities = [str(row.get("topic_id") or "") for row in selected_topics]
    if not identities or any(not identity for identity in identities):
        raise WorkflowConflict("scripts_selected_topics_invalid")
    if len(identities) != len(set(identities)):
        raise WorkflowConflict("scripts_selected_topics_duplicate")
    return identities


def new_checkpoint(
    run_id: str,
    business_date: str,
    selected_topics: list[dict[str, Any]],
    voice_pack: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": RUNTIME_SCHEMA_VERSION,
        "run_id": run_id,
        "business_date": business_date,
        "stage": "scripts",
        "status": "in_progress",
        "selected_topic_ids": selected_topic_ids(selected_topics),
        "selected_count": len(selected_topics),
        "completed_scripts": [],
        "completed_receipts": [],
        "voice_pack": {
            "schema_version": voice_pack["schema_version"],
            "sha256": voice_pack["sha256"],
            "content_bytes": voice_pack["content_bytes"],
            "exemplar_ids": [row["exemplar_id"] for row in voice_pack["exemplars"]],
            "style_only": True,
        },
    }


def validate_checkpoint(
    checkpoint: dict[str, Any],
    run_id: str,
    business_date: str,
    selected_topics: list[dict[str, Any]],
    voice_pack: dict[str, Any],
) -> None:
    if (
        checkpoint.get("schema_version") != RUNTIME_SCHEMA_VERSION
        or checkpoint.get("run_id") != run_id
        or checkpoint.get("business_date") != business_date
        or checkpoint.get("stage") != "scripts"
        or checkpoint.get("status") != "in_progress"
    ):
        raise WorkflowConflict("scripts_checkpoint_identity_conflict")
    if checkpoint.get("selected_topic_ids") != selected_topic_ids(selected_topics):
        raise WorkflowConflict("scripts_checkpoint_selection_conflict")
    voice = checkpoint.get("voice_pack")
    if (
        not isinstance(voice, dict)
        or voice.get("sha256") != voice_pack.get("sha256")
        or voice.get("content_bytes") != voice_pack.get("content_bytes")
        or voice.get("style_only") is not True
    ):
        raise WorkflowConflict("scripts_checkpoint_voice_pack_conflict")
    completed = checkpoint.get("completed_scripts")
    receipts = checkpoint.get("completed_receipts")
    if not isinstance(completed, list) or not isinstance(receipts, list):
        raise WorkflowConflict("scripts_checkpoint_shape_invalid")
    if len(completed) != len(receipts):
        raise WorkflowConflict("scripts_checkpoint_receipt_conflict")
    selected = set(checkpoint["selected_topic_ids"])
    seen: set[str] = set()
    for script in completed:
        if not isinstance(script, dict):
            raise WorkflowConflict("scripts_checkpoint_script_invalid")
        identity = str(script.get("topic_id") or "")
        if identity not in selected or identity in seen:
            raise WorkflowConflict("scripts_checkpoint_script_identity_conflict")
        if set(script) != {"topic_id", "title", "hook", "structure", "body"}:
            raise WorkflowConflict("scripts_checkpoint_script_schema_invalid")
        if not all(str(script.get(key) or "").strip() for key in (
            "topic_id", "title", "hook", "structure", "body",
        )):
            raise WorkflowConflict("scripts_checkpoint_script_incomplete")
        seen.add(identity)
    for receipt in receipts:
        if (
            not isinstance(receipt, dict)
            or str(receipt.get("topic_id") or "") not in seen
            or receipt.get("voice_pack_sha256") != voice_pack.get("sha256")
            or not str(receipt.get("packet_id") or "")
        ):
            raise WorkflowConflict("scripts_checkpoint_receipt_invalid")
    if [receipt["topic_id"] for receipt in receipts] != [
        script["topic_id"] for script in completed
    ]:
        raise WorkflowConflict("scripts_checkpoint_receipt_order_conflict")


def ensure_checkpoint(
    workflow: Any,
    run_id: str,
    business_date: str,
    selected_topics: list[dict[str, Any]],
    voice_pack: dict[str, Any],
) -> dict[str, Any]:
    existing = workflow.stage(run_id, "scripts")
    if existing is None:
        checkpoint = new_checkpoint(run_id, business_date, selected_topics, voice_pack)
        workflow.commit_stage(run_id, "scripts", checkpoint, "in_progress")
        return checkpoint
    if existing.get("status") != "in_progress":
        raise WorkflowConflict("scripts_checkpoint_not_in_progress")
    checkpoint = existing["payload"]
    validate_checkpoint(checkpoint, run_id, business_date, selected_topics, voice_pack)
    return checkpoint


def first_unfinished_index(checkpoint: dict[str, Any]) -> int:
    completed = {
        str(row.get("topic_id") or "")
        for row in checkpoint.get("completed_scripts", [])
    }
    for index, identity in enumerate(checkpoint["selected_topic_ids"]):
        if identity not in completed:
            return index
    return len(checkpoint["selected_topic_ids"])


def _topic_reference_text(topic: dict[str, Any]) -> str:
    values: list[str] = []
    for key, value in topic.items():
        if key in {"topic_id", "run_id", "business_date"}:
            continue
        if isinstance(value, (dict, list)):
            values.append(json.dumps(value, ensure_ascii=False, sort_keys=True))
        elif value not in (None, ""):
            values.append(str(value))
    return "\n".join(values)


def _reference_ledger(
    approved: list[dict[str, Any]],
    private: list[dict[str, Any]],
    catalog: dict[str, Any],
) -> dict[str, Any]:
    return {
        "selection_contract": "per_topic_transient_references_v2",
        "approved_full_script_ids": [row["exemplar_id"] for row in approved],
        "private_excerpt_ids": [row["reference_id"] for row in private],
        "approved_reasons": [row["selection_reason"] for row in approved],
        "private_reasons": [row["selection_reason"] for row in private],
        "private_catalog_sha256": catalog.get("catalog_sha256", ""),
        "raw_text_persisted": False,
        "current_fact_source": "topic_card_only",
    }


def select_topic_references(
    topic: dict[str, Any],
    voice_pack: dict[str, Any],
    private_library: dict[str, Any] | None = None,
    private_source_paths: dict[str, str | Path] | None = None,
) -> dict[str, Any]:
    """Select only relevant references for this topic, keeping selected text transient."""
    topic_text = _topic_reference_text(topic)
    scored_approved: list[tuple[int, dict[str, Any], list[str]]] = []
    for exemplar in voice_pack["exemplars"]:
        matches = _matching_terms(topic_text, exemplar.get("match_terms", []))
        if matches:
            scored_approved.append((len(matches), exemplar, matches))
    scored_approved.sort(key=lambda row: (-row[0], row[1]["exemplar_id"]))
    approved: list[dict[str, Any]] = []
    if scored_approved:
        score, exemplar, matches = scored_approved[0]
        approved.append({
            "exemplar_id": exemplar["exemplar_id"],
            "title": exemplar["title"],
            "body": exemplar["body"],
            "style_focus": exemplar["style_focus"],
            "source_role": "approved_production_full_script",
            "body_sha256": hashlib.sha256(exemplar["body"].encode("utf-8")).hexdigest(),
            "selection_reason": f"topic matches approved production voice situation via {', '.join(matches[:4])}",
            "match_score": score,
        })

    library = private_library or load_private_reference_library(private_source_paths)
    scored_private: list[tuple[int, dict[str, Any], list[str]]] = []
    for reference in library.get("references", []):
        matches = _matching_terms(topic_text, reference.get("match_terms", []))
        if matches:
            scored_private.append((len(matches), reference, matches))
    scored_private.sort(key=lambda row: (-row[0], row[1]["reference_id"]))
    private: list[dict[str, Any]] = []
    for score, reference, matches in scored_private[:2]:
        private.append({
            "reference_id": reference["reference_id"],
            "role": reference["role"],
            "source_role": reference["source_role"],
            "source_sha256": reference["source_sha256"],
            "text": reference["text"],
            "excerpt_sha256": hashlib.sha256(reference["text"].encode("utf-8")).hexdigest(),
            "selection_reason": f"topic-specific private {reference['role']} match via {', '.join(matches[:4])}",
            "match_score": score,
        })
    if not private:
        persona = next(
            (row for row in library.get("references", []) if row.get("role") == "private_persona"),
            None,
        )
        if persona:
            private.append({
                "reference_id": persona["reference_id"],
                "role": persona["role"],
                "source_role": persona["source_role"],
                "source_sha256": persona["source_sha256"],
                "text": persona["text"],
                "excerpt_sha256": hashlib.sha256(persona["text"].encode("utf-8")).hexdigest(),
                "selection_reason": "no private case matched; use the approved general persona excerpt only",
                "match_score": 0,
            })
    ledger = _reference_ledger(approved, private, library["catalog"])
    return {
        "approved_full_scripts": approved,
        "private_excerpts": private,
        "ledger": ledger,
        "catalog": library["catalog"],
    }


def _safe_reference_row(row: dict[str, Any], private: bool = False) -> dict[str, Any]:
    keys = (
        ("reference_id", "excerpt_sha256", "selection_reason")
        if private else
        ("exemplar_id", "body_sha256", "selection_reason")
    )
    return {key: row[key] for key in keys if row.get(key) not in (None, "")}


def sanitize_handoff(value: dict[str, Any]) -> dict[str, Any]:
    """Remove transient private/full reference text before artifact persistence."""
    document = json.loads(json.dumps(value, ensure_ascii=False))
    if document.get("action") != "scripts_required":
        return document
    topic_input = document.get("topic_input")
    if not isinstance(topic_input, dict):
        return document
    transient = topic_input.get("reference_input")
    if isinstance(transient, dict):
        topic_input["reference_input"] = {
            "approved_full_scripts": [
                _safe_reference_row(row)
                for row in transient.get("approved_full_scripts", [])
                if isinstance(row, dict)
            ],
            "private_excerpts": [
                _safe_reference_row(row, private=True)
                for row in transient.get("private_excerpts", [])
                if isinstance(row, dict)
            ],
            "raw_text_persisted": False,
        }
    document.pop("voice_pack", None)
    contract = document.get("voice_pack_contract")
    if isinstance(contract, dict):
        contract["embedded_content"] = False
        contract["shared_full_text_pack"] = False
        contract["transient_topic_reference_input"] = True
    return document


def topic_packet(
    run_id: str,
    business_date: str,
    topic: dict[str, Any],
    index: int,
    selected_count: int,
    completed_count: int,
    voice_pack: dict[str, Any],
    private_source_paths: dict[str, str | Path] | None = None,
    private_library: dict[str, Any] | None = None,
) -> dict[str, Any]:
    private_style_context = load_private_style_context(private_source_paths)
    references = select_topic_references(
        topic,
        voice_pack,
        private_library=private_library,
        private_source_paths=private_source_paths,
    )
    ledger = references["ledger"]
    packet_basis = {
        "run_id": run_id,
        "business_date": business_date,
        "topic": topic,
        "voice_pack_sha256": voice_pack["sha256"],
        "reference_ledger": ledger,
    }
    packet_id = hashlib.sha256(canonical(packet_basis).encode("utf-8")).hexdigest()
    return {
        "ok": True,
        "action": "scripts_required",
        "stage": "scripts",
        "status": "waiting",
        "run_id": run_id,
        "business_date": business_date,
        "selected_topics": [topic],
        "topic_index": index,
        "selected_count": selected_count,
        "completed_count": completed_count,
        "remaining_count": selected_count - completed_count,
        "next_topic_not_exposed_until_submit": True,
        "topic_input": {
            "packet_id": packet_id,
            "topic_id": topic["topic_id"],
            "voice_pack_sha256": voice_pack["sha256"],
            "voice_pack_content_bytes": voice_pack["content_bytes"],
            "private_style_context": private_style_context,
            "reference_selection": ledger,
            "reference_input": {
                "approved_full_scripts": references["approved_full_scripts"],
                "private_excerpts": references["private_excerpts"],
                "current_topic_only": True,
            },
            "semantic_reread": {
                "current_topic_only": True,
                "compare_opening_argument_and_close": True,
                "previous_topic_body_included": False,
                "max_rewrites": 1,
                "quality_judgment": "outer_codex_semantic_review",
            },
        },
        "voice_pack_sha256": voice_pack["sha256"],
        "voice_pack_content_bytes": voice_pack["content_bytes"],
        "voice_pack_contract": {
            "style_only": True,
            "not_current_fact_source": True,
            "embedded_content": False,
            "shared_full_text_pack": False,
            "transient_topic_reference_input": True,
            "approved_full_script_count": len(references["approved_full_scripts"]),
            "private_excerpt_count": len(references["private_excerpts"]),
            "positive_authority": "topic_matched_user_approved_full_bodies_and_private_excerpts",
            "rejected_system_scripts_included": False,
        },
        "private_style_context": private_style_context,
        "reference_selection": ledger,
        "required_script_input": {
            "keys": ["packet_id", "voice_pack_sha256", "script"],
            "script_keys": ["topic_id", "title", "hook", "structure", "body"],
            "current_topic_only": True,
            "reference_text_is_transient_input": True,
            "previous_topic_body_included": False,
        },
    }


def submit_topic(
    workflow: Any,
    run_id: str,
    business_date: str,
    selected_topics: list[dict[str, Any]],
    checkpoint: dict[str, Any],
    voice_pack: dict[str, Any],
    submitted: dict[str, Any],
) -> dict[str, Any]:
    validate_checkpoint(checkpoint, run_id, business_date, selected_topics, voice_pack)
    index = first_unfinished_index(checkpoint)
    if index >= len(selected_topics):
        raise WorkflowConflict("scripts_checkpoint_already_complete")
    if not isinstance(submitted, dict) or set(submitted) != {
        "packet_id", "voice_pack_sha256", "script",
    }:
        raise WorkflowConflict("script_topic_submission_schema_invalid")
    topic = selected_topics[index]
    packet = topic_packet(
        run_id, business_date, topic, index, len(selected_topics),
        len(checkpoint["completed_scripts"]), voice_pack,
    )
    if (
        submitted.get("packet_id") != packet["topic_input"]["packet_id"]
        or submitted.get("voice_pack_sha256") != voice_pack["sha256"]
    ):
        raise WorkflowConflict("script_topic_input_receipt_conflict")
    script = submitted.get("script")
    if not isinstance(script, dict) or set(script) != {
        "topic_id", "title", "hook", "structure", "body",
    }:
        raise WorkflowConflict("script_topic_submission_schema_invalid")
    if script["topic_id"] != topic["topic_id"]:
        raise WorkflowConflict("script_topic_not_current")
    if not all(str(script.get(key) or "").strip() for key in (
        "title", "hook", "structure", "body",
    )):
        raise WorkflowConflict("script_topic_submission_incomplete")
    completed = [*checkpoint["completed_scripts"], script]
    receipts = [*checkpoint["completed_receipts"], {
        "topic_id": script["topic_id"],
        "packet_id": submitted["packet_id"],
        "voice_pack_sha256": submitted["voice_pack_sha256"],
        "voice_pack_content_bytes": voice_pack["content_bytes"],
        "voice_pack_exemplar_ids": [row["exemplar_id"] for row in voice_pack["exemplars"]],
        "reference_selection": packet["reference_selection"],
    }]
    updated = {
        **checkpoint,
        "completed_scripts": completed,
        "completed_receipts": receipts,
    }
    if len(completed) < len(selected_topics):
        workflow.commit_stage(run_id, "scripts", updated, "in_progress")
        next_index = first_unfinished_index(updated)
        return {
            "complete": False,
            "checkpoint": updated,
            "handoff": topic_packet(
                run_id, business_date, selected_topics[next_index], next_index,
                len(selected_topics), len(completed), voice_pack,
            ),
        }
    result = {
        "run_id": run_id,
        "business_date": business_date,
        "scripts": completed,
        "failures": [],
    }
    workflow.commit_stage(run_id, "scripts", result, "completed")
    return {"complete": True, "checkpoint": result, "scripts": result}
