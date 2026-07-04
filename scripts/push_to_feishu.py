#!/usr/bin/env python3
"""
Push generated AI account radar tables to Feishu Base via Open API.

This is the no-manual-import path. It expects a Feishu self-built app with
bitable permissions and reads credentials from environment variables.

Current sync target: the simplified 6-table execution console. The older
12/13-table import layout is intentionally not used here anymore, because it
can recreate deprecated tables such as 热点分析表、对标分析表、选题候选库、发布复盘表.

Required:
  FEISHU_APP_ID
  FEISHU_APP_SECRET

Optional:
  FEISHU_BASE_APP_TOKEN   Existing Base app_token. If absent, create a new Base.
  FEISHU_API_BASE_URL     Defaults to https://open.feishu.cn. Use https://open.larksuite.com if DNS for open.feishu.cn fails.
  FEISHU_FOLDER_TOKEN     Folder token for creating a new Base in a target folder.
  FEISHU_BASE_NAME        Defaults to "AI账号信息雷达 + 飞书执行台 v0.1"
"""
from __future__ import annotations

import csv
from datetime import datetime
import json
import os
import re
import socket
import ssl
import sys
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl
from urllib.request import Request, urlopen

from feishu_table_registry import PROTECTED_TABLE_NAMES, table_name
from local_env import load_local_env


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output"
LOG_DIR = OUT / "logs"
DEFAULT_API_HOST = "https://open.feishu.cn"
SAFE_RETRY_METHODS = {"GET", "PUT", "PATCH", "DELETE"}
TRANSIENT_HTTP_STATUS = {408, 425, 429, 500, 502, 503, 504}

load_local_env()


def api_base_url() -> str:
    host = os.getenv("FEISHU_API_BASE_URL", DEFAULT_API_HOST).rstrip("/")
    return host if host.endswith("/open-apis") else f"{host}/open-apis"

TABLE_FILES = [
    (table_name("source_sampling"), "sources_config.csv"),
    (table_name("content_inbox"), "content_inbox.csv"),
    (table_name("topic_decision"), "topic_candidates.csv"),
    (table_name("review_assets"), "assets.csv"),
]

PROTECTED_TABLES = set(PROTECTED_TABLE_NAMES)
DEPRECATED_TABLE_NAMES = {
    "定位与选题假设",
    "执行台逻辑说明",
    "视图导航表",
    "来源配置表",
    "手动采样入口表",
    "内容收件箱",
    "热点分析表",
    "对标分析表",
    "选题候选库",
    "资产与资料包表",
    "发布复盘表",
    "周复盘与定位校准表",
}


def die(message: str) -> None:
    print(json.dumps({"ok": False, "error": message}, ensure_ascii=False), file=sys.stderr)
    raise SystemExit(1)


def is_transient_error(exc: BaseException) -> bool:
    if isinstance(exc, HTTPError):
        return exc.code in TRANSIENT_HTTP_STATUS
    if isinstance(exc, URLError):
        return True
    if isinstance(exc, (TimeoutError, socket.timeout, ConnectionResetError, ssl.SSLError)):
        return True
    text = f"{exc.__class__.__name__}: {exc}".lower()
    return any(
        marker in text
        for marker in [
            "timed out",
            "timeout",
            "temporarily unavailable",
            "connection reset",
            "remote end closed",
            "ssl",
        ]
    )


def should_retry_request(method: str, retry: bool | None) -> bool:
    if retry is not None:
        return retry
    return method.upper() in SAFE_RETRY_METHODS


def retry_status_unknown_error(method: str, path: str, exc: BaseException) -> RuntimeError:
    return RuntimeError(
        f"{method} {path} failed with transient error; status unknown and not retried "
        f"because request is not marked safe/idempotent: {exc}"
    )


def extract_path_part(path: str, pattern: str) -> str | None:
    match = re.search(pattern, path)
    return match.group(1) if match else None


def sanitized_path_metadata(path: str) -> dict[str, Any]:
    raw_path, separator, query = path.partition("?")
    table_id = extract_path_part(raw_path, r"/tables/([^/?]+)")
    record_id = extract_path_part(raw_path, r"/records/([^/?]+)")
    sanitized = raw_path
    replacements = [
        (r"(/bitable/v1/apps/)[^/?]+", r"\1{app_token}"),
        (r"(/tables/)[^/?]+", r"\1{table_id}"),
        (r"(/records/)[^/?]+", r"\1{record_id}"),
        (r"(/docx/v1/documents/)[^/?]+", r"\1{document_id}"),
        (r"(/blocks/)[^/?]+", r"\1{block_id}"),
    ]
    for pattern, replacement in replacements:
        sanitized = re.sub(pattern, replacement, sanitized)
    query_keys: list[str] = []
    if separator and query:
        query_keys = sorted({key for key, _value in parse_qsl(query, keep_blank_values=True)})
        if query_keys:
            sanitized = f"{sanitized}?" + "&".join(f"{key}=<redacted>" for key in query_keys)
    return {
        "path_template": sanitized,
        "table_id": table_id,
        "record_id": record_id,
        "query_keys": query_keys,
    }


def error_kind(exc: BaseException | None) -> str:
    if exc is None:
        return "none"
    if isinstance(exc, HTTPError):
        return "http_error"
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return "timeout"
    if isinstance(exc, ssl.SSLError):
        return "ssl_error"
    if isinstance(exc, URLError):
        return "url_error"
    if isinstance(exc, ConnectionResetError):
        return "connection_reset"
    return "other"


def retry_decision_for(*, will_retry: bool, transient: bool, retry_enabled: bool, final_attempt: bool) -> str:
    if will_retry:
        return "retry"
    if not transient:
        return "not_transient"
    if not retry_enabled:
        return "retry_disabled_status_unknown"
    if final_attempt:
        return "max_attempts_reached"
    return "not_retried"


def write_feishu_request_telemetry(event: dict[str, Any]) -> Path:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    path = LOG_DIR / f"feishu_request_telemetry_{datetime.now().strftime('%Y-%m-%d')}.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
    return path


def safe_write_feishu_request_telemetry(event: dict[str, Any]) -> None:
    if os.getenv("FEISHU_REQUEST_TELEMETRY", "1").strip().lower() in {"0", "false", "no"}:
        return
    try:
        write_feishu_request_telemetry(event)
    except Exception as exc:  # noqa: BLE001 - telemetry must never mask Feishu result.
        print(
            json.dumps(
                {
                    "ok": False,
                    "event": "feishu_request_telemetry_write_failed",
                    "error": f"{exc.__class__.__name__}: {exc}",
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )


def record_request_telemetry(
    *,
    method: str,
    path: str,
    payload_size_bytes: int,
    attempt: int,
    max_attempts: int,
    duration_ms: int,
    status_code: int | None,
    kind: str,
    retry_decision: str,
    will_retry: bool,
    status_unknown: bool,
    feishu_code: int | None = None,
) -> None:
    path_metadata = sanitized_path_metadata(path)
    now = datetime.now()
    event = {
        "timestamp": now.isoformat(timespec="milliseconds"),
        "run_date": now.strftime("%Y-%m-%d"),
        "method": method.upper(),
        "path_template": path_metadata["path_template"],
        "table_id": path_metadata["table_id"],
        "record_id": path_metadata["record_id"],
        "query_keys": path_metadata["query_keys"],
        "payload_size_bytes": payload_size_bytes,
        "attempt": attempt,
        "max_attempts": max_attempts,
        "duration_ms": duration_ms,
        "status_code": status_code,
        "feishu_code": feishu_code,
        "error_kind": kind,
        "retry_decision": retry_decision,
        "will_retry": will_retry,
        "status_unknown": status_unknown,
    }
    safe_write_feishu_request_telemetry(event)


def request_json(
    method: str,
    path: str,
    token: str | None = None,
    body: dict[str, Any] | None = None,
    *,
    retry: bool | None = None,
    attempts: int = 3,
    base_delay: float = 1.0,
) -> dict[str, Any]:
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json; charset=utf-8"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = Request(api_base_url() + path, data=data, method=method, headers=headers)
    retry_enabled = should_retry_request(method, retry)
    max_attempts = max(1, attempts)
    payload_size_bytes = len(data or b"")
    last_exc: BaseException | None = None
    for attempt in range(1, max_attempts + 1):
        started = time.monotonic()
        try:
            with urlopen(req, timeout=30) as resp:
                status_code = getattr(resp, "status", None)
                if status_code is None and hasattr(resp, "getcode"):
                    status_code = resp.getcode()
                payload = json.loads(resp.read().decode("utf-8"))
            duration_ms = int((time.monotonic() - started) * 1000)
            feishu_code = int(payload.get("code", 0) or 0)
            if feishu_code != 0:
                record_request_telemetry(
                    method=method,
                    path=path,
                    payload_size_bytes=payload_size_bytes,
                    attempt=attempt,
                    max_attempts=max_attempts,
                    duration_ms=duration_ms,
                    status_code=status_code,
                    kind="api_error",
                    retry_decision="not_retried",
                    will_retry=False,
                    status_unknown=False,
                    feishu_code=feishu_code,
                )
                raise RuntimeError(f"{method} {path} failed: {payload}")
            record_request_telemetry(
                method=method,
                path=path,
                payload_size_bytes=payload_size_bytes,
                attempt=attempt,
                max_attempts=max_attempts,
                duration_ms=duration_ms,
                status_code=status_code,
                kind="none",
                retry_decision="not_needed",
                will_retry=False,
                status_unknown=False,
                feishu_code=feishu_code,
            )
            return payload
        except HTTPError as exc:
            last_exc = exc
            duration_ms = int((time.monotonic() - started) * 1000)
            detail = exc.read().decode("utf-8", errors="replace")
            transient = is_transient_error(exc)
            final_attempt = attempt >= max_attempts
            will_retry = transient and retry_enabled and not final_attempt
            record_request_telemetry(
                method=method,
                path=path,
                payload_size_bytes=payload_size_bytes,
                attempt=attempt,
                max_attempts=max_attempts,
                duration_ms=duration_ms,
                status_code=exc.code,
                kind=error_kind(exc),
                retry_decision=retry_decision_for(
                    will_retry=will_retry,
                    transient=transient,
                    retry_enabled=retry_enabled,
                    final_attempt=final_attempt,
                ),
                will_retry=will_retry,
                status_unknown=transient,
            )
            if not transient:
                raise RuntimeError(f"{method} {path} failed: HTTP {exc.code} {detail}") from exc
            if not retry_enabled:
                raise retry_status_unknown_error(method, path, exc) from exc
            if final_attempt:
                raise RuntimeError(
                    f"{method} {path} failed after {attempt} attempts; status unknown: HTTP {exc.code} {detail}"
                ) from exc
            sleep_seconds = base_delay * attempt
            print(
                f"[warn] transient Feishu {method} {path} failed "
                f"(attempt {attempt}/{attempts}); retrying in {sleep_seconds:.1f}s: HTTP {exc.code}",
                file=sys.stderr,
            )
            time.sleep(sleep_seconds)
        except (TimeoutError, socket.timeout, URLError, ConnectionResetError, ssl.SSLError) as exc:
            last_exc = exc
            duration_ms = int((time.monotonic() - started) * 1000)
            final_attempt = attempt >= max_attempts
            will_retry = retry_enabled and not final_attempt
            record_request_telemetry(
                method=method,
                path=path,
                payload_size_bytes=payload_size_bytes,
                attempt=attempt,
                max_attempts=max_attempts,
                duration_ms=duration_ms,
                status_code=None,
                kind=error_kind(exc),
                retry_decision=retry_decision_for(
                    will_retry=will_retry,
                    transient=True,
                    retry_enabled=retry_enabled,
                    final_attempt=final_attempt,
                ),
                will_retry=will_retry,
                status_unknown=True,
            )
            if not retry_enabled:
                raise retry_status_unknown_error(method, path, exc) from exc
            if final_attempt:
                raise RuntimeError(
                    f"{method} {path} failed after {attempt} attempts; status unknown: {exc}"
                ) from exc
            sleep_seconds = base_delay * attempt
            print(
                f"[warn] transient Feishu {method} {path} failed "
                f"(attempt {attempt}/{attempts}); retrying in {sleep_seconds:.1f}s: {exc}",
                file=sys.stderr,
            )
            time.sleep(sleep_seconds)
    else:
        raise RuntimeError(f"{method} {path} failed after {attempts} attempts: {last_exc}")


def tenant_token() -> str:
    app_id = os.getenv("FEISHU_APP_ID")
    app_secret = os.getenv("FEISHU_APP_SECRET")
    if not app_id or not app_secret:
        die("Missing FEISHU_APP_ID or FEISHU_APP_SECRET")
    payload = request_json("POST", "/auth/v3/tenant_access_token/internal", body={
        "app_id": app_id,
        "app_secret": app_secret,
    }, retry=True)
    token = payload.get("tenant_access_token")
    if not token:
        die("Feishu did not return tenant_access_token")
    return token


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def get_or_create_base(token: str) -> str:
    existing = os.getenv("FEISHU_BASE_APP_TOKEN")
    if existing:
        return existing
    body: dict[str, Any] = {
        "name": os.getenv("FEISHU_BASE_NAME", "AI账号信息雷达 + 飞书执行台 v0.1"),
        "time_zone": "Asia/Shanghai",
    }
    folder_token = os.getenv("FEISHU_FOLDER_TOKEN")
    if folder_token:
        body["folder_token"] = folder_token
    payload = request_json("POST", "/bitable/v1/apps", token=token, body=body)
    data = payload.get("data", {})
    app = data.get("app", data)
    app_token = app.get("app_token") or data.get("app_token")
    if not app_token:
        die(f"Could not find app_token in create-base response: {payload}")
    return app_token


def list_tables(token: str, app_token: str) -> list[dict[str, Any]]:
    payload = request_json("GET", f"/bitable/v1/apps/{app_token}/tables", token=token)
    return payload.get("data", {}).get("items", [])


def delete_table(token: str, app_token: str, table_id: str) -> None:
    request_json("DELETE", f"/bitable/v1/apps/{app_token}/tables/{table_id}", token=token)


def field_type(name: str) -> int:
    # Keep the first API version deliberately conservative: text fields are the
    # most reliable across tenants. Numeric fields can be upgraded in Feishu UI
    # later, or by extending this map once the tenant confirms field-type support.
    return 1


def create_table(token: str, app_token: str, table_name: str, headers: list[str]) -> str:
    fields = [{"field_name": h, "type": field_type(h)} for h in headers[:100]]
    payload = request_json(
        "POST",
        f"/bitable/v1/apps/{app_token}/tables",
        token=token,
        body={"table": {"name": table_name, "default_view_name": "全部", "fields": fields}},
    )
    data = payload.get("data", {})
    table = data.get("table", data)
    table_id = table.get("table_id") or data.get("table_id")
    if not table_id:
        die(f"Could not find table_id in create-table response for {table_name}: {payload}")
    return table_id


def batch_create_records(token: str, app_token: str, table_id: str, rows: list[dict[str, str]]) -> int:
    total = 0
    for start in range(0, len(rows), 500):
        chunk = rows[start:start + 500]
        records = [{"fields": {k: (v if v is not None else "") for k, v in row.items()}} for row in chunk]
        request_json(
            "POST",
            f"/bitable/v1/apps/{app_token}/tables/{table_id}/records/batch_create",
            token=token,
            body={"records": records},
        )
        total += len(chunk)
        time.sleep(0.15)
    return total


def main() -> int:
    token = tenant_token()
    app_token = get_or_create_base(token)
    existing_by_name = {table["name"]: table for table in list_tables(token, app_token)}
    if os.getenv("FEISHU_REPLACE_TABLES") == "1":
        die(
            "FEISHU_REPLACE_TABLES is disabled for the current 6-table console. "
            "This prevents accidental deletion/recreation of business tables and always protects 99 规则与字典."
        )
    summary: dict[str, Any] = {"ok": True, "app_token": app_token, "tables": []}
    deprecated_existing = sorted(name for name in existing_by_name if name in DEPRECATED_TABLE_NAMES)
    if deprecated_existing:
        summary["deprecated_tables_present"] = deprecated_existing
    for table_name, filename in TABLE_FILES:
        rows = read_csv_rows(OUT / filename)
        if not rows:
            summary["tables"].append({"name": table_name, "status": "skipped_empty"})
            continue
        headers = list(rows[0].keys())
        if table_name in existing_by_name:
            summary["tables"].append({"name": table_name, "table_id": existing_by_name[table_name]["table_id"], "status": "exists_skipped"})
            continue
        table_id = create_table(token, app_token, table_name, headers)
        count = batch_create_records(token, app_token, table_id, rows)
        summary["tables"].append({"name": table_name, "table_id": table_id, "records": count})
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
