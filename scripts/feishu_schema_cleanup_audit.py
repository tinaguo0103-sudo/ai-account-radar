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


def fields_from_fixture(data: dict[str, Any], table_key: str) -> list[dict[str, Any]]:
    table_data = data.get(table_key) or data.get(TABLES.get(table_key, "")) or {}
    if isinstance(table_data, dict):
        fields = table_data.get("fields") or []
        return fields if isinstance(fields, list) else []
    return []


def list_feishu_fields(token: str, app_token: str, table_id: str) -> list[dict[str, Any]]:
    payload = feishu.request_json("GET", f"/bitable/v1/apps/{app_token}/tables/{table_id}/fields", token=token)
    return payload.get("data", {}).get("items", [])


def feishu_schema() -> dict[str, list[dict[str, Any]]]:
    load_local_env(required=True)
    app_token = os.getenv("FEISHU_BASE_APP_TOKEN")
    if not app_token:
        raise SystemExit("FEISHU_BASE_APP_TOKEN is required for --from-feishu")
    token = feishu.tenant_token()
    tables = list_tables(token, app_token)
    result: dict[str, list[dict[str, Any]]] = {}
    for table_key in AUDIT_TABLE_KEYS:
        table_id = resolve_table_id(tables, table_key)
        if not table_id:
            result[table_key] = []
            continue
        result[table_key] = list_feishu_fields(token, app_token, table_id)
    return result


def audit_schema(schema: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    texts = repo_texts()
    tables: dict[str, Any] = {}
    for table_key in AUDIT_TABLE_KEYS:
        keep_fields = BASE_KEEP_FIELDS.get(table_key, set())
        rows = []
        for field in schema.get(table_key, []):
            name = field_name(field)
            if not name:
                continue
            hits = reference_hits(name, texts)
            options = option_names(field)
            option_hits = {option: reference_hits(option, texts) for option in options}
            referenced = bool(hits) or name in keep_fields
            if name in keep_fields:
                recommendation = "keep"
                risk = "high"
                reason = "业务保留字段或当前脚本读写字段。"
            elif hits:
                recommendation = "keep"
                risk = "medium"
                reason = "代码/config/docs 仍有引用，删除前需人工确认。"
            else:
                recommendation = "delete_candidate"
                risk = "low"
                reason = "未发现当前脚本/config/docs 引用；可作为业务无用字段删除候选。"
            rows.append({
                "field_name": name,
                "field_id": field.get("field_id", ""),
                "field_type": field.get("type", ""),
                "recommendation": recommendation,
                "risk": risk,
                "reason": reason,
                "reference_count": len(hits),
                "references": hits[:20],
                "options": [
                    {
                        "name": option,
                        "reference_count": len(option_hits[option]),
                        "recommendation": "keep" if option_hits[option] else "delete_candidate",
                        "references": option_hits[option][:20],
                    }
                    for option in options
                ],
            })
        tables[table_key] = {
            "table_name": table_name(table_key),
            "field_count": len(rows),
            "delete_candidate_count": sum(1 for row in rows if row["recommendation"] == "delete_candidate"),
            "fields": rows,
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
    parser.add_argument("--write-feishu", action="store_true", help="Reserved for separately authorized production cleanup.")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT))
    args = parser.parse_args()

    if args.write_feishu:
        raise SystemExit("--write-feishu requires a separately reviewed cleanup plan; this dev task only supports dry-run.")
    if args.from_feishu:
        schema = feishu_schema()
    elif args.fixture:
        data = load_fixture(Path(args.fixture))
        schema = {table_key: fields_from_fixture(data, table_key) for table_key in AUDIT_TABLE_KEYS}
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
