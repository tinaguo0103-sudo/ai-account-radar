#!/usr/bin/env python3
"""Logical Feishu table registry.

Keep table names in one place so scripts do not scatter hard-coded names.
Aliases allow the pipeline to keep working during table-name migration.
"""
from __future__ import annotations

from typing import Any


TABLES: dict[str, str] = {
    "console": "00 主控台",
    "source_sampling": "01 来源与采样",
    "url_inbox": "02 URL投喂入口",
    "content_inbox": "03 内容收件箱",
    "topic_decision": "04 分析与选题",
    "brief_production": "05 Brief与制作",
    "task_master": "06 内容任务主表",
    "review_assets": "07 资产与复盘",
    "rules_dictionary": "99 规则与字典",
}

ALIASES: dict[str, list[str]] = {
    "url_inbox": ["06 URL投喂入口"],
    "content_inbox": ["02 内容收件箱"],
    "topic_decision": ["03 分析与选题"],
    "brief_production": ["04 Brief与制作"],
    "review_assets": ["05 资产与复盘"],
}

VIEW_NAMES: dict[str, list[str]] = {
    "console": ["今日工作台", "系统导航"],
    "source_sampling": ["当前主对标池", "历史参考池", "系统/官方源", "手动入口"],
    "url_inbox": ["URL投喂入口"],
    "content_inbox": ["内容收件箱", "今日采集", "最近15天", "永久保留"],
    "topic_decision": ["今日候选池", "今日最值得做", "暂存观察"],
    "brief_production": ["Brief制作后台"],
    "task_master": ["今日待办", "明日预警", "本周任务", "发布相关任务", "直播排期", "复盘任务"],
    "review_assets": ["资产复盘后台"],
    "rules_dictionary": ["规则与字典"],
}

PROTECTED_TABLE_KEYS = {"rules_dictionary"}
PROTECTED_TABLE_NAMES = {TABLES[key] for key in PROTECTED_TABLE_KEYS}


def table_name(key: str) -> str:
    return TABLES[key]


def table_names() -> list[str]:
    return list(TABLES.values())


def candidates(key: str) -> list[str]:
    return [TABLES[key], *ALIASES.get(key, [])]


def resolve_table_id(tables_by_name: dict[str, str], key: str) -> str | None:
    for name in candidates(key):
        if name in tables_by_name:
            return tables_by_name[name]
    return None


def resolve_table_name(tables_by_name: dict[str, str], key: str) -> str | None:
    for name in candidates(key):
        if name in tables_by_name:
            return name
    return None


def resolve_tables(tables: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
    by_name = {table["name"]: table["table_id"] for table in tables}
    resolved: dict[str, dict[str, str]] = {}
    for key, desired_name in TABLES.items():
        actual_name = resolve_table_name(by_name, key)
        if actual_name:
            resolved[key] = {
                "name": desired_name,
                "actual_name": actual_name,
                "table_id": by_name[actual_name],
            }
    return resolved
