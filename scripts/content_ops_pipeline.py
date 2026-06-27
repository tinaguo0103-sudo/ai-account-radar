#!/usr/bin/env python3
"""Create Austin v0.6 script packages from approved topics.

Default mode is dry-run. With --write-feishu, reads 04 分析与选题 records that are
confirmed for script-package generation (legacy statuses: 进入Brief / 本周做),
renders a single Austin full execution package,
creates a light 06 完整脚本与制作包 record, then marks the topic as 已生成脚本稿.

v0.6 skips the old 05 index layer. The user-facing artifact is
full_script_execution_package.md; Feishu 06 stores only status, summary, path,
reminders, and QA.

Production runtime reads the global private Austin Skill only. The repository
Skill is a sanitized mirror for sync/bootstrap/testing and is never used as an
implicit fallback.
"""
from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import push_to_feishu as feishu
from feishu_table_registry import TABLES, resolve_table_id


ROOT = Path(__file__).resolve().parents[1]
LEGACY_TODAY10 = ROOT / "output" / "today_10_topics.csv"
LATEST_WRITE_TODAY10 = ROOT / "output" / "latest_write" / "today_10_topics.csv"
LEGACY_LOG = ROOT / "output" / "content_sampler_log.json"
SCRIPT_VERSION = "austin-production-packager-v0.6"
GLOBAL_AUSTIN_SKILL_DIR = Path.home() / ".codex" / "skills" / "austin-no-overtime-scripting"
REPO_AUSTIN_SKILL_DIR = ROOT / "skills" / "austin-no-overtime-scripting"
SCRIPT_OUTPUT_ROOT = ROOT / "output" / "script_execution_packages"


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


TODAY10 = LATEST_WRITE_TODAY10 if LATEST_WRITE_TODAY10.exists() else (LEGACY_TODAY10 if legacy_today10_is_official() else LATEST_WRITE_TODAY10)
SCRIPT_PACKAGE_FIELDS = [
    "关联选题",
    "脚本状态",
    "推荐模板",
    "核心观点",
    "开头钩子",
    "本地文档",
    "完整脚本与执行包",
    "素材提醒",
    "发布前核验",
    "QA结果",
    "是否可拍",
    "版本",
]
TOPIC_MARK_FIELD = "是否已生成脚本稿"


def today_slug() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def ready_status(value: Any) -> bool:
    text = str(value or "").strip()
    return text in {"进入Brief", "本周做"} or "进入Brief" in text or "本周做" in text or "进入制作" in text


def resolve_austin_skill_dir() -> Path:
    override = os.getenv("AUSTIN_SCRIPT_SKILL_DIR", "").strip()
    if override:
        candidate = Path(override).expanduser()
        if (candidate / "scripts" / "austin_scripting.py").exists():
            return candidate
        raise SystemExit(f"AUSTIN_SCRIPT_SKILL_DIR points to a missing Austin scripting module: {candidate}")
    if (GLOBAL_AUSTIN_SKILL_DIR / "scripts" / "austin_scripting.py").exists():
        return GLOBAL_AUSTIN_SKILL_DIR
    raise SystemExit(
        "Missing global Austin scripting Skill. Production does not fall back "
        f"to the sanitized repo mirror. Expected: {GLOBAL_AUSTIN_SKILL_DIR}. "
        f"For explicit testing only, set AUSTIN_SCRIPT_SKILL_DIR={REPO_AUSTIN_SKILL_DIR}."
    )


def load_austin_module():
    skill_dir = resolve_austin_skill_dir()
    module_path = skill_dir / "scripts" / "austin_scripting.py"
    spec = importlib.util.spec_from_file_location("austin_scripting_runtime", module_path)
    if not spec or not spec.loader:
        raise SystemExit(f"Could not load Austin scripting module from {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module, skill_dir


def require_app_token() -> str:
    token = os.getenv("FEISHU_BASE_APP_TOKEN")
    if not token:
        raise SystemExit("FEISHU_BASE_APP_TOKEN is required")
    return token


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


def fields_by_name(token: str, app_token: str, table_id: str) -> dict[str, dict[str, Any]]:
    payload = feishu.request_json("GET", f"/bitable/v1/apps/{app_token}/tables/{table_id}/fields", token=token)
    return {field["field_name"]: field for field in payload.get("data", {}).get("items", [])}


def ensure_text_fields(token: str, app_token: str, table_id: str, field_names: list[str]) -> list[str]:
    existing = fields_by_name(token, app_token, table_id)
    created: list[str] = []
    for name in field_names:
        if name in existing:
            continue
        feishu.request_json(
            "POST",
            f"/bitable/v1/apps/{app_token}/tables/{table_id}/fields",
            token=token,
            body={"field_name": name, "type": 1},
        )
        created.append(name)
        time.sleep(0.1)
    return created


def batch_create(token: str, app_token: str, table_id: str, rows: list[dict[str, str]]) -> int:
    total = 0
    for start in range(0, len(rows), 500):
        chunk = rows[start:start + 500]
        if not chunk:
            continue
        feishu.request_json(
            "POST",
            f"/bitable/v1/apps/{app_token}/tables/{table_id}/records/batch_create",
            token=token,
            body={"records": [{"fields": row} for row in chunk]},
        )
        total += len(chunk)
        time.sleep(0.15)
    return total


def update_topics_mark(token: str, app_token: str, table_id: str, records: list[dict[str, Any]], packages: list[dict[str, Any]]) -> int:
    updated = 0
    for record, package in zip(records, packages):
        feishu.request_json(
            "PUT",
            f"/bitable/v1/apps/{app_token}/tables/{table_id}/records/{record['record_id']}",
            token=token,
            body={"fields": {
                TOPIC_MARK_FIELD: "是",
            }},
        )
        updated += 1
        time.sleep(0.1)
    return updated


def status_from_validation(validation: Any) -> str:
    if getattr(validation, "missing_required", []):
        return "缺字段"
    if getattr(validation, "notes", []):
        return "完整执行包-待补判断"
    generic_gaps = [
        item for item in getattr(validation, "evidence_gaps", [])
        if "缺少可展示证据" in str(item) or "至少补一组截图" in str(item)
    ]
    if generic_gaps:
        return "完整执行包-待补关键证据"
    return "已生成完整执行包"


def inline_list(items: Any, fallback: str) -> str:
    values = [str(item).strip() for item in (items or []) if str(item).strip()]
    return "；".join(values) if values else fallback


def qa_items_from_package(package: dict[str, Any]) -> list[str]:
    qa_issues = [str(item).strip() for item in (package.get("qa_issues") or []) if str(item).strip()]
    if qa_issues:
        return qa_issues
    values: list[str] = []
    for key in ("missing_required", "evidence_gaps", "notes"):
        values.extend(str(item).strip() for item in (package.get(key) or []) if str(item).strip())
    return values


def script_package_row_from_package(package: dict[str, Any]) -> dict[str, str]:
    qa_status = str(package.get("qa_status", "revise"))
    if qa_status == "blocked":
        script_status = "完整脚本包-阻塞"
        can_shoot = "否：先补字段"
    elif qa_status == "revise":
        script_status = "完整脚本包-待修订"
        can_shoot = "否：先修订关键判断或证据"
    else:
        script_status = "已生成完整脚本包"
        can_shoot = "是：可拍；按素材提醒和发布前核验处理"
    qa_issues = qa_items_from_package(package)
    return {
        "关联选题": str(package.get("topic_title", "")),
        "脚本状态": script_status,
        "推荐模板": str(package.get("recommended_template", "")),
        "核心观点": str(package.get("core_viewpoint") or package.get("core_thesis") or ""),
        "开头钩子": str(package.get("opening_hook") or package.get("reader_summary") or "")[:500],
        "本地文档": str(package.get("document_path") or package.get("output_dir", "")),
        "素材提醒": inline_list(package.get("p0_todos"), "无P0素材缺口"),
        "发布前核验": inline_list(package.get("release_reminders") or package.get("fact_check_points"), "无额外事实核验点"),
        "QA结果": f"{qa_status}｜{inline_list(qa_issues, '可进入拍摄准备')}"[:1000],
        "是否可拍": can_shoot,
        "版本": str(package.get("version", SCRIPT_VERSION)),
    }


def local_ready_topics() -> list[dict[str, Any]]:
    if not TODAY10.exists():
        return []
    with TODAY10.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    ready = []
    for row in rows:
        status = row.get("状态") or row.get("推荐动作") or row.get("今日建议级别", "")
        if ready_status(status):
            fields = dict(row)
            fields["状态"] = status
            ready.append({"record_id": "", "fields": fields})
    return ready


def feishu_ready_topics(token: str, app_token: str) -> tuple[dict[str, str], list[dict[str, Any]]]:
    by_name = {table["name"]: table["table_id"] for table in feishu.list_tables(token, app_token)}
    table_ids = {
        "topic_decision": resolve_table_id(by_name, "topic_decision"),
        "script_package": resolve_table_id(by_name, "script_package"),
    }
    missing = [TABLES[key] for key, table_id in table_ids.items() if not table_id]
    if missing:
        raise SystemExit(f"Missing required Feishu tables: {missing}")
    records = all_records(token, app_token, table_ids["topic_decision"])
    ready = []
    for record in records:
        fields = record.get("fields", {})
        already_generated = str(fields.get(TOPIC_MARK_FIELD, "")) == "是"
        if ready_status(fields.get("状态") or fields.get("推荐动作")) and not already_generated:
            ready.append(record)
    return table_ids, ready


def filter_topics(topics: list[dict[str, Any]], record_id: str = "", limit: int = 0) -> list[dict[str, Any]]:
    selected = topics
    wanted_ids = {item.strip() for item in record_id.split(",") if item.strip()}
    if wanted_ids:
        selected = [record for record in selected if str(record.get("record_id", "")) in wanted_ids]
    if limit > 0:
        selected = selected[:limit]
    return selected


def normalize_topics(topics: list[dict[str, Any]], austin: Any) -> list[dict[str, Any]]:
    return [
        austin.normalize_topic(record["fields"], record_id=str(record.get("record_id", "")))
        for record in topics
    ]


def preview_packages(topic_cards: list[dict[str, Any]], austin: Any) -> list[dict[str, Any]]:
    runtime = austin.load_private_runtime() if hasattr(austin, "load_private_runtime") else {}
    packages: list[dict[str, Any]] = []
    for topic in topic_cards:
        private_cases = austin.matched_private_cases(topic, runtime) if hasattr(austin, "matched_private_cases") else []
        template, template_reason = austin.classify_template(topic)
        validation = austin.validate_topic(topic)
        summary = austin.outline_summary(topic, template, validation) if hasattr(austin, "outline_summary") else austin.director_summary(topic, template, private_cases)
        status = austin.script_status_from_validation(validation) if hasattr(austin, "script_status_from_validation") else status_from_validation(validation)
        outline = austin.outline_segments(topic) if hasattr(austin, "outline_segments") else []
        core_viewpoint = austin.core_viewpoint(topic, validation) if hasattr(austin, "core_viewpoint") else topic.get("core_thesis")
        opening_hook = (
            austin.full_script_opening(topic, validation)
            if hasattr(austin, "full_script_opening")
            else str(topic.get("core_thesis") or "")
        )
        production_context = (
            austin.generation_input_for_06(topic, template, template_reason, validation, private_cases)
            if hasattr(austin, "generation_input_for_06")
            else ""
        )
        key_evidence = austin.key_evidence_items(topic, validation) if hasattr(austin, "key_evidence_items") else list(topic.get("demo_materials", []))
        p0_todos = (
            austin.shooting_reminder_items(validation)
            if hasattr(austin, "shooting_reminder_items")
            else list(validation.evidence_gaps)
        )
        release_reminders = (
            austin.release_reminder_items(validation)
            if hasattr(austin, "release_reminder_items")
            else list(validation.fact_check_points)
        )
        packages.append({
            "topic_id": topic.get("topic_id"),
            "topic_title": topic.get("topic_title"),
            "output_dir": "dry-run未生成；加 --render-local 或 --write-feishu 后生成",
            "document_path": "dry-run未生成；加 --render-local 或 --write-feishu 后生成",
            "recommended_template": template,
            "template_reason": template_reason,
            "director_summary": summary,
            "core_thesis": topic.get("core_thesis"),
            "core_viewpoint": core_viewpoint,
            "outline_segments": outline,
            "production_context": production_context,
            "opening_hook": opening_hook,
            "key_evidence": key_evidence,
            "p0_todos": p0_todos,
            "release_reminders": release_reminders,
            "reader_summary": f"{status}｜{template}｜{topic.get('core_thesis')}",
            "qa_status": validation.status,
            "missing_required": validation.missing_required,
            "evidence_gaps": validation.evidence_gaps,
            "fact_check_points": validation.fact_check_points,
            "notes": validation.notes,
            "private_case_anchors": [case.get("name", "") for case in private_cases],
            "generated_files": [getattr(austin, "FULL_PACKAGE_FILE", "full_script_execution_package.md")],
            "version": SCRIPT_VERSION,
        })
    return packages


def render_packages(topic_cards: list[dict[str, Any]], austin: Any) -> list[dict[str, Any]]:
    packages = [
        austin.render_full_execution_package(topic, output_root=SCRIPT_OUTPUT_ROOT, run_date=today_slug())
        for topic in topic_cards
    ]
    return packages


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write-feishu", action="store_true", help="Create 06 script package records in Feishu. Default dry-run.")
    parser.add_argument("--render-local", action="store_true", help="In dry-run mode, also write local full execution package files. Default dry-run is read-only.")
    parser.add_argument("--record-id", default="", help="Only process the specified 04 record_id. Comma-separated ids are supported.")
    parser.add_argument("--limit", type=int, default=0, help="Cap the number of ready topics processed for safe tests.")
    args = parser.parse_args()

    token = app_token = ""
    table_ids: dict[str, str] = {}
    if args.write_feishu:
        app_token = require_app_token()
        token = feishu.tenant_token()
        table_ids, topics = feishu_ready_topics(token, app_token)
    else:
        if os.getenv("FEISHU_APP_ID") and os.getenv("FEISHU_APP_SECRET") and os.getenv("FEISHU_BASE_APP_TOKEN"):
            app_token = os.getenv("FEISHU_BASE_APP_TOKEN", "")
            token = feishu.tenant_token()
            table_ids, topics = feishu_ready_topics(token, app_token)
        else:
            topics = local_ready_topics()

    topics = filter_topics(topics, record_id=args.record_id, limit=args.limit)
    austin, skill_dir = load_austin_module()
    topic_cards = normalize_topics(topics, austin)
    rendered_local = args.write_feishu or args.render_local
    packages = render_packages(topic_cards, austin) if rendered_local else preview_packages(topic_cards, austin)
    script_package_rows = [script_package_row_from_package(package) for package in packages]

    print(json.dumps({
        "ok": True,
        "mode": "write" if args.write_feishu else "dry-run",
        "source": "feishu" if table_ids else "local_today10_fallback",
        "skill_dir": str(skill_dir),
        "rendered_local": rendered_local,
        "ready_topics": len(topics),
        "topic_cards": topic_cards,
        "script_packages": packages,
        "script_package_records": script_package_rows,
        "task_records": [],
        "note": "v0.6跳过05中间层，直接生成完整口播稿与制作执行包。本地Markdown为主，飞书06只保留状态、摘要、路径、提醒和QA。dry-run默认不落本地文件；需要本地MD时加 --render-local。",
    }, ensure_ascii=False, indent=2))

    if not args.write_feishu:
        return 0

    ensure_text_fields(token, app_token, table_ids["topic_decision"], [TOPIC_MARK_FIELD])
    ensure_text_fields(token, app_token, table_ids["script_package"], SCRIPT_PACKAGE_FIELDS)
    created_script_packages = batch_create(token, app_token, table_ids["script_package"], script_package_rows)
    marked = update_topics_mark(token, app_token, table_ids["topic_decision"], topics, packages)
    print(json.dumps({
        "ok": True,
        "created_script_packages": created_script_packages,
        "created_tasks": 0,
        "marked_topics": marked,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
