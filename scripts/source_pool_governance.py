#!/usr/bin/env python3
"""AR-026 source-pool dry-run governance and coverage reporting.

Default mode is local/config only. `--from-feishu` performs a read-only 01
来源与采样 read, then produces the same dry-run report without updating Feishu.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

import push_to_feishu as feishu
import topic_flow_rework as flow
from feishu_table_registry import resolve_table_id, table_name
from local_env import load_local_env
from sync_source_sampling import all_records, list_fields, list_tables


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "content_sources.yaml"
DEFAULT_OUT = ROOT / "output" / "source_governance"
TABLE_KEY = "source_sampling"
CONTENT_TABLE_KEY = "content_inbox"
POLLUTED_NAMES = set(flow.POLLUTED_SOURCE_NAMES)
MIGRATION_FIELDS = {
    "来源角色": "quarantined_source",
    "默认启用": "停用",
    "是否参与主采样": "否",
    "优先级": "low",
}


def cell_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "、".join(cell_text(item) for item in value if cell_text(item))
    if isinstance(value, dict):
        return str(value.get("text") or value.get("name") or value.get("value") or "")
    return str(value)


def source_from_feishu_record(record: dict[str, Any]) -> dict[str, Any]:
    fields = record.get("fields", {})
    return {
        "id": record.get("record_id", ""),
        "source_group": cell_text(fields.get("来源角色")),
        "source_role": cell_text(fields.get("来源角色")),
        "is_main_competitor": cell_text(fields.get("来源角色")) == "current_main_competitor",
        "participates_main_sampling": cell_text(fields.get("是否参与主采样")) in {"是", "启用", "true", "True", "1"},
        "default_enabled": cell_text(fields.get("默认启用")) in {"是", "启用", "true", "True", "1"},
        "source_type": "competitor_video" if cell_text(fields.get("平台")) == "抖音" else "competitor_article",
        "platform": cell_text(fields.get("平台")),
        "account_name": cell_text(fields.get("名称")),
        "url": cell_text(fields.get("主页链接")),
        "fetch_method": cell_text(fields.get("抓取方式")),
        "priority": cell_text(fields.get("优先级")),
        "remarks": cell_text(fields.get("备注")),
    }


def load_config_sources(path: Path) -> list[dict[str, Any]]:
    return flow.load_json_config(path).get("sources", [])


def canonical_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_feishu_context() -> dict[str, Any]:
    load_local_env(required=True)
    app_token = os.getenv("FEISHU_BASE_APP_TOKEN")
    if not app_token:
        raise SystemExit("FEISHU_BASE_APP_TOKEN is required for --from-feishu")
    token = feishu.tenant_token()
    tables = list_tables(token, app_token)
    table_id = resolve_table_id(tables, TABLE_KEY)
    content_table_id = resolve_table_id(tables, CONTENT_TABLE_KEY)
    if not table_id or not content_table_id:
        raise SystemExit(f"Missing Feishu table: {table_name(TABLE_KEY)}")
    return {
        "source_table_id": table_id,
        "source_fields": list_fields(token, app_token, table_id),
        "source_records": all_records(token, app_token, table_id),
        "content_table_id": content_table_id,
        "content_fields": list_fields(token, app_token, content_table_id),
        "content_records": all_records(token, app_token, content_table_id),
    }


def migration_plan(records: list[dict[str, Any]]) -> dict[str, Any]:
    ordered = sorted(records, key=lambda row: str(row.get("record_id") or ""))
    targets = [
        row for row in ordered
        if cell_text((row.get("fields") or {}).get("名称")) in POLLUTED_NAMES
    ]
    target_names = [cell_text((row.get("fields") or {}).get("名称")) for row in targets]
    duplicates = sorted({name for name in target_names if target_names.count(name) > 1})
    missing = sorted(POLLUTED_NAMES - set(target_names))
    target_ids = {str(row.get("record_id") or "") for row in targets}
    untouched = [row for row in ordered if str(row.get("record_id") or "") not in target_ids]
    mutations = []
    rollback = []
    for row in targets:
        fields = row.get("fields") or {}
        record_id = str(row.get("record_id") or "")
        mutations.append({
            "record_id": record_id,
            "account_name": cell_text(fields.get("名称")),
            "fields": dict(MIGRATION_FIELDS),
        })
        rollback.append({
            "record_id": record_id,
            "account_name": cell_text(fields.get("名称")),
            "fields": {name: fields.get(name) for name in MIGRATION_FIELDS},
        })
    return {
        "ok": len(ordered) == 51 and len(targets) == 8 and not duplicates and not missing,
        "mode": "GET-only-plan",
        "record_count": len(ordered),
        "target_count": len(targets),
        "untouched_count": len(untouched),
        "missing_polluted_names": missing,
        "duplicate_polluted_names": duplicates,
        "before_snapshot_sha256": canonical_hash(ordered),
        "target_snapshot_sha256": canonical_hash(targets),
        "untouched_snapshot_sha256": canonical_hash(untouched),
        "planned_mutations": mutations,
        "readback_contract": mutations,
        "rollback_payload": rollback,
        "writes_feishu": False,
    }


def post_migration_sources(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sources = []
    for record in records:
        copied = {**record, "fields": dict(record.get("fields") or {})}
        if cell_text(copied["fields"].get("名称")) in POLLUTED_NAMES:
            copied["fields"].update(MIGRATION_FIELDS)
        sources.append(source_from_feishu_record(copied))
    return sources


def historical_content_audit(records: list[dict[str, Any]]) -> dict[str, Any]:
    ordered = sorted(records, key=lambda row: str(row.get("record_id") or ""))
    matches = []
    for row in ordered:
        text = json.dumps(row.get("fields") or {}, ensure_ascii=False, sort_keys=True)
        matched = sorted(name for name in POLLUTED_NAMES if name in text)
        if matched:
            matches.append({"record_id": row.get("record_id", ""), "matched_names": matched})
    return {
        "mode": "GET-only",
        "record_count": len(ordered),
        "snapshot_sha256": canonical_hash(ordered),
        "polluted_name_historical_match_count": len(matches),
        "polluted_name_historical_matches": matches,
        "writes_feishu": False,
        "touches_historical_03": False,
    }


def write_feishu_evidence(context: dict[str, Any], out_dir: Path) -> dict[str, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    records = context["source_records"]
    plan = migration_plan(records)
    artifacts: dict[str, tuple[str, Any]] = {
        "before_snapshot": ("production_01_before_snapshot.json", {
            "mode": "GET-only",
            "table_id": context["source_table_id"],
            "field_count": len(context["source_fields"]),
            "record_count": len(records),
            "snapshot_sha256": plan["before_snapshot_sha256"],
            "records": sorted(records, key=lambda row: str(row.get("record_id") or "")),
            "writes_feishu": False,
        }),
        "migration_plan": ("production_01_planned_mutations.json", plan),
        "rollback_payload": ("production_01_rollback_payload.json", {
            "mode": "rollback-plan-only",
            "records": plan["rollback_payload"],
            "writes_feishu": False,
        }),
        "historical_03": ("production_03_readonly_snapshot.json", {
            **historical_content_audit(context["content_records"]),
            "table_id": context["content_table_id"],
            "field_count": len(context["content_fields"]),
        }),
        "post_migration_config": ("post_migration_source_config.json", {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "source": "production_01_get_only_planned_state",
            "sources": post_migration_sources(records),
        }),
    }
    paths = {}
    for key, (filename, payload) in artifacts.items():
        path = out_dir / filename
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        paths[key] = str(path)
    return paths


def zero_fallback_audit(root: Path = ROOT) -> dict[str, Any]:
    node = (root / "scripts" / "douyin_cdp_source_watch_probe.mjs").read_text(encoding="utf-8")
    daily = (root / "scripts" / "daily_pipeline.py").read_text(encoding="utf-8")
    outer = (root / "scripts" / "run_daily_collection_job.py").read_text(encoding="utf-8")
    checks = {
        "scheduled_runs_one_current_douyin_attempt": (
            outer.count("daily_pipeline.py") == 1
            and daily.count("douyin_cdp_source_watch_probe.mjs") == 1
            and "--force-fetch-douyin" not in outer + daily
        ),
        "outer_rejects_limited_plan_before_env": "validate_account_limit_argv(sys.argv" in outer and outer.index("validate_account_limit_argv(sys.argv") < outer.index("load_local_env()"),
        "daily_rejects_limited_plan_before_env": "validate_account_limit_argv(sys.argv" in daily and daily.index("validate_account_limit_argv(sys.argv") < daily.index("load_local_env()"),
        "node_rejects_limited_plan_before_output": "validateFullAccountLimitArgs(process.argv.slice(2))" in node and node.index("validateFullAccountLimitArgs(process.argv.slice(2))") < node.index("fs.mkdirSync(options.outDir"),
        "no_subset_account_alias": "--only-account-names" not in node + daily + outer and "onlyAccountNames" not in node,
        "no_account_limit_environment_override": "DOUYIN_ACCOUNT_LIMIT" not in node + daily + outer,
        "no_account_plan_truncation": "rows.slice(0" not in node,
        "canonical_cdp_only": "127.0.0.1:9333" in node and "127.0.0.1:9222" not in node,
        "no_legacy_http_probe_in_scheduled_path": "douyin_source_watch_probe.py" not in outer + daily,
        "no_editorial_fallback_item_builder": "buildFallbackContentItem" not in node and "fallbackItems" not in node,
        "check_only_does_not_contact_cdp": "cdp_contacted: false" in node and node.index("if (options.checkOnly)") < node.index("let version;"),
    }
    return {
        "ok": all(checks.values()),
        "prohibited_path_count": sum(1 for value in checks.values() if not value),
        "checks": checks,
    }


def load_probe_rows(path: str) -> list[dict[str, Any]]:
    if not path:
        return []
    file_path = Path(path).expanduser()
    if not file_path.exists():
        raise SystemExit(f"Missing probe report: {file_path}")
    if file_path.suffix.lower() == ".csv":
        with file_path.open("r", encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle))
    data = json.loads(file_path.read_text(encoding="utf-8"))
    if isinstance(data, dict) and isinstance(data.get("rows"), list):
        return data["rows"]
    if isinstance(data, list):
        return data
    return []


def write_report(report: dict[str, Any], out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "source_governance_report.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def write_release_sync_plan(report: dict[str, Any], out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "polluted_source_release_sync_plan.md"
    polluted = report.get("governance", {}).get("polluted_matches", [])
    lines = [
        "# AR-026 Polluted Source Release Sync Plan",
        "",
        "Scope: Feishu 01 source-pool release action only. Do not touch historical 03 records.",
        "",
        "## Dry-run Result",
        f"- polluted_source_count: {len(polluted)}",
        "- target state: source_role=quarantined_source, default_enabled=false, participates_main_sampling=false, priority=low",
        "- production write: not performed by this dev tool run",
        "",
        "## Sources",
    ]
    for row in polluted:
        lines.append(f"- {row.get('name')} ({row.get('platform')}) current_role={row.get('role')}")
    lines.extend([
        "",
        "## Release Steps",
        "1. Production/PM runs this tool in read-only mode against Feishu 01 and reviews the JSON report.",
        "2. Confirm the eight source names match the user-approved screenshot-pollution list exactly.",
        "3. Run the separately authorized Feishu 01 sync/reconcile path to update only these source rows.",
        "4. Re-run source governance read-only report and confirm active_competitor_count excludes quarantined sources.",
        "5. Run collection preview/check without writing 03 to confirm planned account coverage stays full-account.",
        "",
        "## Rollback",
        "If a source is quarantined by mistake, restore source_role/default_enabled/participates_main_sampling from the previous Feishu 01 row values; historical 03 remains untouched.",
    ])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="AR-026 source governance dry-run and full-account coverage report.")
    parser.add_argument("--config", default=str(CONFIG))
    parser.add_argument("--from-feishu", action="store_true", help="Read Feishu 01 only; never writes.")
    parser.add_argument("--probe-report", default="", help="Optional Douyin probe JSON/CSV to merge into coverage.")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT))
    args = parser.parse_args()

    context = load_feishu_context() if args.from_feishu else None
    sources = [source_from_feishu_record(record) for record in context["source_records"]] if context else load_config_sources(Path(args.config))
    probe_rows = load_probe_rows(args.probe_report)
    governance = flow.source_governance_plan(sources)
    coverage = flow.collection_coverage_report(sources, probe_rows)
    report = {
        "ok": True,
        "mode": "read-only-feishu" if args.from_feishu else "local-config",
        "dry_run_only": True,
        "source_count": len(sources),
        "governance": governance,
        "coverage": coverage,
        "safety": {
            "writes_feishu": False,
            "deletes_sources": False,
            "touches_historical_03": False,
        },
        "zero_fallback_audit": zero_fallback_audit(),
    }
    report["ok"] = report["ok"] and report["zero_fallback_audit"]["ok"]
    out_dir = Path(args.out_dir)
    if context:
        report["feishu_evidence"] = write_feishu_evidence(context, out_dir)
        plan = migration_plan(context["source_records"])
        report["ok"] = report["ok"] and plan["ok"] and governance["active_competitor_count"] == 33
        report["production_01_migration"] = {
            key: plan[key]
            for key in (
                "ok", "record_count", "target_count", "untouched_count",
                "missing_polluted_names", "duplicate_polluted_names",
                "before_snapshot_sha256", "target_snapshot_sha256", "untouched_snapshot_sha256",
            )
        }
    plan_path = write_release_sync_plan(report, out_dir)
    report["release_sync_plan"] = str(plan_path)
    report["output"] = str(out_dir / "source_governance_report.json")
    path = write_report(report, out_dir)
    audit_path = out_dir / "zero_fallback_audit.json"
    audit_path.write_text(json.dumps(report["zero_fallback_audit"], ensure_ascii=False, indent=2), encoding="utf-8")
    report["zero_fallback_audit_path"] = str(audit_path)
    write_report(report, out_dir)
    report["output"] = str(path)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
