#!/usr/bin/env python3
"""Reconcile manual Feishu edits in 01 来源与采样 back into source config.

Use this after the source pool is edited directly in Feishu. It keeps the repo
config from overwriting manual Feishu additions on the next sync.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

import push_to_feishu as feishu
from feishu_table_registry import configured_table_id, resolve_table_id, table_name
from sync_source_sampling import (
    PRIORITY_ORDER,
    ensure_fields,
    row_from_source,
    sync_rows,
)


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "content_sources.yaml"
TABLE_KEY = "source_sampling"

ACTIVE_ROLES = {"current_main_competitor", "current_aux_competitor"}
PLACEHOLDER_ROLE = "current_main_competitor_placeholder"
HISTORICAL_ROLE = "historical_reference"
QUARANTINED_ROLE = "quarantined_source"
SKIP_ROLES = {"system_hotspot_source", "official_source", "manual_entry", "legacy_manual_entry", QUARANTINED_ROLE}


def text(value: Any) -> str:
    return str(value or "").strip()


def label_bool(value: Any, default: bool) -> bool:
    label = text(value)
    if label in {"是", "启用", "true", "True", "1"}:
        return True
    if label in {"否", "停用", "false", "False", "0"}:
        return False
    return default


def usable_url(value: Any) -> str:
    url = text(value)
    return url if url.startswith(("http://", "https://")) else ""


def infer_source_type(platform: str) -> str:
    if any(key in platform for key in ["抖音", "视频号", "B站", "小红书"]):
        return "competitor_video"
    return "competitor_article"


def infer_content_shape(platform: str) -> str:
    if "抖音" in platform:
        return "short_video"
    if "视频" in platform or "小红书" in platform or "B站" in platform:
        return "short_video_or_post"
    return "article_or_profile"


def infer_fetch_method(platform: str, role: str) -> str:
    if role == PLACEHOLDER_ROLE:
        return "待定"
    if "抖音" in platform:
        return "douyin_shallow_sample_or_manual_text"
    if "公众号" in platform or "文章" in platform:
        return "public_article_url_or_manual_article_list"
    return "manual_or_public_link"


def source_needs_home_url(source: dict[str, Any]) -> bool:
    platform = text(source.get("platform"))
    fetch_method = text(source.get("fetch_method"))
    if fetch_method.startswith("paused_"):
        return False
    if "微信公众号" in platform and fetch_method in {
        "public_article_url_or_manual_article_list",
        "wechat_fulltext_provider_or_single_url_intake",
        "wechat_feed",
    }:
        return False
    return True


def make_id(name: str, url: str) -> str:
    digest = hashlib.sha1(f"{name}|{url}".encode("utf-8")).hexdigest()[:10]
    return f"feishu_source_{digest}"


def list_tables(token: str, app_token: str) -> dict[str, str]:
    payload = feishu.request_json("GET", f"/bitable/v1/apps/{app_token}/tables", token=token)
    return {item["name"]: item["table_id"] for item in payload.get("data", {}).get("items", [])}


def all_records(token: str, app_token: str, table_id: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    page_token = ""
    while True:
        suffix = f"?page_size=500{('&page_token=' + page_token) if page_token else ''}"
        payload = feishu.request_json("GET", f"/bitable/v1/apps/{app_token}/tables/{table_id}/records{suffix}", token=token)
        data = payload.get("data", {})
        records.extend(data.get("items", []))
        if not data.get("has_more"):
            return records
        page_token = data.get("page_token", "")


def record_name(fields: dict[str, Any]) -> str:
    return text(fields.get("名称") or fields.get("来源名称") or fields.get("来源"))


def load_config() -> dict[str, Any]:
    return json.loads(CONFIG.read_text(encoding="utf-8"))


def source_indexes(config: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    by_name: dict[str, dict[str, Any]] = {}
    by_url: dict[str, dict[str, Any]] = {}
    for source in config.get("sources", []):
        name = text(source.get("account_name"))
        url = usable_url(source.get("url"))
        if name:
            by_name[name] = source
        if url:
            by_url[url] = source
    return by_name, by_url


def rows_from_sources(config: dict[str, Any]) -> list[dict[str, str]]:
    rows = [row_from_source(source) for source in config.get("sources", [])]
    return sorted(rows, key=lambda row: (
        row["来源角色"],
        PRIORITY_ORDER.get(row["优先级"], 9),
        row["名称"],
    ))


def update_common_from_feishu(source: dict[str, Any], fields: dict[str, Any], config: dict[str, Any]) -> None:
    platform = text(fields.get("平台")) or text(source.get("platform"))
    url = usable_url(fields.get("主页链接") or fields.get("链接")) or text(source.get("url"))
    role = text(source.get("source_role") or source.get("source_group"))
    source["platform"] = platform
    source["url"] = url
    source["source_type"] = text(source.get("source_type")) or infer_source_type(platform)
    source["content_shape"] = text(source.get("content_shape")) or infer_content_shape(platform)
    source["fetch_method"] = text(fields.get("抓取方式")) or text(source.get("fetch_method")) or infer_fetch_method(platform, role)
    source["sample_frequency"] = "paused" if text(source.get("fetch_method")).startswith("paused_") else "daily_or_when_updated"
    if text(fields.get("关注重点")):
        source["learn_focus"] = text(fields.get("关注重点"))
    elif not text(source.get("learn_focus")):
        source["learn_focus"] = "先观察它最近高互动内容的选题、开头、案例和转化方式，再判断能否转成我的业务现场。"
    if not text(source.get("do_not_copy")):
        source["do_not_copy"] = "不复制对方人设、案例和表达，只学习选题结构和业务转译方式。"
    if not text(source.get("convert_direction")):
        source["convert_direction"] = "转成我的真实工作流、AI介入点、可展示证据和可沉淀资产。"
    source["needs_url"] = source_needs_home_url(source) and not bool(usable_url(source.get("url")))
    for legacy_key in ["column", "weight_group", "column_weight"]:
        source.pop(legacy_key, None)


def promote_to_aux(source: dict[str, Any], fields: dict[str, Any], config: dict[str, Any]) -> None:
    source["source_group"] = "current_aux_competitor"
    source["source_role"] = "current_aux_competitor"
    source["is_main_competitor"] = False
    source["participates_main_sampling"] = True
    source["default_enabled"] = True
    source["priority"] = "medium"
    update_common_from_feishu(source, fields, config)
    if not text(source.get("remarks")) or "历史参考" in text(source.get("remarks")):
        source["remarks"] = "由历史参考池重新纳入当前辅助跟进；优先级中。"


def active_priority(source: dict[str, Any], fields: dict[str, Any], role: str) -> str:
    enabled = label_bool(fields.get("默认启用"), bool(source.get("default_enabled", True)))
    sampling = label_bool(fields.get("是否参与主采样"), bool(source.get("participates_main_sampling", True)))
    if not enabled or not sampling:
        return "low"
    if role == "current_main_competitor":
        return "high"
    if text(source.get("priority")) == "high" or text(fields.get("优先级")) == "high":
        return "high"
    return "medium"


def keep_active(source: dict[str, Any], fields: dict[str, Any], config: dict[str, Any]) -> str:
    role = text(source.get("source_role") or source.get("source_group"))
    if role == PLACEHOLDER_ROLE:
        source["participates_main_sampling"] = False
        source["default_enabled"] = False
        source["priority"] = "low"
        return "low"
    if role not in ACTIVE_ROLES:
        role = "current_aux_competitor"
    source["source_group"] = role
    source["source_role"] = role
    source["is_main_competitor"] = role == "current_main_competitor"
    source["participates_main_sampling"] = label_bool(fields.get("是否参与主采样"), bool(source.get("participates_main_sampling", True)))
    source["default_enabled"] = label_bool(fields.get("默认启用"), bool(source.get("default_enabled", True)))
    source["priority"] = active_priority(source, fields, role)
    update_common_from_feishu(source, fields, config)
    return text(source.get("priority"))


def new_source_from_record(fields: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    name = record_name(fields)
    platform = text(fields.get("平台"))
    url = usable_url(fields.get("主页链接") or fields.get("链接"))
    source = {
        "id": make_id(name, url),
        "source_group": "current_aux_competitor",
        "source_role": "current_aux_competitor",
        "is_main_competitor": False,
        "participates_main_sampling": True,
        "default_enabled": True,
        "source_type": infer_source_type(platform),
        "platform": platform,
        "account_name": name,
        "url": url,
        "content_shape": infer_content_shape(platform),
        "fetch_method": text(fields.get("抓取方式")) or infer_fetch_method(platform, "current_aux_competitor"),
        "priority": "medium",
        "needs_url": "微信公众号" not in platform and not bool(url),
        "sample_frequency": "daily_or_when_updated",
        "learn_focus": text(fields.get("关注重点")) or "新增对标账号，先观察近期高互动内容的选题、开头、案例和转化方式。",
        "do_not_copy": "不复制对方人设、案例和表达，只学习选题结构和业务转译方式。",
        "convert_direction": "转成我的真实工作流、AI介入点、可展示证据和可沉淀资产。",
        "remarks": text(fields.get("备注")) or "飞书 01 手动新增，已纳入当前辅助跟进；优先级中。",
    }
    return source


def reconcile(config: dict[str, Any], records: list[dict[str, Any]]) -> dict[str, Any]:
    by_name, by_url = source_indexes(config)
    summary: dict[str, Any] = {
        "existing_active_high": [],
        "existing_active_medium": [],
        "existing_active_low": [],
        "promoted_historical_medium": [],
        "new_medium": [],
        "renamed_by_same_url": [],
        "skipped": [],
        "issues": {
            "blank_records": [],
            "missing_home_url": [],
        },
    }
    for record in records:
        fields = record.get("fields", {})
        name = record_name(fields)
        role = text(fields.get("来源角色"))
        url = usable_url(fields.get("主页链接") or fields.get("链接"))
        if not name:
            summary["issues"]["blank_records"].append(record.get("record_id"))
            continue
        if role in SKIP_ROLES:
            summary["skipped"].append(name)
            continue
        source = by_name.get(name) or (by_url.get(url) if url else None)
        if source and text(source.get("account_name")) != name:
            summary["renamed_by_same_url"].append({
                "from": text(source.get("account_name")),
                "to": name,
                "url": url,
            })
            old_name = text(source.get("account_name"))
            source["account_name"] = name
            by_name.pop(old_name, None)
            by_name[name] = source
        source_role = text(source.get("source_role") if source else role)
        if source and source_role in ACTIVE_ROLES:
            priority = keep_active(source, fields, config)
            if priority == "high":
                summary["existing_active_high"].append(name)
            elif priority == "medium":
                summary["existing_active_medium"].append(name)
            else:
                summary["existing_active_low"].append(name)
        elif source and source_role == PLACEHOLDER_ROLE:
            keep_active(source, fields, config)
            summary["skipped"].append(name)
        elif source and source_role == QUARANTINED_ROLE:
            summary["skipped"].append(name)
        elif source and (source_role == HISTORICAL_ROLE or role == HISTORICAL_ROLE):
            promote_to_aux(source, fields, config)
            summary["promoted_historical_medium"].append(name)
        elif source:
            promote_to_aux(source, fields, config)
            summary["promoted_historical_medium"].append(name)
        else:
            source = new_source_from_record(fields, config)
            config.setdefault("sources", []).append(source)
            by_name[name] = source
            if url:
                by_url[url] = source
            summary["new_medium"].append(name)
        if text(source.get("source_role")) in ACTIVE_ROLES:
            if source.get("needs_url") and not usable_url(source.get("url")):
                summary["issues"]["missing_home_url"].append(name)
    return summary


def write_config(config: dict[str, Any]) -> None:
    payload = json.dumps(config, ensure_ascii=False, indent=2) + "\n"
    temporary = CONFIG.with_suffix(CONFIG.suffix + ".tmp")
    temporary.write_text(payload, encoding="utf-8")
    temporary.replace(CONFIG)
    if CONFIG.read_text(encoding="utf-8") != payload:
        raise RuntimeError("source_plan_config_readback_mismatch")


def active_source_plan(config: dict[str, Any]) -> list[dict[str, Any]]:
    active = [
        source for source in config.get("sources", [])
        if text(source.get("source_role") or source.get("source_group")) in ACTIVE_ROLES
        and bool(source.get("default_enabled", True))
        and bool(source.get("participates_main_sampling", True))
    ]
    names = [text(source.get("account_name")) for source in active]
    if not active or any(not name for name in names) or len(names) != len(set(names)):
        raise RuntimeError("active_source_plan_invalid")
    return active


def main() -> int:
    parser = argparse.ArgumentParser(description="Reconcile Feishu 01 source-pool edits into config/content_sources.yaml.")
    parser.add_argument("--write-config", action="store_true", help="Write reconciled config/content_sources.yaml.")
    parser.add_argument("--write-feishu", action="store_true", help="Sync reconciled rows back to Feishu 01.")
    args = parser.parse_args()

    app_token = os.getenv("FEISHU_BASE_APP_TOKEN")
    if not app_token:
        raise SystemExit("Missing FEISHU_BASE_APP_TOKEN")
    token = feishu.tenant_token()
    tables = list_tables(token, app_token)
    table_id, table_id_source = configured_table_id(tables, TABLE_KEY)
    if table_id_source == "table_name":
        table_id = resolve_table_id(tables, TABLE_KEY)
    if not table_id:
        raise SystemExit(f"Missing explicit Feishu table for {TABLE_KEY}: {table_id_source}")
    records = all_records(token, app_token, table_id)
    config = load_config()
    summary = reconcile(config, records)
    active_plan = active_source_plan(config)
    output: dict[str, Any] = {
        "ok": True,
        "plan_ready": True,
        "mode": "write" if args.write_config or args.write_feishu else "dry-run",
        "feishu_records_read": len(records),
        "active_account_count": len(active_plan),
        "optional_followup_failed": False,
        "optional_followup_reason": "",
        "summary": summary,
    }
    if args.write_config:
        write_config(config)
        output["config_written"] = str(CONFIG)
    if args.write_feishu:
        rows = rows_from_sources(config)
        try:
            ensure_fields(token, app_token, table_id)
            output["feishu"] = sync_rows(token, app_token, rows)
            time.sleep(0.1)
        except Exception as exc:
            output["optional_followup_failed"] = True
            output["optional_followup_reason"] = f"{type(exc).__name__}: {exc}"
            output["feishu"] = {"ok": False, "optional": True}
    print(json.dumps(output, ensure_ascii=False, indent=2))
    print("SOURCE_PLAN_STATUS_JSON=" + json.dumps({
        "ok": output["ok"],
        "plan_ready": output["plan_ready"],
        "active_account_count": output["active_account_count"],
        "optional_followup_failed": output["optional_followup_failed"],
        "optional_followup_reason": output["optional_followup_reason"],
    }, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
