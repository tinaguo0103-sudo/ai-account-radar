#!/usr/bin/env python3
"""Narrow Codex child adapters for WEB-010 editorial and spoken stages.

The daily controller owns collection, validation and checkpoints. This module
only gives one bounded Codex execution one isolated input and parses its final
structured result. It deliberately has no business write or retry path and
never starts another model/agent.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from codex_cli_path import resolve_codex_cli


ROOT = Path(__file__).resolve().parents[1]
EDITORIAL_SKILL = ROOT / "skills" / "ai-account-editorial-director" / "SKILL.md"
SCRIPTING_SKILL = ROOT / "skills" / "austin-no-overtime-scripting" / "SKILL.md"
VOICE_SKILL = ROOT / "skills" / "austin-voice-scriptwriter" / "SKILL.md"
VOICE_READING_CONTRACT = (
    ROOT / "skills" / "austin-voice-scriptwriter" / "references"
    / "austin_private_context_reading.md"
)
ALLOWLIST = ROOT / "config" / "web010_austin_private_context_allowlist.json"
DEFAULT_TIMEOUT_SECONDS = 900


class ChildExecutionError(RuntimeError):
    """A safe, typed failure from one bounded child execution."""

    def __init__(self, code: str, details: dict[str, Any] | None = None):
        self.code = code
        self.details = details or {}
        super().__init__(code)


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _schema(role: str) -> dict[str, Any]:
    if role == "editorial":
        return {
            "type": "object",
            "required": ["run_id", "result_json"],
            "properties": {
                "run_id": {"type": "string"},
                "result_json": {"type": "string"},
            },
            "additionalProperties": False,
        }
    if role == "writer":
        # The installed Codex response-schema dialect requires every declared
        # property to be required. The existing Python submit validator owns
        # the script/failure one-of contract, so this transport schema only
        # requires the packet identity and leaves the simple result fields to
        # that canonical validator.
        return {
            "type": "object",
            "required": ["packet_id", "result_json"],
            "properties": {
                "packet_id": {"type": "string"},
                "result_json": {"type": "string"},
            },
            "additionalProperties": False,
        }
    raise ChildExecutionError("child_role_invalid")


def _prompt(role: str, input_path: Path) -> str:
    rendered = str(input_path)
    if role == "editorial":
        return f"""You are the dedicated WEB-010 editorial child, not the controller.

Read exactly one input file: {rendered}
Read only the ai-account-editorial-director Skill at {EDITORIAL_SKILL} and the
source evidence named by that input. Do not read PM, Dev, QA, Task Card,
release, workflow history, scripts, or any other topic. Do not browse, call an
API, write files, publish, or start codex/another Agent. Return only JSON.

Judge every candidate independently before ranking. Keep every candidate row
exactly once and preserve the existing simple editorial result contract. A
row must include candidate_id, decision, selection_reason and
standalone_eligibility={{decision, reason}}; the nested decision must equal the
row decision. A select must also include title, hook and structure. Use only
the candidate's facts for the decision; missing evidence is not by itself a
reason to demote a worthwhile topic. Do not create a script body in this child.
Return this transport envelope and nothing else:
{{"run_id":"<input run_id>","result_json":"<JSON-encoded existing editorial result>"}}
The decoded result_json must have the existing run_id and complete topics array.
"""
    return f"""You are the dedicated WEB-010 writer child for exactly one topic.

Read exactly one input file: {rendered}
The file contains one same-run rich Topic Card only. Do not read any other
topic, prior body, editorial batch deliberation, PM/Dev/QA history, release
instructions, or controller rules. Read and apply only these approved
writer Skills/contracts:
- {SCRIPTING_SKILL}
- {VOICE_SKILL}
- {VOICE_READING_CONTRACT}
- {ALLOWLIST}
Follow that allowlist to read the actual user-owned private writer authority
transiently while drafting. Private text is calibration only, never current
topic facts, and must not be quoted or returned.

Return only this transport envelope:
{{"packet_id":"<input packet_id>","result_json":"<JSON-encoded simple submission>"}}
The decoded result_json must be an existing submission envelope with exactly
packet_id plus either script or failure. The script value has exactly
topic_id/title/hook/structure/body. If the current card cannot support a
distinctive source-grounded script, the failure value must be exactly
{{"topic_id":"<input topic_id>","reason":"material_or_angle_insufficiency","detail":"<short truthful explanation>"}}
with no other failure keys. The final writing must be
source-grounded and may choose its own form; do not force a shared outline,
experiment, first-person experience, workflow/acceptance conclusion, or
generic replacement body. Do not write files, browse, call APIs, publish, or
start codex/another Agent/process.
"""


def _injected_failure(role: str, input_document: dict[str, Any]) -> str | None:
    if role != "writer":
        return None
    wanted = os.environ.get("WEB010_INJECT_WRITER_FAILURE_TOPIC", "").strip()
    topic = input_document.get("topic")
    current = ""
    if isinstance(topic, dict):
        topic_input = topic.get("topic_input")
        if isinstance(topic_input, dict):
            current = str(topic_input.get("topic_id") or "")
        if not current:
            selected = topic.get("selected_topics")
            if isinstance(selected, list) and selected and isinstance(selected[0], dict):
                current = str(selected[0].get("topic_id") or "")
        completed_count = int(topic.get("completed_count") or 0)
    else:
        completed_count = 0
    if (
        os.environ.get("WEB010_INJECT_WRITER_FAILURE_AFTER_COMPLETED", "").strip() == "1"
        and completed_count >= 1
    ):
        return "writer_child_injected_failure_after_completed"
    return "writer_child_injected_failure" if wanted and wanted == current else None


def run_child(
    role: str,
    input_document: dict[str, Any],
    *,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Run exactly one fresh bounded child and return result plus safe metadata."""
    if role not in {"editorial", "writer"}:
        raise ChildExecutionError("child_role_invalid")
    injected = _injected_failure(role, input_document)
    input_bytes = (json.dumps(input_document, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
    input_hash = _sha256(input_bytes)
    if injected:
        raise ChildExecutionError(injected, {
            "role": role,
            "input_sha256": input_hash,
            "context_mode": "ephemeral_isolated_child",
            "invoked": False,
        })
    try:
        binary = resolve_codex_cli(os.environ.get("CODEX_BIN", ""))
    except FileNotFoundError as error:
        raise ChildExecutionError("codex_child_unavailable") from error
    try:
        timeout = int(timeout_seconds)
    except (TypeError, ValueError):
        raise ChildExecutionError("codex_child_timeout_invalid") from None
    if timeout <= 0:
        raise ChildExecutionError("codex_child_timeout_invalid")

    with tempfile.TemporaryDirectory(prefix=f"web010-{role}-child-") as temp_dir:
        sandbox = Path(temp_dir)
        codex_home = sandbox / "codex-home"
        codex_home.mkdir()
        auth_source = Path.home() / ".codex" / "auth.json"
        if not auth_source.is_file():
            raise ChildExecutionError("codex_child_auth_unavailable")
        # Keep the existing local Codex login available without copying the
        # credential; all ephemeral state still lands in this child directory.
        (codex_home / "auth.json").symlink_to(auth_source)
        input_path = sandbox / "input.json"
        schema_path = sandbox / "output.schema.json"
        output_path = sandbox / "output.json"
        input_path.write_bytes(input_bytes)
        schema_path.write_text(
            json.dumps(_schema(role), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        command = [
            binary,
            "exec",
            "-C", str(sandbox),
            "--skip-git-repo-check",
            # Codex itself needs its runtime state DB writable. The child cwd
            # is a throwaway directory; the prompt still forbids all business
            # writes and the controller only accepts the final JSON.
            "--sandbox", "workspace-write",
            "--ephemeral",
            "--ignore-user-config",
            "--output-schema", str(schema_path),
            "--output-last-message", str(output_path),
            "-",
        ]
        try:
            child_env = os.environ.copy()
            child_env["CODEX_HOME"] = str(codex_home)
            completed = subprocess.run(
                command,
                input=_prompt(role, input_path),
                text=True,
                cwd=str(sandbox),
                env=child_env,
                capture_output=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            raise ChildExecutionError("codex_child_timeout", {
                "role": role,
                "input_sha256": input_hash,
                "context_mode": "ephemeral_isolated_child",
                "timeout_seconds": timeout,
            }) from error
        if completed.returncode != 0:
            raise ChildExecutionError(f"codex_child_exit_{completed.returncode}", {
                "role": role,
                "input_sha256": input_hash,
                "context_mode": "ephemeral_isolated_child",
                "exit_code": completed.returncode,
            })
        if not output_path.is_file():
            raise ChildExecutionError("codex_child_output_missing", {
                "role": role,
                "input_sha256": input_hash,
                "context_mode": "ephemeral_isolated_child",
            })
        output_bytes = output_path.read_bytes()
        try:
            result = json.loads(output_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ChildExecutionError("codex_child_output_invalid", {
                "role": role,
                "input_sha256": input_hash,
                "context_mode": "ephemeral_isolated_child",
            }) from error
        if not isinstance(result, dict):
            raise ChildExecutionError("codex_child_output_invalid", {
                "role": role,
                "input_sha256": input_hash,
                "context_mode": "ephemeral_isolated_child",
            })
        transport_result = result.get("result_json")
        if not isinstance(transport_result, str):
            raise ChildExecutionError("codex_child_output_invalid", {
                "role": role,
                "input_sha256": input_hash,
                "context_mode": "ephemeral_isolated_child",
            })
        try:
            decoded = json.loads(transport_result)
        except json.JSONDecodeError as error:
            raise ChildExecutionError("codex_child_output_invalid", {
                "role": role,
                "input_sha256": input_hash,
                "context_mode": "ephemeral_isolated_child",
            }) from error
        if not isinstance(decoded, dict):
            raise ChildExecutionError("codex_child_output_invalid", {
                "role": role,
                "input_sha256": input_hash,
                "context_mode": "ephemeral_isolated_child",
            })
        safe = {
            "role": role,
            "input_sha256": input_hash,
            "output_sha256": _sha256(output_bytes),
            "context_mode": "ephemeral_isolated_child",
            "cwd_isolated": True,
            "codex_home_isolated": True,
            "recursive_codex": 0,
            "business_write": 0,
            "exit_code": completed.returncode,
        }
        if role == "editorial":
            safe["candidate_count"] = len(input_document.get("candidates") or [])
        else:
            topic_packet = input_document.get("topic")
            exposed_topics = (
                topic_packet.get("selected_topics")
                if isinstance(topic_packet, dict)
                else None
            )
            if not isinstance(exposed_topics, list) or not exposed_topics:
                exposed_topics = [topic_packet]
            safe.update({
                "input_scope": "one_same_run_rich_topic_card",
                "writer_packet_topic_count": len(exposed_topics),
                "other_topic_count": max(0, len(exposed_topics) - 1),
                "private_authority_transient": True,
            })
        return decoded, safe


def run_editorial_child(
    run_id: str,
    business_date: str,
    candidates: list[dict[str, Any]],
    *,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> tuple[dict[str, Any], dict[str, Any]]:
    return run_child(
        "editorial",
        {"run_id": run_id, "business_date": business_date, "candidates": candidates},
        timeout_seconds=timeout_seconds,
    )


def run_writer_child(
    run_id: str,
    business_date: str,
    packet: dict[str, Any],
    *,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> tuple[dict[str, Any], dict[str, Any]]:
    topic_input = packet.get("topic_input") if isinstance(packet, dict) else None
    packet_id = str(topic_input.get("packet_id") or "") if isinstance(topic_input, dict) else ""
    return run_child(
        "writer",
        {
            "run_id": run_id,
            "business_date": business_date,
            "packet_id": packet_id,
            "topic": packet,
        },
        timeout_seconds=timeout_seconds,
    )
