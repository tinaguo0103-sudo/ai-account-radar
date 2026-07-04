#!/usr/bin/env python3
from __future__ import annotations

import glob
import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
LEDGER_DIR = ROOT / "output" / "feishu_write_ledger"
BLOCKING_UNKNOWN_STATUSES = {
    "unknown_not_found",
    "unknown_ambiguous",
    "delivery_unknown",
    "status_unknown",
}


def compact(value: Any, limit: int = 500) -> str:
    text = str(value or "").replace("\n", " ").strip()
    return text[:limit]


def stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))


def sha1_text(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()


def payload_hash(payload: Any) -> str:
    return "sha1:" + sha1_text(stable_json(payload))


def target_hash(value: str) -> str:
    return "sha1:" + sha1_text(value)[:16]


def operation_id(kind: str, run_id: str, business_key: str, payload_digest: str, target: str = "") -> str:
    return sha1_text("|".join([kind, run_id, business_key, payload_digest, target]))


def ledger_path(now: datetime | None = None, ledger_dir: Path | None = None) -> Path:
    current = now or datetime.now()
    root = ledger_dir or LEDGER_DIR
    return root / current.strftime("%Y-%m-%d") / "feishu_write_ledger.jsonl"


def write_ledger_event(
    *,
    kind: str,
    run_id: str,
    business_key: str,
    status: str,
    target: str = "",
    operation: str = "",
    payload_digest: str = "",
    remote_id: str = "",
    error: str = "",
    recovery_hint: str = "",
    metadata: dict[str, Any] | None = None,
    ledger_dir: Path | None = None,
) -> dict[str, Any]:
    now = datetime.now()
    operation = operation or operation_id(kind, run_id, business_key, payload_digest, target)
    event = {
        "timestamp": now.isoformat(timespec="seconds"),
        "run_date": now.strftime("%Y-%m-%d"),
        "kind": kind,
        "run_id": run_id,
        "target": target,
        "business_key": business_key,
        "payload_hash": payload_digest,
        "operation_id": operation,
        "status": status,
        "remote_id": remote_id,
        "error": compact(error),
        "recovery_hint": compact(recovery_hint),
        "metadata": metadata or {},
    }
    path = ledger_path(now, ledger_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
    return event


def read_ledger_events(ledger_dir: Path | None = None) -> list[dict[str, Any]]:
    root = ledger_dir or LEDGER_DIR
    events: list[dict[str, Any]] = []
    for path_text in sorted(glob.glob(str(root / "*" / "*.jsonl"))):
        path = Path(path_text)
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            continue
        for line in lines:
            if not line.strip():
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return events


def latest_events_by_operation(events: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for event in events:
        operation = str(event.get("operation_id") or "")
        if operation:
            latest[operation] = event
    return latest


def blocking_unknowns(
    *,
    run_id: str,
    kinds: set[str] | None = None,
    ledger_dir: Path | None = None,
) -> list[dict[str, Any]]:
    latest = latest_events_by_operation(read_ledger_events(ledger_dir))
    blocked: list[dict[str, Any]] = []
    for event in latest.values():
        if run_id and str(event.get("run_id") or "") != run_id:
            continue
        if kinds and str(event.get("kind") or "") not in kinds:
            continue
        if str(event.get("status") or "") in BLOCKING_UNKNOWN_STATUSES:
            blocked.append(event)
    return sorted(blocked, key=lambda item: str(item.get("timestamp") or ""))


def guard_summary(run_id: str, unknowns: list[dict[str, Any]]) -> str:
    if not unknowns:
        return ""
    lines = [
        f"检测到 run_id={run_id} 存在 {len(unknowns)} 条 Feishu 非幂等状态未知记录，已阻止后续真实外发。",
    ]
    for event in unknowns[:5]:
        lines.append(
            "- "
            + " | ".join(
                [
                    f"kind={event.get('kind')}",
                    f"status={event.get('status')}",
                    f"target={event.get('target')}",
                    f"business_key={event.get('business_key')}",
                    f"hint={event.get('recovery_hint')}",
                ]
            )
        )
    return "\n".join(lines)

