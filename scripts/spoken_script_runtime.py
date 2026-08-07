#!/usr/bin/env python3
"""Public per-topic spoken-script checkpoint helpers."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from daily_workflow import WorkflowConflict, canonical


RUNTIME_SCHEMA_VERSION = 1
DEFAULT_VOICE_PACK = (
    Path(__file__).resolve().parents[1]
    / "config"
    / "web010_austin_voice_pack.json"
)


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
    if not 3 <= len(exemplars) <= 5:
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
    pack_payload = {
        "schema_version": RUNTIME_SCHEMA_VERSION,
        "exemplars": normalized,
    }
    encoded = canonical(pack_payload).encode("utf-8")
    return {
        **pack_payload,
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


def topic_packet(
    run_id: str,
    business_date: str,
    topic: dict[str, Any],
    index: int,
    selected_count: int,
    completed_count: int,
    voice_pack: dict[str, Any],
) -> dict[str, Any]:
    packet_basis = {
        "run_id": run_id,
        "business_date": business_date,
        "topic": topic,
        "voice_pack_sha256": voice_pack["sha256"],
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
        },
        "voice_pack": voice_pack["exemplars"],
        "voice_pack_sha256": voice_pack["sha256"],
        "voice_pack_content_bytes": voice_pack["content_bytes"],
        "voice_pack_contract": {
            "style_only": True,
            "not_current_fact_source": True,
            "embedded_content": True,
            "embedded_content_bytes": voice_pack["content_bytes"],
            "exemplar_count": len(voice_pack["exemplars"]),
        },
        "required_script_input": {
            "keys": ["packet_id", "voice_pack_sha256", "script"],
            "script_keys": ["topic_id", "title", "hook", "structure", "body"],
            "current_topic_only": True,
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
