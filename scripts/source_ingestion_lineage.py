#!/usr/bin/env python3
"""Fail-closed lineage checks for partial source artifacts and downstream inputs."""
from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import stat
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


class LineageError(RuntimeError):
    pass


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise LineageError(f"manual_artifact_missing:{path}")
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise LineageError(f"manual_artifact_malformed:{line_number}") from exc
        if not isinstance(row, dict):
            raise LineageError(f"manual_artifact_non_object:{line_number}")
        rows.append(row)
    return rows


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise LineageError(f"downstream_artifact_missing:{path}")
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def row_identity(row: dict[str, Any]) -> str:
    return str(row.get("内容指纹") or "").strip()


def row_account(row: dict[str, Any]) -> str:
    return str(row.get("账号名/公众号名") or row.get("原始来源账号") or "").strip()


def artifact_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def safe_legacy_artifact(path: Path, expected: Path, reason: str) -> os.stat_result:
    if not path.is_absolute() or path != expected or path.is_symlink() or path.resolve() != expected:
        raise LineageError(f"{reason}_path_mismatch")
    try:
        info = path.lstat()
    except OSError as exc:
        raise LineageError(f"{reason}_missing") from exc
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid() or info.st_mode & 0o022:
        raise LineageError(f"{reason}_identity_unsafe")
    return info


def legacy_time(value: Any, reason: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise LineageError(reason)
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise LineageError(reason) from exc
    return parsed.replace(tzinfo=ZoneInfo("Asia/Shanghai")) if parsed.tzinfo is None else parsed


def validate_legacy_partial_source_artifact(
    daily_log_path: Path, probe_path: Path, manual_path: Path, *, expected_run_id: str,
) -> dict[str, Any]:
    if re.fullmatch(r"run_\d{8}_\d{6}", expected_run_id) is None:
        raise LineageError("legacy_expected_run_id_invalid")
    expected_date = f"{expected_run_id[4:8]}-{expected_run_id[8:10]}-{expected_run_id[10:12]}"
    production_root = daily_log_path.parent.parent.parent.resolve()
    expected_daily = production_root / "output" / "logs" / f"daily_pipeline_{expected_date}.json"
    expected_probe = production_root / "output" / "spikes" / "douyin_cdp_source_watch_probe" / "cdp_probe_results.json"
    expected_manual = production_root / "output" / "spikes" / "douyin_cdp_source_watch_probe" / "content_items_manual.jsonl"
    daily_stat = safe_legacy_artifact(daily_log_path, expected_daily, "legacy_daily_log")
    probe_stat = safe_legacy_artifact(probe_path, expected_probe, "legacy_probe")
    manual_stat = safe_legacy_artifact(manual_path, expected_manual, "legacy_manual")
    try:
        daily = json.loads(daily_log_path.read_text(encoding="utf-8")); probe = json.loads(probe_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        raise LineageError("legacy_evidence_malformed") from exc
    if not isinstance(daily, dict) or not isinstance(probe, dict):
        raise LineageError("legacy_evidence_non_object")
    if "run_id" in probe or "manual_artifact" in probe:
        raise LineageError("legacy_mode_rejected_for_native_artifact")
    if str(daily.get("run_id") or "") != expected_run_id:
        raise LineageError("legacy_daily_run_mismatch")
    started_date = expected_run_id[4:12]
    if started_date != expected_date.replace("-", ""):
        raise LineageError("legacy_run_date_mismatch")
    steps = [row for row in daily.get("steps", []) if isinstance(row, dict) and row.get("name") == "fetch daily Douyin homepage title/caption samples through Chrome CDP"]
    if len(steps) != 1:
        raise LineageError("legacy_douyin_step_not_unique")
    step = steps[0]
    script = production_root / "scripts" / "douyin_cdp_source_watch_probe.mjs"
    expected_command = ["node", str(script), "--cdp", "http://127.0.0.1:9333", "--account-limit", "0", "--video-limit", "3", "--retries", "2"]
    if step.get("command") != expected_command:
        raise LineageError("legacy_douyin_command_mismatch")
    if int(step.get("returncode") if step.get("returncode") is not None else -1) != 0 or int(step.get("optional_returncode") if step.get("optional_returncode") is not None else -1) != 3 or step.get("optional_failed") is not True:
        raise LineageError("legacy_step_terminal_status_mismatch")
    if probe.get("status") != "completed_with_failures":
        raise LineageError("legacy_probe_terminal_status_mismatch")
    started = legacy_time(step.get("started_at"), "legacy_step_time_invalid")
    generated = legacy_time(daily.get("generated_at"), "legacy_generated_time_invalid")
    if started >= generated or started.date().isoformat() != expected_date or generated.date().isoformat() != expected_date:
        raise LineageError("legacy_time_window_invalid")
    for info in (probe_stat, manual_stat):
        if not (started.timestamp() <= info.st_mtime <= generated.timestamp()):
            raise LineageError("legacy_artifact_time_outside_run")
    resolver = probe.get("resolver") if isinstance(probe.get("resolver"), dict) else {}
    if str(resolver.get("manual_jsonl") or "") != str(expected_manual):
        raise LineageError("legacy_resolver_identity_mismatch")
    coverage = probe.get("coverage") if isinstance(probe.get("coverage"), dict) else {}
    coverage_values = [coverage.get(key) for key in ("planned_accounts", "attempted_accounts", "successful_accounts", "failed_account_count")]
    if any(type(value) is not int or value < 0 for value in coverage_values):
        raise LineageError("legacy_account_coverage_mismatch")
    planned, attempted, successful, failed_count = coverage_values
    if planned == 0 or attempted != planned or successful + failed_count != attempted:
        raise LineageError("legacy_account_coverage_mismatch")
    invariants = coverage.get("invariants") if isinstance(coverage.get("invariants"), dict) else {}
    if not all(invariants.get(key) is True for key in ("attempted_equals_planned", "success_plus_failed_equals_attempted", "account_lineage_unique_and_complete")) or not bool((probe.get("item_lineage") or {}).get("ok")):
        raise LineageError("legacy_lineage_invariant_failed")
    failures = coverage.get("failed_accounts") if isinstance(coverage.get("failed_accounts"), list) else []
    if len(failures) != failed_count or any(not isinstance(row, dict) or type(row.get("artifact_count")) is not int or row["artifact_count"] != 0 for row in failures):
        raise LineageError("legacy_failed_account_artifact_leak")
    failed_names = {str(row.get("account_name") or "") for row in failures}
    counts = coverage.get("per_account_artifact_counts") if isinstance(coverage.get("per_account_artifact_counts"), dict) else {}
    rows = read_jsonl(manual_path)
    homepage_items = resolver.get("homepage_card_items")
    if type(homepage_items) is not int or homepage_items <= 0 or len(rows) != homepage_items:
        raise LineageError("legacy_manual_row_count_mismatch")
    fingerprints = [row_identity(row) for row in rows]
    if any(not value for value in fingerprints) or len(set(fingerprints)) != len(fingerprints):
        raise LineageError("legacy_manual_fingerprint_invalid")
    actual_counts: dict[str, int] = {}
    mapping: dict[str, str] = {}
    for row, fingerprint in zip(rows, fingerprints):
        row_run = str(row.get("运行批次") or "")
        if row_run and row_run != expected_run_id:
            raise LineageError("legacy_manual_row_run_mismatch")
        account = row_account(row)
        if not account or account not in counts or account in failed_names:
            raise LineageError("legacy_manual_account_contamination")
        actual_counts[account] = actual_counts.get(account, 0) + 1; mapping[fingerprint] = account
    expected_counts = {str(name): int(value) for name, value in counts.items() if name not in failed_names}
    if len(expected_counts) != successful or actual_counts != expected_counts or sum(actual_counts.values()) != len(rows):
        raise LineageError("legacy_per_account_count_mismatch")
    command_digest = hashlib.sha256(json.dumps(expected_command, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    return {
        "ok": True, "source_run_id": expected_run_id, "run_id": expected_run_id,
        "legacy_attestation_verified": True, "evidence_basis": "daily_log_probe_manual_v1", "evidence_version": 1,
        "collection_status": "completed_with_failures", "planned_accounts": planned, "attempted_accounts": attempted,
        "successful_accounts": successful, "failed_accounts": failed_count, "successful_item_count": len(rows),
        "daily_log": {"path": str(expected_daily), "sha256": artifact_sha256(expected_daily), "size": daily_stat.st_size, "mtime_ns": daily_stat.st_mtime_ns},
        "probe": {"path": str(expected_probe), "sha256": artifact_sha256(expected_probe), "size": probe_stat.st_size, "mtime_ns": probe_stat.st_mtime_ns},
        "manual": {"path": str(expected_manual), "sha256": artifact_sha256(expected_manual), "size": manual_stat.st_size, "mtime_ns": manual_stat.st_mtime_ns, "row_count": len(rows)},
        "command_identity_sha256": command_digest, "started_at": started.isoformat(), "generated_at": generated.isoformat(),
        "ordered_fingerprints": fingerprints, "fingerprint_accounts": mapping, "failed_account_names": sorted(failed_names),
        "stdout_corroboration": {
            "is_identity_anchor": False,
            "sha256": hashlib.sha256(str(step.get("stdout") or "").encode("utf-8")).hexdigest(),
            "size": len(str(step.get("stdout") or "").encode("utf-8")),
        },
    }


def revalidate_legacy_before_external_write(
    daily_log_path: Path,
    probe_path: Path,
    manual_path: Path,
    *,
    expected_run_id: str,
    attested_report: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(attested_report, dict) or attested_report.get("legacy_attestation_verified") is not True:
        raise LineageError("legacy_attestation_report_invalid")
    current = validate_legacy_partial_source_artifact(
        daily_log_path, probe_path, manual_path, expected_run_id=expected_run_id,
    )
    locked_fields = (
        "source_run_id", "evidence_basis", "evidence_version", "planned_accounts", "attempted_accounts",
        "successful_accounts", "failed_accounts", "successful_item_count", "daily_log", "probe", "manual",
        "command_identity_sha256", "started_at", "generated_at", "ordered_fingerprints",
        "fingerprint_accounts", "failed_account_names",
    )
    if any(current.get(field) != attested_report.get(field) for field in locked_fields):
        raise LineageError("legacy_attestation_drift")
    return current


def validate_partial_source_artifact(
    probe: dict[str, Any], manual_path: Path, *, expected_run_id: str | None = None,
) -> dict[str, Any]:
    coverage = probe.get("coverage") if isinstance(probe.get("coverage"), dict) else {}
    invariants = coverage.get("invariants") if isinstance(coverage.get("invariants"), dict) else {}
    failures = coverage.get("failed_accounts") if isinstance(coverage.get("failed_accounts"), list) else []
    counts = coverage.get("per_account_artifact_counts") if isinstance(coverage.get("per_account_artifact_counts"), dict) else {}
    planned = int(coverage.get("planned_accounts") or 0)
    attempted = int(coverage.get("attempted_accounts") or 0)
    succeeded = int(coverage.get("successful_accounts") or 0)
    failed = int(coverage.get("failed_account_count") or 0)
    if str(probe.get("status") or "") not in {"completed", "completed_with_failures"}:
        raise LineageError("probe_not_terminal")
    run_id = str(probe.get("run_id") or "").strip()
    if expected_run_id and run_id != expected_run_id:
        raise LineageError("probe_run_identity_mismatch")
    if planned != attempted or succeeded + failed != attempted:
        raise LineageError("account_plan_incomplete")
    if not all(bool(invariants.get(key)) for key in (
        "attempted_equals_planned", "success_plus_failed_equals_attempted", "account_lineage_unique_and_complete"
    )):
        raise LineageError("account_lineage_invariant_failed")
    failed_names = {str(row.get("account_name") or "") for row in failures}
    if any(int(row.get("artifact_count") or 0) != 0 for row in failures):
        raise LineageError("failed_account_artifact_leak")
    rows = read_jsonl(manual_path)
    artifact = probe.get("manual_artifact") if isinstance(probe.get("manual_artifact"), dict) else {}
    expected_path = str(manual_path.resolve())
    if not artifact:
        raise LineageError("manual_artifact_identity_missing")
    if str(artifact.get("run_id") or "") != run_id:
        raise LineageError("manual_artifact_run_mismatch")
    if str(Path(str(artifact.get("path") or "")).expanduser().resolve()) != expected_path:
        raise LineageError("manual_artifact_path_mismatch")
    actual_sha = artifact_sha256(manual_path)
    if str(artifact.get("sha256") or "") != actual_sha:
        raise LineageError("manual_artifact_hash_mismatch")
    if int(artifact.get("size") or -1) != manual_path.stat().st_size:
        raise LineageError("manual_artifact_size_mismatch")
    if int(artifact.get("row_count") or -1) != len(rows):
        raise LineageError("manual_artifact_row_count_mismatch")
    if any(str(row.get("运行批次") or "") != run_id for row in rows):
        raise LineageError("manual_row_run_identity_mismatch")
    fingerprints = [row_identity(row) for row in rows]
    if not rows or any(not value for value in fingerprints):
        raise LineageError("successful_artifact_empty_or_unidentified")
    if len(fingerprints) != len(set(fingerprints)):
        raise LineageError("duplicate_success_fingerprint")
    if any(row_account(row) in failed_names for row in rows):
        raise LineageError("failed_account_present_in_success_artifact")
    actual_counts: dict[str, int] = {}
    for row in rows:
        account = row_account(row)
        if not account or account not in counts:
            raise LineageError("unknown_or_missing_account_lineage")
        actual_counts[account] = actual_counts.get(account, 0) + 1
    expected_counts = {str(name): int(count or 0) for name, count in counts.items() if str(name) not in failed_names}
    if actual_counts != expected_counts:
        raise LineageError("per_account_artifact_count_mismatch")
    if not bool((probe.get("item_lineage") or {}).get("ok")):
        raise LineageError("probe_item_lineage_failed")
    return {
        "ok": True,
        "collection_status": str(probe.get("status")),
        "planned_accounts": planned,
        "attempted_accounts": attempted,
        "successful_accounts": succeeded,
        "failed_accounts": failed,
        "successful_item_count": len(rows),
        "manual_path": str(manual_path.resolve()),
        "manual_sha256": artifact_sha256(manual_path),
        "run_id": run_id,
        "manual_artifact_identity_verified": True,
        "ordered_fingerprints": fingerprints,
        "fingerprint_accounts": {row_identity(row): row_account(row) for row in rows},
        "failed_account_names": sorted(failed_names),
    }


def validate_ingestion_bijection(
    source_report: dict[str, Any], combined_path: Path, content_items_path: Path,
    comparison_path: Path | None = None, shortlist_path: Path | None = None,
) -> dict[str, Any]:
    source = list(source_report.get("ordered_fingerprints") or [])
    combined_rows = read_jsonl(combined_path)
    content_rows = read_csv_rows(content_items_path)
    combined = [row_identity(row) for row in combined_rows]
    content = [row_identity(row) for row in content_rows]
    if any(not value for value in combined + content):
        raise LineageError("downstream_identity_missing")
    combined_set, content_set = set(combined), set(content)
    missing_combined = [value for value in source if value not in combined_set]
    missing_content = [value for value in source if value not in content_set]
    if missing_combined or missing_content:
        raise LineageError("successful_source_artifact_dropped")
    fingerprint_accounts = dict(source_report.get("fingerprint_accounts") or {})
    for fingerprint in source:
        combined_matches = [row for row in combined_rows if row_identity(row) == fingerprint]
        content_matches = [row for row in content_rows if row_identity(row) == fingerprint]
        if len(combined_matches) != 1 or len(content_matches) != 1:
            raise LineageError("source_fingerprint_not_bijective")
        expected_account = str(fingerprint_accounts.get(fingerprint) or "")
        if row_account(combined_matches[0]) != expected_account or row_account(content_matches[0]) != expected_account:
            raise LineageError("cross_account_lineage_contamination")
    mapping = [{"source_fingerprint": value, "surviving_fingerprint": value} for value in source]
    comparison: list[str] = []
    shortlist: list[str] = []
    if comparison_path is not None:
        comparison = [row_identity(row) for row in read_csv_rows(comparison_path)]
        if any(not value for value in comparison) or any(value not in set(comparison) for value in source):
            raise LineageError("comparison_universe_fingerprint_drift")
    if shortlist_path is not None:
        shortlist = [row_identity(row) for row in read_csv_rows(shortlist_path)]
        if any(not value for value in shortlist) or any(value not in content_set for value in shortlist):
            raise LineageError("shortlist_unknown_or_unidentified_fingerprint")
    return {
        "ok": True,
        "source_count": len(source),
        "combined_count": len(combined_rows),
        "content_items_count": len(content_rows),
        "source_to_survivor_count": len(mapping),
        "dedupe_mapping": mapping,
        "combined_sha256": artifact_sha256(combined_path),
        "content_items_sha256": artifact_sha256(content_items_path),
        "feishu_03_planned_fingerprints": source,
        "comparison_universe_count": len(comparison),
        "shortlist_count": len(shortlist),
        "shortlist_source_fingerprints": [value for value in shortlist if value in set(source)],
    }


def planned_feishu_identity(source_report: dict[str, Any], run_id: str) -> dict[str, Any]:
    ordered = list(source_report.get("ordered_fingerprints") or [])
    payload = json.dumps({"run_id": run_id, "ordered_fingerprints": ordered}, ensure_ascii=False, separators=(",", ":"))
    return {
        "run_id": run_id,
        "ordered_fingerprints": ordered,
        "identity_sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
        "count": len(ordered),
    }


def validate_feishu_readback_identity(
    source_report: dict[str, Any], read_back: dict[str, Any] | None, run_id: str, *, write_mode: bool,
) -> dict[str, Any]:
    planned = planned_feishu_identity(source_report, run_id)
    if not write_mode:
        return {"ok": True, "mode": "dry_run", "planned_identity": planned, "read_back_required": False}
    if not isinstance(read_back, dict) or not read_back.get("ok"):
        raise LineageError("feishu_03_readback_missing")
    if str(read_back.get("run_id") or "") != run_id:
        raise LineageError("feishu_03_readback_run_mismatch")
    read_fingerprints = list(read_back.get("ordered_fingerprints") or [])
    for fingerprint in planned["ordered_fingerprints"]:
        if read_fingerprints.count(fingerprint) != 1:
            raise LineageError("feishu_03_readback_identity_mismatch")
    return {"ok": True, "mode": "write", "planned_identity": planned, "read_back_required": True}
