#!/usr/bin/env python3
"""Read-only recovery feasibility check for preserved successful source artifacts."""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from source_ingestion_lineage import (
    LineageError,
    revalidate_legacy_before_external_write,
    validate_legacy_partial_source_artifact,
    validate_partial_source_artifact,
)


def csv_source_counts(path: Path) -> dict[str, int]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    counts: dict[str, int] = {}
    for row in rows:
        key = str(row.get("来源类型") or "unknown")
        counts[key] = counts.get(key, 0) + 1
    return counts


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe-result", type=Path, required=True)
    parser.add_argument("--douyin-manual", type=Path, required=True)
    parser.add_argument("--incident-content-items", type=Path, required=True)
    parser.add_argument("--incident-today-candidates", type=Path, required=True)
    parser.add_argument("--check-only", action="store_true", required=True)
    parser.add_argument("--legacy-daily-log", type=Path)
    parser.add_argument("--expected-source-run-id")
    parser.add_argument("--locked-legacy-attestation", type=Path)
    args = parser.parse_args()
    try:
        if bool(args.legacy_daily_log) != bool(args.expected_source_run_id):
            raise LineageError("legacy_mode_arguments_incomplete")
        if args.locked_legacy_attestation and not args.legacy_daily_log:
            raise LineageError("legacy_locked_report_without_legacy_mode")
        if args.legacy_daily_log:
            if args.locked_legacy_attestation:
                locked = json.loads(args.locked_legacy_attestation.read_text(encoding="utf-8"))
                if isinstance(locked, dict) and isinstance(locked.get("preserved_douyin_success_artifact"), dict):
                    locked = locked["preserved_douyin_success_artifact"]
                douyin = revalidate_legacy_before_external_write(
                    args.legacy_daily_log, args.probe_result, args.douyin_manual,
                    expected_run_id=str(args.expected_source_run_id), attested_report=locked,
                )
                douyin["prewrite_revalidated"] = True
            else:
                douyin = validate_legacy_partial_source_artifact(args.legacy_daily_log, args.probe_result, args.douyin_manual, expected_run_id=str(args.expected_source_run_id))
        else:
            probe = json.loads(args.probe_result.read_text(encoding="utf-8"))
            douyin = validate_partial_source_artifact(probe, args.douyin_manual)
        result = {
            "ok": True,
            "check_only": True,
            "incident_run_must_not_resume_stage2": True,
            "preserved_douyin_success_artifact": douyin,
            "incident_content_source_counts": csv_source_counts(args.incident_content_items),
            "incident_candidate_source_counts": csv_source_counts(args.incident_today_candidates),
            "wechat_cache_policy": "exclude_until_fresh_provider_refresh",
            "recovery_requires_new_versioned_run": True,
            "collection_started": False,
            "writes_feishu": False,
            "sends_topic_card": False,
            "triggers_script_generation": False,
        }
    except (OSError, json.JSONDecodeError, LineageError, KeyError) as exc:
        result = {"ok": False, "check_only": True, "error": f"{type(exc).__name__}:{exc}"}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 4


if __name__ == "__main__":
    raise SystemExit(main())
