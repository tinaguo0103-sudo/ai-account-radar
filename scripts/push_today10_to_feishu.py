#!/usr/bin/env python3
"""Write today's candidate topic rows to Feishu 04 分析与选题.

This script is intentionally narrow: it does not rebuild tables, does not
write rejected debug candidates, and does not touch publishing/lead workflows.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import feishu_idempotency as idempotency
import push_to_feishu as feishu
import topic_field_contract as field_contract
from feishu_table_registry import TABLES, resolve_table_id, table_name
from topic_decision_fields import (
    CORE_VISIBLE_FIELDS,
    DAILY_WRITE_FIELDS,
    DETAIL_VISIBLE_FIELDS,
    card_summary_from_fields,
    field_create_body,
)


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output"
TODAY10 = OUT / "today_10_topics.csv"
LATEST_WRITE_TODAY10 = OUT / "latest_write" / "today_10_topics.csv"
LEGACY_LOG = OUT / "content_sampler_log.json"
TARGET_TABLE_KEY = "topic_decision"
CONTENT_INBOX_TABLE_KEY = "content_inbox"
CONTENT_FINGERPRINT_FIELD = "内容指纹"
RECENT_DEDUPE_DAYS = int(os.getenv("TOPIC_CARD_RECENT_DEDUPE_DAYS", "5"))
CONTENT_INBOX_SYNC_RATIO = float(os.getenv("CONTENT_INBOX_SYNC_RATIO", "0.8"))
TOPIC_CREATE_KIND = "topic_candidate_create"
REQUIRED_FIELDS = [
    *DAILY_WRITE_FIELDS,
]
ALLOWED_LEVELS = {"今日最值得做", "可选候选", "暂存观察", "不建议制作"}
FEISHU_VISIBLE_LEVELS = {"今日最值得做", "可选候选"}
VISIBLE_ACTIONS = {"立即蹭热点", "生成脚本包"}
LEVEL_ALIASES = {
    "备选": "可选候选",
    "备选候选": "可选候选",
    "备选，不占今日前三": "可选候选",
    "候选": "可选候选",
    "观察": "暂存观察",
    "暂存": "暂存观察",
    "不做": "不建议制作",
    "放弃": "不建议制作",
}


def normalize_level(value: str) -> str:
    cleaned = (value or "").strip()
    if cleaned in ALLOWED_LEVELS:
        return cleaned
    if cleaned in LEVEL_ALIASES:
        return LEVEL_ALIASES[cleaned]
    for key, target in LEVEL_ALIASES.items():
        if key and key in cleaned:
            return target
    if "最值得" in cleaned:
        return "今日最值得做"
    if "不建议" in cleaned:
        return "不建议制作"
    if "暂存" in cleaned or "观察" in cleaned:
        return "暂存观察"
    return "可选候选" if cleaned else ""


def inferred_visible_level(row: dict[str, str], visible_rank: int) -> str:
    level = normalize_level(row.get("今日建议级别", ""))
    if level:
        return level
    if row.get("推荐动作", "") in VISIBLE_ACTIONS and row.get("是否建议进入制作", "") == "是":
        return "今日最值得做" if visible_rank == 1 else "可选候选"
    return ""


def short_text(value: str, limit: int = 38) -> str:
    text = " ".join((value or "").split())
    return text if len(text) <= limit else text[:limit].rstrip() + "..."


EXPERIMENT_ACTION_TERMS = [
    "测试", "验证", "改造", "压缩", "录成", "接进", "变成", "写回", "沉淀",
    "做成", "复用", "拆成", "跑一轮", "对比", "进入", "重写", "少掉",
    "选择", "选", "记录", "导出", "输出", "标出", "标注", "检查", "统计",
    "回填", "输入", "补", "决定", "复核",
]
FALLBACK_EXPERIMENT_PROMPT = "待补实验动作：写清输入材料、1-2个动作、输出物和通过/失败标准。"
PROPOSITION_OVERLOAD_TERMS = ["旧流程", "AI介入", "验证方式", "需要补", "还缺", "我要证明", "可沉淀"]


def has_experiment_action(value: str) -> bool:
    return any(term in (value or "") for term in EXPERIMENT_ACTION_TERMS)


def workflow_trigger_for(row: dict[str, str]) -> str:
    for field in ["热点触发点", "热点钩子", "原始钩子", "事件锚点", "原始来源标题", "来源内容", "来源标题"]:
        value = short_text(row.get(field, ""), 80)
        if value:
            return value
    return "这条外部素材"


def workflow_pain_for(row: dict[str, str]) -> str:
    for field in ["我的工作流痛点", "我的真实矛盾", "业务场景", "旧流程痛点", "内容核心冲突"]:
        value = short_text(row.get(field, ""), 140)
        if value:
            return value
    return "我的内容生产或业务交付里还缺一段可记录、可复跑、可验收的流程。"


def is_visible_action_candidate(row: dict[str, str]) -> bool:
    return row.get("推荐动作", "") in VISIBLE_ACTIONS and row.get("是否建议进入制作", "") == "是"


def inferred_experiment_for(row: dict[str, str]) -> str:
    if not is_visible_action_candidate(row):
        return ""
    trigger = workflow_trigger_for(row)
    pain = workflow_pain_for(row)
    asset = short_text(row.get("可沉淀资产", "") or "一张可复用检查清单", 42)
    result = short_text(row.get("可展示结果", "") or row.get("可展示证据", "") or "一页旧流程/新流程对比", 42)
    return short_text(
        f"输入{short_text(trigger, 28)}和我的真实场景，跑一轮{short_text(pain, 34)}改造，输出{result}，检查是否能沉淀{asset}。",
        140,
    )


def experiment_for(row: dict[str, str]) -> str:
    for field in ["我要做的实验", "我的改造动作"]:
        value = short_text(row.get(field, ""), 140)
        if value and has_experiment_action(value):
            return value
    inferred = inferred_experiment_for(row)
    if inferred and has_experiment_action(inferred):
        return inferred
    return FALLBACK_EXPERIMENT_PROMPT


def validation_for(row: dict[str, str]) -> str:
    value = short_text(row.get("验证方式", ""), 160)
    if value and has_experiment_action(value):
        return value
    experiment = experiment_for(row)
    if experiment == FALLBACK_EXPERIMENT_PROMPT:
        return ""
    return short_text(f"1. {experiment} 2. 记录输出物、通过/失败原因和下一步补证据。", 160)


def clean_short_proposition(value: str) -> str:
    text = " ".join((value or "").split())
    if text and len(text) <= 90 and not any(term in text for term in PROPOSITION_OVERLOAD_TERMS):
        return text
    return ""


def proposition_for(row: dict[str, str]) -> str:
    for field in ["选题命题", "我要做的实验"]:
        value = clean_short_proposition(row.get(field, ""))
        if value:
            return value
    experiment = experiment_for(row)
    value = clean_short_proposition(experiment)
    if value and experiment != FALLBACK_EXPERIMENT_PROMPT:
        return value
    trigger = workflow_trigger_for(row)
    action = "先暂存，等补出具体实验动作"
    if experiment != FALLBACK_EXPERIMENT_PROMPT:
        action = short_text(experiment, 56)
    return short_text(f"{short_text(trigger, 24)}触发的实验：{action}", 90)


def display_title_for(row: dict[str, str]) -> str:
    if is_visible_action_candidate(row) and row.get("我的选题标题"):
        return short_text(row["我的选题标题"], 88)
    proposition = proposition_for(row)
    if proposition:
        return proposition
    level = normalize_level(row.get("今日建议级别", ""))
    direction = row.get("对应方向") or row.get("对应栏目") or "候选"
    source = row.get("来源内容") or row.get("原始来源标题") or row.get("我的选题标题") or "未命名来源"
    source_label = short_text(source, 42)
    if level == "不建议制作":
        return f"不建议制作｜{source_label}"
    if level == "暂存观察":
        return f"暂存观察｜{source_label}"
    return row.get("我的选题标题") or f"{direction}候选：{short_text(source)}"
ACTION_STATUS = {
    "立即蹭热点": "待判断",
    "生成脚本包": "待判断",
    "暂存观察": "暂存",
    "不做": "不做",
}


def read_today10(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise SystemExit(f"Missing {path}")
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    return rows


def normalize(value: Any) -> str:
    return " ".join(str(value or "").split())


def parse_date(value: Any) -> datetime | None:
    text = normalize(value)
    if not text:
        return None
    try:
        return datetime.strptime(text[:10], "%Y-%m-%d")
    except ValueError:
        return None


def local_content_item_keys(path: Path) -> set[str]:
    if not path.exists() or not path.read_text(encoding="utf-8-sig").strip():
        return set()
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    keys: set[str] = set()
    for row in rows:
        key = (
            normalize(row.get(CONTENT_FINGERPRINT_FIELD))
            or normalize(row.get("内容链接"))
            or normalize(row.get("链接"))
            or normalize(row.get("内容标题"))
            or normalize(row.get("标题"))
        )
        if key:
            keys.add(key)
    return keys


def minimum_expected_content_inbox_records(local_count: int) -> int:
    if local_count <= 0:
        return 0
    if local_count <= 20:
        return local_count
    return max(20, int(local_count * CONTENT_INBOX_SYNC_RATIO))


def validate_content_inbox_synced(token: str, app_token: str, tables: dict[str, str], run_id: str, input_path: Path) -> dict[str, Any]:
    content_items_path = input_path.parent / "content_items.csv"
    local_keys = local_content_item_keys(content_items_path)
    if not local_keys:
        raise SystemExit(
            f"Refusing to write Feishu 04: missing or empty local content inbox source {content_items_path}. "
            "04 must only be written after the matching 03 内容收件箱 source exists."
        )
    table_id = resolve_table_id(tables, CONTENT_INBOX_TABLE_KEY)
    if not table_id:
        raise SystemExit(f"Missing Feishu table: {table_name(CONTENT_INBOX_TABLE_KEY)}")
    records = all_records(token, app_token, table_id)
    today = today_slug()
    run_records = [
        record for record in records
        if normalize(record.get("fields", {}).get("最近参与运行批次")) == run_id
        or normalize(record.get("fields", {}).get("运行批次")) == run_id
    ]
    today_records = [
        record for record in records
        if normalize(record.get("fields", {}).get("最近采样日期")) == today
        or normalize(record.get("fields", {}).get("运行日期")) == today
    ]
    minimum = minimum_expected_content_inbox_records(len(local_keys))
    if len(run_records) < minimum:
        raise SystemExit(
            "Refusing to write Feishu 04: Feishu 03 内容收件箱 is not synced for this run. "
            f"run_id={run_id}; local_unique_items={len(local_keys)}; "
            f"feishu_run_records={len(run_records)}; required_minimum={minimum}; "
            f"feishu_today_records={len(today_records)}. "
            "Fix or backfill 03 before writing 04."
        )
    return {
        "content_items_path": str(content_items_path),
        "local_unique_items": len(local_keys),
        "feishu_run_records": len(run_records),
        "feishu_today_records": len(today_records),
        "required_minimum": minimum,
    }


def feishu_visible_rows(rows: list[dict[str, str]]) -> tuple[list[dict[str, str]], int]:
    visible: list[dict[str, str]] = []
    omitted = 0
    for row in rows:
        if row.get("editorial_engine") != "codex" or row.get("fallback_only") == "true" or row.get("not_editorial_quality") == "true":
            omitted += 1
            continue
        issues = field_contract.validate_field_contract(row)
        if issues:
            omitted += 1
            continue
        if not row.get("我要做的实验") or not has_experiment_action(row.get("我要做的实验", "")):
            omitted += 1
            continue
        level = inferred_visible_level(row, len(visible) + 1)
        if level in FEISHU_VISIBLE_LEVELS:
            normalized = dict(row)
            normalized["今日建议级别"] = level
            visible.append(normalized)
            continue
        omitted += 1
    return visible, omitted


def default_today10_path() -> Path:
    if LATEST_WRITE_TODAY10.exists():
        return LATEST_WRITE_TODAY10
    if TODAY10.exists() and legacy_today10_is_official():
        return TODAY10
    raise SystemExit(
        "No official today candidate CSV found. Use --input with a run-specific CSV, "
        "or run daily_pipeline.py --write-feishu to create output/latest_write/."
    )


def legacy_today10_is_official() -> bool:
    if not LEGACY_LOG.exists():
        return False
    try:
        data = json.loads(LEGACY_LOG.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False
    return (
        data.get("mode") == "write-feishu"
        or "feishu_content_ledger" in data
        or bool(data.get("mirrors", {}).get("latest_write"))
    )


def today_slug() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def default_run_id() -> str:
    return os.getenv("RUN_ID") or os.getenv("AI_ACCOUNT_RADAR_RUN_ID") or f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}"


def list_tables(token: str, app_token: str) -> dict[str, str]:
    payload = feishu.request_json("GET", f"/bitable/v1/apps/{app_token}/tables", token=token)
    return {item["name"]: item["table_id"] for item in payload.get("data", {}).get("items", [])}


def list_fields(token: str, app_token: str, table_id: str) -> dict[str, dict[str, Any]]:
    payload = feishu.request_json("GET", f"/bitable/v1/apps/{app_token}/tables/{table_id}/fields", token=token)
    return {item["field_name"]: item for item in payload.get("data", {}).get("items", [])}


def list_views(token: str, app_token: str, table_id: str) -> list[dict[str, Any]]:
    payload = feishu.request_json("GET", f"/bitable/v1/apps/{app_token}/tables/{table_id}/views", token=token)
    return payload.get("data", {}).get("items", [])


def option_filter_values(field: dict[str, Any], values: list[Any]) -> list[Any]:
    """Feishu view filters require option ids for single/multi select fields."""
    if field.get("type") not in {3, 4}:
        return values
    options = field.get("property", {}).get("options", [])
    option_by_name = {
        str(option.get("name", "")): (
            option.get("id")
            or option.get("option_id")
            or option.get("value")
            or option.get("name")
        )
        for option in options
    }
    return [option_by_name.get(str(value), value) for value in values]


def ensure_fields(token: str, app_token: str, table_id: str) -> list[str]:
    existing = list_fields(token, app_token, table_id)
    created: list[str] = []
    for field_name in REQUIRED_FIELDS:
        if field_name in existing:
            continue
        feishu.request_json(
            "POST",
            f"/bitable/v1/apps/{app_token}/tables/{table_id}/fields",
            token=token,
            body=field_create_body(field_name),
        )
        created.append(field_name)
        time.sleep(0.1)
    return created


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


def map_row(row: dict[str, str], rank: int, date: str, run_id: str) -> dict[str, str]:
    status = ACTION_STATUS.get(row.get("推荐动作", ""), "待判断")
    level = normalize_level(row.get("今日建议级别", ""))
    proposition = row.get("选题命题") or row.get("我的选题标题") or proposition_for(row)
    experiment = row.get("我要做的实验", "")
    trigger = row.get("热点触发点") or workflow_trigger_for(row)
    pain = row.get("我的工作流痛点", "")
    display_title = proposition or display_title_for(row)
    recommendation_reason = row.get("推荐理由", "")
    mapped = {
        "选题标题": display_title,
        "状态": status,
        "今日建议级别": level,
        "AI味风险": row.get("AI味风险", ""),
        "推荐日期": date,
        "今日排名": str(rank),
        "对应方向": row.get("对应方向", row.get("对应栏目", "")),
        "原始来源标题": row.get("来源内容") or row.get("原始来源标题", ""),
        "来源链接": row.get("来源链接", ""),
        CONTENT_FINGERPRINT_FIELD: row.get(CONTENT_FINGERPRINT_FIELD, ""),
        "一句话Brief": row.get("一句话Brief", ""),
        "推荐理由": recommendation_reason,
        "不建议做的原因": row.get("不建议做的原因", ""),
        "我要做的实验": experiment,
        "热点触发点": trigger,
        "我的工作流痛点": pain,
        "旧流程痛点": row.get("旧流程痛点", ""),
        "AI介入点": row.get("AI介入点", ""),
        "验证方式": row.get("验证方式", ""),
        "可沉淀资产": row.get("可沉淀资产", ""),
        "我的思考点": row.get("我的思考点", ""),
        "可展示证据": row.get("可展示证据") or row.get("可展示结果", ""),
        "需要补的证据": row.get("需要补的证据", ""),
        "运行批次": run_id,
    }
    mapped["卡片速读"] = card_summary_from_fields(mapped)
    return mapped


def topic_duplicate_keys(fields: dict[str, Any]) -> set[tuple[str, str]]:
    keys: set[tuple[str, str]] = set()
    for field in [CONTENT_FINGERPRINT_FIELD, "来源链接", "原始来源标题", "选题标题"]:
        value = normalize(fields.get(field))
        if value:
            keys.add((field, value))
    return keys


def recent_duplicate_keys(records: list[dict[str, Any]], date: str, run_id: str, days: int = RECENT_DEDUPE_DAYS) -> set[tuple[str, str]]:
    current = parse_date(date)
    if not current or days <= 0:
        return set()
    cutoff = current - timedelta(days=days)
    keys: set[tuple[str, str]] = set()
    for record in records:
        fields = record.get("fields", {})
        if normalize(fields.get("运行批次")) == run_id:
            continue
        record_date = parse_date(fields.get("推荐日期"))
        if not record_date or record_date >= current or record_date < cutoff:
            continue
        keys.update(topic_duplicate_keys(fields))
    return keys


def filter_recent_duplicate_rows(
    rows: list[dict[str, str]],
    existing_records: list[dict[str, Any]],
    date: str,
    run_id: str,
    days: int = RECENT_DEDUPE_DAYS,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    duplicate_keys = recent_duplicate_keys(existing_records, date, run_id, days)
    if not duplicate_keys:
        return rows, []
    kept: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []
    for row in rows:
        row_keys = topic_duplicate_keys(row)
        if row_keys & duplicate_keys:
            skipped.append(row)
        else:
            kept.append(row)
    return kept, skipped


def dry_run_print(rows: list[dict[str, str]]) -> None:
    print(f"DRY-RUN: will write {len(rows)} 今日候选 rows to {table_name(TARGET_TABLE_KEY)}")
    for row in rows:
        print(
            f"{row['今日排名']}. {row['选题标题']} | "
            f"{row['对应方向']} | {row['状态']} | "
            f"{row.get('今日建议级别', '')} / AI味{row.get('AI味风险', '')}"
        )
        if row.get("我要做的实验"):
            print(f"   实验: {row['我要做的实验'][:120]}")
        if row.get("需要补的证据"):
            print(f"   需补证据: {row['需要补的证据'][:120]}")


def topic_create_business_key(row: dict[str, Any]) -> str:
    run_id = str(row.get("运行批次") or "")
    date = str(row.get("推荐日期") or "")
    source_title = str(row.get("原始来源标题") or "").strip()
    topic_title = str(row.get("选题标题") or "").strip()
    source_part = f"source:{source_title}" if source_title else f"title:{topic_title}"
    return "|".join([run_id, date, source_part])


def topic_create_payload_digest(row: dict[str, str]) -> str:
    return idempotency.payload_hash({"fields": {key: row.get(key, "") for key in REQUIRED_FIELDS}})


def topic_create_operation(row: dict[str, str]) -> str:
    return idempotency.operation_id(
        TOPIC_CREATE_KIND,
        str(row.get("运行批次") or ""),
        topic_create_business_key(row),
        topic_create_payload_digest(row),
        TABLES[TARGET_TABLE_KEY],
    )


def write_topic_create_ledger(
    row: dict[str, str],
    *,
    status: str,
    remote_id: str = "",
    error: str = "",
    recovery_hint: str = "",
    match_count: int | None = None,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "title_hash": idempotency.target_hash(str(row.get("选题标题") or "")),
        "source_hash": idempotency.target_hash(str(row.get("原始来源标题") or "")),
    }
    if match_count is not None:
        metadata["match_count"] = match_count
    return idempotency.write_ledger_event(
        kind=TOPIC_CREATE_KIND,
        run_id=str(row.get("运行批次") or ""),
        business_key=topic_create_business_key(row),
        status=status,
        target=TABLES[TARGET_TABLE_KEY],
        operation=topic_create_operation(row),
        payload_digest=topic_create_payload_digest(row),
        remote_id=remote_id,
        error=error,
        recovery_hint=recovery_hint,
        metadata=metadata,
    )


def records_by_topic_create_key(records: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    by_key: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        fields = record.get("fields", {})
        key = topic_create_business_key(fields)
        if key.strip("|"):
            by_key.setdefault(key, []).append(record)
    return by_key


def response_record_id(payload: dict[str, Any], index: int) -> str:
    data = payload.get("data", {})
    records = data.get("records") or data.get("items") or []
    if isinstance(records, list) and index < len(records):
        record = records[index]
        if isinstance(record, dict):
            return str(record.get("record_id") or record.get("id") or "")
    return ""


def recover_topic_creates_by_read_back(
    token: str,
    app_token: str,
    table_id: str,
    rows: list[dict[str, str]],
    *,
    error: BaseException,
) -> tuple[int, list[dict[str, Any]]]:
    try:
        records = all_records(token, app_token, table_id)
    except Exception as read_exc:  # noqa: BLE001 - read-back failure should still block unsafe continuation.
        unknowns = []
        for row in rows:
            unknowns.append(write_topic_create_ledger(
                row,
                status="unknown_not_found",
                error=f"create_status_unknown:{error}; read_back_failed:{read_exc}",
                recovery_hint="Read-back failed after 04 batch_create status became unknown; manually inspect Feishu 04 before rerun or card send.",
                match_count=0,
            ))
        return 0, unknowns

    by_key = records_by_topic_create_key(records)
    recovered = 0
    unknowns: list[dict[str, Any]] = []
    for row in rows:
        matches = by_key.get(topic_create_business_key(row), [])
        if len(matches) == 1:
            remote_id = str(matches[0].get("record_id") or "")
            write_topic_create_ledger(
                row,
                status="recovered_by_read_back",
                remote_id=remote_id,
                error=f"create_status_unknown:{error}",
                recovery_hint="Unique 04 record found by business key after batch_create status unknown; safe to continue.",
                match_count=1,
            )
            recovered += 1
        elif not matches:
            unknowns.append(write_topic_create_ledger(
                row,
                status="unknown_not_found",
                error=f"create_status_unknown:{error}",
                recovery_hint="No 04 record found by business key after batch_create status unknown; do not send Topic Card until manually confirmed or safely rerun.",
                match_count=0,
            ))
        else:
            unknowns.append(write_topic_create_ledger(
                row,
                status="unknown_ambiguous",
                error=f"create_status_unknown:{error}",
                recovery_hint="Multiple 04 records found by business key after batch_create status unknown; manually deduplicate before sending Topic Card.",
                match_count=len(matches),
            ))
    return recovered, unknowns


def batch_create(token: str, app_token: str, table_id: str, rows: list[dict[str, str]], run_id: str) -> int:
    total = 0
    for start in range(0, len(rows), 500):
        chunk = rows[start:start + 500]
        for row in chunk:
            write_topic_create_ledger(
                row,
                status="pending",
                recovery_hint="04 candidate create intent recorded before batch_create.",
            )
        try:
            payload = feishu.request_json(
                "POST",
                f"/bitable/v1/apps/{app_token}/tables/{table_id}/records/batch_create",
                token=token,
                body={"records": [{"fields": row} for row in chunk]},
            )
        except Exception as exc:
            recovered, unknowns = recover_topic_creates_by_read_back(token, app_token, table_id, chunk, error=exc)
            total += recovered
            if unknowns:
                raise RuntimeError(
                    f"04 batch_create status unknown for run_id={run_id}; "
                    f"unknown_count={len(unknowns)}. Topic Card must be skipped until manual read-back."
                ) from exc
            time.sleep(0.15)
            continue
        for index, row in enumerate(chunk):
            write_topic_create_ledger(
                row,
                status="succeeded",
                remote_id=response_record_id(payload, index),
                recovery_hint="04 candidate create acknowledged by Feishu batch_create.",
            )
        total += len(chunk)
        time.sleep(0.15)
    return total


def update_existing_top10(token: str, app_token: str, table_id: str, record: dict[str, Any], row: dict[str, str]) -> None:
    # Fully overwrite all fields controlled by this script. This intentionally
    # keeps empty strings so stale Feishu values are cleared instead of retained.
    fields = {key: row.get(key, "") for key in REQUIRED_FIELDS}
    feishu.request_json(
        "PUT",
        f"/bitable/v1/apps/{app_token}/tables/{table_id}/records/{record['record_id']}",
        token=token,
        body={"fields": fields},
    )


def patch_candidate_view(token: str, app_token: str, table_id: str, view_name: str, run_id: str, visible: set[str], extra_filter: dict[str, Any] | None = None) -> dict[str, Any]:
    views = {view.get("view_name"): view for view in list_views(token, app_token, table_id)}
    created: list[str] = []
    if view_name not in views:
        payload = feishu.request_json(
            "POST",
            f"/bitable/v1/apps/{app_token}/tables/{table_id}/views",
            token=token,
            body={"view_name": view_name, "view_type": "grid"},
        )
        views[view_name] = payload.get("data", {}).get("view", payload.get("data", {}))
        created.append(view_name)
        time.sleep(0.1)
    fields = list_fields(token, app_token, table_id)
    view = views.get(view_name, {})
    run_field = fields.get("运行批次")
    if not view.get("view_id") or not run_field:
        return {"created": created, "configured": "missing_view_or_run_field"}
    hidden = [field["field_id"] for name, field in fields.items() if name not in visible]
    conditions = [{
        "field_id": run_field["field_id"],
        "operator": "is",
        "value": json.dumps([run_id], ensure_ascii=False),
    }]
    if extra_filter and extra_filter.get("field") in fields:
        field = fields[extra_filter["field"]]
        filter_values = option_filter_values(field, extra_filter.get("value", []))
        conditions.append({
            "field_id": field["field_id"],
            "operator": extra_filter.get("operator", "is"),
            "value": json.dumps(filter_values, ensure_ascii=False),
        })
    body = {
        "view_name": view_name,
        "property": {
            "filter_info": {
                "conditions": conditions,
                "conjunction": "and",
            },
            "hidden_fields": hidden,
        },
    }
    try:
        feishu.request_json("PATCH", f"/bitable/v1/apps/{app_token}/tables/{table_id}/views/{view['view_id']}", token=token, body=body)
        return {"created": created, "configured": "ok", "hidden_fields": len(hidden)}
    except Exception as exc:
        return {"created": created, "configured": f"failed:{exc}"}


def delete_view_if_exists(token: str, app_token: str, table_id: str, view_name: str) -> dict[str, Any]:
    views = {view.get("view_name"): view for view in list_views(token, app_token, table_id)}
    view = views.get(view_name)
    if not view or not view.get("view_id"):
        return {"deleted": False, "reason": "missing"}
    try:
        feishu.request_json("DELETE", f"/bitable/v1/apps/{app_token}/tables/{table_id}/views/{view['view_id']}", token=token)
        return {"deleted": True}
    except Exception as exc:
        return {"deleted": False, "reason": f"failed:{exc}"}


def ensure_today_top10_view(token: str, app_token: str, table_id: str, run_id: str) -> dict[str, Any]:
    core_visible = set(CORE_VISIBLE_FIELDS)
    detail_visible = set(DETAIL_VISIBLE_FIELDS)
    return {
        "今日候选池": patch_candidate_view(token, app_token, table_id, "今日候选池", run_id, core_visible),
        "今日最值得做": patch_candidate_view(
            token,
            app_token,
            table_id,
            "今日最值得做",
            run_id,
            core_visible,
            {"field": "今日建议级别", "operator": "is", "value": ["今日最值得做"]},
        ),
        "暂存观察": patch_candidate_view(
            token,
            app_token,
            table_id,
            "暂存观察",
            run_id,
            detail_visible,
            {"field": "今日建议级别", "operator": "is", "value": ["暂存观察"]},
        ),
        "删除旧今日Top10视图": delete_view_if_exists(token, app_token, table_id, "今日Top10"),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true", help="Actually write to Feishu. Default is dry-run only.")
    parser.add_argument("--run-id", default="", help="Stable run id shared by 03 内容收件箱 and 04 分析与选题.")
    parser.add_argument("--input", default="", help="Path to the today candidate CSV for this run.")
    args = parser.parse_args()

    date = today_slug()
    run_id = args.run_id or default_run_id()
    input_path = Path(args.input) if args.input else default_today10_path()
    source_rows, omitted_rows = feishu_visible_rows(read_today10(input_path))
    mapped = [map_row(row, idx, date, run_id) for idx, row in enumerate(source_rows, start=1)]
    dry_run_print(mapped)
    if omitted_rows:
        print(f"INFO: omitted {omitted_rows} 暂存观察/不建议制作 rows from Feishu 04 今日候选池.")

    if not args.write:
        print(json.dumps({
            "ok": True,
            "mode": "dry-run",
            "rows": len(mapped),
            "omitted_rows": omitted_rows,
            "input": str(input_path),
        }, ensure_ascii=False, indent=2))
        return 0

    app_token = os.getenv("FEISHU_BASE_APP_TOKEN")
    if not app_token:
        raise SystemExit("FEISHU_BASE_APP_TOKEN is required")
    token = feishu.tenant_token()
    tables = list_tables(token, app_token)
    content_inbox_check = validate_content_inbox_synced(token, app_token, tables, run_id, input_path)
    table_id = resolve_table_id(tables, TARGET_TABLE_KEY)
    if not table_id:
        raise SystemExit(f"Missing Feishu table: {TABLES[TARGET_TABLE_KEY]}")
    created_fields = ensure_fields(token, app_token, table_id)

    existing = all_records(token, app_token, table_id)
    mapped, skipped_recent_duplicates = filter_recent_duplicate_rows(mapped, existing, date, run_id)
    if skipped_recent_duplicates:
        skipped_titles = [row.get("选题标题", "") for row in skipped_recent_duplicates]
        print(json.dumps({
            "event": "skip_recent_duplicates",
            "days": RECENT_DEDUPE_DAYS,
            "count": len(skipped_recent_duplicates),
            "titles": skipped_titles,
        }, ensure_ascii=False, indent=2))
    existing_by_source = {
        (str(record.get("fields", {}).get("推荐日期", "")), str(record.get("fields", {}).get("原始来源标题", ""))): record
        for record in existing
        if record.get("fields", {}).get("原始来源标题")
    }
    existing_by_title = {
        (str(record.get("fields", {}).get("推荐日期", "")), str(record.get("fields", {}).get("选题标题", ""))): record
        for record in existing
    }
    to_create = []
    updated_existing = 0
    updated_titles: list[str] = []
    created_titles: list[str] = []
    for row in mapped:
        record = (
            existing_by_source.get((row["推荐日期"], row.get("原始来源标题", "")))
            or existing_by_title.get((row["推荐日期"], row["选题标题"]))
        )
        if record:
            update_existing_top10(token, app_token, table_id, record, row)
            updated_existing += 1
            updated_titles.append(row["选题标题"])
            time.sleep(0.1)
        else:
            to_create.append(row)
            created_titles.append(row["选题标题"])
    created_records = batch_create(token, app_token, table_id, to_create, run_id) if to_create else 0
    print(json.dumps({
        "ok": True,
        "mode": "write",
        "table": TABLES[TARGET_TABLE_KEY],
        "run_id": run_id,
        "input": str(input_path),
        "created_fields": created_fields,
        "created_records": created_records,
        "updated_existing": updated_existing,
        "skipped_existing": len(mapped) - len(to_create),
        "omitted_rows": omitted_rows,
        "skipped_recent_duplicates": len(skipped_recent_duplicates),
        "content_inbox_check": content_inbox_check,
        "created_titles": created_titles,
        "updated_titles": updated_titles,
        "today_view": ensure_today_top10_view(token, app_token, table_id, run_id),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
