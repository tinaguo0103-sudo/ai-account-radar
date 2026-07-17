#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable

import content_sampler
import push_to_feishu as feishu
from source_ingestion_lineage import validate_feishu_readback_identity


ROOT = Path(__file__).resolve().parents[1]
REQUIRED_IDENTITY_FIELDS = {"\u5185\u5bb9\u6307\u7eb9", "\u8fd0\u884c\u6279\u6b21", "\u6700\u8fd1\u53c2\u4e0e\u8fd0\u884c\u6279\u6b21"}


class ReconcileError(RuntimeError):
    pass


class ReconcileAbort(ReconcileError):
    def __init__(self, reason: str, report: dict[str, Any]) -> None:
        super().__init__(reason)
        self.report = report


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_planned_items(items: list[content_sampler.ContentItem], run_id: str) -> list[str]:
    if not run_id or not items:
        raise ReconcileError("planned_identity_missing")
    fingerprints = [item.fingerprint for item in items]
    if any(not isinstance(value, str) or not value for value in fingerprints):
        raise ReconcileError("planned_fingerprint_invalid")
    if len(fingerprints) != len(set(fingerprints)):
        raise ReconcileError("planned_fingerprint_duplicate")
    return fingerprints


def classify_records(planned_fingerprints: list[str], records: list[dict[str, Any]], run_id: str) -> dict[str, Any]:
    if not isinstance(records, list):
        raise ReconcileError("readback_schema_invalid")
    by_fingerprint: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get("fields"), dict):
            raise ReconcileError("readback_schema_invalid")
        value = record["fields"].get("\u5185\u5bb9\u6307\u7eb9")
        if value in (None, ""):
            continue
        if not isinstance(value, str):
            raise ReconcileError("readback_fingerprint_invalid")
        by_fingerprint[value].append(record)

    exact_unique: list[str] = []
    missing: list[str] = []
    duplicates: dict[str, list[str]] = {}
    wrong_run: list[str] = []
    for fingerprint in planned_fingerprints:
        matches = by_fingerprint.get(fingerprint, [])
        if not matches:
            missing.append(fingerprint)
        elif len(matches) > 1:
            duplicates[fingerprint] = [str(row.get("record_id") or row.get("id") or "") for row in matches]
        else:
            fields = matches[0]["fields"]
            run_values = {str(fields.get("\u8fd0\u884c\u6279\u6b21") or ""), str(fields.get("\u6700\u8fd1\u53c2\u4e0e\u8fd0\u884c\u6279\u6b21") or "")}
            (exact_unique if run_id in run_values else wrong_run).append(fingerprint)
    return {
        "planned_count": len(planned_fingerprints),
        "existing_unique_count": len(exact_unique),
        "missing_count": len(missing),
        "actual_duplicate_count": len(duplicates),
        "wrong_run_count": len(wrong_run),
        "exact_unique_fingerprints": exact_unique,
        "missing_fingerprints": missing,
        "duplicate_fingerprints": list(duplicates),
        "duplicate_record_ids": duplicates,
        "wrong_run_fingerprints": wrong_run,
    }


def require_reconcilable(classification: dict[str, Any]) -> None:
    if classification["actual_duplicate_count"]:
        raise ReconcileError("actual_duplicate_identity")
    if classification["wrong_run_count"]:
        raise ReconcileError("wrong_run_identity")


def exact_fingerprint_state(records: list[dict[str, Any]], fingerprint: str, run_id: str) -> str:
    state = classify_records([fingerprint], records, run_id)
    if state["actual_duplicate_count"]:
        return "duplicate"
    if state["wrong_run_count"]:
        return "wrong_run"
    return "committed" if state["existing_unique_count"] == 1 else "absent"


def ambiguous_create_error(exc: BaseException) -> bool:
    text = f"{exc.__class__.__name__}: {exc}".lower()
    return isinstance(exc, ReconcileError) or any(
        marker in text for marker in ("status unknown", "timed out", "timeout", "rate limit", "http 429")
    )


def validate_create_response(payload: Any) -> None:
    if not isinstance(payload, dict) or payload.get("code", 0) not in (0, None):
        raise ReconcileError("create_response_malformed")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise ReconcileError("create_response_malformed")
    record = data.get("record")
    record_id = record.get("record_id") if isinstance(record, dict) else data.get("record_id")
    if not isinstance(record_id, str) or not record_id:
        raise ReconcileError("create_response_malformed")


def reconcile_missing_records(
    items: list[content_sampler.ContentItem], run_id: str, source_closure: dict[str, Any], *,
    read_records: Callable[[], list[dict[str, Any]]],
    create_record: Callable[[dict[str, str]], dict[str, Any]],
    revalidate_plan: Callable[[], None], write: bool,
    max_ambiguous_attempts: int = 2, readback_polls: int = 3,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    fingerprints = validate_planned_items(items, run_id)
    item_by_fingerprint = {item.fingerprint: item for item in items}
    revalidate_plan()
    before = classify_records(fingerprints, read_records(), run_id)
    require_reconcilable(before)
    report: dict[str, Any] = {
        "ok": False, "run_id": run_id, "mode": "write_missing" if write else "check_only",
        "planned": len(fingerprints), "existing": before["existing_unique_count"], "missing": before["missing_count"],
        "attempted": 0, "created": 0, "already_committed": 0, "failed": 0, "outcomes": [],
        "full_writer_called": False, "side_effect_stage": "none",
    }

    def abort(reason: str, *, stage: str) -> None:
        report.update({
            "ok": False,
            "reason": reason,
            "failed": report["failed"] + 1,
            "writes": report["attempted"],
            "side_effect_stage": stage,
        })
        raise ReconcileAbort(reason, report)

    if not write:
        report.update({"ok": True, "writes": 0, "classification": before})
        return report

    revalidate_plan()
    report["side_effect_stage"] = "reconciling_missing"
    for fingerprint in before["missing_fingerprints"]:
        current = exact_fingerprint_state(read_records(), fingerprint, run_id)
        if current == "committed":
            report["already_committed"] += 1
            report["outcomes"].append({"fingerprint": fingerprint, "status": "already_committed", "attempts": 0})
            continue
        if current != "absent":
            abort(f"concurrent_{current}_identity", stage="pre_create_readback_failed")
        fields = content_sampler.item_to_content_inbox_fields(item_by_fingerprint[fingerprint], run_id, is_new=True, duplicate=False)
        outcome = {"fingerprint": fingerprint, "status": "failed", "attempts": 0, "ambiguity_resolved": False}
        for attempt in range(1, max_ambiguous_attempts + 1):
            try:
                revalidate_plan()
            except Exception as exc:  # noqa: BLE001 - preserve exact plan failure after prior writes.
                abort(str(exc), stage="plan_revalidation_failed")
            report["attempted"] += 1
            outcome["attempts"] = attempt
            acknowledged = False
            try:
                validate_create_response(create_record(fields))
                acknowledged = True
            except Exception as exc:  # noqa: BLE001 - exact read-back resolves unknown commit status.
                if not ambiguous_create_error(exc):
                    outcome["reason"] = f"create_hard_failure:{exc.__class__.__name__}"
                    break
            observed = "absent"
            for _ in range(max(1, readback_polls)):
                observed = exact_fingerprint_state(read_records(), fingerprint, run_id)
                if observed != "absent":
                    break
                sleep(0.05)
            if observed == "committed":
                outcome["status"] = "created" if acknowledged else "already_committed_after_ambiguous"
                outcome["ambiguity_resolved"] = not acknowledged
                report["created" if acknowledged else "already_committed"] += 1
                break
            if observed in {"duplicate", "wrong_run"}:
                abort(f"post_create_{observed}_identity", stage="post_create_readback_failed")
            if acknowledged:
                outcome["reason"] = "acknowledged_create_not_visible"
                break
            if attempt >= max_ambiguous_attempts:
                outcome["reason"] = "ambiguous_create_absent_after_bounded_retry"
        if outcome["status"] == "failed":
            report["failed"] += 1
        report["outcomes"].append(outcome)
        if outcome["status"] == "failed":
            break

    final_records = read_records()
    final = classify_records(fingerprints, final_records, run_id)
    if report["failed"]:
        report.update({"classification": final, "side_effect_stage": "partial_write_failed"})
        return report
    try:
        require_reconcilable(final)
    except ReconcileError as exc:
        abort(str(exc), stage="final_readback_failed")
    if final["missing_count"]:
        abort("post_reconcile_missing_identity", stage="final_readback_failed")
    try:
        full_readback = content_sampler.verify_content_ledger_readback(items, final_records, run_id)
        source_projection = validate_feishu_readback_identity(source_closure, full_readback, run_id, write_mode=True)
    except Exception as exc:  # noqa: BLE001 - bind final schema/projection failure to write evidence.
        abort(str(exc), stage="final_projection_failed")
    report.update({"ok": True, "writes": report["attempted"], "classification": final,
                   "full_readback": full_readback, "source_projection": source_projection,
                   "side_effect_stage": "complete"})
    return report


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n"); handle.flush(); os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def validate_local_plan(run_dir: Path, run_id: str, expected_sha256: str, lineage_manifest: Path) -> tuple[list[content_sampler.ContentItem], dict[str, Any]]:
    expected_run_dir = ROOT / "output" / "runs" / run_id
    if run_dir.resolve() != expected_run_dir.resolve():
        raise ReconcileError("run_path_mismatch")
    content_path = run_dir / "content_items.csv"
    if not content_path.is_file() or file_sha256(content_path) != expected_sha256:
        raise ReconcileError("planned_artifact_identity_mismatch")
    items = content_sampler.load_content_items_from_csv(content_path)
    _, closure = content_sampler.validate_source_ingestion_manifest(lineage_manifest, run_dir, run_id)
    validate_planned_items(items, run_id)
    return items, closure


def validate_schema(token: str, app_token: str, table_id: str) -> None:
    fields = content_sampler.fields_by_name(token, app_token, table_id)
    if not REQUIRED_IDENTITY_FIELDS.issubset(fields):
        raise ReconcileError("content_inbox_schema_mismatch")
    if any(fields[name].get("type") != 1 for name in REQUIRED_IDENTITY_FIELDS):
        raise ReconcileError("content_inbox_identity_field_type_mismatch")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--expected-content-items-sha256", required=True)
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--write-missing", action="store_true")
    args = parser.parse_args()
    if args.check_only == args.write_missing:
        parser.error("choose exactly one of --check-only or --write-missing")
    run_dir = ROOT / "output" / "runs" / args.run_id
    lineage_manifest = ROOT / "output" / "recovery" / args.run_id / "douyin_source_lineage.json"
    report_path = run_dir / "content_inbox_reconcile_report.json"
    side_effect_stage = "preflight"
    try:
        items, closure = validate_local_plan(run_dir, args.run_id, args.expected_content_items_sha256, lineage_manifest)

        def revalidate() -> None:
            validate_local_plan(run_dir, args.run_id, args.expected_content_items_sha256, lineage_manifest)

        app_token = content_sampler.require_feishu_env()
        token = feishu.tenant_token()
        table_id = content_sampler.resolve_table_id(content_sampler.list_tables(token, app_token), "content_inbox")
        if not table_id:
            raise ReconcileError("content_inbox_table_missing")
        validate_schema(token, app_token, table_id)

        def create_one(fields: dict[str, str]) -> dict[str, Any]:
            return feishu.request_json("POST", f"/bitable/v1/apps/{app_token}/tables/{table_id}/records", token=token,
                                       body={"fields": {key: fields.get(key, "") for key in content_sampler.CONTENT_INBOX_FIELDS}}, retry=False)

        side_effect_stage = "write_missing" if args.write_missing else "check_only"
        report = reconcile_missing_records(items, args.run_id, closure,
            read_records=lambda: content_sampler.all_records(token, app_token, table_id), create_record=create_one,
            revalidate_plan=revalidate, write=args.write_missing)
        if args.write_missing:
            atomic_write_json(report_path, report)
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
        return 0 if report.get("ok") else 4
    except ReconcileAbort as exc:
        report = exc.report
        if args.write_missing:
            atomic_write_json(report_path, report)
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
        return 4
    except Exception as exc:  # noqa: BLE001 - public recovery CLI is typed and fail-closed.
        print(json.dumps({"ok": False, "run_id": args.run_id, "reason": str(exc), "writes": 0,
                          "side_effect_stage": side_effect_stage}, ensure_ascii=False, sort_keys=True))
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
