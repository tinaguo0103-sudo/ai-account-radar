#!/usr/bin/env python3
"""Run-scoped authority for Douyin candidate selection and understanding packages."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

ALLOWED_DISCOVERY = {"configured_account", "recommendation", "dynamic_search"}
ALLOWED_REASONS = {"title_value", "engagement_relative", "exploration"}
SCREEN_TEXT_KINDS = {"prompt", "url", "tool_name", "parameter", "code", "number", "other"}


class VideoUnderstandingError(RuntimeError):
    pass


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def normalize_title(value: str) -> str:
    return re.sub(r"[\W_]+", "", value.casefold())


def validate_run(run_id: str, business_date: str) -> None:
    if not re.fullmatch(r"run_\d{8}_\d{6}", run_id):
        raise VideoUnderstandingError("wrong_run")
    compact = run_id[4:12]
    if business_date != f"{compact[:4]}-{compact[4:6]}-{compact[6:]}":
        raise VideoUnderstandingError("wrong_business_date")


def merge_candidates(batches: list[list[dict[str, Any]]], run_id: str) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for batch in batches:
        for raw in batch:
            if raw.get("run_id") != run_id:
                raise VideoUnderstandingError("cross_run_candidate")
            discovery = str(raw.get("discovery_source") or "")
            if discovery not in ALLOWED_DISCOVERY:
                raise VideoUnderstandingError("candidate_discovery_source_invalid")
            aweme_id = str(raw.get("aweme_id") or "")
            url = str(raw.get("source_url") or "")
            if not aweme_id or f"/video/{aweme_id}" not in url:
                raise VideoUnderstandingError("candidate_identity_invalid")
            identity = f"douyin:{aweme_id}"
            current = merged.get(identity)
            candidate = {
                "id": identity,
                "run_id": run_id,
                "aweme_id": aweme_id,
                "source_url": url,
                "author": str(raw.get("author") or ""),
                "title": str(raw.get("title") or ""),
                "published_at": str(raw.get("published_at") or ""),
                "published_at_display": str(raw.get("published_at_display") or ""),
                "published_recency": raw.get("published_recency") or {},
                "duration_seconds": max(1, int(raw.get("duration_seconds") or 1)),
                "discovery_source": discovery,
                "public_engagement": {
                    key: (None if raw.get(key) is None else int(raw[key]))
                    for key in ("likes", "comments", "favorites", "shares")
                },
                "fact_missing_reasons": raw.get("fact_missing_reasons") or {},
                "fact_provenance": raw.get("fact_provenance") or {},
                "raw_identity": str(raw.get("raw_identity") or digest(raw)),
            }
            if current and current["raw_identity"] != candidate["raw_identity"]:
                raise VideoUnderstandingError("candidate_identity_conflict")
            merged[identity] = candidate
    return sorted(merged.values(), key=lambda row: row["id"])


def apply_policy_decisions(
    candidates: list[dict[str, Any]],
    decisions: list[dict[str, Any]],
    policy: dict[str, Any],
) -> list[dict[str, Any]]:
    by_id = {row["id"]: row for row in candidates}
    seen: set[str] = set()
    output: list[dict[str, Any]] = []
    for decision in decisions:
        identity = str(decision.get("candidate_id") or "")
        if identity not in by_id or identity in seen:
            raise VideoUnderstandingError("selection_identity_conflict")
        seen.add(identity)
        selected = bool(decision.get("selected"))
        reasons = [str(item) for item in decision.get("reasons") or []]
        if selected and (not reasons or any(reason not in ALLOWED_REASONS for reason in reasons)):
            raise VideoUnderstandingError("selection_reason_invalid")
        if decision.get("requires_title_and_engagement") is True:
            raise VideoUnderstandingError("selection_double_gate_forbidden")
        row = dict(by_id[identity])
        row.update({
            "selected_by_policy": selected,
            "selection_reasons": reasons,
            "selection_explanation": str(decision.get("explanation") or ""),
            "policy_id": policy["policy_id"],
            "policy_sha256": digest(policy),
        })
        output.append(row)
    for identity, candidate in by_id.items():
        if identity not in seen:
            output.append({
                **candidate,
                "selected_by_policy": False,
                "selection_reasons": [],
                "selection_explanation": "策略未选择",
                "policy_id": policy["policy_id"],
                "policy_sha256": digest(policy),
            })
    return sorted(output, key=lambda row: row["id"])


def fold_near_duplicates(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault(normalize_title(row["title"]), []).append(row)
    output: list[dict[str, Any]] = []
    for group in groups.values():
        selected = [row for row in group if row["selected_by_policy"]]
        if len(selected) <= 1:
            output.extend(group)
            continue
        def interaction(row: dict[str, Any]) -> int:
            return sum(value or 0 for value in row["public_engagement"].values())
        winner = max(selected, key=lambda row: (interaction(row), row["id"]))
        for row in group:
            if row is winner or not row["selected_by_policy"]:
                output.append(row)
            else:
                output.append({
                    **row,
                    "selected_by_policy": False,
                    "selection_reasons": ["near_duplicate"],
                    "selection_explanation": f"近似内容折叠，保留 {winner['id']}",
                })
    return sorted(output, key=lambda row: row["id"])


def budget_selection(rows: list[dict[str, Any]], policy: dict[str, Any]) -> dict[str, Any]:
    selected = [row for row in rows if row["selected_by_policy"]]
    reason_order = {"title_value": 0, "engagement_relative": 1, "exploration": 2}
    selected.sort(key=lambda row: (
        min((reason_order.get(reason, 9) for reason in row["selection_reasons"]), default=9),
        -sum(value or 0 for value in row["public_engagement"].values()),
        row["id"],
    ))
    maximum_count = int(policy["target_count_max"])
    maximum_duration = int(policy["maximum_duration_seconds"])
    maximum_video_duration = int(policy.get("maximum_video_duration_seconds") or maximum_duration)
    chosen, skipped, total = [], [], 0
    for row in selected:
        duration = int(row["duration_seconds"])
        if duration > maximum_video_duration:
            skipped.append({
                "candidate_id": row["id"],
                "reason": "video_duration_exceeds_policy",
                "duration_seconds": duration,
            })
            continue
        if len(chosen) >= maximum_count or total + duration > maximum_duration:
            skipped.append({
                "candidate_id": row["id"],
                "reason": "daily_budget_exhausted",
                "duration_seconds": duration,
            })
            continue
        chosen.append(row)
        total += duration
    return {
        "selected": chosen,
        "selected_count": len(chosen),
        "total_duration_seconds": total,
        "target_count": [policy["target_count_min"], policy["target_count_max"]],
        "target_duration_seconds": policy["target_duration_seconds"],
        "maximum_duration_seconds": maximum_duration,
        "maximum_video_duration_seconds": maximum_video_duration,
        "skipped": skipped,
        "under_target_allowed": True,
    }


def validate_package(package: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    for key in ("run_id", "aweme_id", "source_url"):
        expected = candidate["run_id"] if key == "run_id" else candidate[key]
        if package.get(key) != expected:
            raise VideoUnderstandingError("understanding_identity_conflict")
    if package.get("status") not in {"completed", "completed_with_failures", "failed"}:
        raise VideoUnderstandingError("understanding_status_invalid")
    if package.get("temporary_media_remaining") != 0:
        raise VideoUnderstandingError("temporary_media_cleanup_incomplete")
    for item in package.get("screen_text") or []:
        if item.get("kind") not in SCREEN_TEXT_KINDS:
            raise VideoUnderstandingError("screen_text_kind_invalid")
        if item.get("kind") == "url" and item.get("verified") not in {True, False}:
            raise VideoUnderstandingError("screen_url_verification_missing")
    if package.get("asr", {}).get("primary_model") == "whisper-large-v3-turbo":
        raise VideoUnderstandingError("turbo_initial_runtime_forbidden")
    value = dict(package)
    value["package_sha256"] = digest({key: item for key, item in package.items() if key != "package_sha256"})
    return value


def atomic_no_churn_write(path: Path, payload: dict[str, Any]) -> str:
    encoded = (canonical(payload) + "\n").encode()
    if path.exists():
        if path.read_bytes() == encoded:
            return "noop"
        raise VideoUnderstandingError("understanding_package_conflict")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(name):
            os.unlink(name)
    if path.read_bytes() != encoded:
        raise VideoUnderstandingError("understanding_package_readback_unknown")
    return "created"


def materialize(
    *,
    run_id: str,
    business_date: str,
    candidates: list[dict[str, Any]],
    decisions: list[dict[str, Any]],
    packages: list[dict[str, Any]],
    policy: dict[str, Any],
    output_root: Path,
    on_demand_ids: set[str] | None = None,
) -> dict[str, Any]:
    validate_run(run_id, business_date)
    planned = budget_selection(fold_near_duplicates(
        apply_policy_decisions(candidates, decisions, policy)
    ), policy)
    selected = {row["id"]: row for row in planned["selected"]}
    on_demand = on_demand_ids or set()
    for identity in on_demand:
        if identity not in {row["id"] for row in candidates}:
            raise VideoUnderstandingError("on_demand_candidate_missing")
    allowed = set(selected) | on_demand
    results, failures = [], []
    by_id = {row["id"]: row for row in candidates}
    supplied = {f"douyin:{row.get('aweme_id')}": row for row in packages}
    for identity in sorted(allowed):
        candidate = by_id[identity]
        package = supplied.get(identity)
        if not package:
            failures.append({"candidate_id": identity, "failure": "understanding_package_missing"})
            continue
        try:
            checked = validate_package(package, candidate)
            action = atomic_no_churn_write(
                output_root / run_id / "video_understanding" / f"{candidate['aweme_id']}.json",
                checked,
            )
            results.append({"candidate_id": identity, "action": action, "package": checked,
                            "trigger": "on_demand" if identity in on_demand else "automatic"})
            if checked["status"] == "failed":
                failures.append({
                    "candidate_id": identity,
                    "failure": str(checked.get("failure") or "video_understanding_failed"),
                })
        except VideoUnderstandingError as exc:
            failures.append({"candidate_id": identity, "failure": str(exc)})
    return {
        "run_id": run_id,
        "business_date": business_date,
        "policy_id": policy["policy_id"],
        "policy_sha256": digest(policy),
        "plan": planned,
        "understanding_results": results,
        "understanding_failures": failures,
        "completed_count": sum(
            row["package"]["status"] in {"completed", "completed_with_failures"}
            for row in results
        ),
        "failed_count": len(failures),
        "substitute_count": 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--business-date", required=True)
    parser.add_argument("--candidates", required=True)
    parser.add_argument("--decisions", required=True)
    parser.add_argument("--packages", required=True)
    parser.add_argument("--policy", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--on-demand", action="append", default=[])
    args = parser.parse_args()
    try:
        policy = json.loads(Path(args.policy).read_text())
        candidates = merge_candidates(
            json.loads(Path(args.candidates).read_text()), args.run_id
        )
        result = materialize(
            run_id=args.run_id,
            business_date=args.business_date,
            candidates=candidates,
            decisions=json.loads(Path(args.decisions).read_text()),
            packages=json.loads(Path(args.packages).read_text()),
            policy=policy,
            output_root=Path(args.output_root),
            on_demand_ids=set(args.on_demand),
        )
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except (OSError, ValueError, VideoUnderstandingError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
