#!/usr/bin/env python3
"""Create Feishu Base views for the AI account radar execution console."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import push_to_feishu as p


ROOT = Path(__file__).resolve().parents[1]

VIEW_PLAN = {
    "定位与选题假设": ["01 当前定位假设", "02 待验证信号", "03 需要调整"],
    "执行台逻辑说明": ["01 从这里读逻辑"],
    "视图导航表": ["01 每天先看这个"],
    "来源配置表": ["01 重点每日跟踪", "02 对标博主池", "03 可自动来源", "04 手动采样来源"],
    "手动采样入口表": ["01 待处理", "02 已进入收件箱"],
    "内容收件箱": ["01 今日新内容", "02 待分析", "03 AIHOT", "04 对标内容", "05 去重后内容"],
    "热点分析表": ["01 A级热点", "02 可做SOP", "03 Agent方向", "04 AI导演方向", "05 需核对原文"],
    "对标分析表": ["01 高价值对标", "02 钩子库", "03 结构库", "04 不能照搬提醒"],
    "选题候选库": ["01 A级选题", "02 本周待做", "03 按平台", "04 资料包潜力", "05 已排期"],
    "内容Brief表": ["01 待补Brief", "02 可制作", "03 按资料包承接"],
    "资产与资料包表": ["01 高优先级资产", "02 待制作", "03 按栏目"],
    "发布复盘表": ["01 本周发布", "02 高收藏内容", "03 高私信内容", "04 待复盘"],
    "周复盘与定位校准表": ["01 本月四周", "02 待复盘", "03 需要调整规则"],
}


def list_views(token: str, app_token: str, table_id: str) -> list[dict]:
    payload = p.request_json("GET", f"/bitable/v1/apps/{app_token}/tables/{table_id}/views", token=token)
    return payload.get("data", {}).get("items", [])


def create_view(token: str, app_token: str, table_id: str, view_name: str) -> str:
    payload = p.request_json(
        "POST",
        f"/bitable/v1/apps/{app_token}/tables/{table_id}/views",
        token=token,
        body={"view_name": view_name, "view_type": "grid"},
    )
    return payload.get("data", {}).get("view", {}).get("view_id", "")


def main() -> int:
    token = p.tenant_token()
    app_token = p.get_or_create_base(token)
    tables = {table["name"]: table["table_id"] for table in p.list_tables(token, app_token)}
    result = []
    for table_name, view_names in VIEW_PLAN.items():
        table_id = tables.get(table_name)
        if not table_id:
            result.append({"table": table_name, "status": "missing_table"})
            continue
        existing = {view.get("view_name") for view in list_views(token, app_token, table_id)}
        created = []
        skipped = []
        for view_name in view_names:
            if view_name in existing:
                skipped.append(view_name)
                continue
            created.append({"name": view_name, "view_id": create_view(token, app_token, table_id, view_name)})
            time.sleep(0.1)
        result.append({"table": table_name, "created": created, "skipped": skipped})
    print(json.dumps({"ok": True, "app_token": app_token, "views": result}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
