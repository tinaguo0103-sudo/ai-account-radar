#!/usr/bin/env python3
"""Create/reuse isolated Feishu resources for 06 script-package testing."""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any

from local_env import load_local_env
import push_to_feishu as feishu
from script_package_shared import SCRIPT_PACKAGE_FIELDS, ensure_text_fields
from codex_script_package_runner import feishu_doc_token, refresh_user_doc_token_if_needed


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENV_OUT = ROOT / ".env.staging.local"
TEST_TABLE_NAME = "06 完整脚本与制作包__测试"
TEST_FOLDER_NAME = "06完整脚本与制作包_TEST"
CLICKABLE_LINK_TEST_FIELDS = ["飞书文档链接", "飞书文件夹链接"]


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


def create_test_table(token: str, app_token: str) -> str:
    by_name = tables_by_name(token, app_token)
    if TEST_TABLE_NAME in by_name:
        table_id = by_name[TEST_TABLE_NAME]
        ensure_text_fields(token, app_token, table_id, SCRIPT_PACKAGE_FIELDS)
        ensure_url_fields(token, app_token, table_id, CLICKABLE_LINK_TEST_FIELDS)
        return table_id
    payload = feishu.request_json(
        "POST",
        f"/bitable/v1/apps/{app_token}/tables",
        token=token,
        body={
            "table": {
                "name": TEST_TABLE_NAME,
                "default_view_name": "测试脚本包",
                "fields": [{"field_name": name, "type": 1} for name in SCRIPT_PACKAGE_FIELDS],
            }
        },
    )
    data = payload.get("data", {})
    table = data.get("table", data)
    table_id = str(table.get("table_id") or data.get("table_id") or "")
    if not table_id:
        raise RuntimeError(f"Could not create test table: {payload}")
    ensure_url_fields(token, app_token, table_id, CLICKABLE_LINK_TEST_FIELDS)
    time.sleep(0.2)
    return table_id


def ensure_url_fields(token: str, app_token: str, table_id: str, field_names: list[str]) -> list[str]:
    fields = {
        field["field_name"]: field
        for field in feishu.request_json(
            "GET",
            f"/bitable/v1/apps/{app_token}/tables/{table_id}/fields",
            token=token,
        ).get("data", {}).get("items", [])
    }
    created: list[str] = []
    for name in field_names:
        existing = fields.get(name)
        if existing:
            if int(existing.get("type") or 0) != 15:
                raise RuntimeError(f"{name} exists but is not a URL field: type={existing.get('type')}")
            continue
        feishu.request_json(
            "POST",
            f"/bitable/v1/apps/{app_token}/tables/{table_id}/fields",
            token=token,
            body={"field_name": name, "type": 15},
        )
        created.append(name)
        time.sleep(0.1)
    return created


def drive_folder_url(folder_token: str) -> str:
    return f"https://my.feishu.cn/drive/folder/{folder_token}"


def create_test_folder(parent_folder_token: str, tenant_token: str) -> tuple[str, str]:
    doc_token = feishu_doc_token(tenant_token)
    try:
        payload = feishu.request_json(
            "POST",
            "/drive/v1/files/create_folder",
            token=doc_token,
            body={"name": TEST_FOLDER_NAME, "folder_token": parent_folder_token},
        )
    except Exception as exc:
        message = str(exc)
        if "drive:drive" in message or "space:folder:create" in message or "20027" in message:
            raise SystemExit(
                "当前飞书应用还不能自动创建测试文件夹。请先在飞书开发者后台开通并发布 "
                "drive:drive 和 space:folder:create，然后重新运行 feishu_user_oauth.py 授权；"
                "或者手动建测试文件夹后使用 --reuse-folder-token / --reuse-folder-url。"
            ) from exc
        raise
    data = payload.get("data", {})
    token = str(
        data.get("token")
        or data.get("folder_token")
        or data.get("file", {}).get("token")
        or data.get("file", {}).get("folder_token")
        or ""
    )
    url = str(data.get("url") or data.get("file", {}).get("url") or "")
    if not token:
        raise RuntimeError(f"Could not create test folder: {payload}")
    return token, url or drive_folder_url(token)


def user_open_id() -> str:
    token = refresh_user_doc_token_if_needed()
    payload = feishu.request_json("GET", "/authen/v1/user_info", token=token)
    data = payload.get("data", payload)
    return str(data.get("open_id") or "").strip()


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


def main() -> int:
    parser = argparse.ArgumentParser(description="Create isolated Feishu test table/folder for 06 completion-card testing.")
    parser.add_argument("--env-out", default=str(DEFAULT_ENV_OUT))
    parser.add_argument("--reuse-folder-token", default="", help="Use an existing test folder token instead of creating one.")
    parser.add_argument("--reuse-folder-url", default="", help="Browser URL for --reuse-folder-token.")
    args = parser.parse_args()

    load_local_env(required=True)
    base_values = required_env(["FEISHU_APP_ID", "FEISHU_APP_SECRET", "FEISHU_BASE_APP_TOKEN"])
    app_token = base_values["FEISHU_BASE_APP_TOKEN"]
    tenant_token = feishu.tenant_token()
    table_id = create_test_table(tenant_token, app_token)

    parent_folder_token = os.getenv("FEISHU_SCRIPT_PACKAGE_VISIBLE_FOLDER_TOKEN", "").strip()
    if args.reuse_folder_token:
        folder_token = args.reuse_folder_token.strip()
        folder_url = args.reuse_folder_url.strip() or drive_folder_url(folder_token)
    else:
        if not parent_folder_token:
            raise SystemExit("Missing FEISHU_SCRIPT_PACKAGE_VISIBLE_FOLDER_TOKEN; cannot create test folder.")
        folder_token, folder_url = create_test_folder(parent_folder_token, tenant_token)

    open_id = user_open_id()
    if not open_id:
        raise SystemExit("Could not resolve current Feishu user open_id for personal test card target.")

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
    values = {
        **base_values,
        **token_values,
        "FEISHU_SCRIPT_PACKAGE_TABLE_ID": table_id,
        "FEISHU_SCRIPT_PACKAGE_VISIBLE_FOLDER_TOKEN": folder_token,
        "FEISHU_SCRIPT_PACKAGE_VISIBLE_FOLDER_URL": folder_url,
        "FEISHU_SCRIPT_PACKAGE_FEEDBACK_RECEIVE_TARGETS": f"open_id:{open_id}",
    }
    env_out = Path(args.env_out).expanduser()
    write_env_file(env_out, values)
    print(json.dumps({
        "ok": True,
        "env_out": str(env_out),
        "test_table_name": TEST_TABLE_NAME,
        "test_table_id": table_id,
        "test_folder_name": TEST_FOLDER_NAME,
        "test_folder_token": folder_token,
        "test_folder_url": folder_url,
        "feedback_target": "self_open_id",
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
