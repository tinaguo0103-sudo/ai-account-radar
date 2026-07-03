#!/usr/bin/env python3
"""Build a daily learning digest from topic and script-package feedback.

Default mode is local-only. Feishu writes are opt-in and protected so staging
cannot accidentally write production learning records from production source
tables.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote

from local_env import load_local_env
import push_to_feishu as feishu
from feishu_table_registry import TABLES, resolve_table_id
from script_package_shared import ensure_text_fields


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output" / "daily_feedback_learning"
LATEST_JSON = OUT / "latest_daily_feedback_learning.json"
LATEST_MD = OUT / "latest_daily_feedback_learning.md"

TOPIC_TABLE_ENV = "FEISHU_TOPIC_DECISION_TABLE_ID"
SCRIPT_TABLE_ENV = "FEISHU_SCRIPT_PACKAGE_TABLE_ID"
LEARNING_TABLE_ENV = "FEISHU_LEARNING_TABLE_ID"
LEARNING_CONFIRM_ACTION = "submit_learning_feedback_confirmation"
LEARNING_FEEDBACK_TARGETS_ENV = "FEISHU_LEARNING_FEEDBACK_RECEIVE_TARGETS"
LEARNING_CONFIRM_NOTE_KEYS = ["learning_confirmation_note", "learning_confirmation_note__2", "learning_confirmation_note__3"]
LEARNING_CONFIRM_DECISIONS = ["已采纳", "部分采纳", "暂不采纳"]

TOPIC_TEST_TABLE_NAME = "04 分析与选题__测试"
SCRIPT_TEST_TABLE_NAME = "06 完整脚本与制作包__测试"
LEARNING_TEST_TABLE_NAME = "08 学习记录__测试"

POSITIVE_TOPIC_STATUSES = {"生成脚本包"}
NEGATIVE_TOPIC_STATUSES = {"暂存", "归档", "不做"}
DECISION_TOPIC_STATUSES = POSITIVE_TOPIC_STATUSES | NEGATIVE_TOPIC_STATUSES
EXCLUDED_TOPIC_LEARNING_STATUSES = {"已学习", "忽略", "待确认学习"}
EXCLUDED_SCRIPT_LEARNING_STATUSES = {"已学习", "忽略", "待确认学习"}

TOPIC_LEARNING_FIELDS = [
    "状态",
    "学习状态",
    "运行批次",
    "推荐日期",
    "选题标题",
    "今日建议级别",
    "对应方向",
    "AI味风险",
    "一句话Brief",
    "我要做的实验",
    "我的工作流痛点",
    "AI介入点",
    "可沉淀资产",
    "可展示证据",
    "需要补的证据",
    "推荐理由",
    "不建议做的原因",
    "选择原因标签",
    "人工一句话判断",
    "选择学习批次",
    "选择学习摘要",
]

SCRIPT_FEEDBACK_FIELDS = [
    "脚本标题",
    "关联选题",
    "脚本状态",
    "核心观点",
    "开头钩子",
    "飞书文档",
    "人工质量反馈",
    "质量问题标签",
    "人工修改意见",
    "反馈时间",
    "反馈来源",
    "内容学习状态",
    "内容学习批次",
    "内容学习摘要",
]

LEARNING_RECORD_FIELDS = [
    "学习批次",
    "学习日期",
    "环境",
    "学习类型",
    "样本数量",
    "选题样本数",
    "内容反馈样本数",
    "学习结论",
    "建议沉淀规则",
    "不应沉淀的个案",
    "关联04记录",
    "关联06记录",
    "确认状态",
    "确认时间",
    "确认备注",
    "Skill同步状态",
]


def now_slug() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def batch_id() -> str:
    return "learn_" + datetime.now().strftime("%Y%m%d_%H%M%S")


def compact(value: Any, limit: int = 500) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[:limit].rstrip() + "..."


def card_uuid(prefix: str, *parts: str) -> str:
    seed = "|".join(str(part) for part in parts if str(part))
    digest = hashlib.sha1((seed or prefix).encode("utf-8")).hexdigest()[:16]
    return f"{prefix}-{digest}"[:50]


def normalize(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return "、".join(str(item).strip() for item in value if str(item).strip())
    return str(value).strip()


def normalize_tags(value: Any) -> list[str]:
    text = normalize(value)
    if not text:
        return []
    return [part.strip() for part in text.replace("；", "、").replace(",", "、").split("、") if part.strip()]


def parse_learning_card_targets() -> list[tuple[str, str]]:
    raw = os.getenv(LEARNING_FEEDBACK_TARGETS_ENV, "").strip()
    targets: list[tuple[str, str]] = []
    for part in raw.split(","):
        item = part.strip()
        if not item or ":" not in item:
            continue
        receive_id_type, receive_id = item.split(":", 1)
        receive_id_type = receive_id_type.strip()
        receive_id = receive_id.strip()
        if receive_id_type and receive_id:
            targets.append((receive_id_type, receive_id))
    return targets


def env_label() -> str:
    explicit = (os.getenv("AI_ACCOUNT_RADAR_ENV") or "").strip().lower()
    explicit_file = (os.getenv("AI_ACCOUNT_RADAR_ENV_FILE") or os.getenv("ENV_FILE") or "").strip().lower()
    if explicit:
        return "production" if explicit in {"prod", "production"} else explicit
    if any(name in explicit_file for name in ("staging", "stage", "test", "测试")):
        return "staging"
    return "production"


def is_test_table_name(name: str) -> bool:
    lowered = name.lower()
    return "测试" in name or "test" in lowered or "staging" in lowered


def table_map(token: str, app_token: str) -> dict[str, str]:
    return {table["name"]: table["table_id"] for table in feishu.list_tables(token, app_token)}


def table_name_by_id(tables_by_name: dict[str, str], table_id: str) -> str:
    for name, value in tables_by_name.items():
        if value == table_id:
            return name
    return ""


def resolve_table(
    tables_by_name: dict[str, str],
    *,
    env_name: str,
    registry_key: str | None = None,
    fallback_name: str = "",
) -> tuple[str, str, bool]:
    explicit = os.getenv(env_name, "").strip()
    if explicit:
        return explicit, table_name_by_id(tables_by_name, explicit), True
    if fallback_name and fallback_name in tables_by_name:
        return tables_by_name[fallback_name], fallback_name, False
    if registry_key:
        table_id = resolve_table_id(tables_by_name, registry_key) or ""
        return table_id, table_name_by_id(tables_by_name, table_id), False
    return "", "", False


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
        page_token = str(data.get("page_token") or "")


def topic_sample(record: dict[str, Any]) -> dict[str, Any]:
    fields = record.get("fields", {})
    status = normalize(fields.get("状态"))
    return {
        "record_id": str(record.get("record_id") or ""),
        "run_id": normalize(fields.get("运行批次")),
        "date": normalize(fields.get("推荐日期")),
        "title": normalize(fields.get("选题标题")),
        "status": status,
        "is_positive": status in POSITIVE_TOPIC_STATUSES,
        "level": normalize(fields.get("今日建议级别")),
        "direction": normalize(fields.get("对应方向")),
        "ai_taste_risk": normalize(fields.get("AI味风险")),
        "brief": normalize(fields.get("一句话Brief")),
        "experiment": normalize(fields.get("我要做的实验")),
        "pain": normalize(fields.get("我的工作流痛点")),
        "ai_intervention": normalize(fields.get("AI介入点")),
        "asset": normalize(fields.get("可沉淀资产")),
        "demo_evidence": normalize(fields.get("可展示证据")),
        "missing_evidence": normalize(fields.get("需要补的证据")),
        "recommend_reason": normalize(fields.get("推荐理由")),
        "reject_reason": normalize(fields.get("不建议做的原因")),
        "selection_tags": normalize_tags(fields.get("选择原因标签")),
        "human_note": normalize(fields.get("人工一句话判断")),
        "learning_status": normalize(fields.get("学习状态")),
    }


def script_feedback_sample(record: dict[str, Any]) -> dict[str, Any]:
    fields = record.get("fields", {})
    return {
        "record_id": str(record.get("record_id") or ""),
        "title": normalize(fields.get("脚本标题") or fields.get("关联选题")),
        "script_status": normalize(fields.get("脚本状态")),
        "core_viewpoint": normalize(fields.get("核心观点")),
        "opening_hook": normalize(fields.get("开头钩子")),
        "doc_url": normalize(fields.get("飞书文档")),
        "quality": normalize(fields.get("人工质量反馈")),
        "issue_tags": normalize_tags(fields.get("质量问题标签")),
        "note": normalize(fields.get("人工修改意见")),
        "feedback_at": normalize(fields.get("反馈时间")),
        "feedback_source": normalize(fields.get("反馈来源")),
        "learning_status": normalize(fields.get("内容学习状态")),
    }


def select_topic_samples(records: list[dict[str, Any]], include_learned: bool) -> list[dict[str, Any]]:
    samples = [topic_sample(record) for record in records]
    return [
        sample for sample in samples
        if sample["status"] in DECISION_TOPIC_STATUSES
        and (include_learned or sample["learning_status"] not in EXCLUDED_TOPIC_LEARNING_STATUSES)
    ]


def select_script_feedback(records: list[dict[str, Any]], include_learned: bool) -> list[dict[str, Any]]:
    samples = [script_feedback_sample(record) for record in records]
    return [
        sample for sample in samples
        if (sample["quality"] or sample["issue_tags"] or sample["note"])
        and (include_learned or sample["learning_status"] not in EXCLUDED_SCRIPT_LEARNING_STATUSES)
    ]


def summarize(topic_samples: list[dict[str, Any]], script_samples: list[dict[str, Any]], learning_batch_id: str, environment: str) -> dict[str, Any]:
    positive_topics = [sample for sample in topic_samples if sample["is_positive"]]
    negative_topics = [sample for sample in topic_samples if not sample["is_positive"]]
    positive_tags = Counter(tag for sample in positive_topics for tag in sample["selection_tags"])
    negative_tags = Counter(tag for sample in negative_topics for tag in sample["selection_tags"])
    positive_directions = Counter(sample["direction"] or "未标方向" for sample in positive_topics)
    negative_directions = Counter(sample["direction"] or "未标方向" for sample in negative_topics)
    quality_counts = Counter(sample["quality"] or "未标质量" for sample in script_samples)
    issue_counts = Counter(tag for sample in script_samples for tag in sample["issue_tags"])

    durable_rules: list[str] = []
    preference_rules: list[str] = []
    one_off_notes: list[str] = []

    if positive_tags:
        preference_rules.append(f"选题更应关注这些正向选择信号：{'、'.join(tag for tag, _ in positive_tags.most_common(5))}。")
    if negative_tags:
        preference_rules.append(f"选题应警惕这些负向选择信号：{'、'.join(tag for tag, _ in negative_tags.most_common(5))}。")
    if positive_directions:
        preference_rules.append(f"本轮更愿意推进的方向：{'、'.join(name for name, _ in positive_directions.most_common(3))}。")
    if negative_directions:
        preference_rules.append(f"本轮更容易暂存或不做的方向：{'、'.join(name for name, _ in negative_directions.most_common(3))}。")
    if issue_counts:
        preference_rules.append(f"06 内容生成优先修正这些问题：{'、'.join(tag for tag, _ in issue_counts.most_common(5))}。")
    if quality_counts and any(name in quality_counts for name in ("需要重写", "暂不采用")):
        durable_rules.append("当 06 反馈为需要重写或暂不采用时，不应自动把该样本升级为长期风格规则，必须先人工复核。")
    if not durable_rules and not preference_rules:
        one_off_notes.append("样本不足；先继续记录选择和内容反馈，不更新长期规则。")

    return {
        "learning_batch_id": learning_batch_id,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "learning_date": now_slug(),
        "environment": environment,
        "topic_sample_count": len(topic_samples),
        "script_feedback_count": len(script_samples),
        "sample_count": len(topic_samples) + len(script_samples),
        "positive_topic_count": len(positive_topics),
        "negative_topic_count": len(negative_topics),
        "topic_positive_tags": dict(positive_tags),
        "topic_negative_tags": dict(negative_tags),
        "topic_positive_directions": dict(positive_directions),
        "topic_negative_directions": dict(negative_directions),
        "script_quality_counts": dict(quality_counts),
        "script_issue_counts": dict(issue_counts),
        "durable_rules": durable_rules,
        "preference_rules": preference_rules,
        "one_off_notes": one_off_notes,
        "topic_samples": topic_samples,
        "script_feedback_samples": script_samples,
    }


def markdown_report(summary: dict[str, Any]) -> str:
    lines = [
        f"# 学习反馈日结 {summary['learning_date']}",
        "",
        f"- 学习批次：{summary['learning_batch_id']}",
        f"- 环境：{summary['environment']}",
        f"- 总样本：{summary['sample_count']}",
        f"- 选题样本：{summary['topic_sample_count']}（推进 {summary['positive_topic_count']} / 暂存或不做 {summary['negative_topic_count']}）",
        f"- 06 内容反馈：{summary['script_feedback_count']}",
        "",
        "## 建议沉淀规则",
        "",
    ]
    rules = list(summary["durable_rules"]) + list(summary["preference_rules"])
    lines.extend(f"- {rule}" for rule in (rules or ["暂无足够稳定规则，继续观察。"]))
    lines.extend(["", "## 不应沉淀的个案", ""])
    lines.extend(f"- {note}" for note in (summary["one_off_notes"] or ["暂无。"]))

    lines.extend(["", "## 选题反馈样本", ""])
    for sample in summary["topic_samples"][:30]:
        tags = "、".join(sample["selection_tags"]) or sample["reject_reason"] or sample["human_note"] or "未标原因"
        lines.append(f"- {sample['title']}｜{sample['status']}｜{sample['direction']}｜{tags}")

    lines.extend(["", "## 06 内容反馈样本", ""])
    for sample in summary["script_feedback_samples"][:30]:
        issues = "、".join(sample["issue_tags"]) or "未标问题"
        lines.append(f"- {sample['title']}｜{sample['quality'] or '未标质量'}｜{issues}｜{compact(sample['note'], 120)}")
    lines.append("")
    return "\n".join(lines)


def learning_conclusion(summary: dict[str, Any]) -> str:
    parts = [
        f"选题样本 {summary['topic_sample_count']} 条，06 内容反馈 {summary['script_feedback_count']} 条。",
    ]
    if summary["preference_rules"]:
        parts.append("核心偏好：" + "；".join(summary["preference_rules"][:3]))
    if summary["durable_rules"]:
        parts.append("硬规则：" + "；".join(summary["durable_rules"][:2]))
    if len(parts) == 1:
        parts.append("样本不足，暂不建议更新长期规则。")
    return "\n".join(parts)


def learning_record_fields(summary: dict[str, Any], markdown: str) -> dict[str, str]:
    topic_ids = [sample["record_id"] for sample in summary["topic_samples"] if sample.get("record_id")]
    script_ids = [sample["record_id"] for sample in summary["script_feedback_samples"] if sample.get("record_id")]
    return {
        "学习批次": summary["learning_batch_id"],
        "学习日期": summary["learning_date"],
        "环境": summary["environment"],
        "学习类型": "综合",
        "样本数量": str(summary["sample_count"]),
        "选题样本数": str(summary["topic_sample_count"]),
        "内容反馈样本数": str(summary["script_feedback_count"]),
        "学习结论": learning_conclusion(summary)[:5000],
        "建议沉淀规则": "\n".join(summary["durable_rules"] + summary["preference_rules"])[:5000],
        "不应沉淀的个案": "\n".join(summary["one_off_notes"])[:5000],
        "关联04记录": "、".join(topic_ids)[:5000],
        "关联06记录": "、".join(script_ids)[:5000],
        "确认状态": "待确认",
        "确认时间": "",
        "确认备注": markdown[:5000],
        "Skill同步状态": "未同步",
    }


def should_write_learning_record(sample_count: int, write_empty_learning: bool) -> bool:
    return sample_count > 0 or write_empty_learning


def learning_confirmation_note_inputs() -> list[dict[str, Any]]:
    placeholders = [
        "确认备注 1：哪些规则可以沉淀，哪些先别动",
        "确认备注 2：如果只是部分采纳，写清楚边界",
        "确认备注 3：后续希望补看的样本或判断口径",
    ]
    return [
        {
            "tag": "input",
            "name": name,
            "required": False,
            "width": "fill",
            "placeholder": {"tag": "plain_text", "content": placeholder},
            "default_value": "",
        }
        for name, placeholder in zip(LEARNING_CONFIRM_NOTE_KEYS, placeholders)
    ]


def build_learning_confirmation_card(summary: dict[str, Any], learning_record_id: str, ttl_days: int = 5) -> dict[str, Any]:
    issued_at = datetime.utcnow()
    expires_at = issued_at + timedelta(days=ttl_days)
    topic_ids = [sample["record_id"] for sample in summary["topic_samples"] if sample.get("record_id")]
    script_ids = [sample["record_id"] for sample in summary["script_feedback_samples"] if sample.get("record_id")]
    conclusion = compact(learning_conclusion(summary), 900)
    rules = list(summary["durable_rules"]) + list(summary["preference_rules"])
    rule_lines = "\n".join(f"- {compact(rule, 220)}" for rule in (rules[:6] or ["暂无足够稳定规则，继续观察。"]))
    reject_lines = "\n".join(f"- {compact(note, 180)}" for note in (summary["one_off_notes"][:4] or ["暂无。"]))
    base_value = {
        "action": LEARNING_CONFIRM_ACTION,
        "learning_record_id": learning_record_id,
        "learning_batch_id": summary["learning_batch_id"],
        "environment": summary["environment"],
        "topic_record_ids": topic_ids,
        "script_record_ids": script_ids,
        "learning_summary": conclusion,
        "card_issued_at": issued_at.isoformat(timespec="seconds") + "Z",
        "card_expires_at": expires_at.isoformat(timespec="seconds") + "Z",
        "card_ttl_days": ttl_days,
    }
    decision_buttons = []
    for decision in LEARNING_CONFIRM_DECISIONS:
        decision_buttons.append({
            "tag": "column",
            "width": "auto",
            "elements": [
                {
                    "tag": "button",
                    "type": "primary" if decision == "已采纳" else "default",
                    "width": "default",
                    "text": {"tag": "plain_text", "content": decision},
                    "form_action_type": "submit",
                    "name": f"learning_feedback_{decision}",
                    "behaviors": [{"type": "callback", "value": {**base_value, "decision": decision}}],
                },
            ],
        })
    form_elements: list[dict[str, Any]] = [
        {"tag": "markdown", "content": "**确认备注（可选）**"},
        *learning_confirmation_note_inputs(),
        {"tag": "column_set", "columns": decision_buttons},
    ]
    return {
        "schema": "2.0",
        "config": {
            "update_multi": True,
            "enable_forward": False,
            "width_mode": "fill",
        },
        "header": {
            "template": "green",
            "title": {"tag": "plain_text", "content": "学习反馈日结待确认"},
        },
        "body": {
            "elements": [
                {
                    "tag": "markdown",
                    "content": (
                        f"学习批次：{summary['learning_batch_id']}\n"
                        f"环境：{summary['environment']}\n"
                        f"样本：选题 {summary['topic_sample_count']} 条，06 反馈 {summary['script_feedback_count']} 条。\n\n"
                        f"**结论**\n{conclusion}\n\n"
                        f"**建议沉淀规则**\n{rule_lines}\n\n"
                        f"**不应沉淀的个案**\n{reject_lines}\n\n"
                        f"这张卡只回写学习确认状态，不直接修改 Skill 文件；{ttl_days} 天后提交无效。"
                    ),
                },
                {
                    "tag": "form",
                    "name": "learning_feedback_confirmation",
                    "padding": "8px 0px 0px 0px",
                    "vertical_spacing": "8px",
                    "elements": form_elements,
                },
            ],
        },
    }


def send_interactive_card(token: str, card: dict[str, Any], uuid_base: str) -> dict[str, Any]:
    targets = parse_learning_card_targets()
    if not targets:
        return {"sent_count": 0, "skipped": "missing_learning_receive_targets"}
    sends = []
    for receive_id_type, receive_id in targets:
        payload = feishu.request_json(
            "POST",
            f"/im/v1/messages?receive_id_type={quote(receive_id_type)}",
            token=token,
            body={
                "receive_id": receive_id,
                "msg_type": "interactive",
                "content": json.dumps(card, ensure_ascii=False),
                "uuid": card_uuid("learning-card", uuid_base, receive_id_type, receive_id),
            },
        )
        sends.append({"receive_id_type": receive_id_type, "receive_id": receive_id, "message_id": payload.get("data", {}).get("message_id", "")})
    return {"sent_count": len(sends), "sends": sends}


def send_learning_confirmation_card(token: str, summary: dict[str, Any], learning_record_id: str) -> dict[str, Any]:
    card = build_learning_confirmation_card(summary, learning_record_id)
    return send_interactive_card(token, card, f"{summary['learning_batch_id']}|{learning_record_id}")


def ensure_or_create_table(token: str, app_token: str, tables_by_name: dict[str, str], table_name: str, fields: list[str], ensure_create: bool) -> str:
    table_id = tables_by_name.get(table_name, "")
    if not table_id:
        if not ensure_create:
            raise SystemExit(f"Missing Feishu table: {table_name}. Run setup or pass --ensure-learning-table.")
        payload = feishu.request_json(
            "POST",
            f"/bitable/v1/apps/{app_token}/tables",
            token=token,
            body={"table": {"name": table_name, "default_view_name": "学习日结", "fields": [{"field_name": name, "type": 1} for name in fields]}},
        )
        data = payload.get("data", {})
        table = data.get("table", data)
        table_id = str(table.get("table_id") or data.get("table_id") or "")
        if not table_id:
            raise RuntimeError(f"Could not create learning table: {payload}")
        time.sleep(0.2)
        return table_id
    ensure_text_fields(token, app_token, table_id, fields)
    return table_id


def assert_write_safety(args: argparse.Namespace, environment: str, table_names: dict[str, str], source_explicit: dict[str, bool]) -> None:
    if environment == "production" and not args.allow_production_write:
        raise SystemExit("Refusing to write production learning data without --allow-production-write.")
    if environment != "production":
        unsafe = {key: name for key, name in table_names.items() if name and not is_test_table_name(name)}
        if unsafe:
            raise SystemExit(f"Refusing staging/test write with non-test tables: {unsafe}")
        missing_explicit_sources = [key for key in ("topic", "script") if not source_explicit.get(key)]
        if missing_explicit_sources:
            raise SystemExit(
                "Refusing staging/test write unless source tables are explicit test table IDs. "
                f"Missing explicit env for: {', '.join(missing_explicit_sources)}"
            )


def create_learning_record(token: str, app_token: str, table_id: str, fields: dict[str, str]) -> str:
    payload = feishu.request_json(
        "POST",
        f"/bitable/v1/apps/{app_token}/tables/{table_id}/records",
        token=token,
        body={"fields": fields},
    )
    data = payload.get("data", {})
    record = data.get("record", data)
    return str(record.get("record_id") or "")


def mark_source_records(token: str, app_token: str, table_id: str, record_ids: list[str], fields: dict[str, str]) -> int:
    updated = 0
    for record_id in record_ids:
        feishu.request_json(
            "PUT",
            f"/bitable/v1/apps/{app_token}/tables/{table_id}/records/{record_id}",
            token=token,
            body={"fields": fields},
        )
        updated += 1
        time.sleep(0.08)
    return updated


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a daily learning digest from 04 selection and 06 script feedback.")
    parser.add_argument("--include-learned", action="store_true", help="Include records already marked learned/ignored/pending confirmation.")
    parser.add_argument("--write-feishu", action="store_true", help="Write a learning record to Feishu 08. Protected by environment safety checks.")
    parser.add_argument("--ensure-learning-table", action="store_true", help="Create the learning table if missing.")
    parser.add_argument("--mark-pending-confirm", action="store_true", help="After writing Feishu, mark source 04/06 samples as pending confirmation.")
    parser.add_argument("--send-card", action="store_true", help="Send a learning confirmation card to FEISHU_LEARNING_FEEDBACK_RECEIVE_TARGETS.")
    parser.add_argument("--write-empty-learning", action="store_true", help="Write/send a learning record even when no feedback samples are selected.")
    parser.add_argument("--allow-production-write", action="store_true", help="Allow writing production 08/source learning status.")
    parser.add_argument("--learning-table-name", default="", help="Override learning table name. Defaults to 08 学习记录 or 08 学习记录__测试 by env.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    load_local_env(required=bool(args.write_feishu))
    environment = env_label()
    app_token = os.getenv("FEISHU_BASE_APP_TOKEN", "").strip()
    if not app_token:
        raise SystemExit("FEISHU_BASE_APP_TOKEN is required")
    token = feishu.tenant_token()
    tables_by_name = table_map(token, app_token)

    topic_table_id, topic_table_name, topic_explicit = resolve_table(
        tables_by_name,
        env_name=TOPIC_TABLE_ENV,
        registry_key="topic_decision",
    )
    script_table_id, script_table_name, script_explicit = resolve_table(
        tables_by_name,
        env_name=SCRIPT_TABLE_ENV,
        registry_key="script_package",
    )
    if not topic_table_id:
        raise SystemExit(f"Missing table: {TABLES['topic_decision']}")
    if not script_table_id:
        raise SystemExit(f"Missing table: {TABLES['script_package']}")

    topic_records = all_records(token, app_token, topic_table_id)
    script_records = all_records(token, app_token, script_table_id)
    topic_samples = select_topic_samples(topic_records, include_learned=args.include_learned)
    script_samples = select_script_feedback(script_records, include_learned=args.include_learned)
    learning_batch_id = batch_id()
    summary = summarize(topic_samples, script_samples, learning_batch_id, environment)
    summary["source_tables"] = {
        "topic": {"table_id": topic_table_id, "table_name": topic_table_name, "explicit": topic_explicit},
        "script": {"table_id": script_table_id, "table_name": script_table_name, "explicit": script_explicit},
    }

    OUT.mkdir(parents=True, exist_ok=True)
    json_path = OUT / f"{summary['learning_date']}_{learning_batch_id}.json"
    md_path = OUT / f"{summary['learning_date']}_{learning_batch_id}.md"
    markdown = markdown_report(summary)
    json_text = json.dumps(summary, ensure_ascii=False, indent=2)
    json_path.write_text(json_text, encoding="utf-8")
    md_path.write_text(markdown, encoding="utf-8")
    LATEST_JSON.write_text(json_text, encoding="utf-8")
    LATEST_MD.write_text(markdown, encoding="utf-8")

    write_result: dict[str, Any] = {"written": False}
    if args.send_card and not args.write_feishu:
        raise SystemExit("--send-card requires --write-feishu so the card has a learning record to confirm.")

    if args.write_feishu:
        if not should_write_learning_record(int(summary["sample_count"] or 0), args.write_empty_learning):
            write_result = {
                "written": False,
                "skipped": "no_learning_samples",
                "note": "No 04/06 feedback samples selected; skipped Feishu 08 write and learning card.",
            }
        else:
            learning_table_name = args.learning_table_name.strip() or (TABLES["learning_record"] if environment == "production" else LEARNING_TEST_TABLE_NAME)
            learning_table_id = os.getenv(LEARNING_TABLE_ENV, "").strip() or tables_by_name.get(learning_table_name, "")
            resolved_learning_name = table_name_by_id(tables_by_name, learning_table_id) or learning_table_name
            assert_write_safety(
                args,
                environment,
                {"topic": topic_table_name, "script": script_table_name, "learning": resolved_learning_name},
                {"topic": topic_explicit, "script": script_explicit},
            )
            if not learning_table_id:
                learning_table_id = ensure_or_create_table(
                    token,
                    app_token,
                    tables_by_name,
                    learning_table_name,
                    LEARNING_RECORD_FIELDS,
                    args.ensure_learning_table,
                )
            else:
                ensure_text_fields(token, app_token, learning_table_id, LEARNING_RECORD_FIELDS)
            learning_record_id = create_learning_record(token, app_token, learning_table_id, learning_record_fields(summary, markdown))
            write_result = {
                "written": True,
                "learning_table_name": resolved_learning_name,
                "learning_table_id": learning_table_id,
                "learning_record_id": learning_record_id,
            }
            if args.mark_pending_confirm:
                topic_ids = [sample["record_id"] for sample in topic_samples if sample.get("record_id")]
                script_ids = [sample["record_id"] for sample in script_samples if sample.get("record_id")]
                ensure_text_fields(token, app_token, topic_table_id, ["学习状态", "选择学习批次", "选择学习摘要"])
                ensure_text_fields(token, app_token, script_table_id, ["内容学习状态", "内容学习批次", "内容学习摘要"])
                write_result["marked_topic_pending"] = mark_source_records(token, app_token, topic_table_id, topic_ids, {
                    "学习状态": "待确认学习",
                    "选择学习批次": learning_batch_id,
                    "选择学习摘要": learning_conclusion(summary)[:1000],
                })
                write_result["marked_script_pending"] = mark_source_records(token, app_token, script_table_id, script_ids, {
                    "内容学习状态": "待确认学习",
                    "内容学习批次": learning_batch_id,
                    "内容学习摘要": learning_conclusion(summary)[:1000],
                })
            if args.send_card:
                write_result["card_result"] = send_learning_confirmation_card(token, summary, learning_record_id)

    print(json.dumps({
        "ok": True,
        "environment": environment,
        "sample_count": summary["sample_count"],
        "topic_sample_count": summary["topic_sample_count"],
        "script_feedback_count": summary["script_feedback_count"],
        "json": str(json_path),
        "markdown": str(md_path),
        "latest_markdown": str(LATEST_MD),
        "write_result": write_result,
        "source_tables": summary["source_tables"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
