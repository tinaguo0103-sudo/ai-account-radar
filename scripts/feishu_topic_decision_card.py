#!/usr/bin/env python3
"""Send and handle one-message topic decision cards for Feishu.

The source of truth remains Feishu 04 分析与选题. This script builds a single
interactive card containing several candidates, then applies the submitted form
values back to the same table.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import threading
import time
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import feishu_idempotency as idempotency
import push_to_feishu as feishu
from feishu_table_registry import TABLES, resolve_table_id
from local_env import load_local_env
from topic_decision_fields import SELECTION_REASON_OPTIONS


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output" / "decision_cards"
TARGET_TABLE_KEY = "topic_decision"
TOPIC_TABLE_ID_ENV_KEYS = ("FEISHU_TOPIC_TABLE_ID", "FEISHU_TOPIC_DECISION_TABLE_ID")
TOPIC_CREATE_KIND = "topic_candidate_create"
TOPIC_CARD_SEND_KIND = "topic_card_send"
DEFAULT_LIMIT = 7
CARD_EXPIRE_DAYS = 5
DEFAULT_STATUS_FILTER = {"待判断", ""}
ENTER_SCRIPT_PACKAGE_FORM_KEY = "script_package_records"
SCRIPT_PACKAGE_READY_STATUS = "生成脚本包"
SUPPLEMENT_ACTIONS = {"补证据", "存素材", "观察", "暂存观察", "不做"}
SUBMIT_SELECTION_ACTION = "submit_topic_decisions"
SUBMIT_NO_SELECTION_ACTION = "submit_no_selection"
SUPPORTED_SUBMIT_ACTIONS = {SUBMIT_SELECTION_ACTION, SUBMIT_NO_SELECTION_ACTION}
RECEIPT_LOG = OUT / "callback_receipts.jsonl"
CANDIDATE_LEDGER = OUT / "topic_card_candidate_ledger.jsonl"
POSITIVE_REASON_OPTIONS = [
    "有真实业务现场",
    "实验能马上做",
    "证据够",
    "资产价值高",
    "判断够强",
]
NEGATIVE_REASON_OPTIONS = [
    "太泛",
    "太像资讯",
    "没有我的经验",
    "素材不够",
    "制作成本高",
    "事实风险高",
    "以后再说",
]


def latest_run_id() -> str:
    log_path = ROOT / "output" / "latest_write" / "content_sampler_log.json"
    if not log_path.exists():
        return ""
    try:
        return str(json.loads(log_path.read_text(encoding="utf-8")).get("run_id") or "")
    except json.JSONDecodeError:
        return ""


def require_app_token() -> str:
    app_token = os.getenv("FEISHU_BASE_APP_TOKEN")
    if not app_token:
        raise SystemExit("FEISHU_BASE_APP_TOKEN is required")
    return app_token


def normalize(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return "、".join(str(item).strip() for item in value if str(item).strip())
    if isinstance(value, dict):
        return str(value.get("text") or "").strip()
    return str(value).strip()


def selection_reason_value(tags: Any, current_value: Any = None) -> Any:
    values = [str(item).strip() for item in (tags if isinstance(tags, list) else [tags]) if str(item).strip()]
    if isinstance(current_value, list):
        return values
    return "、".join(values)


def compact(value: Any, limit: int) -> str:
    text = " ".join(normalize(value).split())
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


def parse_record_date(value: Any) -> date | None:
    text = normalize(value)
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def candidate_rank(fields: dict[str, Any]) -> int:
    try:
        return int(normalize(fields.get("今日排名")) or "9999")
    except ValueError:
        return 9999


def candidate_title(fields: dict[str, Any]) -> str:
    return normalize(fields.get("选题标题") or fields.get("选题命题") or fields.get("一句话Brief"))


def candidate_dedupe_key(fields: dict[str, Any]) -> str:
    title = candidate_title(fields)
    source = normalize(fields.get("原始来源标题") or fields.get("来源URL") or fields.get("来源内容"))
    key = "|".join(part for part in [source, title] if part)
    return hashlib.sha1(key.encode("utf-8")).hexdigest() if key else ""


def compensation_pool_window(today: date | None = None, days: int = 3) -> tuple[date, date]:
    current = today or datetime.now().date()
    return current - timedelta(days=max(days, 1) - 1), current


def is_candidate_pending(fields: dict[str, Any], include_decided: bool = False) -> bool:
    if include_decided:
        return True
    status = normalize(fields.get("状态"))
    if status not in DEFAULT_STATUS_FILTER:
        return False
    generated = normalize(fields.get("是否已生成脚本稿"))
    return not (generated and generated not in {"否", "未生成"})


def load_card_candidate_ledger() -> set[str]:
    sent: set[str] = set()
    if CANDIDATE_LEDGER.exists():
        for line in CANDIDATE_LEDGER.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if str(event.get("status") or "") in {"sent", "submitted"}:
                record_id = str(event.get("record_id") or "")
                if record_id:
                    sent.add(record_id)
    if RECEIPT_LOG.exists():
        for line in RECEIPT_LOG.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                receipt = json.loads(line)
            except json.JSONDecodeError:
                continue
            sent.update(str(record_id) for record_id in receipt.get("candidate_ids", []) if str(record_id))
    return sent


def card_candidate_value(card: dict[str, Any]) -> dict[str, Any]:
    for element in card.get("body", {}).get("elements", []):
        if element.get("tag") != "form":
            continue
        for form_element in element.get("elements", []):
            if form_element.get("tag") != "column_set":
                continue
            for column in form_element.get("columns", []):
                for button in column.get("elements", []):
                    for behavior in button.get("behaviors", []):
                        value = behavior.get("value") if isinstance(behavior.get("value"), dict) else {}
                        if value.get("action") == SUBMIT_SELECTION_ACTION:
                            return value
    return {}


def write_card_candidate_ledger(card: dict[str, Any], run_id: str, preview_path: str) -> None:
    value = card_candidate_value(card)
    candidate_ids = [str(item) for item in value.get("display_candidate_ids") or value.get("candidate_ids", []) if str(item)]
    snapshots = value.get("candidate_snapshots") if isinstance(value.get("candidate_snapshots"), dict) else {}
    if not candidate_ids:
        return
    CANDIDATE_LEDGER.parent.mkdir(parents=True, exist_ok=True)
    created_at = datetime.now().isoformat(timespec="seconds")
    with CANDIDATE_LEDGER.open("a", encoding="utf-8") as handle:
        for record_id in candidate_ids:
            snapshot = snapshots.get(record_id, {}) if isinstance(snapshots.get(record_id), dict) else {}
            event = {
                "created_at": created_at,
                "status": "sent",
                "run_id": run_id,
                "record_id": record_id,
                "original_run_id": str(snapshot.get("run_id") or ""),
                "original_date": str(snapshot.get("date") or ""),
                "title_hash": hashlib.sha1(str(snapshot.get("title") or "").encode("utf-8")).hexdigest()[:16],
                "preview_path": preview_path,
            }
            handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")


def option_text(index: int, fields: dict[str, Any]) -> str:
    title = compact(fields.get("选题标题"), 32) or f"候选 {index}"
    level = compact(fields.get("今日建议级别"), 8)
    risk = compact(fields.get("AI味风险"), 4)
    original_date = compact(fields.get("推荐日期"), 10)
    suffix = " / ".join(part for part in [original_date, level, f"AI味{risk}" if risk else ""] if part)
    return f"{index}. {title}" + (f"｜{suffix}" if suffix else "")


def candidate_action(fields: dict[str, Any]) -> str:
    return normalize(fields.get("推荐动作"))


def title_permission(fields: dict[str, Any]) -> str:
    return normalize(fields.get("title_permission"))


def is_script_package_candidate(fields: dict[str, Any]) -> bool:
    action = candidate_action(fields)
    permission = title_permission(fields)
    if action == SCRIPT_PACKAGE_READY_STATUS:
        return permission != "不生成标题"
    if action in SUPPLEMENT_ACTIONS:
        return False
    return normalize(fields.get("今日建议级别")) == "今日最值得做" and permission != "不生成标题"


def candidate_caveat(fields: dict[str, Any]) -> str:
    action = candidate_action(fields)
    level = normalize(fields.get("今日建议级别"))
    permission = title_permission(fields)
    missing = compact(fields.get("需要补的证据"), 64)
    if is_script_package_candidate(fields):
        return ""
    parts: list[str] = []
    if action:
        parts.append(action)
    elif level:
        parts.append(level)
    if permission == "内部测试标题":
        parts.append("内部测试标题")
    elif permission == "不生成标题":
        parts.append("缺发布标题")
    if missing:
        parts.append("需补证据")
    label = " / ".join(dict.fromkeys(parts)) or "暂不直接生成"
    return f"处理：{label}；不会进入下方“生成脚本包”勾选列表。"


def card_markdown_for_candidate(index: int, fields: dict[str, Any]) -> str:
    title = compact(fields.get("选题标题"), 50) or f"候选 {index}"
    brief = compact(fields.get("一句话Brief"), 88)
    experiment = compact(fields.get("我要做的实验"), 76)
    evidence = compact(fields.get("可展示证据"), 64)
    missing = compact(fields.get("需要补的证据"), 52)
    direction = compact(fields.get("对应方向"), 18)
    source_mix = compact(fields.get("来源构成"), 48)
    source_weight = compact(fields.get("来源权重类型"), 24)
    translation = compact(fields.get("对标转译角度"), 86)
    aihot_reason = compact(fields.get("AIHOT重大性说明"), 86)
    risk = compact(fields.get("AI味风险"), 8)
    original_date = compact(fields.get("推荐日期"), 10)
    original_run_id = compact(fields.get("运行批次"), 40)
    lines = [f"**{index}. {title}**"]
    meta = "｜".join(part for part in [
        f"日期：{original_date}" if original_date else "",
        f"run：{original_run_id}" if original_run_id else "",
        direction,
        f"AI味：{risk}" if risk else "",
    ] if part)
    if meta:
        lines.append(meta)
    if brief:
        lines.append(f"Brief：{brief}")
    source_line = "｜".join(part for part in [source_mix, source_weight] if part)
    if source_line:
        lines.append(f"来源：{source_line}")
    if translation:
        lines.append(f"转译：{translation}")
    if aihot_reason:
        lines.append(f"重大性：{aihot_reason}")
    if experiment:
        lines.append(f"实验：{experiment}")
    if evidence:
        lines.append(f"证据：{evidence}")
    if missing:
        lines.append(f"缺口：{missing}")
    caveat = candidate_caveat(fields)
    if caveat:
        lines.append(caveat)
    return "\n".join(lines)


def explicit_topic_table_id() -> tuple[str, str]:
    for key in TOPIC_TABLE_ID_ENV_KEYS:
        value = os.getenv(key, "").strip()
        if value:
            return value, key
    return "", ""


def get_topic_table(token: str, app_token: str) -> str:
    explicit_table_id, _source = explicit_topic_table_id()
    if explicit_table_id:
        return explicit_table_id
    table_id = resolve_table_id({table["name"]: table["table_id"] for table in feishu.list_tables(token, app_token)}, TARGET_TABLE_KEY)
    if not table_id:
        raise SystemExit(f"Missing table: {TABLES[TARGET_TABLE_KEY]}")
    return table_id


def all_records(token: str, app_token: str, table_id: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    page_token = ""
    while True:
        query = {"page_size": 500}
        if page_token:
            query["page_token"] = page_token
        payload = feishu.request_json(
            "GET",
            f"/bitable/v1/apps/{app_token}/tables/{table_id}/records?{urlencode(query)}",
            token=token,
        )
        data = payload.get("data", {})
        records.extend(data.get("items", []))
        if not data.get("has_more"):
            return records
        page_token = str(data.get("page_token") or "")


def fetch_candidates(run_id: str, limit: int, include_decided: bool = False) -> tuple[str, str, str, list[dict[str, Any]]]:
    return fetch_candidates_for_card(run_id, limit, include_decided=include_decided)


def fetch_candidates_for_card(
    run_id: str,
    limit: int,
    include_decided: bool = False,
    *,
    strict_run_id: bool = False,
    record_ids: set[str] | None = None,
) -> tuple[str, str, str, list[dict[str, Any]]]:
    token = feishu.tenant_token()
    app_token = require_app_token()
    table_id = get_topic_table(token, app_token)
    window_start, window_end = compensation_pool_window()
    sent_candidate_ids = load_card_candidate_ledger() if not include_decided else set()
    best_by_key: dict[str, dict[str, Any]] = {}
    for record in all_records(token, app_token, table_id):
        fields = record.get("fields", {})
        record_run_id = normalize(fields.get("运行批次"))
        record_date = parse_record_date(fields.get("推荐日期"))
        record_id = str(record.get("record_id") or "")
        if record_ids is not None and record_id not in record_ids:
            continue
        is_today_run = bool(run_id and record_run_id == run_id)
        if strict_run_id and not is_today_run:
            continue
        in_compensation_window = bool(record_date and window_start <= record_date <= window_end)
        if not is_today_run and not in_compensation_window:
            continue
        if not is_candidate_pending(fields, include_decided=include_decided):
            continue
        if record_id in sent_candidate_ids:
            continue
        dedupe_key = candidate_dedupe_key(fields) or record_id
        existing = best_by_key.get(dedupe_key)
        if not existing:
            best_by_key[dedupe_key] = record
            continue
        existing_fields = existing.get("fields", {})
        existing_is_today = normalize(existing_fields.get("运行批次")) == run_id
        if is_today_run and not existing_is_today:
            best_by_key[dedupe_key] = record
        elif is_today_run == existing_is_today and candidate_rank(fields) < candidate_rank(existing_fields):
            best_by_key[dedupe_key] = record
    selected = list(best_by_key.values())
    selected.sort(key=lambda record: (
        candidate_rank(record.get("fields", {})),
        -(parse_record_date(record.get("fields", {}).get("推荐日期")) or date.min).toordinal(),
        normalize(record.get("fields", {}).get("运行批次")),
    ))
    return token, app_token, table_id, selected[:limit]


def select_component(name: str, placeholder: str, options: list[dict[str, str]], required: bool = False) -> dict[str, Any]:
    return {
        "tag": "multi_select_static",
        "name": name,
        "required": required,
        "type": "default",
        "width": "fill",
        "placeholder": {"tag": "plain_text", "content": placeholder},
        "behaviors": [{"type": "callback", "value": {"component": name}}],
        "selected_values": [],
        "options": [
            {
                "text": {"tag": "plain_text", "content": option["text"][:100]},
                "value": option["value"],
            }
            for option in options
        ],
    }


def text_input_component(name: str, placeholder: str) -> dict[str, Any]:
    return {
        "tag": "input",
        "name": name,
        "required": False,
        "width": "fill",
        "placeholder": {"tag": "plain_text", "content": placeholder},
        "default_value": "",
    }


def tag_options(values: list[str]) -> list[dict[str, str]]:
    allowed = [value for value in values if value in SELECTION_REASON_OPTIONS]
    return [{"text": value, "value": value} for value in allowed]


def build_card(records: list[dict[str, Any]], run_id: str) -> dict[str, Any]:
    issued_at = datetime.now(timezone.utc).replace(microsecond=0)
    expires_at = issued_at + timedelta(days=CARD_EXPIRE_DAYS)
    coverage_dates = sorted({
        normalize(record.get("fields", {}).get("推荐日期"))
        for record in records
        if normalize(record.get("fields", {}).get("推荐日期"))
    })
    script_records = [
        (index, record)
        for index, record in enumerate(records, start=1)
        if record.get("record_id") and is_script_package_candidate(record.get("fields", {}))
    ]
    supplement_records = [
        (index, record)
        for index, record in enumerate(records, start=1)
        if record.get("record_id") and not is_script_package_candidate(record.get("fields", {}))
    ]
    options = [
        {"text": option_text(index, record.get("fields", {})), "value": str(record.get("record_id") or "")}
        for index, record in script_records
    ]
    display_candidate_ids = [str(record.get("record_id") or "") for record in records if record.get("record_id")]
    card_meta = {
        "run_id": run_id,
        "candidate_ids": [option["value"] for option in options],
        "display_candidate_ids": display_candidate_ids,
        "supplement_candidate_ids": [str(record.get("record_id") or "") for _index, record in supplement_records],
        "coverage_dates": coverage_dates,
        "card_issued_at": issued_at.isoformat().replace("+00:00", "Z"),
        "card_expires_at": expires_at.isoformat().replace("+00:00", "Z"),
        "card_ttl_days": CARD_EXPIRE_DAYS,
    }
    candidate_snapshots = {
        str(record.get("record_id") or ""): {
            "title": compact(record.get("fields", {}).get("选题标题"), 72),
            "brief": compact(record.get("fields", {}).get("一句话Brief"), 120),
            "experiment": compact(record.get("fields", {}).get("我要做的实验"), 120),
            "run_id": normalize(record.get("fields", {}).get("运行批次")),
            "date": normalize(record.get("fields", {}).get("推荐日期")),
        }
        for record in records
        if record.get("record_id")
    }
    elements: list[dict[str, Any]] = [
        {
            "tag": "markdown",
            "content": f"一张卡片处理一批候选。下方“生成脚本包”只包含已经具备脚本包条件的编号；`补证据 / 可选候选 / 缺发布标题` 只展示判断和缺口，不会进入 06。\n\n提交后，已选记录进入 `{SCRIPT_PACKAGE_READY_STATUS}`；未选的可生成候选标记为 `不做`。如果有选中记录，系统会稍后再发一张卡片让你逐条补制作方向。\n\n这张卡只能提交一次，{CARD_EXPIRE_DAYS} 天后提交无效。\n\n运行批次：`{run_id or '未指定'}`\n\n本次候选覆盖：{('、'.join(coverage_dates) if coverage_dates else '未识别日期')}\n可生成候选：{len(script_records)} 条｜补证据/观察候选：{len(supplement_records)} 条",
        }
    ]
    for index, record in enumerate(records, start=1):
        elements.append({"tag": "markdown", "content": card_markdown_for_candidate(index, record.get("fields", {}))})
        if index != len(records):
            elements.append({"tag": "hr"})

    form_elements: list[dict[str, Any]] = [
        select_component(ENTER_SCRIPT_PACKAGE_FORM_KEY, "生成脚本包：只显示可直接进入 06 的编号", options),
        select_component("positive_reason_tags", "推进原因标签", tag_options(POSITIVE_REASON_OPTIONS)),
        text_input_component("manual_reason", "手工原因：标签不够用时，写一句真实判断"),
        {
            "tag": "column_set",
            "columns": [
                {
                    "tag": "column",
                    "width": "auto",
                    "elements": [
                        {
                            "tag": "button",
                            "type": "primary",
                            "width": "default",
                            "text": {"tag": "plain_text", "content": "提交选择"},
                            "form_action_type": "submit",
                            "name": "submit_topic_decisions",
                            "behaviors": [
                                {
                                    "type": "callback",
                                    "value": {
                                        "action": SUBMIT_SELECTION_ACTION,
                                        **card_meta,
                                        "candidate_snapshots": candidate_snapshots,
                                        "unselected_status": "不做",
                                    },
                                }
                            ],
                        }
                    ],
                },
                {
                    "tag": "column",
                    "width": "auto",
                    "elements": [
                        {
                            "tag": "button",
                            "type": "default",
                            "width": "default",
                            "text": {"tag": "plain_text", "content": "本批都不选"},
                            "form_action_type": "submit",
                            "name": "submit_no_selection",
                            "behaviors": [
                                {
                                    "type": "callback",
                                    "value": {
                                        "action": SUBMIT_NO_SELECTION_ACTION,
                                        **card_meta,
                                        "candidate_snapshots": candidate_snapshots,
                                        "unselected_status": "不做",
                                    },
                                }
                            ],
                        }
                    ],
                },
                {
                    "tag": "column",
                    "width": "auto",
                    "elements": [
                        {
                            "tag": "button",
                            "type": "default",
                            "width": "default",
                            "text": {"tag": "plain_text", "content": "重置"},
                            "form_action_type": "reset",
                            "name": "reset_topic_decisions",
                        }
                    ],
                },
            ],
        },
    ]
    elements.append(
        {
            "tag": "form",
            "name": "topic_decision_batch",
            "padding": "8px 0px 0px 0px",
            "vertical_spacing": "8px",
            "elements": form_elements,
        }
    )
    return {
        "schema": "2.0",
        "config": {
            "update_multi": True,
            "enable_forward": False,
            "width_mode": "fill",
        },
        "header": {
            "template": "blue",
            "title": {
                "tag": "plain_text",
                "content": f"今日选题速选 · {datetime.now().strftime('%Y-%m-%d')}",
            },
        },
        "body": {"elements": elements},
    }


def write_card_preview(card: dict[str, Any], run_id: str) -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    safe_run_id = run_id or "latest"
    path = OUT / f"{datetime.now().strftime('%Y-%m-%d')}_{safe_run_id}_topic_decision_card.json"
    latest = OUT / "latest_topic_decision_card.json"
    text = json.dumps(card, ensure_ascii=False, indent=2)
    path.write_text(text, encoding="utf-8")
    latest.write_text(text, encoding="utf-8")
    return path


def card_send_business_key(run_id: str, receive_id_type: str, receive_id: str, uuid: str) -> str:
    return "|".join([run_id, receive_id_type, idempotency.target_hash(receive_id), uuid])


def is_status_unknown_error(exc: BaseException) -> bool:
    return "status unknown" in str(exc).lower()


def write_card_send_ledger(
    *,
    run_id: str,
    receive_id_type: str,
    receive_id: str,
    uuid: str,
    status: str,
    payload_digest: str,
    preview_path: str = "",
    remote_id: str = "",
    error: str = "",
    recovery_hint: str = "",
) -> dict[str, Any]:
    business_key = card_send_business_key(run_id, receive_id_type, receive_id, uuid)
    metadata = {
        "receive_id_type": receive_id_type,
        "receive_id_hash": idempotency.target_hash(receive_id),
        "preview_path": preview_path,
        "uuid": uuid,
    }
    return idempotency.write_ledger_event(
        kind=TOPIC_CARD_SEND_KIND,
        run_id=run_id,
        business_key=business_key,
        status=status,
        target="Feishu Topic Card",
        operation=idempotency.operation_id(TOPIC_CARD_SEND_KIND, run_id, business_key, payload_digest, "Feishu Topic Card"),
        payload_digest=payload_digest,
        remote_id=remote_id,
        error=error,
        recovery_hint=recovery_hint,
        metadata=metadata,
    )


def ensure_no_blocking_unknown_for_card_send(run_id: str) -> list[dict[str, Any]]:
    return idempotency.blocking_unknowns(run_id=run_id, kinds={TOPIC_CREATE_KIND, TOPIC_CARD_SEND_KIND})


def send_card(token: str, card: dict[str, Any], run_id: str, receive_id: str, receive_id_type: str, preview_path: str = "") -> dict[str, Any]:
    if not receive_id:
        raise SystemExit("Missing receive_id. Set FEISHU_CARD_RECEIVE_ID or pass --receive-id.")
    seed = "|".join([run_id or datetime.now().strftime("%Y%m%d%H%M"), receive_id_type, receive_id])
    uuid = f"topic-decision-card-{hashlib.sha1(seed.encode('utf-8')).hexdigest()[:16]}"
    body = {
        "receive_id": receive_id,
        "msg_type": "interactive",
        "content": json.dumps(card, ensure_ascii=False),
        "uuid": uuid,
    }
    payload_digest = idempotency.payload_hash({
        "uuid": uuid,
        "receive_id_type": receive_id_type,
        "receive_id_hash": idempotency.target_hash(receive_id),
        "content_hash": idempotency.payload_hash(card),
    })
    write_card_send_ledger(
        run_id=run_id,
        receive_id_type=receive_id_type,
        receive_id=receive_id,
        uuid=uuid,
        status="pending",
        payload_digest=payload_digest,
        preview_path=preview_path,
        recovery_hint="Topic Card send intent recorded before Feishu message POST.",
    )
    try:
        payload = feishu.request_json(
            "POST",
            f"/im/v1/messages?receive_id_type={receive_id_type}",
            token=token,
            body=body,
        )
    except Exception as exc:
        status = "delivery_unknown" if is_status_unknown_error(exc) else "failed_before_send"
        hint = (
            "Message delivery status unknown; manually confirm chat delivery before rerunning the same run_id."
            if status == "delivery_unknown"
            else "Message send failed before a status-unknown response; inspect error before rerun."
        )
        write_card_send_ledger(
            run_id=run_id,
            receive_id_type=receive_id_type,
            receive_id=receive_id,
            uuid=uuid,
            status=status,
            payload_digest=payload_digest,
            preview_path=preview_path,
            error=exc,
            recovery_hint=hint,
        )
        raise
    message_id = str(payload.get("data", {}).get("message_id") or "")
    write_card_send_ledger(
        run_id=run_id,
        receive_id_type=receive_id_type,
        receive_id=receive_id,
        uuid=uuid,
        status="succeeded",
        payload_digest=payload_digest,
        preview_path=preview_path,
        remote_id=message_id,
        recovery_hint="Topic Card send acknowledged by Feishu.",
    )
    return payload


def parse_receive_targets(targets: list[str], receive_id: str, receive_id_type: str) -> list[tuple[str, str]]:
    parsed: list[tuple[str, str]] = []
    for raw in targets:
        for part in raw.split(","):
            text = part.strip()
            if not text:
                continue
            if ":" not in text:
                raise SystemExit(f"Invalid receive target: {text}. Expected type:id, e.g. open_id:ou_xxx")
            target_type, target_id = text.split(":", 1)
            target_type = target_type.strip()
            target_id = target_id.strip()
            if not target_type or not target_id:
                raise SystemExit(f"Invalid receive target: {text}. Expected type:id, e.g. open_id:ou_xxx")
            parsed.append((target_type, target_id))
    if not parsed and receive_id:
        parsed.append((receive_id_type, receive_id))
    if not parsed:
        raise SystemExit("Missing receive target. Set FEISHU_CARD_RECEIVE_TARGETS or FEISHU_CARD_RECEIVE_ID, or pass --receive-target type:id.")
    return parsed


def coerce_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    if isinstance(value, str):
        return [value] if value else []
    return []


def canonical_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): canonical_value(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        normalized_items = [canonical_value(item) for item in value]
        return sorted(normalized_items, key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True))
    if value is None:
        return ""
    return str(value).strip()


def submission_fingerprint(action_name: str, run_id: str, candidate_ids: list[str], form_value: dict[str, Any], mode: str) -> str:
    payload = {
        "action": action_name,
        "mode": mode,
        "run_id": run_id,
        "candidate_ids": sorted(candidate_ids),
        "form_value": canonical_value(form_value),
    }
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_receipts() -> set[str]:
    if not RECEIPT_LOG.exists():
        return set()
    receipts: set[str] = set()
    for line in RECEIPT_LOG.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            receipt = json.loads(line)
        except json.JSONDecodeError:
            continue
        key = str(receipt.get("key") or "")
        if key:
            receipts.add(key)
    return receipts


def remember_receipt(key: str, *, action_name: str, run_id: str, candidate_ids: list[str]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    receipt = {
        "key": key,
        "action": action_name,
        "run_id": run_id,
        "candidate_ids": candidate_ids,
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    with RECEIPT_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(receipt, ensure_ascii=False, sort_keys=True) + "\n")
    CANDIDATE_LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with CANDIDATE_LEDGER.open("a", encoding="utf-8") as handle:
        for record_id in candidate_ids:
            handle.write(json.dumps({
                "created_at": datetime.now().isoformat(timespec="seconds"),
                "status": "submitted",
                "run_id": run_id,
                "record_id": record_id,
            }, ensure_ascii=False, sort_keys=True) + "\n")


def decisions_from_form(
    form_value: dict[str, Any],
    candidate_ids: list[str] | None = None,
    *,
    force_no_selection: bool = False,
) -> dict[str, dict[str, Any]]:
    decisions: dict[str, dict[str, Any]] = {}
    positive_tags = coerce_list(form_value.get("positive_reason_tags"))
    manual_reason = compact(form_value.get("manual_reason"), 240)
    if force_no_selection:
        return {
            record_id: {"status": "不做", "tags": [], "manual_reason": manual_reason}
            for record_id in candidate_ids or []
            if record_id
        }

    selected_record_ids = set(coerce_list(form_value.get(ENTER_SCRIPT_PACKAGE_FORM_KEY)))
    for record_id in selected_record_ids:
        decisions[record_id] = {"status": SCRIPT_PACKAGE_READY_STATUS, "tags": positive_tags, "manual_reason": manual_reason}

    for record_id in candidate_ids or []:
        if record_id and record_id not in selected_record_ids and record_id not in decisions:
            decisions[record_id] = {"status": "不做", "tags": [], "manual_reason": ""}
    return decisions


def apply_form_value(
    token: str,
    app_token: str,
    table_id: str,
    form_value: dict[str, Any],
    *,
    candidate_ids: list[str] | None = None,
    candidate_snapshots: dict[str, Any] | None = None,
    run_id: str = "",
    write: bool = False,
    force_no_selection: bool = False,
) -> dict[str, Any]:
    decisions = decisions_from_form(form_value, candidate_ids, force_no_selection=force_no_selection)
    records = {record.get("record_id"): record for record in all_records(token, app_token, table_id)}
    updates: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for record_id, decision in decisions.items():
        record = records.get(record_id)
        if not record:
            skipped.append({"record_id": record_id, "reason": "record_not_found"})
            continue
        fields = record.get("fields", {})
        if run_id and normalize(fields.get("运行批次")) != run_id:
            snapshot = (candidate_snapshots or {}).get(record_id, {})
            snapshot_run_id = str(snapshot.get("run_id") or "") if isinstance(snapshot, dict) else ""
            if snapshot_run_id != normalize(fields.get("运行批次")):
                skipped.append({"record_id": record_id, "title": normalize(fields.get("选题标题")), "reason": "run_id_mismatch"})
                continue
        update_fields: dict[str, Any] = {
            "状态": decision["status"],
            "学习状态": "待学习",
            "选择原因标签": selection_reason_value(decision["tags"], fields.get("选择原因标签")),
            "人工一句话判断": decision.get("manual_reason") or "",
        }
        updates.append({
            "record_id": record_id,
            "title": normalize(fields.get("选题标题")),
            "fields": update_fields,
        })

    if write:
        for update in updates:
            feishu.request_json(
                "PUT",
                f"/bitable/v1/apps/{app_token}/tables/{table_id}/records/{update['record_id']}",
                token=token,
                body={"fields": update["fields"]},
            )
            time.sleep(0.08)
    return {
        "ok": True,
        "mode": "write" if write else "dry-run",
        "run_id": run_id,
        "updated_count": len(updates) if write else 0,
        "candidate_update_count": len(updates),
        "updates": updates,
        "skipped": skipped,
    }


def process_card_submission(
    token: str,
    app_token: str,
    table_id: str,
    value: dict[str, Any],
    form_value: dict[str, Any],
    *,
    write: bool,
    receipts: set[str] | None = None,
    receipt_lock: threading.Lock | None = None,
) -> dict[str, Any]:
    action_name = str(value.get("action") or "")
    if action_name not in SUPPORTED_SUBMIT_ACTIONS:
        return {
            "ok": False,
            "ignored": True,
            "reason": "unsupported_action",
            "action": action_name,
        }

    candidate_ids = coerce_list(value.get("candidate_ids"))
    run_id = str(value.get("run_id") or "")
    if action_name == SUBMIT_NO_SELECTION_ACTION:
        form_value = dict(form_value)
        form_value[ENTER_SCRIPT_PACKAGE_FORM_KEY] = []
        form_value["positive_reason_tags"] = []
    mode = "write" if write else "dry-run"
    receipt_key = submission_fingerprint(action_name, run_id, candidate_ids, form_value, mode)

    if receipts is not None:
        lock = receipt_lock or threading.Lock()
        with lock:
            if receipt_key in receipts:
                return {
                    "ok": True,
                    "duplicate": True,
                    "mode": "write" if write else "dry-run",
                    "run_id": run_id,
                    "updated_count": 0,
                    "candidate_update_count": 0,
                    "updates": [],
                    "skipped": [],
                }

    summary = apply_form_value(
        token,
        app_token,
        table_id,
        form_value,
        candidate_ids=candidate_ids,
        candidate_snapshots=value.get("candidate_snapshots") if isinstance(value.get("candidate_snapshots"), dict) else {},
        run_id=run_id,
        write=write,
        force_no_selection=action_name == SUBMIT_NO_SELECTION_ACTION,
    )
    summary["action"] = action_name
    summary["duplicate"] = False

    if receipts is not None:
        lock = receipt_lock or threading.Lock()
        with lock:
            receipts.add(receipt_key)
            remember_receipt(receipt_key, action_name=action_name, run_id=run_id, candidate_ids=candidate_ids)
    return summary


def callback_response(status: str, content: str) -> bytes:
    return json.dumps({"toast": {"type": status, "content": content}}, ensure_ascii=False).encode("utf-8")


def serve_callback(host: str, port: int, write: bool) -> None:
    token = feishu.tenant_token()
    app_token = require_app_token()
    table_id = get_topic_table(token, app_token)
    verification_token = os.getenv("FEISHU_VERIFICATION_TOKEN", "")
    receipts = load_receipts()
    receipt_lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 - stdlib callback name
            length = int(self.headers.get("Content-Length", "0") or "0")
            payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
            if "challenge" in payload:
                body = json.dumps({"challenge": payload["challenge"]}, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if verification_token and payload.get("header", {}).get("token") != verification_token:
                body = callback_response("error", "回调 token 校验失败")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            event = payload.get("event", {})
            action = event.get("action", {})
            value = action.get("value") if isinstance(action.get("value"), dict) else {}
            form_value = action.get("form_value") or {}
            if value.get("action") not in SUPPORTED_SUBMIT_ACTIONS:
                body = callback_response("warning", "不是选题速选提交")
            else:
                summary = process_card_submission(
                    token,
                    app_token,
                    table_id,
                    value,
                    form_value,
                    write=write,
                    receipts=receipts,
                    receipt_lock=receipt_lock,
                )
                count = summary["updated_count"] if write else summary["candidate_update_count"]
                if summary.get("duplicate"):
                    body = callback_response("warning", "这次提交已经处理过")
                else:
                    verb = "已回写" if write else "已接收预演"
                    body = callback_response("success", f"{verb} {count} 条选择")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt: str, *args: Any) -> None:
            print(fmt % args, file=sys.stderr)

    server = ThreadingHTTPServer((host, port), Handler)
    print(json.dumps({"ok": True, "listening": f"http://{host}:{port}", "mode": "write" if write else "dry-run"}, ensure_ascii=False))
    server.serve_forever()


def serve_long_connection(write: bool) -> None:
    try:
        import lark_oapi as lark
        from lark_oapi.core.enum import LogLevel
    except ModuleNotFoundError as exc:
        raise SystemExit("Missing dependency: install lark-oapi in the project environment first.") from exc

    app_id = os.getenv("FEISHU_APP_ID", "")
    app_secret = os.getenv("FEISHU_APP_SECRET", "")
    if not app_id or not app_secret:
        raise SystemExit("FEISHU_APP_ID and FEISHU_APP_SECRET are required")

    app_token = require_app_token()
    table_id_cache = {"value": ""}
    receipts = load_receipts()
    receipt_lock = threading.Lock()

    def table_id_for(token: str) -> str:
        if not table_id_cache["value"]:
            table_id_cache["value"] = get_topic_table(token, app_token)
        return table_id_cache["value"]

    def handle_card_action(data: Any) -> dict[str, Any]:
        event = getattr(data, "event", None)
        action = getattr(event, "action", None) if event else None
        value = getattr(action, "value", None) or {}
        form_value = getattr(action, "form_value", None) or {}
        if value.get("action") not in SUPPORTED_SUBMIT_ACTIONS:
            return {"toast": {"type": "warning", "content": "不是选题速选提交"}}

        token = feishu.tenant_token()
        summary = process_card_submission(
            token,
            app_token,
            table_id_for(token),
            value,
            form_value,
            write=write,
            receipts=receipts,
            receipt_lock=receipt_lock,
        )
        count = summary["updated_count"] if write else summary["candidate_update_count"]
        print(json.dumps(summary, ensure_ascii=False), flush=True)
        if summary.get("duplicate"):
            return {"toast": {"type": "warning", "content": "这次提交已经处理过"}}
        verb = "已回写" if write else "已接收预演"
        return {"toast": {"type": "success", "content": f"{verb} {count} 条选择"}}

    verification_token = os.getenv("FEISHU_VERIFICATION_TOKEN", "")
    encrypt_key = os.getenv("FEISHU_ENCRYPT_KEY", "")
    event_handler = (
        lark.EventDispatcherHandler.builder(encrypt_key, verification_token, LogLevel.INFO)
        .register_p2_card_action_trigger(handle_card_action)
        .build()
    )
    print(
        json.dumps(
            {
                "ok": True,
                "connection": "starting",
                "mode": "write" if write else "dry-run",
                "callback": "card.action.trigger",
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    lark.ws.Client(app_id, app_secret, event_handler=event_handler, log_level=LogLevel.INFO).start()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build, send, and apply Feishu topic decision cards.")
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("build", help="Build a card preview JSON from Feishu 04.")
    build.add_argument("--run-id", default="latest")
    build.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    build.add_argument("--include-decided", action="store_true")
    build.add_argument("--strict-run-id", action="store_true", help="Only include records whose 运行批次 exactly matches --run-id; disables compensation-pool additions.")
    build.add_argument("--record-id", action="append", default=[], help="Limit preview to specific Feishu record_id. Can be repeated.")

    send = sub.add_parser("send", help="Send the daily decision card as one interactive bot message.")
    send.add_argument("--run-id", default="latest")
    send.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    send.add_argument("--include-decided", action="store_true")
    send.add_argument("--strict-run-id", action="store_true", help="Only include records whose 运行批次 exactly matches --run-id; disables compensation-pool additions.")
    send.add_argument("--record-id", action="append", default=[], help="Limit send preview to specific Feishu record_id. Can be repeated.")
    send.add_argument("--receive-id", default=os.getenv("FEISHU_CARD_RECEIVE_ID", ""))
    send.add_argument("--receive-id-type", default=os.getenv("FEISHU_CARD_RECEIVE_ID_TYPE", "open_id"))
    send.add_argument("--receive-target", action="append", default=[], help="Receive target in type:id form. Can be repeated. Env FEISHU_CARD_RECEIVE_TARGETS also supports comma-separated type:id values.")
    send.add_argument("--dry-run", action="store_true")

    apply = sub.add_parser("apply", help="Apply submitted form_value JSON back to Feishu 04.")
    apply.add_argument("--run-id", default="latest")
    apply.add_argument("--form-value-json", required=True)
    apply.add_argument("--candidate-ids-json", default="[]")
    apply.add_argument("--write", action="store_true")
    apply.add_argument("--no-selection", action="store_true", help="Treat the whole submitted batch as not selected.")

    serve = sub.add_parser("serve-callback", help="Run a minimal card callback webhook receiver.")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8787)
    serve.add_argument("--write", action="store_true")

    long_conn = sub.add_parser("serve-long-connection", help="Run a Feishu SDK long-connection receiver for card callbacks.")
    long_conn.add_argument("--write", action="store_true")
    return parser.parse_args()


def resolved_run_id(raw: str) -> str:
    return latest_run_id() if raw == "latest" else raw


def main() -> int:
    load_local_env()
    args = parse_args()
    if args.command in {"build", "send"}:
        run_id = resolved_run_id(args.run_id)
        record_ids = {str(item).strip() for item in args.record_id if str(item).strip()} or None
        token, _app_token, _table_id, records = fetch_candidates_for_card(
            run_id,
            args.limit,
            include_decided=args.include_decided,
            strict_run_id=args.strict_run_id,
            record_ids=record_ids,
        )
        card = build_card(records, run_id)
        preview_path = write_card_preview(card, run_id)
        summary: dict[str, Any] = {
            "ok": True,
            "run_id": run_id,
            "record_count": len(records),
            "strict_run_id": args.strict_run_id,
            "record_id_filter_count": len(record_ids or []),
            "coverage_dates": card_candidate_value(card).get("coverage_dates", []),
            "candidate_ids": card_candidate_value(card).get("candidate_ids", []),
            "supplement_candidate_ids": card_candidate_value(card).get("supplement_candidate_ids", []),
            "preview_path": str(preview_path),
            "latest_preview_path": str(OUT / "latest_topic_decision_card.json"),
        }
        if args.command == "send":
            if args.dry_run:
                target_inputs = [os.getenv("FEISHU_CARD_RECEIVE_TARGETS", ""), *args.receive_target]
                if args.receive_id or any(part.strip() for raw in target_inputs for part in raw.split(",")):
                    targets = parse_receive_targets(target_inputs, args.receive_id, args.receive_id_type)
                    summary["targets"] = [{"receive_id_type": target_type, "receive_id": target_id} for target_type, target_id in targets]
                summary["send"] = "dry-run"
            else:
                unknowns = ensure_no_blocking_unknown_for_card_send(run_id)
                if unknowns:
                    print(json.dumps({
                        "ok": False,
                        "send": "blocked_by_feishu_idempotency_unknown",
                        "run_id": run_id,
                        "unknown_count": len(unknowns),
                        "summary": idempotency.guard_summary(run_id, unknowns),
                    }, ensure_ascii=False, indent=2))
                    return 2
                targets = parse_receive_targets(
                    [os.getenv("FEISHU_CARD_RECEIVE_TARGETS", ""), *args.receive_target],
                    args.receive_id,
                    args.receive_id_type,
                )
                summary["targets"] = [{"receive_id_type": target_type, "receive_id": target_id} for target_type, target_id in targets]
                sends = []
                candidate_ledger_written = False
                for target_type, target_id in targets:
                    sends.append(send_card(token, card, run_id, target_id, target_type, preview_path=str(preview_path)).get("data", {}))
                    if not candidate_ledger_written:
                        write_card_candidate_ledger(card, run_id, str(preview_path))
                        candidate_ledger_written = True
                summary["send"] = sends
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0

    if args.command == "apply":
        run_id = resolved_run_id(args.run_id)
        token = feishu.tenant_token()
        app_token = require_app_token()
        table_id = get_topic_table(token, app_token)
        form_value = json.loads(args.form_value_json)
        candidate_ids = coerce_list(json.loads(args.candidate_ids_json))
        print(json.dumps(apply_form_value(token, app_token, table_id, form_value, candidate_ids=candidate_ids, run_id=run_id, write=args.write, force_no_selection=args.no_selection), ensure_ascii=False, indent=2))
        return 0

    if args.command == "serve-callback":
        serve_callback(args.host, args.port, args.write)
        return 0

    if args.command == "serve-long-connection":
        serve_long_connection(args.write)
        return 0

    raise SystemExit(f"Unknown command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
