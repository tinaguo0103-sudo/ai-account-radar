#!/usr/bin/env python3
"""AR-027 Feishu 01/03/04 schema cleanup dry-run.

This tool audits fields/options/view columns against code/config/docs
references. It never deletes fields by default; `--write-feishu` is present as
an explicit future production hook and currently refuses without a plan file.
"""
from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any

import push_to_feishu as feishu
from feishu_table_registry import TABLES, resolve_table_id, table_name
from local_env import load_local_env
from sync_source_sampling import all_records, list_tables
from topic_decision_fields import FEISHU_KEEP_FIELDS as TOPIC_KEEP_FIELDS


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "output" / "schema_cleanup"
AUDIT_TABLE_KEYS = ("source_sampling", "content_inbox", "topic_decision")
SCAN_DIRS = ("scripts", "config", "docs/spikes", "cloud_functions")

BASE_KEEP_FIELDS = {
    "source_sampling": {"名称", "来源角色", "是否参与主采样", "默认启用", "优先级", "平台", "主页链接", "抓取方式", "跟踪频率", "关注重点", "备注"},
    "content_inbox": {
        "标题", "来源类型", "来源名称", "平台", "链接", "发布时间", "采集时间", "采集状态", "失败原因",
        "摘要/片段", "作者/账号", "内容指纹", "正文/全文", "正文长度", "是否全文解析", "原始payload路径",
        "解析说明", "运行日期", "运行批次", "是否本次新增", "最近参与运行批次", "最近采样日期",
        "是否重复", "处理状态", "保留策略",
    },
    "topic_decision": set(TOPIC_KEEP_FIELDS),
}


def load_fixture(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def repo_texts() -> dict[str, str]:
    texts: dict[str, str] = {}
    for folder in SCAN_DIRS:
        base = ROOT / folder
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if not path.is_file() or path.suffix not in {".py", ".js", ".mjs", ".json", ".yaml", ".yml", ".md"}:
                continue
            try:
                texts[str(path.relative_to(ROOT))] = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
    return texts


def reference_hits(field_name: str, texts: dict[str, str]) -> list[str]:
    hits: list[str] = []
    pattern = re.compile(re.escape(field_name))
    for rel_path, text in texts.items():
        if pattern.search(text):
            hits.append(rel_path)
    return sorted(hits)


def option_names(field: dict[str, Any]) -> list[str]:
    prop = field.get("property") or {}
    options = prop.get("options") if isinstance(prop, dict) else []
    if not isinstance(options, list):
        return []
    return [str(option.get("name") or option.get("text") or "") for option in options if isinstance(option, dict)]


def field_name(field: dict[str, Any]) -> str:
    return str(field.get("field_name") or field.get("name") or "")


def payload_from_fixture(data: dict[str, Any], table_key: str) -> dict[str, Any]:
    table_data = data.get(table_key) or data.get(TABLES.get(table_key, "")) or {}
    if isinstance(table_data, dict):
        fields = table_data.get("fields") or []
        return {
            "fields": fields if isinstance(fields, list) else [],
            "views": table_data.get("views") if isinstance(table_data.get("views"), list) else [],
            "records": table_data.get("records") if isinstance(table_data.get("records"), list) else [],
        }
    if isinstance(table_data, list):
        return {"fields": table_data, "views": [], "records": []}
    return {"fields": [], "views": [], "records": []}


def list_feishu_fields(token: str, app_token: str, table_id: str) -> list[dict[str, Any]]:
    payload = feishu.request_json("GET", f"/bitable/v1/apps/{app_token}/tables/{table_id}/fields", token=token)
    return payload.get("data", {}).get("items", [])


def list_feishu_views(token: str, app_token: str, table_id: str) -> list[dict[str, Any]]:
    payload = feishu.request_json("GET", f"/bitable/v1/apps/{app_token}/tables/{table_id}/views", token=token)
    return payload.get("data", {}).get("items", [])


def feishu_schema(record_sample_limit: int = 500) -> dict[str, dict[str, Any]]:
    load_local_env(required=True)
    app_token = os.getenv("FEISHU_BASE_APP_TOKEN")
    if not app_token:
        raise SystemExit("FEISHU_BASE_APP_TOKEN is required for --from-feishu")
    token = feishu.tenant_token()
    tables = list_tables(token, app_token)
    result: dict[str, dict[str, Any]] = {}
    for table_key in AUDIT_TABLE_KEYS:
        table_id = resolve_table_id(tables, table_key)
        if not table_id:
            result[table_key] = {"fields": [], "views": [], "records": []}
            continue
        records = all_records(token, app_token, table_id)
        result[table_key] = {
            "table_id": table_id,
            "fields": list_feishu_fields(token, app_token, table_id),
            "views": list_feishu_views(token, app_token, table_id),
            "records": records[:record_sample_limit],
            "record_sample_count": min(len(records), record_sample_limit),
            "record_total_read": len(records),
        }
    return result


def table_payload(schema: dict[str, Any], table_key: str) -> dict[str, Any]:
    value = schema.get(table_key, [])
    if isinstance(value, list):
        return {"fields": value, "views": [], "records": []}
    if isinstance(value, dict):
        return {
            "table_id": value.get("table_id", ""),
            "fields": value.get("fields") or [],
            "views": value.get("views") or [],
            "records": value.get("records") or [],
            "record_sample_count": value.get("record_sample_count", len(value.get("records") or [])),
            "record_total_read": value.get("record_total_read", len(value.get("records") or [])),
        }
    return {"fields": [], "views": [], "records": []}


def record_value(record: dict[str, Any], name: str) -> Any:
    fields = record.get("fields") if isinstance(record.get("fields"), dict) else record
    return fields.get(name) if isinstance(fields, dict) else None


def is_filled(value: Any) -> bool:
    if value is None or value == "":
        return False
    if isinstance(value, list):
        return any(is_filled(item) for item in value)
    if isinstance(value, dict):
        return any(is_filled(item) for item in value.values())
    return True


def sample_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        text = value.get("text") or value.get("name") or value.get("value")
        return str(text or "")[:80]
    if isinstance(value, list):
        return "、".join(sample_text(item) for item in value if sample_text(item))[:80]
    return str(value)[:80]


def option_values(value: Any) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        values: list[str] = []
        for item in value:
            values.extend(option_values(item))
        return values
    if isinstance(value, dict):
        text = value.get("name") or value.get("text") or value.get("value")
        return [str(text)] if text else []
    return [str(value)]


def field_usage(records: list[dict[str, Any]], name: str) -> dict[str, Any]:
    values = [record_value(record, name) for record in records]
    filled_values = [value for value in values if is_filled(value)]
    samples: list[str] = []
    for value in filled_values:
        text = sample_text(value)
        if text and text not in samples:
            samples.append(text)
        if len(samples) >= 3:
            break
    total = len(records)
    return {
        "record_sample_count": total,
        "fill_count": len(filled_values),
        "fill_rate": round(len(filled_values) / total, 4) if total else 0.0,
        "sample_values": samples,
    }


def option_usage(records: list[dict[str, Any]], field: str, option: str) -> int:
    count = 0
    for record in records:
        if option in option_values(record_value(record, field)):
            count += 1
    return count


def audit_views(views: list[dict[str, Any]], fields: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = {str(field.get("field_id", "")): field_name(field) for field in fields if field.get("field_id")}
    all_names = [field_name(field) for field in fields if field_name(field)]
    rows: list[dict[str, Any]] = []
    for view in views:
        prop = view.get("property") if isinstance(view.get("property"), dict) else {}
        hidden_ids = prop.get("hidden_fields") if isinstance(prop.get("hidden_fields"), list) else []
        hidden_names = [by_id.get(str(field_id), str(field_id)) for field_id in hidden_ids]
        visible_names = [name for name in all_names if name not in hidden_names] if hidden_names else []
        rows.append({
            "view_name": view.get("view_name") or view.get("name") or "",
            "view_id": view.get("view_id", ""),
            "view_type": view.get("view_type", ""),
            "hidden_field_count": len(hidden_names),
            "visible_field_count": len(visible_names) if visible_names else None,
            "visible_fields_sample": visible_names[:20],
            "hidden_fields_sample": hidden_names[:20],
            "needs_manual_review": not bool(prop),
        })
    return rows


def audit_schema(schema: dict[str, Any]) -> dict[str, Any]:
    texts = repo_texts()
    tables: dict[str, Any] = {}
    for table_key in AUDIT_TABLE_KEYS:
        keep_fields = BASE_KEEP_FIELDS.get(table_key, set())
        payload = table_payload(schema, table_key)
        fields = payload["fields"]
        records = payload["records"]
        rows = []
        cleanup_matrix: list[dict[str, Any]] = []
        for field in fields:
            name = field_name(field)
            if not name:
                continue
            hits = reference_hits(name, texts)
            options = option_names(field)
            option_hits = {option: reference_hits(option, texts) for option in options}
            usage = field_usage(records, name)
            referenced = bool(hits) or name in keep_fields
            if name in keep_fields:
                recommendation = "keep"
                risk = "high"
                reason = "业务保留字段或当前脚本读写字段。"
            elif hits:
                recommendation = "keep"
                risk = "medium"
                reason = "代码/config/docs 仍有引用，删除前需人工确认。"
            elif usage["fill_count"] > 0:
                recommendation = "needs_manual_review"
                risk = "medium"
                reason = "未发现代码引用，但真实记录仍有填充值；需业务确认后才能删除。"
            else:
                recommendation = "delete_candidate"
                risk = "low"
                reason = "未发现当前脚本/config/docs 引用；可作为业务无用字段删除候选。"
            option_rows = []
            for option in options:
                usage_count = option_usage(records, name, option)
                if option_hits[option]:
                    option_rec = "keep"
                    option_reason = "代码/config/docs 仍引用该选项。"
                    option_risk = "medium"
                elif usage_count:
                    option_rec = "needs_manual_review"
                    option_reason = "真实记录仍使用该选项。"
                    option_risk = "medium"
                else:
                    option_rec = "delete_candidate"
                    option_reason = "未发现引用，样本记录也未使用。"
                    option_risk = "low"
                option_row = {
                    "name": option,
                    "reference_count": len(option_hits[option]),
                    "usage_count": usage_count,
                    "recommendation": option_rec,
                    "risk": option_risk,
                    "reason": option_reason,
                    "references": option_hits[option][:20],
                }
                option_rows.append(option_row)
                if option_rec in {"delete_candidate", "needs_manual_review"}:
                    cleanup_matrix.append({
                        "kind": "option",
                        "field_name": name,
                        "option_name": option,
                        "recommendation": option_rec,
                        "risk": option_risk,
                        "evidence": option_reason,
                        "reference_count": len(option_hits[option]),
                        "usage_count": usage_count,
                    })
            row = {
                "field_name": name,
                "field_id": field.get("field_id", ""),
                "field_type": field.get("type", ""),
                "recommendation": recommendation,
                "risk": risk,
                "reason": reason,
                "fill_count": usage["fill_count"],
                "fill_rate": usage["fill_rate"],
                "sample_values": usage["sample_values"],
                "reference_count": len(hits),
                "references": hits[:20],
                "options": option_rows,
                "option_delete_candidate_count": sum(1 for option in option_rows if option["recommendation"] == "delete_candidate"),
                "option_manual_review_count": sum(1 for option in option_rows if option["recommendation"] == "needs_manual_review"),
            }
            rows.append(row)
            if recommendation in {"delete_candidate", "needs_manual_review"}:
                cleanup_matrix.append({
                    "kind": "field",
                    "field_name": name,
                    "recommendation": recommendation,
                    "risk": risk,
                    "evidence": reason,
                    "reference_count": len(hits),
                    "fill_count": usage["fill_count"],
                    "fill_rate": usage["fill_rate"],
                    "sample_values": usage["sample_values"],
                })
        view_rows = audit_views(payload.get("views", []), fields)
        tables[table_key] = {
            "table_name": table_name(table_key),
            "table_id": payload.get("table_id", ""),
            "record_sample_count": payload.get("record_sample_count", len(records)),
            "record_total_read": payload.get("record_total_read", len(records)),
            "field_count": len(rows),
            "delete_candidate_count": sum(1 for row in rows if row["recommendation"] == "delete_candidate"),
            "manual_review_count": sum(1 for row in rows if row["recommendation"] == "needs_manual_review"),
            "option_delete_candidate_count": sum(row["option_delete_candidate_count"] for row in rows),
            "option_manual_review_count": sum(row["option_manual_review_count"] for row in rows),
            "view_count": len(view_rows),
            "views": view_rows,
            "fields": rows,
            "cleanup_matrix": cleanup_matrix,
        }
    return {
        "ok": True,
        "dry_run_only": True,
        "tables": tables,
        "write_policy": "Production deletion requires separate PM/production authorization and --write-feishu with a reviewed plan.",
    }


def write_report(report: dict[str, Any], out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "feishu_schema_cleanup_dry_run.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="AR-027 Feishu 01/03/04 schema cleanup audit.")
    parser.add_argument("--fixture", default="", help="Local schema fixture JSON. Default uses a built-in minimal schema.")
    parser.add_argument("--from-feishu", action="store_true", help="Read Feishu fields only; never deletes.")
    parser.add_argument("--record-sample-limit", type=int, default=500, help="Max read-only records per table for fill-rate/option usage.")
    parser.add_argument("--write-feishu", action="store_true", help="Reserved for separately authorized production cleanup.")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT))
    args = parser.parse_args()

    if args.write_feishu:
        raise SystemExit("--write-feishu requires a separately reviewed cleanup plan; this dev task only supports dry-run.")
    if args.from_feishu:
        schema = feishu_schema(record_sample_limit=args.record_sample_limit)
    elif args.fixture:
        data = load_fixture(Path(args.fixture))
        schema = {table_key: payload_from_fixture(data, table_key) for table_key in AUDIT_TABLE_KEYS}
    else:
        schema = {
            table_key: [{"field_name": name, "type": 1, "property": {}} for name in sorted(BASE_KEEP_FIELDS.get(table_key, set()))]
            for table_key in AUDIT_TABLE_KEYS
        }
    report = audit_schema(schema)
    path = write_report(report, Path(args.out_dir))
    report["output"] = str(path)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
