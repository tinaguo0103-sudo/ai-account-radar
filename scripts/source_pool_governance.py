#!/usr/bin/env python3
"""AR-026 source-pool dry-run governance and coverage reporting.

Default mode is local/config only. `--from-feishu` performs a read-only 01
来源与采样 read, then produces the same dry-run report without updating Feishu.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
from pathlib import Path
from typing import Any

import push_to_feishu as feishu
import topic_flow_rework as flow
from feishu_table_registry import resolve_table_id, table_name
from local_env import load_local_env
from sync_source_sampling import all_records, list_tables


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "content_sources.yaml"
DEFAULT_OUT = ROOT / "output" / "source_governance"
TABLE_KEY = "source_sampling"


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
        "account_name": cell_text(fields.get("名称") or fields.get("来源名称") or fields.get("来源")),
        "url": cell_text(fields.get("主页链接") or fields.get("链接")),
        "fetch_method": cell_text(fields.get("抓取方式")),
        "priority": cell_text(fields.get("优先级")),
        "remarks": cell_text(fields.get("备注")),
    }


def load_config_sources(path: Path) -> list[dict[str, Any]]:
    return flow.load_json_config(path).get("sources", [])


def load_feishu_sources() -> list[dict[str, Any]]:
    load_local_env(required=True)
    app_token = os.getenv("FEISHU_BASE_APP_TOKEN")
    if not app_token:
        raise SystemExit("FEISHU_BASE_APP_TOKEN is required for --from-feishu")
    token = feishu.tenant_token()
    table_id = resolve_table_id(list_tables(token, app_token), TABLE_KEY)
    if not table_id:
        raise SystemExit(f"Missing Feishu table: {table_name(TABLE_KEY)}")
    return [source_from_feishu_record(record) for record in all_records(token, app_token, table_id)]


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

    sources = load_feishu_sources() if args.from_feishu else load_config_sources(Path(args.config))
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
    }
    out_dir = Path(args.out_dir)
    plan_path = write_release_sync_plan(report, out_dir)
    report["release_sync_plan"] = str(plan_path)
    report["output"] = str(out_dir / "source_governance_report.json")
    path = write_report(report, out_dir)
    report["output"] = str(path)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
