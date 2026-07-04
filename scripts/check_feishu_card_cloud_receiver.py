#!/usr/bin/env python3
"""Health-check the Tencent SCF Feishu topic-card receiver without writing records."""
from __future__ import annotations

import argparse
import json
import os
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import push_to_feishu as feishu
from feishu_table_registry import TABLES, resolve_table_id
from local_env import load_local_env


DEFAULT_TABLE_KEY = "topic_decision"
RECEIVER_URL_ENV_KEYS = ("FEISHU_TENCENT_SCF_URL",)
TOPIC_TABLE_ID_ENV_KEYS = ("FEISHU_TOPIC_TABLE_ID", "FEISHU_TOPIC_DECISION_TABLE_ID")
TABLE_ID_ENV_KEYS: dict[str, tuple[str, ...]] = {
    "topic_decision": TOPIC_TABLE_ID_ENV_KEYS,
    "script_package": ("FEISHU_SCRIPT_PACKAGE_TABLE_ID",),
    "learning_record": ("FEISHU_LEARNING_TABLE_ID",),
}
TEST_CARD_REQUIRED_ENV_KEYS = (
    "FEISHU_CARD_RECEIVE_TARGETS",
    "FEISHU_PRODUCTION_DIRECTION_RECEIVE_TARGETS",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check Tencent SCF receiver URL and 04 table read access.")
    parser.add_argument("--url", default="", help="Tencent SCF Function URL. Defaults to FEISHU_TENCENT_SCF_URL.")
    parser.add_argument("--table-key", default=DEFAULT_TABLE_KEY, choices=sorted(TABLES), help="Table key to read.")
    parser.add_argument("--skip-receiver", action="store_true", help="Skip receiver challenge check.")
    parser.add_argument("--skip-feishu-read", action="store_true", help="Skip Feishu table read check.")
    parser.add_argument(
        "--require-test-card-config",
        action="store_true",
        help="Fail unless staging/test receiver URL, receive targets, and explicit topic table id are configured.",
    )
    return parser.parse_args()


def env_receiver_url() -> str:
    for key in RECEIVER_URL_ENV_KEYS:
        value = os.getenv(key, "").strip()
        if value:
            return value
    return ""


def env_value(key: str) -> str:
    return os.getenv(key, "").strip()


def explicit_table_id(table_key: str) -> tuple[str, str]:
    for key in TABLE_ID_ENV_KEYS.get(table_key, ()):
        value = env_value(key)
        if value:
            return value, key
    return "", ""


def post_json(url: str, body: dict[str, Any]) -> dict[str, Any]:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    request = Request(
        url,
        data=data,
        method="POST",
        headers={"content-type": "application/json; charset=utf-8"},
    )
    try:
        with urlopen(request, timeout=25) as response:
            text = response.read().decode("utf-8")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"receiver returned HTTP {exc.code}: {detail[:500]}") from exc
    except URLError as exc:
        raise RuntimeError(f"receiver request failed: {exc.reason}") from exc
    try:
        return json.loads(text or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"receiver returned non-JSON response: {text[:500]}") from exc


def check_receiver(url: str) -> dict[str, Any]:
    challenge = f"codex-healthcheck-{int(time.time())}"
    payload = post_json(url, {"challenge": challenge})
    ok = payload.get("challenge") == challenge
    return {
        "ok": ok,
        "url": url,
        "expected_challenge": challenge,
        "response_keys": sorted(str(key) for key in payload),
        "error": "" if ok else "challenge mismatch",
    }


def check_feishu_read(table_key: str) -> dict[str, Any]:
    app_token = os.getenv("FEISHU_BASE_APP_TOKEN", "").strip()
    if not app_token:
        raise RuntimeError("Missing FEISHU_BASE_APP_TOKEN")
    token = feishu.tenant_token()
    table_id, table_id_source = explicit_table_id(table_key)
    table_name = TABLES[table_key]
    if not table_id:
        tables = feishu.list_tables(token, app_token)
        table_id = resolve_table_id({table["name"]: table["table_id"] for table in tables}, table_key) or ""
        table_id_source = f"name:{table_name}" if table_id else ""
    if not table_id:
        raise RuntimeError(f"Missing table for key {table_key}: {table_name}")
    query = urlencode({"page_size": 1})
    payload = feishu.request_json(
        "GET",
        f"/bitable/v1/apps/{app_token}/tables/{table_id}/records?{query}",
        token=token,
    )
    data = payload.get("data", {})
    return {
        "ok": True,
        "table_key": table_key,
        "table_name": table_name,
        "table_id": table_id,
        "table_id_source": table_id_source,
        "sample_record_count": len(data.get("items", [])),
        "has_more": bool(data.get("has_more")),
    }


def check_test_card_config(receiver_url: str, table_key: str) -> dict[str, Any]:
    missing: list[str] = []
    if not receiver_url:
        missing.append("FEISHU_TENCENT_SCF_URL or --url")
    for key in TEST_CARD_REQUIRED_ENV_KEYS:
        if not env_value(key):
            missing.append(key)
    table_id, table_id_source = explicit_table_id(table_key)
    if table_key == "topic_decision" and not table_id:
        missing.append("FEISHU_TOPIC_TABLE_ID or FEISHU_TOPIC_DECISION_TABLE_ID")
    environment = env_value("AI_ACCOUNT_RADAR_ENV")
    if environment.lower() in {"prod", "production"}:
        missing.append("non-production AI_ACCOUNT_RADAR_ENV")
    return {
        "ok": not missing,
        "receiver_url_configured": bool(receiver_url),
        "table_key": table_key,
        "table_id": table_id,
        "table_id_source": table_id_source,
        "card_receive_targets_configured": bool(env_value("FEISHU_CARD_RECEIVE_TARGETS")),
        "production_direction_receive_targets_configured": bool(env_value("FEISHU_PRODUCTION_DIRECTION_RECEIVE_TARGETS")),
        "environment": environment or "unset",
        "missing": missing,
        "error": "" if not missing else "Missing isolated test card receiver config: " + ", ".join(missing),
        "note": "This checks local staging/test config only; deploy/test receiver cloud env must use the same explicit table id before Flow QA.",
    }


def run_check(func: Any, *args: Any) -> dict[str, Any]:
    try:
        return func(*args)
    except Exception as exc:  # noqa: BLE001 - health checks should report compactly.
        return {"ok": False, "error": str(exc)}


def main() -> int:
    args = parse_args()
    load_local_env()

    checks: dict[str, Any] = {}
    receiver_url = args.url.strip() or env_receiver_url()
    if args.require_test_card_config:
        checks["test_card_config"] = check_test_card_config(receiver_url, args.table_key)
    if not args.skip_receiver:
        if not receiver_url:
            checks["receiver_challenge"] = {
                "ok": False,
                "error": "Set FEISHU_TENCENT_SCF_URL or pass --url.",
            }
        else:
            checks["receiver_challenge"] = run_check(check_receiver, receiver_url)

    if not args.skip_feishu_read:
        checks["feishu_table_read"] = run_check(check_feishu_read, args.table_key)

    ok = all(check.get("ok") for check in checks.values()) if checks else False
    print(json.dumps({"ok": ok, "checks": checks}, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
