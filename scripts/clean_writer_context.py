#!/usr/bin/env python3
"""One fresh, content-only Writer context for an exact workflow run.

This module is deliberately a narrow runtime boundary.  The deterministic
controller prepares a read-only, run-scoped input root, invokes one ephemeral
Codex context, validates the typed result, and persists a receipt.  It never
creates a per-topic model process and never falls back to the outer caller for
prose generation.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable, Mapping

from codex_cli_path import resolve_codex_cli
from daily_workflow import DailyWorkflow, WorkflowConflict, canonical
import spoken_script_runtime as script_runtime


CONTEXT_SCHEMA_VERSION = 1
WRITER_MODEL = "gpt-5.6-luna"
WRITER_REASONING_EFFORT = "max"
CONTEXT_KIND = "fresh_non_user_visible_content_only"
DEFAULT_TIMEOUT_SECONDS = 900

_FORBIDDEN_PATH_TOKENS = (
    "AGENTS.md",
    ".git",
    "daily_workflow.sqlite3",
    "docs/",
    "qa正文",
    "old稿",
)
_FORBIDDEN_TOPIC_KEYS = {
    "article",
    "body",
    "script",
    "spoken",
    "previous_topic_body",
    "old_body",
    "qa_body",
    "private_style_context",
    "editing_reference",
    "austin_private_context",
}
_ALLOWED_TOPIC_KEYS = {"topic_id", "trend_event_id", "source_evidence", "author_input"}
_REFERENCE_FILES = tuple(script_runtime.ARTICLE_REQUIRED_REFERENCES)


Runner = Callable[[list[str], str, Path, Path], Any]


def clean_writer_contract() -> dict[str, Any]:
    """Return the existing checkpoint contract with the Phase A topology."""
    contract = script_runtime.load_writer_contract()
    contract["topology"] = "one_fresh_codex_writer_context_per_exact_run"
    contract["input_scope"] = [
        "same_run_selected_topic_cards_only",
        "active_r4_skill_and_managed_references_snapshot",
        "same_run_source_evidence_only",
        "article_then_frozen_spoken_adaptation",
    ]
    contract["raw_text_persistence"] = "typed_output_only"
    contract["fresh_context"] = True
    contract["non_user_visible"] = True
    contract["one_context_per_exact_run"] = True
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


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _regular_file(path: Path, error: str) -> Path:
    if path.is_symlink() or not path.is_file():
        raise WorkflowConflict(error)
    return path.resolve()


def _safe_topic(topic: Any) -> dict[str, Any]:
    if not isinstance(topic, dict):
        raise WorkflowConflict("clean_writer_context_topic_scope_invalid")
    if set(topic) - _ALLOWED_TOPIC_KEYS:
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


def _authority_files(authority: Mapping[str, Any]) -> tuple[Path, dict[str, str]]:
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
    return skill_path, files


def _prompt(run_id: str, business_date: str, topic_ids: list[str]) -> str:
    rendered_ids = ", ".join(topic_ids)
    return f"""You are the sole prose owner in one fresh, non-user-visible, content-only Writer context.

Exact run: {run_id}
Business date: {business_date}
Exact topic order: {rendered_ids}

Read only files below the current working directory. Do not use the network,
do not open a parent or absolute path, and do not read any project, PM, QA,
Git, SQLite, publisher, old-draft, or other-run material. First read
skill/SKILL.md completely, then read every file in skill/references completely,
and then read topics.json.

Write each topic in the listed order. For a topic, complete the article first
and adapt that same article into the spoken script. Never use another topic's
identity or body. If the supplied material is genuinely insufficient, return
an item-local material_or_angle_insufficiency failure instead of inventing
facts. Do not add commentary, markdown fences, source indexes, or extra keys.

Return exactly this JSON object:
{{
  "run_id": "{run_id}",
  "business_date": "{business_date}",
  "topics": [
    {{
      "topic_id": "...",
      "article": {{"topic_id": "...", "title": "...", "body": "..."}} or null,
      "article_failure": {{"topic_id": "...", "reason": "material_or_angle_insufficiency", "detail": "..."}} or null,
      "script": {{"topic_id": "...", "title": "...", "hook": "...", "structure": "...", "body": "..."}} or null,
      "spoken_failure": {{"topic_id": "...", "reason": "material_or_angle_insufficiency", "detail": "..."}} or null
    }}
  ]
}}
"""


def _output_schema() -> dict[str, Any]:
    failure = {
        "type": "object",
        "additionalProperties": False,
        "required": ["topic_id", "reason", "detail"],
        "properties": {
            "topic_id": {"type": "string"},
            "reason": {"type": "string", "enum": ["material_or_angle_insufficiency"]},
            "detail": {"type": "string"},
        },
    }
    article = {
        "type": "object",
        "additionalProperties": False,
        "required": ["topic_id", "title", "body"],
        "properties": {
            "topic_id": {"type": "string"},
            "title": {"type": "string"},
            "body": {"type": "string"},
        },
    }
    script = {
        "type": "object",
        "additionalProperties": False,
        "required": ["topic_id", "title", "hook", "structure", "body"],
        "properties": {
            "topic_id": {"type": "string"},
            "title": {"type": "string"},
            "hook": {"type": "string"},
            "structure": {"type": "string"},
            "body": {"type": "string"},
        },
    }
    nullable = lambda schema: {"anyOf": [{"type": "null"}, schema]}
    topic = {
        "type": "object",
        "additionalProperties": False,
        "required": ["topic_id", "article", "article_failure", "script", "spoken_failure"],
        "properties": {
            "topic_id": {"type": "string"},
            "article": nullable(article),
            "article_failure": nullable(failure),
            "script": nullable(script),
            "spoken_failure": nullable(failure),
        },
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["run_id", "business_date", "topics"],
        "properties": {
            "run_id": {"type": "string"},
            "business_date": {"type": "string"},
            "topics": {"type": "array", "items": topic},
        },
    }


def _identity_seed(
    run_id: str,
    business_date: str,
    topics: list[dict[str, Any]],
    authority: Mapping[str, Any],
) -> dict[str, Any]:
    skill = authority.get("active_skill") or {}
    refs = authority.get("required_managed_references") or []
    return {
        "schema_version": CONTEXT_SCHEMA_VERSION,
        "run_id": run_id,
        "business_date": business_date,
        "selected_topic_ids": _topic_ids(topics),
        "topics_sha256": _sha256(canonical(topics).encode("utf-8")),
        "skill_sha256": str(skill.get("sha256") or ""),
        "reference_sha256": [str(row.get("sha256") or "") for row in refs],
        "model": WRITER_MODEL,
        "reasoning_effort": WRITER_REASONING_EFFORT,
        "ephemeral": True,
        "context_kind": CONTEXT_KIND,
    }


def _context_paths(artifact_root: Path | str, run_id: str) -> dict[str, Path]:
    context = Path(artifact_root).resolve() / run_id / "clean_writer_context"
    return {
        "context": context,
        "input": context / "clean_root",
        "manifest": context / "clean_root" / "manifest.json",
        "topics": context / "clean_root" / "topics.json",
        "prompt": context / "clean_root" / "writer_prompt.md",
        "schema": context / "output_schema.json",
        "output": context / "output.json",
        "stdout": context / "cli_stdout.jsonl",
        "stderr": context / "cli_stderr.log",
        "events": context / "events_summary.json",
        "identity": context / "identity.json",
        "receipt": context / "receipt.json",
    }


def _validate_existing_manifest(path: Path, identity_hash: str, expected_files: set[str]) -> None:
    try:
        manifest = _read_json(path)
    except (OSError, json.JSONDecodeError) as error:
        raise WorkflowConflict("clean_writer_context_manifest_invalid") from error
    if (
        not isinstance(manifest, dict)
        or manifest.get("identity_hash") != identity_hash
        or manifest.get("context_kind") != CONTEXT_KIND
        or set(manifest.get("allowed_files") or []) != expected_files
    ):
        raise WorkflowConflict("clean_writer_context_manifest_identity_conflict")


def _prepare_inputs(
    artifact_root: Path | str,
    run_id: str,
    business_date: str,
    selected_topics: list[dict[str, Any]],
    authority: Mapping[str, Any],
) -> tuple[dict[str, Path], dict[str, Any], str]:
    DailyWorkflow.validate_identity(run_id, business_date)
    topics = [_safe_topic(row) for row in selected_topics]
    topic_ids = _topic_ids(topics)
    _skill_root, source_files = _authority_files(authority)
    identity_seed = _identity_seed(run_id, business_date, topics, authority)
    identity_hash = _sha256(canonical(identity_seed).encode("utf-8"))
    paths = _context_paths(artifact_root, run_id)
    context = paths["context"]
    context.mkdir(parents=True, exist_ok=True)
    if paths["receipt"].exists():
        try:
            receipt = _read_json(paths["receipt"])
        except (OSError, json.JSONDecodeError) as error:
            raise WorkflowConflict("clean_writer_context_receipt_invalid") from error
        if receipt.get("identity_hash") != identity_hash:
            raise WorkflowConflict("clean_writer_context_identity_conflict")
        return paths, identity_seed, identity_hash
    if paths["output"].exists() or paths["events"].exists():
        raise WorkflowConflict("clean_writer_context_state_conflict")
    expected_files = {
        "manifest.json", "topics.json", "writer_prompt.md", "skill/SKILL.md",
        *(f"skill/{relative}" for relative in _REFERENCE_FILES),
    }
    if paths["manifest"].exists():
        _validate_existing_manifest(paths["manifest"], identity_hash, expected_files)
        return paths, identity_seed, identity_hash

    input_root = paths["input"]
    input_root.mkdir(parents=True, exist_ok=True)
    expected_hashes = {
        "SKILL.md": str((authority.get("active_skill") or {}).get("sha256") or ""),
        **{
            str(row.get("relative_path") or ""): str(row.get("sha256") or "")
            for row in (authority.get("required_managed_references") or [])
            if isinstance(row, dict)
        },
    }
    for relative, source_text in source_files.items():
        source = _regular_file(Path(source_text), "clean_writer_context_skill_missing")
        if expected_hashes.get(relative) and _sha256(source.read_bytes()) != expected_hashes[relative]:
            raise WorkflowConflict("clean_writer_context_skill_manifest_conflict")
        target = input_root / "skill" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        target.chmod(0o444)
    _atomic_json(paths["topics"], {"run_id": run_id, "business_date": business_date, "topics": topics})
    paths["topics"].chmod(0o444)
    paths["prompt"].write_text(_prompt(run_id, business_date, topic_ids), encoding="utf-8")
    paths["prompt"].chmod(0o444)
    _atomic_json(paths["schema"], _output_schema())
    paths["schema"].chmod(0o444)
    manifest = {
        "schema_version": CONTEXT_SCHEMA_VERSION,
        "identity_hash": identity_hash,
        "run_id": run_id,
        "business_date": business_date,
        "model": WRITER_MODEL,
        "reasoning_effort": WRITER_REASONING_EFFORT,
        "ephemeral": True,
        "context_kind": CONTEXT_KIND,
        "selected_topic_ids": topic_ids,
        "allowed_files": sorted(expected_files),
        "forbidden_paths": list(_FORBIDDEN_PATH_TOKENS),
        "skill_files": [
            {
                "relative_path": relative,
                "sha256": _sha256((input_root / "skill" / relative).read_bytes()),
                "bytes": (input_root / "skill" / relative).stat().st_size,
            }
            for relative in ("SKILL.md", *_REFERENCE_FILES)
        ],
    }
    _atomic_json(paths["manifest"], manifest)
    paths["manifest"].chmod(0o444)
    for directory in (input_root, input_root / "skill", input_root / "skill" / "references"):
        directory.chmod(0o555)
    return paths, identity_seed, identity_hash


def _default_runner(command: list[str], prompt: str, input_root: Path, _output: Path) -> Any:
    return subprocess.run(
        command,
        input=prompt,
        text=True,
        cwd=str(input_root),
        capture_output=True,
        timeout=DEFAULT_TIMEOUT_SECONDS,
    )


def _command(codex_bin: str, paths: Mapping[str, Path]) -> list[str]:
    return [
        codex_bin,
        "exec",
        "--ephemeral",
        "--ignore-user-config",
        "--ignore-rules",
        "--skip-git-repo-check",
        "--cd",
        str(paths["input"]),
        "--sandbox",
        "read-only",
        "--model",
        WRITER_MODEL,
        "-c",
        'model_reasoning_effort="max"',
        "--output-schema",
        str(paths["schema"]),
        "--output-last-message",
        str(paths["output"]),
        "--json",
        "--color",
        "never",
        "-",
    ]


def _event_summaries(stdout: str, input_root: Path) -> tuple[list[dict[str, Any]], str]:
    summaries: list[dict[str, Any]] = []
    thread_id = ""
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        if event.get("type") == "thread.started":
            thread_id = str(event.get("thread_id") or "")
        item = event.get("item")
        if isinstance(item, dict):
            command = str(item.get("command") or "")
            if command and any(token in command for token in _FORBIDDEN_PATH_TOKENS):
                raise WorkflowConflict("clean_writer_context_forbidden_path_read")
            if command:
                try:
                    tokens = shlex.split(command)
                except ValueError:
                    raise WorkflowConflict("clean_writer_context_command_scope_invalid") from None
                root_text = str(input_root.resolve())
                for token in tokens:
                    if token == ".." or "/../" in token or token.startswith("../"):
                        raise WorkflowConflict("clean_writer_context_parent_path_read")
                    if not token.startswith("/"):
                        continue
                    if token == "/bin/zsh" or token.startswith("/bin/") or token.startswith("/usr/bin/"):
                        continue
                    if token.startswith(root_text + "/") or token == root_text:
                        continue
                    raise WorkflowConflict("clean_writer_context_absolute_path_read")
                absolute_paths = re.findall(r"/(?:Users|private|tmp|Volumes|Applications)/[^\s'\"`]+", command)
                for absolute in absolute_paths:
                    if not (absolute == root_text or absolute.startswith(root_text + "/")):
                        raise WorkflowConflict("clean_writer_context_absolute_path_read")
            summary = {"type": event.get("type"), "item_type": item.get("type")}
            if command:
                summary["command"] = command[:500]
            summaries.append(summary)
        elif event.get("type") in {"thread.started", "turn.started", "turn.completed"}:
            summaries.append({"type": event.get("type")})
    if not thread_id:
        raise WorkflowConflict("clean_writer_context_thread_identity_missing")
    return summaries, thread_id


def _required_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WorkflowConflict(f"clean_writer_context_output_{field}_invalid")
    return value


def _validate_output(
    value: Any,
    run_id: str,
    business_date: str,
    topics: list[dict[str, Any]],
) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"run_id", "business_date", "topics"}:
        raise WorkflowConflict("clean_writer_context_output_schema_invalid")
    if value.get("run_id") != run_id or value.get("business_date") != business_date:
        raise WorkflowConflict("clean_writer_context_output_identity_conflict")
    rows = value.get("topics")
    if not isinstance(rows, list) or len(rows) != len(topics):
        raise WorkflowConflict("clean_writer_context_output_coverage_invalid")
    expected_ids = _topic_ids(topics)
    actual_ids = [str(row.get("topic_id") or "") for row in rows if isinstance(row, dict)]
    if actual_ids != expected_ids:
        raise WorkflowConflict("clean_writer_context_output_topic_order_invalid")
    for row, topic_id in zip(rows, expected_ids):
        if not isinstance(row, dict) or set(row) != {
            "topic_id", "article", "article_failure", "script", "spoken_failure",
        }:
            raise WorkflowConflict("clean_writer_context_output_topic_schema_invalid")
        article = row.get("article")
        article_failure = row.get("article_failure")
        script = row.get("script")
        spoken_failure = row.get("spoken_failure")
        if (article is None) == (article_failure is None):
            raise WorkflowConflict("clean_writer_context_output_article_phase_invalid")
        if article is not None:
            if not isinstance(article, dict) or set(article) != {"topic_id", "title", "body"}:
                raise WorkflowConflict("clean_writer_context_output_article_invalid")
            if article.get("topic_id") != topic_id:
                raise WorkflowConflict("clean_writer_context_output_topic_identity_conflict")
            _required_text(article.get("title"), "article_title")
            _required_text(article.get("body"), "article_body")
            if (script is None) == (spoken_failure is None):
                raise WorkflowConflict("clean_writer_context_output_spoken_phase_invalid")
            if script is not None:
                if not isinstance(script, dict) or set(script) != {
                    "topic_id", "title", "hook", "structure", "body",
                }:
                    raise WorkflowConflict("clean_writer_context_output_script_invalid")
                if script.get("topic_id") != topic_id:
                    raise WorkflowConflict("clean_writer_context_output_topic_identity_conflict")
                for field in ("title", "hook", "structure", "body"):
                    _required_text(script.get(field), f"script_{field}")
            else:
                _validate_failure(spoken_failure, topic_id, "spoken")
        elif script is not None:
            raise WorkflowConflict("clean_writer_context_output_spoken_without_article")
        elif spoken_failure is not None:
            # A writer may explain why adaptation could not proceed after an
            # item-local article failure.  The controller records only the
            # article failure and never treats this as a spoken artifact.
            _validate_failure(spoken_failure, topic_id, "spoken")
        if article_failure is not None:
            _validate_failure(article_failure, topic_id, "article")
    return value


def _validate_failure(value: Any, topic_id: str, phase: str) -> None:
    if not isinstance(value, dict) or set(value) != {"topic_id", "reason", "detail"}:
        raise WorkflowConflict(f"clean_writer_context_output_{phase}_failure_invalid")
    if value.get("topic_id") != topic_id or value.get("reason") != "material_or_angle_insufficiency":
        raise WorkflowConflict(f"clean_writer_context_output_{phase}_failure_invalid")
    _required_text(value.get("detail"), f"{phase}_failure_detail")


def _receipt(path: Path, value: dict[str, Any], *, exclusive: bool = False) -> None:
    if exclusive:
        path.parent.mkdir(parents=True, exist_ok=True)
        encoded = (canonical(value) + "\n").encode("utf-8")
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        except FileExistsError:
            raise WorkflowConflict("clean_writer_context_already_attempted") from None
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
        except Exception:
            path.unlink(missing_ok=True)
            raise
        return
    _atomic_json(path, value)


def _result_from_output(
    output: dict[str, Any],
    identity: dict[str, Any],
    *,
    cached: bool,
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "output": output,
        "by_topic": {row["topic_id"]: row for row in output["topics"]},
        "identity": identity,
        "cached": cached,
        "events": events,
    }


def run_clean_writer_context(
    *,
    artifact_root: Path | str,
    run_id: str,
    business_date: str,
    selected_topics: list[dict[str, Any]],
    writer_authority: Mapping[str, Any],
    codex_bin: str = "",
    runner: Runner | None = None,
) -> dict[str, Any]:
    """Run or resume the sole fresh Writer context for one exact run."""
    paths, identity_seed, identity_hash = _prepare_inputs(
        artifact_root, run_id, business_date, selected_topics, writer_authority,
    )
    receipt_path = paths["receipt"]
    if receipt_path.exists():
        try:
            receipt = _read_json(receipt_path)
        except (OSError, json.JSONDecodeError) as error:
            raise WorkflowConflict("clean_writer_context_receipt_invalid") from error
        if receipt.get("identity_hash") != identity_hash:
            raise WorkflowConflict("clean_writer_context_identity_conflict")
        if (
            receipt.get("model") != WRITER_MODEL
            or receipt.get("reasoning_effort") != WRITER_REASONING_EFFORT
            or receipt.get("ephemeral") is not True
            or receipt.get("context_kind") != CONTEXT_KIND
        ):
            raise WorkflowConflict("clean_writer_context_model_conflict")
        if receipt.get("status") != "completed":
            raise WorkflowConflict("clean_writer_context_already_attempted")
        if not paths["output"].is_file():
            raise WorkflowConflict("clean_writer_context_output_missing")
        expected_output_sha = str(receipt.get("output_sha256") or "")
        if expected_output_sha and _sha256(paths["output"].read_bytes()) != expected_output_sha:
            raise WorkflowConflict("clean_writer_context_output_hash_conflict")
        try:
            output_value = _read_json(paths["output"])
            events = _read_json(paths["events"]) if paths["events"].is_file() else []
        except (OSError, json.JSONDecodeError) as error:
            raise WorkflowConflict("clean_writer_context_artifact_invalid") from error
        if not isinstance(events, list):
            raise WorkflowConflict("clean_writer_context_artifact_invalid")
        output = _validate_output(
            output_value, run_id, business_date,
            [_safe_topic(row) for row in selected_topics],
        )
        return _result_from_output(output, receipt, cached=True, events=events)

    input_root = paths["input"]
    if not input_root.is_dir():
        raise WorkflowConflict("clean_writer_context_input_missing")
    try:
        binary = resolve_codex_cli(codex_bin or os.environ.get("CODEX_BIN", ""))
    except FileNotFoundError:
        raise WorkflowConflict("clean_writer_context_codex_missing") from None
    identity = {
        **identity_seed,
        "identity_hash": identity_hash,
        "input_root": str(input_root),
        "manifest_sha256": _sha256(paths["manifest"].read_bytes()),
        "codex_bin": binary,
    }
    _atomic_json(paths["identity"], identity)
    _receipt(receipt_path, {
        "schema_version": CONTEXT_SCHEMA_VERSION,
        "identity_hash": identity_hash,
        "status": "running",
        "invocation_count": 1,
        "model": WRITER_MODEL,
        "reasoning_effort": WRITER_REASONING_EFFORT,
        "ephemeral": True,
        "context_kind": CONTEXT_KIND,
    }, exclusive=True)
    command = _command(binary, paths)
    try:
        result = (runner or _default_runner)(
            command, paths["prompt"].read_text(encoding="utf-8"), input_root, paths["output"],
        )
        if hasattr(result, "returncode"):
            returncode = int(result.returncode)
            stdout = str(getattr(result, "stdout", ""))
            stderr = str(getattr(result, "stderr", ""))
        elif isinstance(result, dict):
            returncode = int(result.get("returncode", 1))
            stdout = str(result.get("stdout", ""))
            stderr = str(result.get("stderr", ""))
        else:
            raise WorkflowConflict("clean_writer_context_runner_invalid")
        if returncode != 0:
            _atomic_text(paths["stdout"], stdout)
            _atomic_text(paths["stderr"], stderr)
            raise WorkflowConflict("clean_writer_context_cli_failed")
        _atomic_text(paths["stdout"], stdout)
        _atomic_text(paths["stderr"], stderr)
        events, thread_id = _event_summaries(stdout, input_root)
        if not paths["output"].is_file():
            raise WorkflowConflict("clean_writer_context_output_missing")
        try:
            output_value = _read_json(paths["output"])
        except (OSError, json.JSONDecodeError) as error:
            raise WorkflowConflict("clean_writer_context_output_invalid") from error
        output = _validate_output(
            output_value, run_id, business_date,
            [_safe_topic(row) for row in selected_topics],
        )
        identity = {
            **identity,
            "thread_id": thread_id,
            "event_count": len(events),
            "stderr_present": bool(stderr.strip()),
            "stdout_sha256": _sha256(stdout.encode("utf-8")),
            "stderr_sha256": _sha256(stderr.encode("utf-8")),
        }
        _atomic_json(paths["events"], events)
        _atomic_json(paths["identity"], identity)
        _receipt(paths["receipt"], {
            "schema_version": CONTEXT_SCHEMA_VERSION,
            "identity_hash": identity_hash,
            "status": "completed",
            "invocation_count": 1,
            "thread_id": thread_id,
            "model": WRITER_MODEL,
            "reasoning_effort": WRITER_REASONING_EFFORT,
            "ephemeral": True,
            "context_kind": CONTEXT_KIND,
            "output_sha256": _sha256(paths["output"].read_bytes()),
        })
        return _result_from_output(output, identity, cached=False, events=events)
    except Exception as error:
        code = str(error) if isinstance(error, WorkflowConflict) else "clean_writer_context_unexpected_error"
        try:
            _receipt(paths["receipt"], {
                "schema_version": CONTEXT_SCHEMA_VERSION,
                "identity_hash": identity_hash,
                "status": "failed",
                "invocation_count": 1,
                "error": code,
                "model": WRITER_MODEL,
                "reasoning_effort": WRITER_REASONING_EFFORT,
                "ephemeral": True,
                "context_kind": CONTEXT_KIND,
            })
        except Exception:
            pass
        if isinstance(error, WorkflowConflict):
            raise
        raise WorkflowConflict(code) from None
