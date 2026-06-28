"""Shared helpers for the 04 -> 06 script package pipeline."""
from __future__ import annotations

import importlib.util
import os
import sys
import time
from pathlib import Path
from typing import Any

import push_to_feishu as feishu
from feishu_table_registry import TABLES, resolve_table_id


ROOT = Path(__file__).resolve().parents[1]
GLOBAL_AUSTIN_SKILL_DIR = Path.home() / ".codex" / "skills" / "austin-no-overtime-scripting"
REPO_AUSTIN_SKILL_DIR = ROOT / "skills" / "austin-no-overtime-scripting"
SCRIPT_VERSION = "austin-production-packager-v0.6"
TOPIC_MARK_FIELD = "是否已生成脚本稿"
SCRIPT_PACKAGE_FIELDS = [
    "关联选题",
    "脚本状态",
    "推荐模板",
    "核心观点",
    "开头钩子",
    "本地文档",
    "素材提醒",
    "发布前核验",
    "QA结果",
    "是否可拍",
    "版本",
]


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


def load_austin_module() -> tuple[Any, Path]:
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
