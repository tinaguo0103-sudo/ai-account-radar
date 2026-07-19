#!/usr/bin/env python3
"""Owned local lifecycle for truthful Douyin candidate artifacts."""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LEDGER = ROOT / "output/state/douyin_candidate_lifecycle.json"
TERMINAL_STATES = {"reviewed", "written_04", "generated_06"}


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_ledger(path: Path = DEFAULT_LEDGER) -> dict[str, Any]:
    if not path.exists():
        return {"schema_version": 1, "items": {}}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema_version") != 1 or not isinstance(payload.get("items"), dict):
        raise RuntimeError("douyin_lifecycle_malformed")
    return payload


def write_ledger(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def _normalized(value: Any) -> str:
    return " ".join(str(value or "").split())


def mark_reviewed_candidates(
    candidates: list[dict[str, Any]], *, run_id: str, ledger_path: Path = DEFAULT_LEDGER,
) -> dict[str, Any]:
    ledger = load_ledger(ledger_path)
    updated = []
    missing = []
    now = datetime.now().isoformat(timespec="seconds")
    for candidate in candidates:
        canonical_fingerprint = _normalized(candidate.get("content_fingerprint"))
        entry_key = canonical_fingerprint if canonical_fingerprint in ledger["items"] else ""
        if not entry_key:
            identity = (
                _normalized(candidate.get("exact_url")), _normalized(candidate.get("source_account")),
                _normalized(candidate.get("csv_title") or candidate.get("artifact_text")),
            )
            if not all(identity):
                missing.append(canonical_fingerprint)
                continue
            matches = [
                key for key, value in ledger["items"].items()
                if isinstance(value, dict) and (
                    _normalized(value.get("url")), _normalized(value.get("account")), _normalized(value.get("title")),
                ) == identity
            ]
            if len(matches) > 1:
                raise RuntimeError(f"douyin_lifecycle_identity_ambiguous:{canonical_fingerprint}")
            entry_key = matches[0] if matches else ""
        entry = ledger["items"].get(entry_key)
        if not isinstance(entry, dict):
            missing.append(canonical_fingerprint)
            continue
        if entry.get("state") in TERMINAL_STATES:
            continue
        decision = _normalized(candidate.get("terminal_decision"))
        if decision not in {"select", "observe", "reject"}:
            raise RuntimeError(f"douyin_terminal_decision_missing:{canonical_fingerprint}")
        entry.update({
            "state": "reviewed",
            "reviewed_run_id": run_id,
            "reviewed_at": now,
            "terminal_decision": decision,
            "canonical_fingerprint": canonical_fingerprint,
        })
        updated.append(entry_key)
    if updated:
        write_ledger(ledger_path, ledger)
    return {"updated": updated, "missing": missing, "ledger_path": str(ledger_path)}


def mark_written_04(fingerprints: list[str], *, run_id: str, ledger_path: Path = DEFAULT_LEDGER) -> dict[str, Any]:
    ledger = load_ledger(ledger_path)
    wanted = {_normalized(value) for value in fingerprints if _normalized(value)}
    updated = []
    for key, entry in ledger["items"].items():
        if not isinstance(entry, dict) or _normalized(entry.get("canonical_fingerprint")) not in wanted:
            continue
        if entry.get("state") == "reviewed":
            entry.update({"state": "written_04", "written_04_run_id": run_id})
            updated.append(key)
    if updated:
        write_ledger(ledger_path, ledger)
    return {"updated": updated, "ledger_path": str(ledger_path)}


def validate_artifact(entry: dict[str, Any]) -> dict[str, Any]:
    path = Path(str(entry.get("artifact_path") or ""))
    if not path.is_file() or file_sha256(path) != str(entry.get("artifact_sha256") or ""):
        raise RuntimeError("douyin_lifecycle_artifact_missing_or_corrupt")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("内容指纹") != entry.get("fingerprint"):
        raise RuntimeError("douyin_lifecycle_artifact_identity_mismatch")
    return payload
