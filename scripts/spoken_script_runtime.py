#!/usr/bin/env python3
"""Public per-topic spoken-script checkpoint helpers."""
from __future__ import annotations

import hashlib
import json
import os
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
        "contract": "per_topic_reference_catalog_v3",
        "loaded": len(sources) == len(PRIVATE_STYLE_SOURCE_SPECS),
        "loaded_source_count": len(sources),
        "expected_source_count": len(PRIVATE_STYLE_SOURCE_SPECS),
        "source_roles": [row["role"] for row in rows],
        "source_hashes": {row["role"]: row["sha256"] for row in rows},
        "catalog_sha256": hashlib.sha256(encoded).hexdigest(),
        "case_selection": "outer_codex_semantic",
        "keyword_matching": False,
        "current_fact_source": False,
        "raw_content_embedded": False,
    }


def load_private_style_context(
    source_paths: dict[str, str | Path] | None = None,
) -> dict[str, Any]:
    """Return only safe catalog metadata; raw private text stays in memory."""
    return _catalog_from_sources(_read_private_sources(source_paths))


def _anchor_excerpt(
    anchor: dict[str, Any],
    topic_card: dict[str, Any] | None = None,
) -> str:
    """Build a transient excerpt from approved private fields only."""
    card_fields = (
        ("原始题目", "topic_title"),
        ("核心判断", "core_thesis"),
        ("现场痛点", "pain_point"),
        ("旧流程", "old_workflow"),
        ("AI介入", "ai_intervention"),
        ("独有判断", "unique_judgment"),
    )
    fields = (
        ("案例名称", "name"),
        ("适用场景", "usable_for"),
        ("语义线索", "keywords"),
        ("可展示证据", "shootable_evidence"),
        ("事实边界", "boundaries"),
    )
    lines: list[str] = []
    if isinstance(topic_card, dict):
        for label, key in card_fields:
            value = topic_card.get(key)
            if str(value or "").strip():
                lines.append(f"{label}：{value}")
    for label, key in fields:
        value = anchor.get(key)
        if isinstance(value, list):
            value = "、".join(str(item) for item in value if str(item).strip())
        if str(value or "").strip():
            lines.append(f"{label}：{value}")
    return "\n".join(lines)


def load_private_reference_library(
    source_paths: dict[str, str | Path] | None = None,
) -> dict[str, Any]:
    """Load approved private excerpts without deciding which topic needs them."""
    sources = _read_private_sources(source_paths)
    catalog = _catalog_from_sources(sources)
    runtime: dict[str, Any] = {}
    try:
        runtime = json.loads(sources.get("private_runtime", {}).get("text", "{}"))
    except (json.JSONDecodeError, TypeError):
        runtime = {}
    try:
        raw_cards = json.loads(sources.get("private_topic_cards", {}).get("text", "[]"))
        topic_cards = raw_cards if isinstance(raw_cards, list) else []
    except (json.JSONDecodeError, TypeError):
        topic_cards = []
    anchors = runtime.get("case_anchors", []) if isinstance(runtime, dict) else []
    if not isinstance(anchors, list) or len(anchors) != 7:
        raise WorkflowConflict("private_case_catalog_invalid")
    cases: list[dict[str, Any]] = []
    safe_case_catalog: list[dict[str, Any]] = []
    runtime_sha256 = sources.get("private_runtime", {}).get("sha256", "")
    for index, anchor in enumerate(anchors, start=1):
        if not isinstance(anchor, dict) or not str(anchor.get("name") or "").strip():
            raise WorkflowConflict("private_case_catalog_invalid")
        required_lists = ("usable_for", "shootable_evidence", "boundaries")
        if any(not isinstance(anchor.get(key), list) for key in required_lists):
            raise WorkflowConflict("private_case_catalog_invalid")
        reference_id = f"private_anchor:{index:02d}"
        # The first approved anchors have corresponding full Topic Cards; the
        # remaining anchors retain only runtime fields that actually exist.
        topic_card = topic_cards[index - 1] if index <= len(topic_cards) else None
        text = _anchor_excerpt(anchor, topic_card)
        excerpt_sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()
        source_hashes = {"private_runtime": runtime_sha256}
        source_roles = ["private_runtime"]
        if isinstance(topic_card, dict):
            source_hashes["private_topic_cards"] = sources.get(
                "private_topic_cards", {}
            ).get("sha256", "")
            source_roles.append("private_topic_cards")
        cases.append({
            "reference_id": reference_id,
            "role": "private_case",
            "source_role": "private_runtime",
            "source_sha256": runtime_sha256,
            "source_roles": source_roles,
            "source_hashes": source_hashes,
            "text": text,
            "excerpt_sha256": excerpt_sha256,
        })
        safe_case_catalog.append({
            "reference_id": reference_id,
            "name": str(anchor["name"]),
            "usable_for": [str(item) for item in anchor["usable_for"]],
            "evidence_shape": [str(item) for item in anchor["shootable_evidence"]],
            "fact_boundaries": [str(item) for item in anchor["boundaries"]],
            "semantic_cues": [str(item) for item in anchor.get("keywords", [])],
            "source_role": "private_runtime",
            "source_roles": source_roles,
            "source_hashes": source_hashes,
            "source_sha256": runtime_sha256,
            "excerpt_sha256": excerpt_sha256,
            "full_topic_card_excerpt": isinstance(topic_card, dict),
        })

    persona_text = sources.get("production_context", {}).get("text", "")
    paragraphs = [part.strip() for part in persona_text.split("\n\n") if part.strip()]
    persona_candidates = [
        part for part in paragraphs
        if any(term in part for term in ("AI业务系统导演", "真人轨", "录屏轨", "人工判断"))
    ]
    if persona_candidates:
        persona_text_value = persona_candidates[0]
        persona_excerpt_sha256 = hashlib.sha256(
            persona_text_value.encode("utf-8")
        ).hexdigest()
        cases.append({
            "reference_id": "private_persona:production_context:director",
            "role": "private_persona",
            "source_role": "production_context",
            "source_sha256": sources.get("production_context", {}).get("sha256", ""),
            "text": persona_text_value,
            "excerpt_sha256": persona_excerpt_sha256,
        })
        persona_catalog = [{
            "reference_id": "private_persona:production_context:director",
            "role": "private_persona",
            "source_role": "production_context",
            "source_sha256": sources.get("production_context", {}).get("sha256", ""),
            "excerpt_sha256": persona_excerpt_sha256,
            "selection_note": "optional persona stance excerpt; no topic fallback",
        }]
    else:
        persona_catalog = []
    return {
        "catalog": catalog,
        "references": cases,
        "private_case_catalog": safe_case_catalog,
        "persona_catalog": persona_catalog,
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
        if not exemplar_id or exemplar_id in seen or not title or not body or not style_focus:
            raise WorkflowConflict("voice_pack_exemplar_invalid")
        seen.add(exemplar_id)
        normalized.append({
            "exemplar_id": exemplar_id,
            "title": title,
            "body": body,
            "style_focus": style_focus,
            "source_role": "style_only",
        })
    # Keep the established library digest based on approved body/title/style
    # fields; reference selection is performed by the outer Codex, not keywords.
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
        "current_reference_selection": None,
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
    current_selection = checkpoint.get("current_reference_selection")
    if current_selection is not None and not isinstance(current_selection, dict):
        raise WorkflowConflict("scripts_checkpoint_reference_selection_invalid")
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


def set_reference_selection(
    workflow: Any,
    run_id: str,
    business_date: str,
    selected_topics: list[dict[str, Any]],
    checkpoint: dict[str, Any],
    voice_pack: dict[str, Any],
    selection: dict[str, Any],
    private_library: dict[str, Any] | None = None,
) -> dict[str, Any]:
    validate_checkpoint(checkpoint, run_id, business_date, selected_topics, voice_pack)
    index = first_unfinished_index(checkpoint)
    if index >= len(selected_topics):
        raise WorkflowConflict("scripts_checkpoint_already_complete")
    if checkpoint.get("current_reference_selection") is not None:
        raise WorkflowConflict("script_reference_selection_already_set")
    normalized = validate_reference_selection(
        selected_topics[index], voice_pack, selection, private_library,
    )
    updated = {
        **checkpoint,
        "current_reference_selection": normalized,
    }
    workflow.commit_stage(run_id, "scripts", updated, "in_progress")
    return updated


def _reference_ledger(
    approved: list[dict[str, Any]],
    private: list[dict[str, Any]],
    catalog: dict[str, Any],
    selection: dict[str, Any],
) -> dict[str, Any]:
    return {
        "selection_contract": "per_topic_transient_references_v3",
        "approved_full_script_ids": [row["exemplar_id"] for row in approved],
        "private_excerpt_ids": [row["reference_id"] for row in private],
        "approved_full_script_hashes": [row["body_sha256"] for row in approved],
        "private_excerpt_hashes": [row["excerpt_sha256"] for row in private],
        "approved_reasons": [row["selection_reason"] for row in approved],
        "private_reasons": [row["selection_reason"] for row in private],
        "selection_reason": selection["reason"],
        "selection_basis": [
            "central_contradiction",
            "responsible_person",
            "consequence",
            "judgment_motion",
        ],
        "private_catalog_sha256": catalog.get("catalog_sha256", ""),
        "raw_text_persisted": False,
        "current_fact_source": "topic_card_only",
    }


def approved_exemplar_catalog(voice_pack: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "exemplar_id": row["exemplar_id"],
            "title": row["title"],
            "style_focus": row["style_focus"],
            "body_sha256": hashlib.sha256(row["body"].encode("utf-8")).hexdigest(),
            "source_role": "approved_production_full_script",
        }
        for row in voice_pack["exemplars"]
    ]


def validate_reference_selection(
    topic: dict[str, Any],
    voice_pack: dict[str, Any],
    selection: dict[str, Any],
    private_library: dict[str, Any] | None = None,
) -> dict[str, Any]:
    allowed = {
        "topic_id", "approved_exemplar_id", "private_case_id", "persona_id", "reason",
    }
    if not isinstance(selection, dict) or set(selection) - allowed:
        raise WorkflowConflict("script_reference_selection_schema_invalid")
    if not str(selection.get("topic_id") or "") or not str(selection.get("reason") or "").strip():
        raise WorkflowConflict("script_reference_selection_incomplete")
    if selection["topic_id"] != topic.get("topic_id"):
        raise WorkflowConflict("script_reference_selection_not_current")
    normalized = {
        "topic_id": str(selection["topic_id"]),
        "approved_exemplar_id": str(selection.get("approved_exemplar_id") or "") or None,
        "private_case_id": str(selection.get("private_case_id") or "") or None,
        "persona_id": str(selection.get("persona_id") or "") or None,
        "reason": str(selection["reason"]).strip(),
    }
    exemplar_ids = {str(row["exemplar_id"]) for row in voice_pack["exemplars"]}
    if normalized["approved_exemplar_id"] not in (None, *exemplar_ids):
        raise WorkflowConflict("script_reference_selection_exemplar_unknown")
    library = private_library or load_private_reference_library()
    case_ids = {
        str(row["reference_id"])
        for row in library.get("references", [])
        if row.get("role") == "private_case"
    }
    persona_ids = {
        str(row["reference_id"])
        for row in library.get("references", [])
        if row.get("role") == "private_persona"
    }
    if normalized["private_case_id"] not in (None, *case_ids):
        raise WorkflowConflict("script_reference_selection_case_unknown")
    if normalized["persona_id"] not in (None, *persona_ids):
        raise WorkflowConflict("script_reference_selection_persona_unknown")
    if (
        normalized["approved_exemplar_id"] is None
        and normalized["private_case_id"] is None
        and normalized["persona_id"] is None
    ):
        normalized["reason"] = normalized["reason"]
    return normalized


def load_selected_references(
    topic: dict[str, Any],
    voice_pack: dict[str, Any],
    selection: dict[str, Any],
    private_library: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Load only IDs selected by the outer Codex; returned text is transient."""
    library = private_library or load_private_reference_library()
    normalized = validate_reference_selection(topic, voice_pack, selection, library)
    approved: list[dict[str, Any]] = []
    private: list[dict[str, Any]] = []
    if normalized["approved_exemplar_id"] is not None:
        exemplar = next(
            row for row in voice_pack["exemplars"]
            if row["exemplar_id"] == normalized["approved_exemplar_id"]
        )
        approved.append({
            "exemplar_id": exemplar["exemplar_id"],
            "title": exemplar["title"],
            "body": exemplar["body"],
            "style_focus": exemplar["style_focus"],
            "source_role": "approved_production_full_script",
            "body_sha256": hashlib.sha256(exemplar["body"].encode("utf-8")).hexdigest(),
            "selection_reason": normalized["reason"],
        })
    selected_ids = [
        value for value in (normalized["private_case_id"], normalized["persona_id"])
        if value is not None
    ]
    for reference_id in selected_ids:
        reference = next(
            row for row in library["references"]
            if row["reference_id"] == reference_id
        )
        private.append({
            "reference_id": reference["reference_id"],
            "role": reference["role"],
            "source_role": reference["source_role"],
            "source_sha256": reference["source_sha256"],
            "text": reference["text"],
            "excerpt_sha256": reference["excerpt_sha256"],
            "selection_reason": normalized["reason"],
        })
    return {
        "approved_full_scripts": approved,
        "private_excerpts": private,
        "ledger": _reference_ledger(
            approved, private, library["catalog"], normalized,
        ),
        "catalog": library["catalog"],
        "selection": normalized,
    }


def select_topic_references(
    topic: dict[str, Any],
    voice_pack: dict[str, Any],
    selection: dict[str, Any],
    private_library: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compatibility name for the explicit outer-Codex selection boundary."""
    return load_selected_references(topic, voice_pack, selection, private_library)


def reference_selector_handoff(
    run_id: str,
    business_date: str,
    topic: dict[str, Any],
    index: int,
    selected_count: int,
    completed_count: int,
    voice_pack: dict[str, Any],
    private_library: dict[str, Any] | None = None,
) -> dict[str, Any]:
    library = private_library or load_private_reference_library()
    return {
        "ok": True,
        "action": "script_reference_selection_required",
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
        "topic_selector_input": {
            "topic": topic,
            "approved_exemplar_catalog": approved_exemplar_catalog(voice_pack),
            "private_case_catalog": library.get("private_case_catalog", []),
            "persona_catalog": library.get("persona_catalog", []),
            "previous_topic_body_included": False,
        },
        "reference_selection_contract": {
            "required_keys": [
                "topic_id", "approved_exemplar_id", "private_case_id",
                "persona_id", "reason",
            ],
            "approved_full_script_default": None,
            "private_case_default": None,
            "semantic_basis": [
                "central_contradiction", "responsible_person",
                "consequence", "judgment_motion",
            ],
            "generic_keyword_reason_not_valid": True,
            "raw_text_in_selector_input": False,
        },
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
    reference_selection: dict[str, Any],
    private_source_paths: dict[str, str | Path] | None = None,
    private_library: dict[str, Any] | None = None,
) -> dict[str, Any]:
    private_style_context = load_private_style_context(private_source_paths)
    selected_library = private_library or load_private_reference_library(private_source_paths)
    references = load_selected_references(
        topic,
        voice_pack,
        reference_selection,
        private_library=selected_library,
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
    reference_selection = checkpoint.get("current_reference_selection")
    if not isinstance(reference_selection, dict):
        raise WorkflowConflict("script_reference_selection_required")
    if not isinstance(submitted, dict) or set(submitted) != {
        "packet_id", "voice_pack_sha256", "script",
    }:
        raise WorkflowConflict("script_topic_submission_schema_invalid")
    topic = selected_topics[index]
    packet = topic_packet(
        run_id, business_date, topic, index, len(selected_topics),
        len(checkpoint["completed_scripts"]), voice_pack, reference_selection,
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
        "voice_pack_exemplar_ids": packet["reference_selection"]["approved_full_script_ids"],
        "reference_selection": packet["reference_selection"],
    }]
    updated = {
        **checkpoint,
        "completed_scripts": completed,
        "completed_receipts": receipts,
        "current_reference_selection": None,
    }
    if len(completed) < len(selected_topics):
        workflow.commit_stage(run_id, "scripts", updated, "in_progress")
        next_index = first_unfinished_index(updated)
        return {
            "complete": False,
            "checkpoint": updated,
            "handoff": reference_selector_handoff(
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
