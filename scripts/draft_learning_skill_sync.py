#!/usr/bin/env python3
"""Draft Skill updates from confirmed daily learning records.

This script is deliberately conservative: it creates local review drafts only.
It never edits the global private Skill. Feishu status write-back is opt-in and
protected by the same staging/production boundary as the daily learning writer.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from local_env import load_local_env
import push_to_feishu as feishu
from feishu_table_registry import TABLES
from learn_from_daily_feedback import (
    LEARNING_TABLE_ENV,
    LEARNING_TEST_TABLE_NAME,
    all_records,
    compact,
    env_label,
    is_test_table_name,
    normalize,
    resolve_table,
    table_map,
)


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output" / "skill_sync_drafts"
LATEST_MD = OUT / "latest_skill_sync_draft.md"
LATEST_JSON = OUT / "latest_skill_sync_draft.json"

TARGET_SKILL = "ai-account-editorial-director"
SYNC_READY_STATUSES = {"待同步"}
CONFIRMED_STATUSES = {"已采纳", "部分采纳"}
DRAFTED_STATUS = "草稿已生成"
SYNCED_STATUS = "已同步"

HARD_RULE_HINTS = ("必须", "不得", "不应", "禁止", "必须先", "不能", "先人工复核")
PREFERENCE_HINTS = ("更应", "更愿意", "优先", "关注", "警惕", "倾向", "建议")


def split_lines(value: Any) -> list[str]:
    text = normalize(value)
    lines: list[str] = []
    for raw in text.replace("；", "\n").splitlines():
        line = raw.strip(" -\t")
        if line:
            lines.append(line)
    return lines


def unique_ordered(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        key = item.strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def classify_rule(rule: str) -> str:
    if any(hint in rule for hint in HARD_RULE_HINTS):
        return "hard"
    if any(hint in rule for hint in PREFERENCE_HINTS):
        return "preference"
    return "candidate"


def learning_record(record: dict[str, Any]) -> dict[str, Any]:
    fields = record.get("fields", {})
    rules = split_lines(fields.get("建议沉淀规则"))
    avoid = split_lines(fields.get("不应沉淀的个案"))
    return {
        "record_id": str(record.get("record_id") or ""),
        "batch_id": normalize(fields.get("学习批次")),
        "date": normalize(fields.get("学习日期")),
        "environment": normalize(fields.get("环境")),
        "learning_type": normalize(fields.get("学习类型")),
        "sample_count": normalize(fields.get("样本数量")),
        "topic_sample_count": normalize(fields.get("选题样本数")),
        "script_feedback_count": normalize(fields.get("内容反馈样本数")),
        "conclusion": normalize(fields.get("学习结论")),
        "rules": rules,
        "avoid": avoid,
        "topic_records": normalize(fields.get("关联04记录")),
        "script_records": normalize(fields.get("关联06记录")),
        "confirmation_status": normalize(fields.get("确认状态")),
        "confirmed_at": normalize(fields.get("确认时间")),
        "confirmation_note": normalize(fields.get("确认备注")),
        "skill_sync_status": normalize(fields.get("Skill同步状态")),
    }


def select_ready_records(
    records: list[dict[str, Any]],
    include_drafted: bool = False,
    sync_statuses: set[str] | None = None,
) -> list[dict[str, Any]]:
    allowed_sync_statuses = set(sync_statuses or SYNC_READY_STATUSES)
    if include_drafted:
        allowed_sync_statuses.add(DRAFTED_STATUS)
    selected = []
    for record in records:
        item = learning_record(record)
        if item["confirmation_status"] in CONFIRMED_STATUSES and item["skill_sync_status"] in allowed_sync_statuses:
            selected.append(item)
    return selected


def summarize_for_draft(records: list[dict[str, Any]], target_skill: str, environment: str) -> dict[str, Any]:
    hard_rules: list[str] = []
    preference_rules: list[str] = []
    candidate_rules: list[str] = []
    avoid_notes: list[str] = []
    confirmation_notes: list[str] = []

    for record in records:
        for rule in record["rules"]:
            bucket = classify_rule(rule)
            if bucket == "hard":
                hard_rules.append(rule)
            elif bucket == "preference":
                preference_rules.append(rule)
            else:
                candidate_rules.append(rule)
        avoid_notes.extend(record["avoid"])
        if record["confirmation_note"]:
            confirmation_notes.append(f"{record['batch_id'] or record['record_id']}：{record['confirmation_note']}")

    return {
        "draft_id": "skill_sync_" + datetime.now().strftime("%Y%m%d_%H%M%S"),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "environment": environment,
        "target_skill": target_skill,
        "record_count": len(records),
        "record_ids": [record["record_id"] for record in records],
        "batch_ids": [record["batch_id"] for record in records],
        "hard_rules": unique_ordered(hard_rules),
        "preference_rules": unique_ordered(preference_rules),
        "candidate_rules": unique_ordered(candidate_rules),
        "avoid_notes": unique_ordered(avoid_notes),
        "confirmation_notes": unique_ordered(confirmation_notes),
        "records": records,
    }


def markdown_draft(summary: dict[str, Any]) -> str:
    lines = [
        f"# Skill 同步草稿 {summary['generated_at']}",
        "",
        f"- 目标 Skill：{summary['target_skill']}",
        f"- 环境：{summary['environment']}",
        f"- 学习记录数：{summary['record_count']}",
        f"- 学习批次：{', '.join(summary['batch_ids']) or '无'}",
        "",
        "## 建议写入的硬规则",
        "",
    ]
    lines.extend(f"- {rule}" for rule in (summary["hard_rules"] or ["暂无。"]))
    lines.extend(["", "## 建议写入的偏好规则", ""])
    lines.extend(f"- {rule}" for rule in (summary["preference_rules"] or ["暂无。"]))
    lines.extend(["", "## 候选规则（需要人工判断落点）", ""])
    lines.extend(f"- {rule}" for rule in (summary["candidate_rules"] or ["暂无。"]))
    lines.extend(["", "## 不应写入长期 Skill 的个案", ""])
    lines.extend(f"- {note}" for note in (summary["avoid_notes"] or ["暂无。"]))
    lines.extend(["", "## 人工确认备注", ""])
    lines.extend(f"- {note}" for note in (summary["confirmation_notes"] or ["暂无。"]))
    lines.extend(["", "## 建议落点", ""])
    lines.extend([
        "- 先作为草稿审查，不自动修改全局私有 Skill。",
        "- 硬规则适合进入 Skill 的流程约束或质量门控。",
        "- 偏好规则适合进入 private reference，用于影响选题判断和表达取舍。",
        "- 个案只保留在学习记录里，不写入长期规则。",
        "",
        "## 来源记录",
        "",
    ])
    for record in summary["records"]:
        lines.append(
            f"- {record['record_id']}｜{record['batch_id']}｜{record['confirmation_status']}｜"
            f"样本 {record['sample_count']}（04: {record['topic_sample_count']} / 06: {record['script_feedback_count']}）"
        )
    lines.append("")
    return "\n".join(lines)


def write_outputs(summary: dict[str, Any]) -> dict[str, str]:
    OUT.mkdir(parents=True, exist_ok=True)
    stem = f"{datetime.now().strftime('%Y-%m-%d')}_{summary['draft_id']}"
    md_path = OUT / f"{stem}.md"
    json_path = OUT / f"{stem}.json"
    markdown = markdown_draft(summary)
    json_text = json.dumps(summary, ensure_ascii=False, indent=2)
    md_path.write_text(markdown, encoding="utf-8")
    json_path.write_text(json_text, encoding="utf-8")
    LATEST_MD.write_text(markdown, encoding="utf-8")
    LATEST_JSON.write_text(json_text, encoding="utf-8")
    return {
        "markdown": str(md_path),
        "json": str(json_path),
        "latest_markdown": str(LATEST_MD),
        "latest_json": str(LATEST_JSON),
    }


def assert_read_safety(environment: str, table_name: str, explicit: bool) -> None:
    if environment != "production":
        if not explicit:
            raise SystemExit("Refusing staging/test read unless FEISHU_LEARNING_TABLE_ID is explicit.")
        if table_name and not is_test_table_name(table_name):
            raise SystemExit(f"Refusing staging/test read from non-test learning table: {table_name}")


def mark_skill_sync_status(token: str, app_token: str, table_id: str, record_ids: list[str], status: str) -> int:
    updated = 0
    for record_id in record_ids:
        feishu.request_json(
            "PUT",
            f"/bitable/v1/apps/{app_token}/tables/{table_id}/records/{record_id}",
            token=token,
            body={"fields": {"Skill同步状态": status}},
        )
        updated += 1
        time.sleep(0.08)
    return updated


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create local Skill sync drafts from confirmed learning records.")
    parser.add_argument("--include-drafted", action="store_true", help="Also include records already marked 草稿已生成.")
    parser.add_argument("--write-empty-draft", action="store_true", help="Write an empty draft even when no records are selected.")
    parser.add_argument("--mark-drafted", action="store_true", help="After drafting, mark selected Feishu learning records as 草稿已生成.")
    parser.add_argument("--mark-synced", action="store_true", help="Mark 草稿已生成 records as 已同步 after manual private Skill update.")
    parser.add_argument("--allow-production-write", action="store_true", help="Allow --mark-drafted/--mark-synced against production 08.")
    parser.add_argument("--target-skill", default=TARGET_SKILL)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    load_local_env(required=True)
    environment = env_label()
    app_token = os.getenv("FEISHU_BASE_APP_TOKEN", "").strip()
    if not app_token:
        raise SystemExit("FEISHU_BASE_APP_TOKEN is required")
    if args.mark_drafted and args.mark_synced:
        raise SystemExit("Use only one of --mark-drafted or --mark-synced.")
    if environment == "production" and (args.mark_drafted or args.mark_synced) and not args.allow_production_write:
        raise SystemExit("Refusing to mark production learning records without --allow-production-write.")

    token = feishu.tenant_token()
    tables_by_name = table_map(token, app_token)
    table_id, table_name, explicit = resolve_table(
        tables_by_name,
        env_name=LEARNING_TABLE_ENV,
        registry_key="learning_record",
        fallback_name=LEARNING_TEST_TABLE_NAME if environment != "production" else TABLES["learning_record"],
    )
    if not table_id:
        raise SystemExit(f"Missing table: {TABLES['learning_record']}")
    assert_read_safety(environment, table_name, explicit)

    records = all_records(token, app_token, table_id)
    sync_statuses = {DRAFTED_STATUS} if args.mark_synced else None
    selected = select_ready_records(records, include_drafted=args.include_drafted, sync_statuses=sync_statuses)
    summary = summarize_for_draft(selected, args.target_skill, environment)
    summary["source_table"] = {"table_id": table_id, "table_name": table_name, "explicit": explicit}
    should_write_draft = not args.mark_synced and (selected or args.write_empty_draft)
    paths = write_outputs(summary) if should_write_draft else {}
    write_result: dict[str, Any] = {"marked": False}
    if args.mark_drafted and selected:
        write_result = {
            "marked": True,
            "updated_count": mark_skill_sync_status(token, app_token, table_id, summary["record_ids"], DRAFTED_STATUS),
            "status": DRAFTED_STATUS,
        }
    elif args.mark_synced and selected:
        write_result = {
            "marked": True,
            "updated_count": mark_skill_sync_status(token, app_token, table_id, summary["record_ids"], SYNCED_STATUS),
            "status": SYNCED_STATUS,
        }

    print(json.dumps({
        "ok": True,
        "environment": environment,
        "target_skill": args.target_skill,
        "selected_count": len(selected),
        "draft_id": summary["draft_id"],
        "paths": paths,
        "write_result": write_result,
        "source_table": summary["source_table"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
