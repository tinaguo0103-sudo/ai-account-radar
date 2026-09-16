#!/usr/bin/env python3
"""Run one clean Writer context through ordered, resumable turns.

The public workflow owns the article/spoken checkpoint.  This module owns only
the content context boundary: one ephemeral App Server thread per exact run,
one current-topic input at a time, typed output, and durable turn evidence.
"""
from __future__ import annotations

import hashlib
import atexit
import json
import os
import re
import selectors
import shlex
import shutil
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from codex_cli_path import resolve_codex_cli
from daily_workflow import DailyWorkflow, WorkflowConflict, canonical
import spoken_script_runtime as script_runtime


CONTEXT_SCHEMA_VERSION = 2
WRITER_MODEL = "gpt-5.6-luna"
WRITER_REASONING_EFFORT = "max"
CONTEXT_KIND = "fresh_non_user_visible_content_only"
DEFAULT_TIMEOUT_SECONDS = 900
RECONCILE_WAIT_SECONDS = 30.0
RECONCILE_INTERRUPT_WAIT_SECONDS = 15.0
_TURN_PHASES = {"article_required", "spoken_adaptation_required"}
_FORBIDDEN_PATH_TOKENS = (
    "AGENTS.md", ".git", "daily_workflow.sqlite3", "docs/", "qa正文", "old稿",
)
_FORBIDDEN_TOPIC_KEYS = {
    "article", "body", "script", "spoken", "previous_topic_body", "old_body",
    "qa_body", "private_style_context", "editing_reference", "austin_private_context",
}
_ALLOWED_TOPIC_KEYS = {"topic_id", "trend_event_id", "source_evidence", "author_input"}
_ARTICLE_REFERENCE_FILES = tuple(script_runtime.ARTICLE_REQUIRED_REFERENCES)
_REFERENCE_FILES = tuple(script_runtime.ALL_REQUIRED_REFERENCES)
_PATH_LINK_RE = re.compile(r"(?:^|[`( ])((?:cases|voice-samples)/[A-Za-z0-9._-]+\.md)(?:[`), .]|$)")
_READ_TRACE_VERSION = 1


Runner = Callable[[list[str], str, Path, Path], Any]
_SESSIONS: dict[str, "_AppServerSession"] = {}


def _close_sessions_at_exit() -> None:
    for session in list(_SESSIONS.values()):
        session.close()
    _SESSIONS.clear()


atexit.register(_close_sessions_at_exit)


def clean_writer_contract() -> dict[str, Any]:
    """Return the direct writer contract with the clean multi-turn topology."""
    contract = script_runtime.load_writer_contract()
    contract.update({
        "topology": "one_fresh_codex_writer_context_per_exact_run_controlled_turns",
        "input_scope": [
            "same_run_current_topic_card_only",
            "active_r4_skill_and_managed_references_snapshot",
            "same_run_source_evidence_only",
            "selected_controlled_cases_and_necessary_voice_sample",
            "necessary_public_first_party_read_only_research_when_capable",
            "article_then_frozen_spoken_adaptation",
        ],
        "raw_text_persistence": "typed_output_only",
        "fresh_context": True,
        "non_user_visible": True,
        "one_context_per_exact_run": True,
        "one_app_server_process_per_exact_run": True,
        "controlled_turns": True,
        "batch_output_forbidden": True,
    })
    return contract


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (canonical(value) + "\n").encode("utf-8")
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _append_jsonl(path: Path, value: Mapping[str, Any]) -> None:
    """Append one durable, machine-readable event without buffering a batch."""
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (canonical(dict(value)) + "\n").encode("utf-8")
    with path.open("ab") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _regular_file(path: Path, error: str) -> Path:
    if path.is_symlink() or not path.is_file():
        raise WorkflowConflict(error)
    return path.resolve()


def _safe_topic(topic: Any) -> dict[str, Any]:
    if not isinstance(topic, dict) or set(topic) - _ALLOWED_TOPIC_KEYS:
        raise WorkflowConflict("clean_writer_context_topic_scope_invalid")
    topic_id = str(topic.get("topic_id") or "").strip()
    if not topic_id or "/" in topic_id or "\\" in topic_id or ".." in topic_id:
        raise WorkflowConflict("clean_writer_context_topic_identity_invalid")

    def scan(value: Any) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if str(key).lower() in _FORBIDDEN_TOPIC_KEYS:
                    raise WorkflowConflict("clean_writer_context_forbidden_topic_body")
                scan(child)
        elif isinstance(value, list):
            for child in value:
                scan(child)

    scan(topic)
    output = json.loads(json.dumps(topic, ensure_ascii=False))
    output["topic_id"] = topic_id
    return output


def _topic_ids(topics: list[dict[str, Any]]) -> list[str]:
    ids = [str(row["topic_id"]) for row in topics]
    if not ids or len(ids) != len(set(ids)):
        raise WorkflowConflict("clean_writer_context_topic_identity_invalid")
    return ids


def _authority_files(authority: Mapping[str, Any]) -> tuple[Path, dict[str, str], list[str], list[str]]:
    """Resolve mandatory refs plus every path the R4 indexes can point at."""
    skill = authority.get("active_skill")
    references = authority.get("required_managed_references")
    if not isinstance(skill, dict) or not isinstance(references, list):
        raise WorkflowConflict("clean_writer_context_skill_manifest_invalid")
    skill_path = Path(str(skill.get("path") or ""))
    files: dict[str, str] = {"SKILL.md": str(skill_path)}
    for row in references:
        if not isinstance(row, dict):
            raise WorkflowConflict("clean_writer_context_skill_manifest_invalid")
        relative = str(row.get("relative_path") or "")
        if relative not in _REFERENCE_FILES:
            raise WorkflowConflict("clean_writer_context_skill_manifest_invalid")
        files[relative] = str(row.get("path") or "")
    if set(files) != {"SKILL.md", *_REFERENCE_FILES}:
        raise WorkflowConflict("clean_writer_context_skill_manifest_incomplete")
    skill_root = skill_path.parent.resolve()
    try:
        index_text = _regular_file(skill_root / "references/case-index.md", "clean_writer_context_skill_missing").read_text(encoding="utf-8")
        voice_text = _regular_file(skill_root / "references/voice-excerpts.md", "clean_writer_context_skill_missing").read_text(encoding="utf-8")
    except OSError as error:
        raise WorkflowConflict("clean_writer_context_skill_missing") from error
    case_files = sorted(set(_PATH_LINK_RE.findall(index_text)))
    voice_files = sorted(set(_PATH_LINK_RE.findall(voice_text)))
    if not case_files:
        raise WorkflowConflict("clean_writer_context_controlled_case_index_empty")
    if not voice_files:
        raise WorkflowConflict("clean_writer_context_voice_index_empty")
    for relative in [*case_files, *voice_files]:
        if relative in files:
            continue
        source = skill_root / "references" / relative
        _regular_file(source, "clean_writer_context_controlled_reference_missing")
        files[f"references/{relative}"] = str(source)
    return skill_path, files, case_files, voice_files


def _identity_seed(run_id: str, business_date: str, topics: list[dict[str, Any]], authority: Mapping[str, Any]) -> dict[str, Any]:
    skill = authority.get("active_skill") or {}
    refs = authority.get("required_managed_references") or []
    return {
        "schema_version": CONTEXT_SCHEMA_VERSION, "run_id": run_id, "business_date": business_date,
        "selected_topic_ids": _topic_ids(topics), "topics_sha256": _sha256(canonical(topics).encode("utf-8")),
        "skill_sha256": str(skill.get("sha256") or ""),
        "reference_sha256": [str(row.get("sha256") or "") for row in refs],
        "model": WRITER_MODEL, "reasoning_effort": WRITER_REASONING_EFFORT,
        "ephemeral": True, "context_kind": CONTEXT_KIND,
    }


def _context_paths(artifact_root: Path | str, run_id: str) -> dict[str, Path]:
    context = Path(artifact_root).resolve() / run_id / "clean_writer_context"
    return {
        "context": context, "input": context / "clean_root",
        "manifest": context / "clean_root" / "manifest.json", "prompt": context / "clean_root" / "writer_prompt.md",
        "schema": context / "clean_root" / "turn_output_schema.json", "output": context / "output.json",
        "events": context / "events_summary.json", "event_ledger": context / "events.jsonl", "identity": context / "identity.json",
        "receipt": context / "receipt.json", "turns": context / "turns",
        "stderr": context / "app_server_stderr.log",
    }


def _validate_existing_manifest(path: Path, identity_hash: str, expected_files: set[str]) -> None:
    try:
        manifest = _read_json(path)
    except (OSError, json.JSONDecodeError) as error:
        raise WorkflowConflict("clean_writer_context_manifest_invalid") from error
    if not isinstance(manifest, dict) or manifest.get("identity_hash") != identity_hash or manifest.get("context_kind") != CONTEXT_KIND or set(manifest.get("static_allowed_files") or []) != expected_files:
        raise WorkflowConflict("clean_writer_context_manifest_identity_conflict")


def _prepare_context(artifact_root: Path | str, run_id: str, business_date: str, selected_topics: list[dict[str, Any]], authority: Mapping[str, Any]) -> tuple[dict[str, Path], dict[str, Any], str, dict[str, str], list[str], list[str]]:
    DailyWorkflow.validate_identity(run_id, business_date)
    topics = [_safe_topic(row) for row in selected_topics]
    _topic_ids(topics)
    _skill_root, source_files, case_files, voice_files = _authority_files(authority)
    identity_seed = _identity_seed(run_id, business_date, topics, authority)
    identity_hash = _sha256(canonical(identity_seed).encode("utf-8"))
    paths = _context_paths(artifact_root, run_id)
    context = paths["context"]
    context.mkdir(parents=True, exist_ok=True)
    expected_static = {"manifest.json", "writer_prompt.md", "turn_output_schema.json", "skill/SKILL.md", *(f"skill/{relative}" for relative in source_files if relative != "SKILL.md")}
    if paths["receipt"].exists():
        try:
            receipt = _read_json(paths["receipt"])
        except (OSError, json.JSONDecodeError) as error:
            raise WorkflowConflict("clean_writer_context_receipt_invalid") from error
        if receipt.get("identity_hash") != identity_hash:
            raise WorkflowConflict("clean_writer_context_identity_conflict")
        if receipt.get("model") != WRITER_MODEL or receipt.get("reasoning_effort") != WRITER_REASONING_EFFORT or receipt.get("ephemeral") is not True or receipt.get("context_kind") != CONTEXT_KIND:
            raise WorkflowConflict("clean_writer_context_model_conflict")
        _validate_existing_manifest(paths["manifest"], identity_hash, expected_static)
        return paths, identity_seed, identity_hash, source_files, case_files, voice_files
    if paths["manifest"].exists() or paths["output"].exists() or paths["turns"].exists():
        raise WorkflowConflict("clean_writer_context_state_conflict")
    input_root = paths["input"]
    input_root.mkdir(parents=True, exist_ok=True)
    expected_hashes = {"SKILL.md": str((authority.get("active_skill") or {}).get("sha256") or ""), **{str(row.get("relative_path") or ""): str(row.get("sha256") or "") for row in (authority.get("required_managed_references") or []) if isinstance(row, dict)}}
    for relative, source_text in source_files.items():
        source = _regular_file(Path(source_text), "clean_writer_context_skill_missing")
        if expected_hashes.get(relative) and _sha256(source.read_bytes()) != expected_hashes[relative]:
            raise WorkflowConflict("clean_writer_context_skill_manifest_conflict")
        target = input_root / ("skill/SKILL.md" if relative == "SKILL.md" else f"skill/{relative}")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        target.chmod(0o444)
    _atomic_json(paths["schema"], _output_schema())
    paths["schema"].chmod(0o444)
    manifest = {
        "schema_version": CONTEXT_SCHEMA_VERSION, "identity_hash": identity_hash, "run_id": run_id, "business_date": business_date,
        "model": WRITER_MODEL, "reasoning_effort": WRITER_REASONING_EFFORT, "ephemeral": True, "context_kind": CONTEXT_KIND,
        "selected_topic_ids": _topic_ids(topics), "static_allowed_files": sorted(expected_static),
        "dynamic_current_files": ["current_topic.json", "frozen_article.json"], "forbidden_paths": list(_FORBIDDEN_PATH_TOKENS),
        "controlled_case_files": case_files, "voice_sample_files": voice_files,
        "skill_files": [
            {"relative_path": relative, "sha256": _sha256((input_root / ("skill/SKILL.md" if relative == "SKILL.md" else f"skill/{relative}")).read_bytes()), "bytes": (input_root / ("skill/SKILL.md" if relative == "SKILL.md" else f"skill/{relative}")).stat().st_size, "optional": relative.startswith("references/cases/") or relative.startswith("references/voice-samples/")}
            for relative in source_files
        ],
    }
    _atomic_json(paths["manifest"], manifest)
    paths["manifest"].chmod(0o444)
    # The child receives a read-only sandbox; the controller keeps directory
    # write permission so it can atomically replace the current turn input and
    # so the run-scoped evidence remains recoverable/cleanable.
    paths["turns"].mkdir(parents=True, exist_ok=True)
    return paths, identity_seed, identity_hash, source_files, case_files, voice_files


def _output_schema() -> dict[str, Any]:
    failure = {"type": "object", "additionalProperties": False, "required": ["topic_id", "reason", "detail"], "properties": {"topic_id": {"type": "string"}, "reason": {"type": "string", "enum": ["material_or_angle_insufficiency"]}, "detail": {"type": "string"}}}
    article = {"type": "object", "additionalProperties": False, "required": ["topic_id", "title", "body"], "properties": {"topic_id": {"type": "string"}, "title": {"type": "string"}, "body": {"type": "string"}}}
    script = {"type": "object", "additionalProperties": False, "required": ["topic_id", "title", "hook", "structure", "body"], "properties": {"topic_id": {"type": "string"}, "title": {"type": "string"}, "hook": {"type": "string"}, "structure": {"type": "string"}, "body": {"type": "string"}}}
    return {"type": "object", "additionalProperties": False, "required": ["run_id", "business_date", "topic_id", "phase", "article", "script", "failure"], "properties": {"run_id": {"type": "string"}, "business_date": {"type": "string"}, "topic_id": {"type": "string"}, "phase": {"type": "string", "enum": sorted(_TURN_PHASES)}, "article": {"anyOf": [{"type": "null"}, article]}, "script": {"anyOf": [{"type": "null"}, script]}, "failure": {"anyOf": [{"type": "null"}, failure]}}}


def _prompt(run_id: str, business_date: str, topic: Mapping[str, Any], phase: str, *, common_references_read: bool = False) -> str:
    topic_id = str(topic["topic_id"])
    if phase == "article_required":
        if common_references_read:
            skill_instruction = "The active R4 Skill and shared managed references were already read earlier in this same context. Do not mechanically reread them."
            source_instruction = "Read current_topic.json for this topic."
            extra = "Use controlled references only when the current topic needs a specific fact or speaking-distance reference. Open a matching file under skill/references/cases/ or skill/references/voice-samples/ only when needed, and do not infer details from an index."
        else:
            skill_instruction = "Read skill/SKILL.md completely and follow the active R4 contract."
            source_instruction = "Read every required managed reference under skill/references/ completely, then read current_topic.json."
            extra = "Use controlled references only when the current topic needs a specific fact or speaking-distance reference. Open a matching file under skill/references/cases/ or skill/references/voice-samples/ only when needed, and do not infer details from an index."
        output_instruction = "Return one object with article set and script/failure null."
        stage = "article"
    else:
        skill_instruction = "The active R4 Skill and shared managed references were already read earlier in this same context." if common_references_read else "Read skill/SKILL.md completely and follow the active R4 contract."
        source_instruction = "Read frozen_article.json completely; it is the only current-topic prose input."
        output_instruction = "Return one object with script set and article/failure null."
        stage = "spoken adaptation"
        extra = "Read references/spoken-adaptation.md completely and use the frozen article as the sole source for facts and argument. Do not read current_topic.json or any prior topic body."
    return f"""You are the sole prose owner in one fresh, non-user-visible, content-only Writer context.

Exact run: {run_id}
Business date: {business_date}
Current topic: {topic_id}
Current phase: {phase}

Use only files under the current working directory. This is one controlled {stage} turn;
do not produce a batch, do not process another topic, and do not create a child agent or
another model context. {skill_instruction}
{extra}
{source_instruction}

The runtime may expose bounded public first-party read-only research. If the current material
needs factual support and that capability is available, use only public pages, never log in,
handle a challenge, or write to an external system. If it is not available, do not invent facts;
return the existing item-local material_or_angle_insufficiency failure and say research_unavailable
in its detail. Research may supplement but never replace same-run facts or controlled Austin
material. Never read AGENTS.md, Git, PM/QA/Production material, SQLite, publisher state, old
drafts, failed drafts, another run, or private/retired references.

{output_instruction}
Return exactly this JSON shape and no markdown fences or commentary:
{{
  "run_id": "{run_id}", "business_date": "{business_date}",
  "topic_id": "{topic_id}", "phase": "{phase}",
  "article": null, "script": null, "failure": null
}}
For an item-local failure, set only failure to {{"topic_id":"{topic_id}","reason":"material_or_angle_insufficiency","detail":"..."}}.
"""


def _validate_failure(value: Any, topic_id: str) -> None:
    if not isinstance(value, dict) or set(value) != {"topic_id", "reason", "detail"}:
        raise WorkflowConflict("clean_writer_context_output_failure_invalid")
    if value.get("topic_id") != topic_id or value.get("reason") != "material_or_angle_insufficiency":
        raise WorkflowConflict("clean_writer_context_output_failure_invalid")
    if not isinstance(value.get("detail"), str) or not value["detail"].strip():
        raise WorkflowConflict("clean_writer_context_output_failure_invalid")


def _required_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WorkflowConflict(f"clean_writer_context_output_{field}_invalid")
    return value


def _validate_turn_output(value: Any, run_id: str, business_date: str, topic: Mapping[str, Any], phase: str) -> dict[str, Any]:
    topic_id = str(topic["topic_id"])
    if not isinstance(value, dict) or set(value) != {"run_id", "business_date", "topic_id", "phase", "article", "script", "failure"}:
        raise WorkflowConflict("clean_writer_context_output_schema_invalid")
    if value.get("run_id") != run_id or value.get("business_date") != business_date or value.get("topic_id") != topic_id or value.get("phase") != phase:
        raise WorkflowConflict("clean_writer_context_output_identity_conflict")
    article, script, failure = value.get("article"), value.get("script"), value.get("failure")
    if phase == "article_required":
        if (article is None) == (failure is None) or script is not None:
            raise WorkflowConflict("clean_writer_context_output_phase_invalid")
        if article is not None:
            if not isinstance(article, dict) or set(article) != {"topic_id", "title", "body"} or article.get("topic_id") != topic_id:
                raise WorkflowConflict("clean_writer_context_output_article_invalid")
            _required_text(article.get("title"), "article_title")
            _required_text(article.get("body"), "article_body")
    else:
        if article is not None or (script is None) == (failure is None):
            raise WorkflowConflict("clean_writer_context_output_phase_invalid")
        if script is not None:
            if not isinstance(script, dict) or set(script) != {"topic_id", "title", "hook", "structure", "body"} or script.get("topic_id") != topic_id:
                raise WorkflowConflict("clean_writer_context_output_script_invalid")
            for field in ("title", "hook", "structure", "body"):
                _required_text(script.get(field), f"script_{field}")
    if failure is not None:
        _validate_failure(failure, topic_id)
    return value


def _command(codex_bin: str, paths: Mapping[str, Path], thread_id: str = "") -> list[str]:
    if not thread_id:
        return [codex_bin, "exec", "--ephemeral", "--ignore-user-config", "--ignore-rules", "--skip-git-repo-check", "--cd", str(paths["input"]), "--sandbox", "read-only", "--model", WRITER_MODEL, "-c", 'model_reasoning_effort="max"', "--output-schema", str(paths["schema"]), "--output-last-message", str(paths["output"]), "--json", "-"]
    return [codex_bin, "exec", "resume", thread_id, "--ephemeral", "--ignore-user-config", "--ignore-rules", "--skip-git-repo-check", "--model", WRITER_MODEL, "-c", 'model_reasoning_effort="max"', "--output-schema", str(paths["schema"]), "--output-last-message", str(paths["output"]), "--json", "-"]


def _command_scope(command: str, input_root: Path) -> None:
    if not command:
        return
    if any(token in command for token in _FORBIDDEN_PATH_TOKENS):
        raise WorkflowConflict("clean_writer_context_forbidden_path_read")
    try:
        tokens = shlex.split(command)
    except ValueError:
        raise WorkflowConflict("clean_writer_context_command_scope_invalid") from None
    root_text = str(input_root.resolve())
    for token in tokens:
        if token in {"~", "$HOME", "${HOME}"} or token.startswith(("~/", "$HOME/", "${HOME}/")):
            raise WorkflowConflict("clean_writer_context_home_path_read")
        if token == ".." or "/../" in token or token.startswith("../"):
            raise WorkflowConflict("clean_writer_context_parent_path_read")
        if not token.startswith("/"):
            continue
        if token == "/bin/zsh" or token.startswith("/bin/") or token.startswith("/usr/bin/"):
            continue
        if token.startswith(root_text + "/") or token == root_text:
            continue
        raise WorkflowConflict("clean_writer_context_absolute_path_read")
    # A path can be embedded inside a quoted interpreter expression rather
    # than appear as a standalone shell token.  Inspect common absolute-root
    # forms as well so `python -c 'open("/Users/...")'` cannot evade the
    # clean-root boundary.
    embedded = re.findall(r"(?<![A-Za-z0-9_])/(?:Users|private|tmp|Volumes|Applications|etc|var|opt|home)/[^\s'\"`),;]+", command)
    for value in embedded:
        value = value.rstrip(".,;)]}")
        if value == root_text or value.startswith(root_text + "/"):
            continue
        raise WorkflowConflict("clean_writer_context_absolute_path_read")


def _event_summaries(stdout: str, input_root: Path, expected_thread: str = "") -> tuple[list[dict[str, Any]], str]:
    summaries: list[dict[str, Any]] = []
    thread_id = expected_thread
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        event_type = str(event.get("type") or event.get("method") or "")
        if event_type in {"thread.started", "thread/started"}:
            params = event.get("params") if isinstance(event.get("params"), dict) else {}
            thread = params.get("thread") if isinstance(params.get("thread"), dict) else {}
            value = str(event.get("thread_id") or thread.get("id") or "")
            if not value:
                raise WorkflowConflict("clean_writer_context_thread_identity_missing")
            if thread_id and value != thread_id:
                raise WorkflowConflict("clean_writer_context_thread_identity_conflict")
            thread_id = value
        item = event.get("item")
        if not isinstance(item, dict):
            params = event.get("params") if isinstance(event.get("params"), dict) else {}
            item = params.get("item") if isinstance(params.get("item"), dict) else None
        if isinstance(item, dict):
            command = str(item.get("command") or "")
            _command_scope(command, input_root)
            summary = {"type": event_type, "item_type": item.get("type")}
            if command:
                summary["command"] = command[:500]
            summaries.append(summary)
        elif event_type in {"thread.started", "thread/started", "turn.started", "turn/started", "turn.completed", "turn/completed"}:
            summaries.append({"type": event_type})
    return summaries, thread_id


def _default_read_trace() -> dict[str, Any]:
    return {
        "schema_version": _READ_TRACE_VERSION,
        "common_references_read": False,
        "article_current_topic_reads": 0,
        "spoken_frozen_article_reads": 0,
        "spoken_reference_reads": 0,
    }


def _read_trace_state(identity: Mapping[str, Any], receipt: Mapping[str, Any]) -> dict[str, Any]:
    """Read the cumulative authority-read evidence for this one context."""
    value = identity.get("read_trace")
    if value is None:
        value = receipt.get("read_trace")
    if value is None:
        return _default_read_trace()
    if not isinstance(value, dict):
        raise WorkflowConflict("clean_writer_context_read_trace_invalid")
    state = _default_read_trace()
    state.update(value)
    if state.get("schema_version") != _READ_TRACE_VERSION or not isinstance(state.get("common_references_read"), bool):
        raise WorkflowConflict("clean_writer_context_read_trace_invalid")
    for key in ("article_current_topic_reads", "spoken_frozen_article_reads", "spoken_reference_reads"):
        try:
            state[key] = int(state.get(key, 0))
        except (TypeError, ValueError):
            raise WorkflowConflict("clean_writer_context_read_trace_invalid") from None
        if state[key] < 0:
            raise WorkflowConflict("clean_writer_context_read_trace_invalid")
    return state


def _validate_read_trace(events: list[dict[str, Any]], phase: str, state: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Require per-turn reads while accumulating shared R4 authority evidence."""
    commands = [str(item.get("command") or "") for item in events if item.get("command")]
    if not commands:
        raise WorkflowConflict("clean_writer_context_skill_read_trace_missing")
    next_state = _default_read_trace()
    if state is not None:
        next_state.update(state)
    if phase == "article_required":
        required = ["current_topic.json"]
        if not next_state.get("common_references_read"):
            required = ["skill/SKILL.md", *[f"skill/{relative}" for relative in _ARTICLE_REFERENCE_FILES], *required]
    else:
        required = ["skill/references/spoken-adaptation.md", "frozen_article.json"]
    missing = [relative for relative in required if not any(relative in command for command in commands)]
    if missing:
        raise WorkflowConflict("clean_writer_context_skill_read_trace_incomplete")
    if phase == "article_required":
        next_state["common_references_read"] = True
        next_state["article_current_topic_reads"] = int(next_state.get("article_current_topic_reads", 0)) + 1
    else:
        next_state["spoken_frozen_article_reads"] = int(next_state.get("spoken_frozen_article_reads", 0)) + 1
        next_state["spoken_reference_reads"] = int(next_state.get("spoken_reference_reads", 0)) + 1
    next_state["schema_version"] = _READ_TRACE_VERSION
    return next_state


class _AppServerSession:
    """One headless App Server process and one ephemeral Thread.

    The controller deliberately keeps the protocol state in this one process.
    Ephemeral App Server threads expose metadata-only ``thread/read`` and do
    not provide a durable turn-history API, so a missing turn identity is an
    unreconciled state rather than permission to create another context.
    """

    def __init__(self, binary: str, input_root: Path, stderr_path: Path):
        self.binary, self.input_root, self.stderr_path = binary, input_root, stderr_path
        self.process: subprocess.Popen[str] | None = None
        self.thread_id = ""
        self.events: list[dict[str, Any]] = []
        self.last_events: list[dict[str, Any]] = []
        self.last_turn_id = ""
        self.last_turn_status = ""
        self.last_turn_state = "unknown"
        self.last_turn_output = ""
        self.last_turn_error = ""
        self.turn_statuses: dict[str, str] = {}
        self._on_event: Callable[[dict[str, Any]], None] | None = None
        self._on_turn_accepted: Callable[[str, str], None] | None = None
        self._on_output_snapshot: Callable[[str], None] | None = None
        self._counter = 0

    def configure_persistence(
        self,
        *,
        on_event: Callable[[dict[str, Any]], None] | None = None,
        on_turn_accepted: Callable[[str, str], None] | None = None,
        on_output_snapshot: Callable[[str], None] | None = None,
    ) -> None:
        """Attach run-scoped durable callbacks for the next controlled turn."""
        self._on_event = on_event
        self._on_turn_accepted = on_turn_accepted
        self._on_output_snapshot = on_output_snapshot

    def _send(self, value: dict[str, Any]) -> None:
        if self.process is None or self.process.stdin is None:
            raise WorkflowConflict("clean_writer_context_session_closed")
        self.process.stdin.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n")
        self.process.stdin.flush()

    def _read(
        self,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        *,
        timeout_code: str = "clean_writer_context_turn_timeout",
    ) -> dict[str, Any]:
        if self.process is None or self.process.stdout is None:
            raise WorkflowConflict("clean_writer_context_session_closed")
        selector = selectors.DefaultSelector()
        selector.register(self.process.stdout, selectors.EVENT_READ)
        ready = selector.select(timeout)
        selector.close()
        if not ready:
            raise WorkflowConflict(timeout_code)
        line = self.process.stdout.readline()
        if not line:
            raise WorkflowConflict("clean_writer_context_session_ended")
        try:
            value = json.loads(line)
        except json.JSONDecodeError as error:
            raise WorkflowConflict("clean_writer_context_protocol_invalid") from error
        if not isinstance(value, dict):
            raise WorkflowConflict("clean_writer_context_protocol_invalid")
        return value

    def _consume_notification(self, message: dict[str, Any], text_parts: list[str] | None = None) -> None:
        method = message.get("method")
        params = message.get("params") if isinstance(message.get("params"), dict) else {}
        if method == "thread/started":
            thread = params.get("thread") if isinstance(params.get("thread"), dict) else {}
            value = str(thread.get("id") or "")
            if value and self.thread_id and value != self.thread_id:
                raise WorkflowConflict("clean_writer_context_thread_identity_conflict")
            if value:
                self.thread_id = value
        item = params.get("item") if isinstance(params.get("item"), dict) else {}
        turn = params.get("turn") if isinstance(params.get("turn"), dict) else {}
        turn_id = str(turn.get("id") or params.get("turnId") or "")
        if turn_id and isinstance(turn.get("status"), str):
            self.turn_statuses[turn_id] = str(turn["status"])
        if method == "item/agentMessage/delta" and text_parts is not None and isinstance(params.get("delta"), str):
            text_parts.append(params["delta"])
            if self._on_output_snapshot is not None:
                self._on_output_snapshot("".join(text_parts))
        if method in {"item/completed", "item/updated"} and text_parts is not None and item.get("type") in {"agent_message", "agentMessage"}:
            text = self._item_text(item)
            if isinstance(text, str) and text:
                # Protocol versions may emit deltas followed by the complete
                # item.  Keep the complete item once; never duplicate JSON.
                text_parts.clear()
                text_parts.append(text)
                if self._on_output_snapshot is not None:
                    self._on_output_snapshot(text)
        if method == "turn/completed" and text_parts is not None:
            items = turn.get("items") if isinstance(turn.get("items"), list) else []
            for completed_item in items:
                if not isinstance(completed_item, dict) or completed_item.get("type") not in {"agent_message", "agentMessage"}:
                    continue
                text = self._item_text(completed_item)
                if isinstance(text, str) and text:
                    text_parts.clear()
                    text_parts.append(text)
                    if self._on_output_snapshot is not None:
                        self._on_output_snapshot(text)
        if method and isinstance(method, str):
            summary: dict[str, Any] = {"type": method}
            if isinstance(params.get("threadId"), str):
                summary["thread_id"] = params["threadId"]
            elif method == "thread/started" and self.thread_id:
                summary["thread_id"] = self.thread_id
            if turn_id:
                summary["turn_id"] = turn_id
            if isinstance(turn.get("status"), str):
                summary["status"] = turn["status"]
            thread_status = params.get("status") if isinstance(params.get("status"), dict) else {}
            if isinstance(thread_status.get("type"), str):
                summary["thread_status"] = thread_status["type"]
            if isinstance(item.get("type"), str):
                summary["item_type"] = item["type"]
            if isinstance(item.get("command"), str):
                _command_scope(item["command"], self.input_root)
                summary["command"] = item["command"][:500]
            self.events.append(summary)
            if self._on_event is not None:
                self._on_event(dict(summary))
        if method and "id" in message:
            self._send({"id": message.get("id"), "error": {"code": -32000, "message": "clean writer context is non-interactive"}})

    @staticmethod
    def _item_text(item: Mapping[str, Any]) -> str:
        """Extract the App Server's output_text content from an agent item."""
        direct = item.get("text")
        if isinstance(direct, str):
            return direct
        content = item.get("content")
        if not isinstance(content, list):
            return ""
        parts = []
        for entry in content:
            if not isinstance(entry, dict) or not isinstance(entry.get("text"), str):
                continue
            if entry.get("type") in {"output_text", "text", "input_text"}:
                parts.append(entry["text"])
        return "".join(parts)

    def _rpc(
        self,
        method: str,
        params: dict[str, Any],
        *,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        text_parts: list[str] | None = None,
    ) -> dict[str, Any]:
        self._counter += 1
        request_id = f"clean-{self._counter}"
        self._send({"id": request_id, "method": method, "params": params})
        while True:
            message = self._read(timeout, timeout_code="clean_writer_context_protocol_timeout")
            if message.get("id") == request_id:
                if "error" in message:
                    raise WorkflowConflict(f"clean_writer_context_{method.replace('/', '_')}_failed")
                result = message.get("result")
                if not isinstance(result, dict):
                    raise WorkflowConflict("clean_writer_context_protocol_invalid")
                return result
            self._consume_notification(message, text_parts)

    def start(self) -> None:
        try:
            self.process = subprocess.Popen([self.binary, "app-server", "--stdio"], cwd=str(self.input_root), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
            # runtimeWorkspaceRoots is the supported App Server capability that
            # pins this thread to the run-scoped clean root.  The protocol
            # requires the client to opt in explicitly during initialize.
            initialized = self._rpc("initialize", {"clientInfo": {"name": "clean-writer-context", "title": "content-only writer", "version": "2"}, "capabilities": {"experimentalApi": True}})
            if not isinstance(initialized.get("userAgent"), str):
                raise WorkflowConflict("clean_writer_context_initialize_invalid")
            self._send({"method": "initialized", "params": {}})
            result = self._rpc("thread/start", {"model": WRITER_MODEL, "cwd": str(self.input_root), "sandbox": "read-only", "approvalPolicy": "never", "ephemeral": True, "threadSource": "startup", "multiAgentMode": "explicitRequestOnly", "baseInstructions": "You are a single content-only writer. Follow each controlled user turn and never create child agents or access paths outside cwd.", "developerInstructions": "Only the current controlled turn and files under cwd are in scope. Never access parent or absolute paths, private/old/QA/Production material, or another model context.", "runtimeWorkspaceRoots": [str(self.input_root)]})
            thread = result.get("thread") if isinstance(result.get("thread"), dict) else {}
            self.thread_id = str(thread.get("id") or result.get("threadId") or "")
            if not self.thread_id:
                raise WorkflowConflict("clean_writer_context_thread_identity_missing")
            if result.get("model") not in {None, WRITER_MODEL}:
                raise WorkflowConflict("clean_writer_context_model_conflict")
            if result.get("approvalPolicy") not in {None, "never"}:
                raise WorkflowConflict("clean_writer_context_policy_conflict")
            sandbox = result.get("sandbox") if isinstance(result.get("sandbox"), dict) else {}
            if sandbox and sandbox.get("type") not in {"readOnly", "read-only"}:
                raise WorkflowConflict("clean_writer_context_sandbox_conflict")
        except Exception:
            self.close()
            raise

    @staticmethod
    def _terminal_resolution(status: str) -> str:
        if status == "completed":
            return "completed"
        if status == "failed":
            return "failed"
        if status == "interrupted":
            return "interrupted"
        return ""

    def _resolution(self, state: str, turn_id: str, text_parts: list[str], *, detail: str = "") -> dict[str, Any]:
        output = "".join(text_parts)
        self.last_turn_id = turn_id
        self.last_turn_status = self.turn_statuses.get(turn_id, "")
        self.last_turn_state = state
        self.last_turn_output = output
        if detail:
            self.last_turn_error = detail
        return {"state": state, "thread_id": self.thread_id, "turn_id": turn_id, "status": self.last_turn_status, "output": output, "detail": detail}

    def reconcile_turn(self, turn_id: str, text_parts: list[str] | None = None) -> dict[str, Any]:
        """Read the exact Thread status using the installed supported API.

        Ephemeral threads only support metadata-only ``thread/read``.  The
        method therefore returns ``unknown`` when no terminal event carrying
        this exact turn has been observed; it never invents a turn-history
        method or starts a replacement turn.
        """
        parts = text_parts if text_parts is not None else []
        known = self._terminal_resolution(self.turn_statuses.get(turn_id, ""))
        if known:
            return self._resolution(known, turn_id, parts)
        if not turn_id or not self.thread_id or self.process is None:
            return self._resolution("unknown", turn_id, parts, detail="no_live_same_turn_protocol")
        try:
            result = self._rpc(
                "thread/read",
                {"threadId": self.thread_id, "includeTurns": False},
                timeout=min(DEFAULT_TIMEOUT_SECONDS, RECONCILE_INTERRUPT_WAIT_SECONDS),
                text_parts=parts,
            )
        except WorkflowConflict as error:
            known = self._terminal_resolution(self.turn_statuses.get(turn_id, ""))
            if known:
                return self._resolution(known, turn_id, parts)
            return self._resolution("unknown", turn_id, parts, detail=str(error))
        known = self._terminal_resolution(self.turn_statuses.get(turn_id, ""))
        if known:
            return self._resolution(known, turn_id, parts)
        thread = result.get("thread") if isinstance(result.get("thread"), dict) else {}
        status = thread.get("status") if isinstance(thread.get("status"), dict) else {}
        if status.get("type") == "active":
            return self._resolution("running", turn_id, parts, detail="thread/read:active")
        # ``idle`` and ``systemError`` do not identify the requested turn on
        # an ephemeral thread.  Without a terminal notification the state is
        # deliberately unreconciled.
        return self._resolution("unknown", turn_id, parts, detail=f"thread/read:{status.get('type', 'missing')}")

    def continue_turn(self, turn_id: str, text_parts: list[str] | None = None) -> dict[str, Any]:
        """Wait once for the same turn, then interrupt that exact turn.

        This is the only bounded continuation path.  It never sends another
        ``turn/start`` request and never opens another App Server process.
        """
        parts = text_parts if text_parts is not None else []
        deadline = time.monotonic() + max(0.0, RECONCILE_WAIT_SECONDS)
        while time.monotonic() < deadline:
            try:
                message = self._read(
                    max(0.0, deadline - time.monotonic()),
                    timeout_code="clean_writer_context_reconcile_timeout",
                )
            except WorkflowConflict:
                break
            if message.get("method"):
                self._consume_notification(message, parts)
            known = self._terminal_resolution(self.turn_statuses.get(turn_id, ""))
            if known:
                return self._resolution(known, turn_id, parts)
        # The protocol exposes an exact-turn interrupt.  If it is accepted,
        # wait briefly for the terminal event; otherwise retain unknown.
        try:
            self._rpc(
                "turn/interrupt",
                {"threadId": self.thread_id, "turnId": turn_id},
                timeout=RECONCILE_INTERRUPT_WAIT_SECONDS,
                text_parts=parts,
            )
        except WorkflowConflict as error:
            known = self._terminal_resolution(self.turn_statuses.get(turn_id, ""))
            if known:
                return self._resolution(known, turn_id, parts)
            return self._resolution("unknown", turn_id, parts, detail=str(error))
        known = self._terminal_resolution(self.turn_statuses.get(turn_id, ""))
        if known:
            return self._resolution(known, turn_id, parts)
        deadline = time.monotonic() + max(0.0, RECONCILE_INTERRUPT_WAIT_SECONDS)
        while time.monotonic() < deadline:
            try:
                message = self._read(
                    max(0.0, deadline - time.monotonic()),
                    timeout_code="clean_writer_context_reconcile_timeout",
                )
            except WorkflowConflict:
                break
            if message.get("method"):
                self._consume_notification(message, parts)
            known = self._terminal_resolution(self.turn_statuses.get(turn_id, ""))
            if known:
                return self._resolution(known, turn_id, parts)
        return self._resolution("unknown", turn_id, parts, detail="turn/interrupt:terminal_event_missing")

    def turn(self, prompt: str, schema: dict[str, Any]) -> str:
        if not self.thread_id:
            raise WorkflowConflict("clean_writer_context_thread_identity_missing")
        self._counter += 1
        request_id, turn_id, accepted = f"clean-turn-{self._counter}", "", False
        event_start = len(self.events)
        text_parts: list[str] = []
        completed, status = False, ""
        self.last_turn_id = ""
        self.last_turn_status = ""
        self.last_turn_state = "unknown"
        self.last_turn_output = ""
        self.last_turn_error = ""
        self._send({"id": request_id, "method": "turn/start", "params": {"threadId": self.thread_id, "input": [{"type": "text", "text": prompt}], "model": WRITER_MODEL, "effort": WRITER_REASONING_EFFORT, "approvalPolicy": "never", "multiAgentMode": "explicitRequestOnly", "cwd": str(self.input_root), "sandboxPolicy": {"type": "readOnly", "networkAccess": True}, "outputSchema": schema, "turnTrigger": "clean_writer_context_controlled_turn"}})

        def accept(value: str) -> None:
            nonlocal turn_id, accepted
            if value and turn_id and value != turn_id:
                raise WorkflowConflict("clean_writer_context_turn_identity_conflict")
            if value:
                turn_id = value
            if not turn_id:
                raise WorkflowConflict("clean_writer_context_turn_identity_missing")
            if not accepted:
                accepted = True
                self.turn_statuses.setdefault(turn_id, "inProgress")
                if self._on_turn_accepted is not None:
                    self._on_turn_accepted(self.thread_id, turn_id)

        try:
            # Keep reading until both the request response and terminal
            # notification arrive; protocol implementations are allowed to
            # emit ``turn/completed`` before the JSON-RPC response is flushed.
            while not (completed and accepted):
                message = self._read()
                if message.get("id") == request_id:
                    if "error" in message:
                        raise WorkflowConflict("clean_writer_context_turn_start_failed")
                    turn = message.get("result", {}).get("turn") if isinstance(message.get("result"), dict) else {}
                    if not isinstance(turn, dict):
                        raise WorkflowConflict("clean_writer_context_turn_identity_missing")
                    if turn.get("threadId") and turn.get("threadId") != self.thread_id:
                        raise WorkflowConflict("clean_writer_context_thread_identity_conflict")
                    accept(str(turn.get("id") or ""))
                    continue
                method = message.get("method")
                params = message.get("params") if isinstance(message.get("params"), dict) else {}
                if method == "turn/started":
                    started = params.get("turn") if isinstance(params.get("turn"), dict) else {}
                    if started.get("id"):
                        accept(str(started.get("id")))
                    if turn_id and started.get("id") and started.get("id") != turn_id:
                        raise WorkflowConflict("clean_writer_context_turn_identity_conflict")
                if method == "turn/completed":
                    done = params.get("turn") if isinstance(params.get("turn"), dict) else {}
                    if done.get("id"):
                        accept(str(done.get("id")))
                    if done.get("id") and turn_id and done.get("id") != turn_id:
                        raise WorkflowConflict("clean_writer_context_turn_identity_conflict")
                    status, completed = str(done.get("status") or params.get("status") or ""), True
                self._consume_notification(message, text_parts)
            self.last_events = self.events[event_start:]
            self.last_turn_id = turn_id
            self.last_turn_status = status or self.turn_statuses.get(turn_id, "")
            self.last_turn_output = "".join(text_parts)
            self.last_turn_state = self._terminal_resolution(self.last_turn_status) or "unknown"
            if self.last_turn_state != "completed":
                raise WorkflowConflict("clean_writer_context_turn_failed")
            if not text_parts:
                raise WorkflowConflict("clean_writer_context_output_missing")
            return self.last_turn_output
        except WorkflowConflict as error:
            self.last_events = self.events[event_start:]
            self.last_turn_id = turn_id
            self.last_turn_status = status or self.turn_statuses.get(turn_id, "")
            self.last_turn_output = "".join(text_parts)
            self.last_turn_error = str(error)
            self.last_turn_state = self._terminal_resolution(self.last_turn_status) or "unknown"
            if str(error) in {"clean_writer_context_turn_timeout", "clean_writer_context_session_ended", "clean_writer_context_protocol_timeout"}:
                resolution = self.reconcile_turn(turn_id, text_parts)
                if resolution["state"] == "running":
                    resolution = self.continue_turn(turn_id, text_parts)
                self.last_turn_state = str(resolution.get("state") or "unknown")
                self.last_turn_status = str(resolution.get("status") or self.last_turn_status)
                self.last_turn_output = str(resolution.get("output") or self.last_turn_output)
                self.last_turn_error = str(resolution.get("detail") or error)
                self.last_events = self.events[event_start:]
                if resolution["state"] == "completed":
                    if self.last_turn_output:
                        return self.last_turn_output
                    raise WorkflowConflict("clean_writer_context_output_missing") from None
                if resolution["state"] == "failed":
                    raise WorkflowConflict("clean_writer_context_turn_failed") from None
                if resolution["state"] == "interrupted":
                    raise WorkflowConflict("clean_writer_context_turn_interrupted") from None
                raise WorkflowConflict("clean_writer_context_turn_status_unknown") from None
            raise

    def close(self) -> None:
        process = self.process
        self.process = None
        if process is None:
            return
        try:
            if process.stdin:
                process.stdin.close()
        except OSError:
            pass
        try:
            process.terminate()
            process.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            try:
                process.kill()
                process.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                pass
        try:
            stderr = process.stderr.read() if process.stderr else ""
        except OSError:
            stderr = ""
        if stderr:
            _atomic_text(self.stderr_path, stderr)


def _write_turn_input(paths: Mapping[str, Path], run_id: str, business_date: str, topic: Mapping[str, Any], phase: str, frozen_article: Mapping[str, Any] | None, *, common_references_read: bool = False) -> tuple[str, str]:
    input_root, current, frozen = paths["input"], paths["input"] / "current_topic.json", paths["input"] / "frozen_article.json"
    if phase == "article_required":
        frozen.unlink(missing_ok=True)
        payload = {"run_id": run_id, "business_date": business_date, "phase": phase, "topic": _safe_topic(topic)}
        _atomic_json(current, payload)
        current.chmod(0o444)
        active = current
    else:
        current.unlink(missing_ok=True)
        if not isinstance(frozen_article, dict):
            raise WorkflowConflict("clean_writer_context_frozen_article_missing")
        article = script_runtime.read_article_artifact(frozen_article, run_id=run_id, business_date=business_date, topic_id=str(topic["topic_id"]))
        _atomic_json(frozen, {"run_id": run_id, "business_date": business_date, "phase": phase, "article": article})
        frozen.chmod(0o444)
        active = frozen
    prompt = _prompt(run_id, business_date, topic, phase, common_references_read=common_references_read)
    _atomic_text(paths["prompt"], prompt)
    paths["prompt"].chmod(0o444)
    return _sha256(prompt.encode("utf-8")), _sha256(active.read_bytes())


def _turn_records(paths: Mapping[str, Path]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in sorted(paths["turns"].glob("turn-*.json")):
        try:
            value = _read_json(path)
        except (OSError, json.JSONDecodeError):
            raise WorkflowConflict("clean_writer_context_turn_record_invalid") from None
        if isinstance(value, dict):
            records.append(value)
    return records


def _turn_partial_path(paths: Mapping[str, Path], sequence: int) -> Path:
    return paths["turns"] / f"turn-{sequence:04d}.output.partial"


def _read_event_ledger(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise WorkflowConflict("clean_writer_context_event_ledger_invalid") from error
    events: list[dict[str, Any]] = []
    for line in lines:
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as error:
            raise WorkflowConflict("clean_writer_context_event_ledger_invalid") from error
        if not isinstance(value, dict) or not isinstance(value.get("type"), str):
            raise WorkflowConflict("clean_writer_context_event_ledger_invalid")
        events.append(value)
    return events


def _find_record(records: list[dict[str, Any]], topic_id: str, phase: str) -> dict[str, Any] | None:
    for record in records:
        if record.get("topic_id") == topic_id and record.get("phase") == phase:
            return record
    return None


def _receipt_write(paths: Mapping[str, Path], identity_hash: str, value: dict[str, Any], *, exclusive: bool = False) -> None:
    if exclusive:
        paths["receipt"].parent.mkdir(parents=True, exist_ok=True)
        encoded = (canonical(value) + "\n").encode("utf-8")
        try:
            descriptor = os.open(paths["receipt"], os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        except FileExistsError:
            raise WorkflowConflict("clean_writer_context_already_attempted") from None
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
        except Exception:
            paths["receipt"].unlink(missing_ok=True)
            raise
        return
    _atomic_json(paths["receipt"], {**value, "identity_hash": identity_hash})


def _write_event_summary_from_ledger(paths: Mapping[str, Path]) -> list[dict[str, Any]]:
    events = _read_event_ledger(paths["event_ledger"])
    _atomic_json(paths["events"], events)
    return events


def _result(output: dict[str, Any], identity: dict[str, Any], record: dict[str, Any], *, cached: bool, events: list[dict[str, Any]]) -> dict[str, Any]:
    return {"output": output, "identity": identity, "turn": record, "cached": cached, "events": events}


def _promote_completed_turn(
    *,
    paths: Mapping[str, Path],
    identity: dict[str, Any],
    receipt: dict[str, Any],
    record: dict[str, Any],
    turn_path: Path,
    partial_path: Path,
    run_id: str,
    business_date: str,
    topic: Mapping[str, Any],
    phase: str,
    identity_hash: str,
    cached: bool,
) -> dict[str, Any] | None:
    """Promote a terminal same-turn output captured before a process exit."""
    if record.get("protocol_state") != "completed" or record.get("read_trace_validated") is not True or not partial_path.is_file():
        return None
    try:
        raw = partial_path.read_text(encoding="utf-8")
        output = _validate_turn_output(json.loads(raw), run_id, business_date, topic, phase)
    except (OSError, json.JSONDecodeError, WorkflowConflict):
        return None
    events = [event for event in _read_event_ledger(paths["event_ledger"]) if event.get("sequence") == record.get("sequence")]
    record.update({
        "status": "completed",
        "output": output,
        "output_sha256": _sha256(canonical(output).encode("utf-8")),
        "events": events,
        "recovered": True,
        "recovered_at": _utc_now(),
    })
    _atomic_json(turn_path, record)
    partial_path.unlink(missing_ok=True)
    identity.update({"thread_id": record.get("thread_id") or identity.get("thread_id"), "active_turn_id": "", "active_turn_state": "completed"})
    _atomic_json(paths["identity"], identity)
    all_records = _turn_records(paths)
    receipt.update({
        "status": "active",
        "thread_id": identity.get("thread_id") or receipt.get("thread_id", ""),
        "active_turn_id": "",
        "active_turn_state": "completed",
        "completed_turn_count": sum(item.get("status") == "completed" for item in all_records),
        "last_completed_sequence": record.get("sequence"),
    })
    _receipt_write(paths, identity_hash, receipt)
    _write_event_summary_from_ledger(paths)
    return _result(output, identity, record, cached=cached, events=events)


def _invoke_fake_or_session(
    *,
    paths: Mapping[str, Path],
    prompt: str,
    schema: dict[str, Any],
    thread_id: str,
    binary: str,
    runner: Runner | None,
    on_event: Callable[[dict[str, Any]], None] | None = None,
    on_turn_accepted: Callable[[str, str], None] | None = None,
    on_output_snapshot: Callable[[str], None] | None = None,
) -> tuple[str, str, list[dict[str, Any]]]:
    if runner is not None:
        command = _command(binary, paths, thread_id)
        result = runner(command, prompt, paths["input"], paths["output"])
        if hasattr(result, "returncode"):
            returncode, stdout, stderr = int(result.returncode), str(getattr(result, "stdout", "")), str(getattr(result, "stderr", ""))
        elif isinstance(result, dict):
            returncode, stdout, stderr = int(result.get("returncode", 1)), str(result.get("stdout", "")), str(result.get("stderr", ""))
        else:
            raise WorkflowConflict("clean_writer_context_runner_invalid")
        if returncode != 0:
            _atomic_text(paths["stderr"], stderr)
            raise WorkflowConflict("clean_writer_context_cli_failed")
        events, observed = _event_summaries(stdout, paths["input"], thread_id)
        if not paths["output"].is_file():
            raise WorkflowConflict("clean_writer_context_output_missing")
        return paths["output"].read_text(encoding="utf-8"), observed, events
    key = str(paths["context"])
    session = _SESSIONS.get(key)
    if session is None:
        if thread_id:
            # An ephemeral thread cannot be recreated from a new App Server
            # process.  Fail typed rather than silently starting a second
            # context; a live process can still be retried through the map.
            raise WorkflowConflict("clean_writer_context_context_recovery_unavailable")
        session = _AppServerSession(binary, paths["input"], paths["stderr"])
        session.start()
        _SESSIONS[key] = session
        # Persist the Thread identity before the first model turn.  A process
        # interruption during that turn must leave a durable signal that the
        # exact ephemeral context had already started; recovery can then
        # resume in-process or fail typed after process loss rather than
        # silently creating a second context.
        if paths["identity"].is_file():
            identity = _read_json(paths["identity"])
            if isinstance(identity, dict):
                identity.update({"thread_id": session.thread_id, "context_start_count": 1})
                _atomic_json(paths["identity"], identity)
        if paths["receipt"].is_file():
            receipt = _read_json(paths["receipt"])
            if isinstance(receipt, dict):
                receipt.update({"thread_id": session.thread_id, "context_start_count": 1, "status": "active"})
                _receipt_write(paths, str(receipt.get("identity_hash") or ""), receipt)
    if thread_id and session.thread_id != thread_id:
        raise WorkflowConflict("clean_writer_context_thread_identity_conflict")
    session.configure_persistence(
        on_event=on_event,
        on_turn_accepted=on_turn_accepted,
        on_output_snapshot=on_output_snapshot,
    )
    text = session.turn(prompt, schema)
    _atomic_text(paths["output"], text)
    return text, session.thread_id, list(session.last_events)


def run_clean_writer_context(*, artifact_root: Path | str, run_id: str, business_date: str, selected_topics: list[dict[str, Any]], writer_authority: Mapping[str, Any], current_topic: dict[str, Any], phase: str, frozen_article: Mapping[str, Any] | None = None, codex_bin: str = "", runner: Runner | None = None) -> dict[str, Any]:
    """Run one controlled turn, or read its durable typed result on retry."""
    if phase not in _TURN_PHASES:
        raise WorkflowConflict("clean_writer_context_phase_invalid")
    topics, current = [_safe_topic(row) for row in selected_topics], _safe_topic(current_topic)
    if current["topic_id"] not in _topic_ids(topics):
        raise WorkflowConflict("clean_writer_context_topic_identity_invalid")
    paths, identity_seed, identity_hash, _files, _cases, _voices = _prepare_context(artifact_root, run_id, business_date, topics, writer_authority)
    if paths["identity"].exists():
        try:
            identity = _read_json(paths["identity"])
        except (OSError, json.JSONDecodeError) as error:
            raise WorkflowConflict("clean_writer_context_identity_invalid") from error
        if identity.get("identity_hash") != identity_hash or identity.get("model") != WRITER_MODEL or identity.get("reasoning_effort") != WRITER_REASONING_EFFORT:
            raise WorkflowConflict("clean_writer_context_identity_conflict")
    else:
        identity = {**identity_seed, "identity_hash": identity_hash, "input_root": str(paths["input"]), "manifest_sha256": _sha256(paths["manifest"].read_bytes()), "codex_bin": "", "thread_id": "", "context_start_count": 0, "active_turn_id": "", "active_turn_state": "", "event_count": 0}
        _atomic_json(paths["identity"], identity)
    if paths["receipt"].exists():
        try:
            receipt = _read_json(paths["receipt"])
        except (OSError, json.JSONDecodeError) as error:
            raise WorkflowConflict("clean_writer_context_receipt_invalid") from error
        if receipt.get("identity_hash") != identity_hash:
            raise WorkflowConflict("clean_writer_context_identity_conflict")
    else:
        receipt = {"schema_version": CONTEXT_SCHEMA_VERSION, "identity_hash": identity_hash, "status": "active", "invocation_count": 1, "context_start_count": 0, "turn_count": 0, "completed_turn_count": 0, "event_count": 0, "active_turn_id": "", "active_turn_state": "", "model": WRITER_MODEL, "reasoning_effort": WRITER_REASONING_EFFORT, "ephemeral": True, "context_kind": CONTEXT_KIND, "selected_topic_ids": _topic_ids(topics)}
        _receipt_write(paths, identity_hash, receipt, exclusive=True)
    records = _turn_records(paths)
    topic_id, existing = str(current["topic_id"]), _find_record(records, str(current["topic_id"]), phase)
    if existing and existing.get("status") == "completed":
        output = existing.get("output")
        if not isinstance(output, dict):
            raise WorkflowConflict("clean_writer_context_turn_record_invalid")
        output = _validate_turn_output(output, run_id, business_date, current, phase)
        return _result(output, identity, existing, cached=True, events=existing.get("events") or [])
    if existing and existing.get("protocol_state") == "completed":
        recovered = _promote_completed_turn(
            paths=paths,
            identity=identity,
            receipt=receipt,
            record=existing,
            turn_path=paths["turns"] / f"turn-{int(existing.get('sequence', 0)):04d}.json",
            partial_path=_turn_partial_path(paths, int(existing.get("sequence", 0))),
            run_id=run_id,
            business_date=business_date,
            topic=current,
            phase=phase,
            identity_hash=identity_hash,
            cached=True,
        )
        if recovered is not None:
            return recovered
        raise WorkflowConflict("clean_writer_context_turn_status_unknown")
    try:
        binary = resolve_codex_cli(codex_bin or os.environ.get("CODEX_BIN", ""))
    except FileNotFoundError:
        raise WorkflowConflict("clean_writer_context_codex_missing") from None
    if identity.get("codex_bin") != binary:
        identity["codex_bin"] = binary
        _atomic_json(paths["identity"], identity)
    read_trace = _read_trace_state(identity, receipt)
    sequence = int(existing.get("sequence")) if existing else len(records) + 1
    attempt_count = int(existing.get("attempt_count", 0)) + 1 if existing else 1
    thread_id = str(identity.get("thread_id") or receipt.get("thread_id") or "")
    live_session = _SESSIONS.get(str(paths["context"]))
    if not thread_id and live_session is not None and live_session.thread_id:
        thread_id = live_session.thread_id
        identity.update({"thread_id": thread_id, "context_start_count": 1})
        receipt.update({"thread_id": thread_id, "context_start_count": 1})
        _atomic_json(paths["identity"], identity)
        _receipt_write(paths, identity_hash, receipt)
    if (receipt.get("status") in {"failed", "running", "unknown"} or (existing and existing.get("status") in {"running", "unknown"})) and not thread_id and live_session is None:
        # A receipt with no context-start evidence can be retried: the
        # previous attempt failed before App Server returned a Thread id, so
        # no fresh writer context was established.  Once a Thread id has been
        # durably recorded, fail closed after process death rather than create
        # a second ephemeral context that would violate the one-context rule.
        if int(receipt.get("context_start_count", 0) or 0) > 0:
            raise WorkflowConflict("clean_writer_context_context_recovery_unavailable")
        receipt.update({"status": "active"})
        receipt.pop("error", None)
        _receipt_write(paths, identity_hash, receipt)
    if existing and existing.get("protocol_state") in {"running", "unknown"} and not existing.get("turn_id") and (thread_id or int(receipt.get("context_start_count", 0) or 0) > 0):
        # The request may have been accepted before its turn id was observed;
        # the absence of an identity is itself unknown and cannot be retried.
        raise WorkflowConflict("clean_writer_context_turn_status_unknown")
    recovering_turn = bool(existing and existing.get("protocol_state") in {"running", "unknown"} and existing.get("turn_id"))
    if recovering_turn and (runner is not None or live_session is None):
        # An ephemeral Thread cannot be loaded by a new process.  A previous
        # accepted turn must be reconciled in-place or stop as unknown; it may
        # never be replaced with a new context/turn.
        raise WorkflowConflict("clean_writer_context_context_recovery_unavailable")
    if not recovering_turn:
        prompt_hash, input_hash = _write_turn_input(
            paths,
            run_id,
            business_date,
            current,
            phase,
            frozen_article,
            common_references_read=bool(read_trace.get("common_references_read")),
        )
    else:
        prompt_hash = str(existing.get("prompt_sha256") or "")
        input_hash = str(existing.get("input_sha256") or "")
    record = dict(existing) if existing else {"schema_version": CONTEXT_SCHEMA_VERSION, "sequence": sequence, "topic_id": topic_id, "phase": phase, "status": "running", "attempt_count": attempt_count, "thread_id": thread_id, "input_sha256": input_hash, "prompt_sha256": prompt_hash}
    if not existing:
        record.update({"protocol_state": "submitted", "acceptance_state": "pending", "submitted_at": _utc_now(), "event_count": 0})
    elif not recovering_turn:
        previous_turn_id = str(record.get("turn_id") or "")
        record.pop("error", None)
        record.pop("error_at", None)
        record.update({"status": "running", "attempt_count": attempt_count, "thread_id": thread_id, "input_sha256": input_hash, "prompt_sha256": prompt_hash, "protocol_state": "submitted", "acceptance_state": "pending", "submitted_at": _utc_now()})
        if previous_turn_id:
            record["previous_turn_id"] = previous_turn_id
    turn_path = paths["turns"] / f"turn-{sequence:04d}.json"
    _atomic_json(turn_path, record)

    partial_path = _turn_partial_path(paths, sequence)
    event_count = int(record.get("event_count", 0) or 0)
    total_event_count = len(_read_event_ledger(paths["event_ledger"]))

    def persist_event(summary: dict[str, Any]) -> None:
        nonlocal event_count, total_event_count
        event = dict(summary)
        event.setdefault("sequence", sequence)
        event.setdefault("recorded_at", _utc_now())
        event_turn_id = str(event.get("turn_id") or "")
        event_thread_id = str(event.get("thread_id") or "")
        expected_thread = thread_id or str(record.get("thread_id") or "")
        if event_thread_id and expected_thread and event_thread_id != expected_thread:
            raise WorkflowConflict("clean_writer_context_thread_identity_conflict")
        if event_turn_id and record.get("turn_id") and event_turn_id != record.get("turn_id"):
            raise WorkflowConflict("clean_writer_context_turn_identity_conflict")
        if event_thread_id and not record.get("thread_id"):
            record["thread_id"] = event_thread_id
        if event_turn_id and not record.get("turn_id"):
            record.update({"turn_id": event_turn_id, "acceptance_state": "accepted_by_event", "accepted_at": _utc_now(), "protocol_state": "running"})
        _append_jsonl(paths["event_ledger"], event)
        event_count += 1
        total_event_count += 1
        record["event_count"] = event_count
        if event.get("type") == "turn/completed" and event.get("status") in {"completed", "failed", "interrupted"}:
            record.update({"protocol_state": event["status"], "terminal_observed_at": event.get("recorded_at")})
        _atomic_json(turn_path, record)
        identity.update({"thread_id": record.get("thread_id") or identity.get("thread_id", ""), "active_turn_id": record.get("turn_id", ""), "active_turn_state": record.get("protocol_state", "submitted"), "event_count": total_event_count})
        _atomic_json(paths["identity"], identity)
        receipt.update({"thread_id": identity.get("thread_id") or receipt.get("thread_id", ""), "active_turn_id": record.get("turn_id", ""), "active_turn_state": record.get("protocol_state", "submitted"), "event_count": total_event_count})
        _receipt_write(paths, identity_hash, receipt)

    def persist_turn_accepted(observed_thread: str, observed_turn: str) -> None:
        if observed_thread and thread_id and observed_thread != thread_id:
            raise WorkflowConflict("clean_writer_context_thread_identity_conflict")
        record.update({"thread_id": observed_thread or thread_id, "turn_id": observed_turn, "accepted_at": _utc_now(), "acceptance_state": "accepted", "protocol_state": "running"})
        _atomic_json(turn_path, record)
        identity.update({"thread_id": observed_thread or thread_id, "active_turn_id": observed_turn, "active_turn_state": "running", "context_start_count": 1})
        _atomic_json(paths["identity"], identity)
        receipt.update({"thread_id": observed_thread or thread_id, "active_turn_id": observed_turn, "active_turn_state": "running", "context_start_count": 1, "status": "active"})
        _receipt_write(paths, identity_hash, receipt)

    def persist_output_snapshot(value: str) -> None:
        # This is a recoverable output buffer, not a published artifact.  It
        # lets a terminal event be promoted after a process exception without
        # treating an un-terminated stream as completed.
        _atomic_text(partial_path, value)

    try:
        if recovering_turn:
            session = live_session
            if session is None:
                raise WorkflowConflict("clean_writer_context_context_recovery_unavailable")
            session.configure_persistence(on_event=persist_event, on_turn_accepted=persist_turn_accepted, on_output_snapshot=persist_output_snapshot)
            resolution = session.reconcile_turn(str(record.get("turn_id") or ""))
            if resolution.get("state") == "running":
                resolution = session.continue_turn(str(record.get("turn_id") or ""))
            if resolution.get("state") == "completed" and resolution.get("output"):
                raw_output, observed_thread, events = str(resolution["output"]), session.thread_id, list(session.last_events)
            elif resolution.get("state") == "failed":
                raise WorkflowConflict("clean_writer_context_turn_failed")
            elif resolution.get("state") == "interrupted":
                raise WorkflowConflict("clean_writer_context_turn_interrupted")
            else:
                raise WorkflowConflict("clean_writer_context_turn_status_unknown")
        else:
            raw_output, observed_thread, events = _invoke_fake_or_session(
                paths=paths,
                prompt=paths["prompt"].read_text(encoding="utf-8"),
                schema=_output_schema(),
                thread_id=thread_id,
                binary=binary,
                runner=runner,
                on_event=persist_event,
                on_turn_accepted=persist_turn_accepted,
                on_output_snapshot=persist_output_snapshot,
            )
            if runner is not None:
                for event in events:
                    persist_event(event)
        read_trace = _validate_read_trace(events, phase, read_trace)
        # Record the context identity before validating model prose.  A bad
        # turn must be resumable on the same Thread, never treated as a reason
        # to start a second fresh context.
        if observed_thread:
            thread_id = observed_thread
            identity["thread_id"] = thread_id
            identity["context_start_count"] = 1
            _atomic_json(paths["identity"], identity)
            receipt.update({"thread_id": thread_id, "context_start_count": 1})
            _receipt_write(paths, identity_hash, receipt)
        identity["read_trace"] = read_trace
        receipt["read_trace"] = read_trace
        _atomic_json(paths["identity"], identity)
        _receipt_write(paths, identity_hash, receipt)
        record["read_trace_validated"] = True
        _atomic_json(turn_path, record)
        output = _validate_turn_output(json.loads(raw_output), run_id, business_date, current, phase)
        if thread_id and observed_thread and thread_id != observed_thread:
            raise WorkflowConflict("clean_writer_context_thread_identity_conflict")
        thread_id = observed_thread or thread_id
        record.update({"status": "completed", "protocol_state": "completed", "thread_id": thread_id, "output": output, "output_sha256": _sha256(canonical(output).encode("utf-8")), "events": events, "completed_at": _utc_now()})
        _atomic_json(turn_path, record)
        partial_path.unlink(missing_ok=True)
        identity.update({"thread_id": thread_id, "context_start_count": 1, "active_turn_id": "", "active_turn_state": "completed", "event_count": len(_read_event_ledger(paths["event_ledger"]))})
        _atomic_json(paths["identity"], identity)
        all_records = _turn_records(paths)
        receipt.update({"status": "active", "thread_id": thread_id, "context_start_count": 1, "active_turn_id": "", "active_turn_state": "completed", "turn_count": sum(int(item.get("attempt_count", 1)) for item in all_records), "completed_turn_count": sum(item.get("status") == "completed" for item in all_records), "last_completed_sequence": sequence, "event_count": len(_read_event_ledger(paths["event_ledger"]))})
        _receipt_write(paths, identity_hash, receipt)
        _write_event_summary_from_ledger(paths)
        return _result(output, identity, record, cached=False, events=events)
    except Exception as error:
        code = str(error) if isinstance(error, WorkflowConflict) else "clean_writer_context_unexpected_error"
        live_after_error = _SESSIONS.get(str(paths["context"]))
        if live_after_error is not None:
            if not thread_id and live_after_error.thread_id:
                thread_id = live_after_error.thread_id
                identity.update({"thread_id": thread_id, "context_start_count": 1})
                _atomic_json(paths["identity"], identity)
            if live_after_error.last_turn_id:
                record["turn_id"] = live_after_error.last_turn_id
            protocol_state = live_after_error.last_turn_state
        else:
            protocol_state = str(record.get("protocol_state") or "unknown")
        transport_unknown_codes = {
            "clean_writer_context_turn_timeout",
            "clean_writer_context_session_ended",
            "clean_writer_context_protocol_timeout",
            "clean_writer_context_reconcile_timeout",
            "clean_writer_context_turn_status_unknown",
        }
        if protocol_state == "unknown" and code not in transport_unknown_codes:
            protocol_state = "failed"
        if protocol_state == "completed" and (
            code.startswith("clean_writer_context_output_")
            or code.startswith("clean_writer_context_skill_read_trace_")
        ):
            # The model turn ended, but its typed/provenance contract failed;
            # this is a known failed attempt that may be retried on the same
            # existing context, not an unknown protocol state.
            protocol_state = "failed"
        if code == "clean_writer_context_turn_interrupted":
            protocol_state = "interrupted"
        if protocol_state not in {"completed", "failed", "interrupted", "running", "unknown"}:
            protocol_state = "unknown" if code in transport_unknown_codes else "failed"
        record.update({
            "status": "unknown" if protocol_state == "unknown" else "failed",
            "protocol_state": protocol_state,
            "error": code,
            "error_at": _utc_now(),
            "thread_id": thread_id,
        })
        _atomic_json(turn_path, record)
        identity.update({
            "thread_id": thread_id,
            "context_start_count": 1 if thread_id else 0,
            "active_turn_id": record.get("turn_id", ""),
            "active_turn_state": protocol_state,
            "event_count": len(_read_event_ledger(paths["event_ledger"])),
        })
        _atomic_json(paths["identity"], identity)
        receipt.update({
            "status": "unknown" if protocol_state == "unknown" else "failed",
            "error": code,
            "thread_id": thread_id,
            "context_start_count": 1 if thread_id else 0,
            "active_turn_id": record.get("turn_id", ""),
            "active_turn_state": protocol_state,
            "event_count": identity.get("event_count", 0),
        })
        _receipt_write(paths, identity_hash, receipt)
        _write_event_summary_from_ledger(paths)
        if isinstance(error, WorkflowConflict):
            raise
        raise WorkflowConflict(code) from None


def finalize_clean_writer_context(*, artifact_root: Path | str, run_id: str, selected_topic_ids: list[str]) -> dict[str, Any]:
    """Close the single context after public article/spoken checkpoints finish."""
    paths = _context_paths(artifact_root, run_id)
    if not paths["receipt"].is_file():
        raise WorkflowConflict("clean_writer_context_receipt_missing")
    receipt, records = _read_json(paths["receipt"]), _turn_records(paths)
    for topic_id in selected_topic_ids:
        completed_spoken = any(item.get("topic_id") == topic_id and item.get("phase") == "spoken_adaptation_required" and item.get("status") == "completed" and isinstance((item.get("output") or {}).get("script"), dict) for item in records)
        article_failed = any(item.get("topic_id") == topic_id and item.get("phase") == "article_required" and item.get("status") == "completed" and isinstance((item.get("output") or {}).get("failure"), dict) for item in records)
        if not completed_spoken and not article_failed:
            raise WorkflowConflict("clean_writer_context_checkpoint_incomplete")
    receipt.update({"status": "completed", "finalized": True, "completed_turn_count": sum(item.get("status") == "completed" for item in records)})
    _receipt_write(paths, str(receipt.get("identity_hash") or ""), receipt)
    session = _SESSIONS.pop(str(paths["context"]), None)
    if session is not None:
        session.close()
    return receipt


__all__ = ["clean_writer_contract", "run_clean_writer_context", "finalize_clean_writer_context"]
