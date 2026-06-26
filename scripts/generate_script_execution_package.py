#!/usr/bin/env python3
"""Generate a full Austin script/execution package from one Feishu 04 topic.

This is the post-selection step:

04 进入Brief + 我的制作补充 -> local full_script_execution_package.md -> light 05 record.

It does not split 06 task rows yet. The user-facing artifact is the local
Markdown package; Feishu keeps only the index and short production fields.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from local_env import load_local_env

import push_to_feishu as feishu
from feishu_table_registry import TABLES, resolve_table_id


ROOT = Path(__file__).resolve().parents[1]
GLOBAL_AUSTIN_SKILL_DIR = Path.home() / ".codex" / "skills" / "austin-no-overtime-scripting"
REPO_AUSTIN_SKILL_DIR = ROOT / "skills" / "austin-no-overtime-scripting"
OUTPUT_ROOT = ROOT / "output" / "script_execution_packages"
TOPIC_MARK_FIELD = "是否已生成脚本稿"
BRIEF_FIELDS = [
    "关联选题",
    "脚本状态",
    "推荐模板",
    "核心观点",
    "视频大纲",
    "给06的生成输入",
    "一句话说明",
    "本地文档",
    "是否可进入06",
    "版本",
]


def today_slug() -> str:
    return datetime.now().strftime("%Y-%m-%d")


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


def load_austin_module() -> tuple[Any, Path]:
    skill_dir = resolve_austin_skill_dir()
    module_path = skill_dir / "scripts" / "austin_scripting.py"
    spec = importlib.util.spec_from_file_location("austin_scripting_runtime", module_path)
    if not spec or not spec.loader:
        raise SystemExit(f"Could not load Austin scripting module from {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    if not hasattr(module, "render_full_execution_package"):
        raise SystemExit(f"Austin scripting module does not support full execution packages: {module_path}")
    return module, skill_dir


def all_tables(token: str, app_token: str) -> dict[str, str]:
    by_name = {table["name"]: table["table_id"] for table in feishu.list_tables(token, app_token)}
    topic_table = resolve_table_id(by_name, "topic_decision")
    brief_table = resolve_table_id(by_name, "brief_production")
    missing = []
    if not topic_table:
        missing.append(TABLES["topic_decision"])
    if not brief_table:
        missing.append(TABLES["brief_production"])
    if missing:
        raise SystemExit(f"Missing required Feishu tables: {missing}")
    return {"topic_decision": topic_table, "brief_production": brief_table}


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


def read_topic_record(token: str, app_token: str, table_id: str, record_id: str) -> dict[str, Any]:
    payload = feishu.request_json("GET", f"/bitable/v1/apps/{app_token}/tables/{table_id}/records/{record_id}", token=token)
    record = payload.get("data", {}).get("record", {})
    if not record:
        raise SystemExit(f"Could not read topic record: {record_id}")
    return record


def package_to_brief_row(package: dict[str, Any]) -> dict[str, str]:
    qa_status = str(package.get("qa_status") or "revise")
    if qa_status == "pass":
        script_status = "已生成完整脚本包"
        can_enter = "是：可制作，可按需拆06任务"
    elif qa_status == "blocked":
        script_status = "完整脚本包-阻塞"
        can_enter = "否：先补字段"
    else:
        script_status = "完整脚本包-待补素材"
        can_enter = "待补素材后可制作，可按需拆06任务"
    outline = package.get("outline_segments") or []
    issues = package.get("qa_issues") or []
    return {
        "关联选题": str(package.get("topic_title") or ""),
        "脚本状态": script_status,
        "推荐模板": str(package.get("recommended_template") or ""),
        "核心观点": str(package.get("core_viewpoint") or package.get("core_thesis") or ""),
        "视频大纲": "\n".join(f"{idx}. {item}" for idx, item in enumerate(outline, 1)),
        "给06的生成输入": str(package.get("generation_input_06") or ""),
        "一句话说明": "；".join(str(item) for item in issues[:3])[:500] if issues else str(package.get("opening_hook") or "")[:500],
        "本地文档": str(package.get("document_path") or package.get("output_dir") or ""),
        "是否可进入06": can_enter,
        "版本": str(package.get("version") or ""),
    }


def create_brief_record(token: str, app_token: str, table_id: str, row: dict[str, str]) -> str:
    payload = feishu.request_json(
        "POST",
        f"/bitable/v1/apps/{app_token}/tables/{table_id}/records",
        token=token,
        body={"fields": row},
    )
    return str(payload.get("data", {}).get("record", {}).get("record_id") or "")


def mark_topic_generated(token: str, app_token: str, table_id: str, record_id: str) -> None:
    feishu.request_json(
        "PUT",
        f"/bitable/v1/apps/{app_token}/tables/{table_id}/records/{record_id}",
        token=token,
        body={"fields": {TOPIC_MARK_FIELD: "是"}},
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--record-id", required=True, help="Feishu 04 record_id to render.")
    parser.add_argument("--write-feishu", action="store_true", help="Create a light 05 record and mark the 04 topic generated.")
    parser.add_argument("--output-root", default=str(OUTPUT_ROOT), help="Local output root.")
    args = parser.parse_args()

    load_local_env()
    app_token = os.getenv("FEISHU_BASE_APP_TOKEN", "").strip()
    if not app_token:
        raise SystemExit("FEISHU_BASE_APP_TOKEN is required")
    token = feishu.tenant_token()
    table_ids = all_tables(token, app_token)
    topic_record = read_topic_record(token, app_token, table_ids["topic_decision"], args.record_id)
    austin, skill_dir = load_austin_module()
    topic = austin.normalize_topic(topic_record.get("fields", {}), record_id=args.record_id)
    package = austin.render_full_execution_package(topic, output_root=Path(args.output_root), run_date=today_slug())
    brief_row = package_to_brief_row(package)

    created_brief_id = ""
    if args.write_feishu:
        ensure_text_fields(token, app_token, table_ids["topic_decision"], [TOPIC_MARK_FIELD])
        ensure_text_fields(token, app_token, table_ids["brief_production"], BRIEF_FIELDS)
        created_brief_id = create_brief_record(token, app_token, table_ids["brief_production"], brief_row)
        mark_topic_generated(token, app_token, table_ids["topic_decision"], args.record_id)

    print(json.dumps({
        "ok": True,
        "mode": "write" if args.write_feishu else "dry-run",
        "skill_dir": str(skill_dir),
        "topic": {
            "record_id": args.record_id,
            "title": topic.get("topic_title"),
            "status": topic.get("status"),
            "production_direction": topic.get("production_direction"),
        },
        "package": package,
        "brief_record": brief_row,
        "created_brief_id": created_brief_id,
        "note": "06 execution package is a local Markdown artifact; Feishu 05 only stores a light index.",
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
