#!/usr/bin/env python3
"""Summarize user topic-selection feedback from Feishu 04.

The script turns daily selection actions into visible learning notes. It does
not auto-change future scoring yet; that should happen only after the user can
inspect what the system believes it learned.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

import push_to_feishu as feishu
from feishu_table_registry import TABLES, resolve_table_id


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output" / "selection_learning"
LATEST_JSON = OUT / "latest_selection_learning.json"
LATEST_MD = OUT / "latest_selection_learning.md"
APPROVED_JSON = OUT / "approved_selection_learning.json"
APPROVED_MD = OUT / "approved_selection_learning.md"
TARGET_TABLE_KEY = "topic_decision"
POSITIVE_STATUSES = {"生成脚本包"}
NEGATIVE_STATUSES = {"暂存", "归档", "不做"}
DECISION_STATUSES = POSITIVE_STATUSES | NEGATIVE_STATUSES
EXCLUDED_LEARNING_STATUSES = {"已学习", "忽略", "待确认学习"}


def today_slug() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def latest_run_id() -> str:
    log_path = ROOT / "output" / "logs" / f"daily_pipeline_{today_slug()}.json"
    if not log_path.exists():
        return ""
    try:
        return json.loads(log_path.read_text(encoding="utf-8")).get("run_id", "")
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
    return str(value).strip()


def normalize_tags(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    if not text:
        return []
    return [part.strip() for part in text.replace("；", "、").replace(",", "、").split("、") if part.strip()]


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


def fetch_topic_records() -> tuple[str, str, list[dict[str, Any]]]:
    app_token = require_app_token()
    token = feishu.tenant_token()
    table_id = resolve_table_id({table["name"]: table["table_id"] for table in feishu.list_tables(token, app_token)}, TARGET_TABLE_KEY)
    if not table_id:
        raise SystemExit(f"Missing table: {TABLES[TARGET_TABLE_KEY]}")
    return token, app_token, all_records(token, app_token, table_id)


def record_to_sample(record: dict[str, Any]) -> dict[str, Any]:
    fields = record.get("fields", {})
    status = normalize(fields.get("状态"))
    return {
        "record_id": record.get("record_id", ""),
        "run_id": normalize(fields.get("运行批次")),
        "date": normalize(fields.get("推荐日期")),
        "title": normalize(fields.get("选题标题")),
        "status": status,
        "is_positive": status in POSITIVE_STATUSES,
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


def selected_samples(records: list[dict[str, Any]], include_learned: bool, run_id: str = "") -> list[dict[str, Any]]:
    samples = [record_to_sample(record) for record in records]
    return [
        sample for sample in samples
        if sample["status"] in DECISION_STATUSES
        and (not run_id or sample["run_id"] == run_id)
        and (include_learned or sample["learning_status"] not in EXCLUDED_LEARNING_STATUSES)
    ]


def summarize(samples: list[dict[str, Any]]) -> dict[str, Any]:
    positive = [sample for sample in samples if sample["is_positive"]]
    negative = [sample for sample in samples if not sample["is_positive"]]
    direction_positive = Counter(sample["direction"] or "未标方向" for sample in positive)
    direction_negative = Counter(sample["direction"] or "未标方向" for sample in negative)
    tag_positive = Counter(tag for sample in positive for tag in sample["selection_tags"])
    tag_negative = Counter(tag for sample in negative for tag in sample["selection_tags"])
    risk_positive = Counter(sample["ai_taste_risk"] or "未标AI味" for sample in positive)
    risk_negative = Counter(sample["ai_taste_risk"] or "未标AI味" for sample in negative)
    status_counts = Counter(sample["status"] for sample in samples)

    inferred_rules: list[str] = []
    if tag_positive:
        inferred_rules.append(f"提高带有这些正向标签的候选权重：{'、'.join(tag for tag, _ in tag_positive.most_common(5))}。")
    if tag_negative:
        inferred_rules.append(f"降低带有这些负向标签的候选权重：{'、'.join(tag for tag, _ in tag_negative.most_common(5))}。")
    if direction_positive:
        inferred_rules.append(f"用户本轮更愿意推进的方向：{'、'.join(name for name, _ in direction_positive.most_common(3))}。")
    if direction_negative:
        inferred_rules.append(f"用户本轮更容易暂存/不做的方向：{'、'.join(name for name, _ in direction_negative.most_common(3))}。")
    if not inferred_rules:
        inferred_rules.append("样本还不够；先继续记录选择状态和原因标签。")

    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "sample_count": len(samples),
        "positive_count": len(positive),
        "negative_count": len(negative),
        "status_counts": dict(status_counts),
        "positive_tags": dict(tag_positive),
        "negative_tags": dict(tag_negative),
        "positive_directions": dict(direction_positive),
        "negative_directions": dict(direction_negative),
        "positive_ai_taste_risk": dict(risk_positive),
        "negative_ai_taste_risk": dict(risk_negative),
        "inferred_rules": inferred_rules,
        "positive_samples": positive,
        "negative_samples": negative,
    }


def markdown_report(summary: dict[str, Any]) -> str:
    lines = [
        f"# 选题选择学习摘要 {today_slug()}",
        "",
        f"- 样本数：{summary['sample_count']}",
        f"- 推进：{summary['positive_count']}",
        f"- 暂存/不做：{summary['negative_count']}",
        "",
        "## 本轮学到的规则",
        "",
    ]
    lines.extend(f"- {rule}" for rule in summary["inferred_rules"])
    lines.extend(["", "## 推进样本", ""])
    for sample in summary["positive_samples"]:
        tags = "、".join(sample["selection_tags"]) or "未标原因"
        lines.append(f"- {sample['title']}｜{sample['direction']}｜{tags}")
    lines.extend(["", "## 暂存/不做样本", ""])
    for sample in summary["negative_samples"]:
        tags = "、".join(sample["selection_tags"]) or sample["reject_reason"] or "未标原因"
        lines.append(f"- {sample['title']}｜{sample['status']}｜{sample['direction']}｜{tags}")
    lines.append("")
    return "\n".join(lines)


def mark_learned(token: str, app_token: str, samples: list[dict[str, Any]]) -> int:
    table_id = resolve_table_id({table["name"]: table["table_id"] for table in feishu.list_tables(token, app_token)}, TARGET_TABLE_KEY)
    if not table_id:
        return 0
    updated = 0
    for sample in samples:
        if sample.get("learning_status") in {"已学习", "忽略"}:
            continue
        feishu.request_json(
            "PUT",
            f"/bitable/v1/apps/{app_token}/tables/{table_id}/records/{sample['record_id']}",
            token=token,
            body={"fields": {"学习状态": "已学习"}},
        )
        updated += 1
        time.sleep(0.08)
    return updated


def mark_pending_confirmation(token: str, app_token: str, samples: list[dict[str, Any]]) -> int:
    table_id = resolve_table_id({table["name"]: table["table_id"] for table in feishu.list_tables(token, app_token)}, TARGET_TABLE_KEY)
    if not table_id:
        return 0
    updated = 0
    for sample in samples:
        if sample.get("learning_status") in {"已学习", "忽略", "待确认学习"}:
            continue
        feishu.request_json(
            "PUT",
            f"/bitable/v1/apps/{app_token}/tables/{table_id}/records/{sample['record_id']}",
            token=token,
            body={"fields": {"学习状态": "待确认学习"}},
        )
        updated += 1
        time.sleep(0.08)
    return updated


def approve_latest(mark_records_learned: bool) -> dict[str, Any]:
    if not LATEST_JSON.exists() or not LATEST_MD.exists():
        raise SystemExit("No latest learning summary to approve. Run without --approve-latest first.")
    OUT.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(LATEST_JSON, APPROVED_JSON)
    shutil.copyfile(LATEST_MD, APPROVED_MD)
    marked = 0
    if mark_records_learned:
        token, app_token, _records = fetch_topic_records()
        summary = json.loads(LATEST_JSON.read_text(encoding="utf-8"))
        samples = list(summary.get("positive_samples", [])) + list(summary.get("negative_samples", []))
        marked = mark_learned(token, app_token, samples)
    return {
        "ok": True,
        "approved": True,
        "marked_learned": marked,
        "approved_json": str(APPROVED_JSON),
        "approved_markdown": str(APPROVED_MD),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--include-learned", action="store_true", help="Include records already marked 已学习.")
    parser.add_argument("--mark-learned", action="store_true", help="With --approve-latest, mark approved samples as 已学习 in Feishu.")
    parser.add_argument("--mark-pending-confirm", action="store_true", help="Mark processed samples as 待确认学习 in Feishu.")
    parser.add_argument("--approve-latest", action="store_true", help="Approve the latest learning summary for the next editorial run, then exit.")
    parser.add_argument("--all-runs", action="store_true", help="Learn from all historical records instead of the latest write run.")
    args = parser.parse_args()

    if args.approve_latest:
        print(json.dumps(approve_latest(args.mark_learned), ensure_ascii=False, indent=2))
        return 0
    if args.mark_learned:
        raise SystemExit("--mark-learned can only be used together with --approve-latest. Use --mark-pending-confirm before user approval.")

    token, app_token, records = fetch_topic_records()
    run_id = "" if args.all_runs else latest_run_id()
    samples = selected_samples(records, include_learned=args.include_learned, run_id=run_id)
    summary = summarize(samples)
    summary["run_id"] = run_id or "all"
    OUT.mkdir(parents=True, exist_ok=True)
    json_path = OUT / f"{today_slug()}_selection_learning.json"
    md_path = OUT / f"{today_slug()}_selection_learning.md"
    json_text = json.dumps(summary, ensure_ascii=False, indent=2)
    md_text = markdown_report(summary)
    json_path.write_text(json_text, encoding="utf-8")
    md_path.write_text(md_text, encoding="utf-8")
    LATEST_JSON.write_text(json_text, encoding="utf-8")
    LATEST_MD.write_text(md_text, encoding="utf-8")
    marked_pending = mark_pending_confirmation(token, app_token, samples) if args.mark_pending_confirm else 0
    print(json.dumps({
        "ok": True,
        "run_id": run_id or "all",
        "sample_count": len(samples),
        "marked_learned": 0,
        "marked_pending_confirm": marked_pending,
        "json": str(json_path),
        "markdown": str(md_path),
        "latest_markdown": str(LATEST_MD),
        "approved_markdown": str(APPROVED_MD) if APPROVED_MD.exists() else "",
        "inferred_rules": summary["inferred_rules"],
        "approval_note": "学习摘要尚未进入下一轮主编判断；需要用户确认后运行 --approve-latest。",
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
