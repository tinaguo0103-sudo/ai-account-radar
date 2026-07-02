#!/usr/bin/env python3
"""Create isolated Feishu test tables for daily feedback learning."""
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
from codex_script_package_runner import refresh_user_doc_token_if_needed
from learn_from_daily_feedback import (
    LEARNING_RECORD_FIELDS,
    LEARNING_TEST_TABLE_NAME,
    SCRIPT_FEEDBACK_FIELDS,
    SCRIPT_TEST_TABLE_NAME,
    TOPIC_LEARNING_FIELDS,
    TOPIC_TEST_TABLE_NAME,
)
from script_package_shared import ensure_text_fields


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENV_OUT = ROOT / ".env.staging.local"


def quote_env(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def write_env_file(path: Path, values: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    seen: set[str] = set()
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            out.append(line)
            continue
        key = line.split("=", 1)[0].strip()
        if key in values:
            out.append(f"{key}={quote_env(values[key])}")
            seen.add(key)
        else:
            out.append(line)
    for key, value in values.items():
        if key not in seen:
            out.append(f"{key}={quote_env(value)}")
    path.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")


def tables_by_name(token: str, app_token: str) -> dict[str, str]:
    return {table["name"]: table["table_id"] for table in feishu.list_tables(token, app_token)}


def create_or_reuse_table(token: str, app_token: str, name: str, fields: list[str]) -> str:
    by_name = tables_by_name(token, app_token)
    if name in by_name:
        table_id = by_name[name]
        ensure_text_fields(token, app_token, table_id, fields)
        return table_id
    payload = feishu.request_json(
        "POST",
        f"/bitable/v1/apps/{app_token}/tables",
        token=token,
        body={"table": {"name": name, "default_view_name": "测试", "fields": [{"field_name": field, "type": 1} for field in fields]}},
    )
    data = payload.get("data", {})
    table = data.get("table", data)
    table_id = str(table.get("table_id") or data.get("table_id") or "")
    if not table_id:
        raise RuntimeError(f"Could not create table {name}: {payload}")
    time.sleep(0.2)
    return table_id


def create_record(token: str, app_token: str, table_id: str, fields: dict[str, str]) -> str:
    payload = feishu.request_json(
        "POST",
        f"/bitable/v1/apps/{app_token}/tables/{table_id}/records",
        token=token,
        body={"fields": fields},
    )
    data = payload.get("data", {})
    record = data.get("record", data)
    return str(record.get("record_id") or "")


def seed_samples(token: str, app_token: str, topic_table_id: str, script_table_id: str) -> dict[str, str]:
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    topic_id = create_record(token, app_token, topic_table_id, {
        "状态": "生成脚本包",
        "学习状态": "待学习",
        "运行批次": f"learning_test_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
        "推荐日期": datetime.now().strftime("%Y-%m-%d"),
        "选题标题": f"【测试】学习闭环选题样本 {stamp}",
        "对应方向": "AI业务系统导演",
        "AI味风险": "中",
        "我要做的实验": "测试学习日结能否识别被推进的选题。",
        "我的工作流痛点": "测试与生产数据不能混用。",
        "选择原因标签": "真实业务痛点、可形成资产",
        "人工一句话判断": "这是 staging 学习闭环样本，不进入生产。",
    })
    script_id = create_record(token, app_token, script_table_id, {
        "脚本标题": f"【测试】学习闭环06反馈样本 {stamp}",
        "关联选题": f"【测试】学习闭环选题样本 {stamp}",
        "脚本状态": "已生成完整脚本包",
        "核心观点": "学习闭环必须先在隔离测试表里跑通。",
        "开头钩子": "这次测试不看感觉，直接看反馈能不能沉淀成规则。",
        "飞书文档": "https://example.com/test-doc",
        "人工质量反馈": "小修可拍",
        "质量问题标签": "不像我、标题弱",
        "人工修改意见": "第一段要更像真实复盘。标题需要更直接。",
        "反馈时间": datetime.now().isoformat(timespec="seconds"),
        "反馈来源": "06完成卡",
        "内容学习状态": "待学习",
    })
    return {"topic_record_id": topic_id, "script_record_id": script_id}


def required_env(keys: list[str]) -> dict[str, str]:
    values: dict[str, str] = {}
    missing: list[str] = []
    for key in keys:
        value = os.getenv(key, "").strip()
        if value:
            values[key] = value
        else:
            missing.append(key)
    if missing:
        raise SystemExit(f"Missing env keys: {', '.join(missing)}")
    return values


def user_open_id() -> str:
    token = refresh_user_doc_token_if_needed()
    payload = feishu.request_json("GET", "/authen/v1/user_info", token=token)
    data = payload.get("data", payload)
    return str(data.get("open_id") or "").strip()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create isolated Feishu test tables for learning feedback loop.")
    parser.add_argument("--env-out", default=str(DEFAULT_ENV_OUT))
    parser.add_argument("--seed-smoke-samples", action="store_true", help="Insert one test 04 and one test 06 feedback sample.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    load_local_env(required=True)
    base_values = required_env(["FEISHU_APP_ID", "FEISHU_APP_SECRET", "FEISHU_BASE_APP_TOKEN"])
    app_token = base_values["FEISHU_BASE_APP_TOKEN"]
    token = feishu.tenant_token()

    topic_table_id = create_or_reuse_table(token, app_token, TOPIC_TEST_TABLE_NAME, TOPIC_LEARNING_FIELDS)
    script_table_id = create_or_reuse_table(token, app_token, SCRIPT_TEST_TABLE_NAME, SCRIPT_FEEDBACK_FIELDS)
    learning_table_id = create_or_reuse_table(token, app_token, LEARNING_TEST_TABLE_NAME, LEARNING_RECORD_FIELDS)

    token_values = {
        key: os.getenv(key, "").strip()
        for key in [
            "FEISHU_SCRIPT_PACKAGE_USER_ACCESS_TOKEN",
            "FEISHU_SCRIPT_PACKAGE_USER_REFRESH_TOKEN",
            "FEISHU_SCRIPT_PACKAGE_USER_ACCESS_TOKEN_EXPIRES_AT",
            "FEISHU_SCRIPT_PACKAGE_USER_REFRESH_TOKEN_EXPIRES_AT",
            "FEISHU_API_BASE_URL",
            "FEISHU_VERIFICATION_TOKEN",
        ]
        if os.getenv(key, "").strip()
    }
    open_id = user_open_id()
    if not open_id:
        raise SystemExit("Could not resolve current Feishu user open_id for personal learning card target.")
    env_out = Path(args.env_out).expanduser()
    write_env_file(env_out, {
        **base_values,
        **token_values,
        "AI_ACCOUNT_RADAR_ENV": "staging",
        "FEISHU_TOPIC_DECISION_TABLE_ID": topic_table_id,
        "FEISHU_TOPIC_TABLE_ID": topic_table_id,
        "FEISHU_SCRIPT_PACKAGE_TABLE_ID": script_table_id,
        "FEISHU_LEARNING_TABLE_ID": learning_table_id,
        "FEISHU_LEARNING_FEEDBACK_RECEIVE_TARGETS": f"open_id:{open_id}",
    })

    seeded = seed_samples(token, app_token, topic_table_id, script_table_id) if args.seed_smoke_samples else {}
    print(json.dumps({
        "ok": True,
        "env_out": str(env_out),
        "topic_test_table_name": TOPIC_TEST_TABLE_NAME,
        "topic_test_table_id": topic_table_id,
        "script_test_table_name": SCRIPT_TEST_TABLE_NAME,
        "script_test_table_id": script_table_id,
        "learning_test_table_name": LEARNING_TEST_TABLE_NAME,
        "learning_test_table_id": learning_table_id,
        "feedback_target": "self_open_id",
        "seeded": seeded,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
