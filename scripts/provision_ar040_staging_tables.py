#!/usr/bin/env python3
"""Provision only the dedicated AR-040 staging 01/03 tables."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import content_sampler
import push_to_feishu as feishu
import sync_source_sampling


ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_ENV_FILE = ROOT.parent / "ai_account_radar" / ".env.local"
STAGING_ENV_NAME = ".env.staging.local"


class ProvisionError(RuntimeError):
    pass


@dataclass(frozen=True)
class TableSpec:
    key: str
    env_key: str
    name: str
    fields: tuple[str, ...]
    views: tuple[str, ...]
    ensure_views: Callable[[str, str, str], dict[str, Any]]


TABLE_SPECS = (
    TableSpec(
        "source_sampling", "FEISHU_SOURCE_TABLE_ID", "01 来源与采样__AR040_TEST",
        tuple(sync_source_sampling.SYNC_FIELDS), tuple(sync_source_sampling.SOURCE_VIEW_PLANS),
        sync_source_sampling.ensure_views,
    ),
    TableSpec(
        "content_inbox", "FEISHU_CONTENT_TABLE_ID", "03 内容收件箱__AR040_TEST",
        tuple(content_sampler.CONTENT_INBOX_FIELDS), ("今日采集", "最近15天", "永久保留"),
        content_sampler.ensure_content_inbox_today_view,
    ),
)


def parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def validate_staging_context(env_file: Path, environ: dict[str, str], production_env_file: Path | None = None) -> dict[str, str]:
    production_env_file = production_env_file or PRODUCTION_ENV_FILE
    if env_file.name != STAGING_ENV_NAME or not env_file.is_absolute():
        raise ProvisionError("explicit_staging_env_file_required")
    if environ.get("AI_ACCOUNT_RADAR_ENV", "").strip().lower() not in {"staging", "test"}:
        raise ProvisionError("staging_environment_required")
    if not env_file.is_file():
        raise ProvisionError("staging_env_file_missing")
    staging = parse_env_file(env_file)
    missing = [key for key in ("FEISHU_APP_ID", "FEISHU_APP_SECRET", "FEISHU_BASE_APP_TOKEN") if not staging.get(key)]
    if missing:
        raise ProvisionError("staging_core_environment_missing")
    if not production_env_file.is_file():
        raise ProvisionError("production_identity_reference_missing")
    production = parse_env_file(production_env_file)
    for key in ("FEISHU_APP_ID", "FEISHU_BASE_APP_TOKEN"):
        if not production.get(key):
            raise ProvisionError("production_identity_reference_incomplete")
        if staging[key] == production[key]:
            raise ProvisionError("staging_identity_matches_production")
    return staging


def id_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def list_tables(token: str, app_token: str) -> list[dict[str, Any]]:
    payload = feishu.request_json("GET", f"/bitable/v1/apps/{app_token}/tables", token=token)
    return list(payload.get("data", {}).get("items", []))


def list_fields(token: str, app_token: str, table_id: str) -> list[dict[str, Any]]:
    payload = feishu.request_json("GET", f"/bitable/v1/apps/{app_token}/tables/{table_id}/fields", token=token)
    return list(payload.get("data", {}).get("items", []))


def list_views(token: str, app_token: str, table_id: str) -> list[dict[str, Any]]:
    payload = feishu.request_json("GET", f"/bitable/v1/apps/{app_token}/tables/{table_id}/views", token=token)
    return list(payload.get("data", {}).get("items", []))


def validate_schema(token: str, app_token: str, table_id: str, spec: TableSpec) -> dict[str, Any]:
    fields = list_fields(token, app_token, table_id)
    by_name = {str(item.get("field_name") or ""): item for item in fields}
    missing = [name for name in spec.fields if name not in by_name]
    wrong_type = [name for name in spec.fields if name in by_name and int(by_name[name].get("type") or 0) != 1]
    if missing or wrong_type:
        raise ProvisionError(f"{spec.key}_schema_incompatible")
    views = {str(item.get("view_name") or "") for item in list_views(token, app_token, table_id)}
    return {
        "field_count": len(fields),
        "required_field_count": len(spec.fields),
        "views": sorted(views),
        "missing_views": [name for name in spec.views if name not in views],
    }


def exact_table(tables: list[dict[str, Any]], spec: TableSpec, configured_id: str) -> tuple[dict[str, Any] | None, str]:
    by_id = [item for item in tables if str(item.get("table_id") or "") == configured_id] if configured_id else []
    by_name = [item for item in tables if str(item.get("name") or "") == spec.name]
    if len(by_name) > 1 or len(by_id) > 1:
        raise ProvisionError(f"{spec.key}_table_duplicate")
    if configured_id:
        if not by_id:
            raise ProvisionError(f"{spec.key}_configured_table_missing")
        if str(by_id[0].get("name") or "") != spec.name:
            raise ProvisionError(f"{spec.key}_configured_table_name_mismatch")
        return by_id[0], "configured"
    if by_name:
        return by_name[0], "bind_existing"
    return None, "create"


def create_table_once(token: str, app_token: str, spec: TableSpec) -> str:
    try:
        payload = feishu.request_json(
            "POST", f"/bitable/v1/apps/{app_token}/tables", token=token,
            body={"table": {
                "name": spec.name,
                "default_view_name": spec.views[0],
                "fields": [{"field_name": name, "type": 1} for name in spec.fields],
            }},
        )
        data = payload.get("data", {})
        table = data.get("table", data)
        table_id = str(table.get("table_id") or data.get("table_id") or "")
        if table_id:
            return table_id
    except Exception:
        pass
    matches = [item for item in list_tables(token, app_token) if str(item.get("name") or "") == spec.name]
    if len(matches) == 1 and str(matches[0].get("table_id") or ""):
        return str(matches[0]["table_id"])
    raise ProvisionError(f"{spec.key}_create_status_unknown")


def provision_table(token: str, app_token: str, spec: TableSpec, configured_id: str, *, write: bool) -> tuple[dict[str, Any], str]:
    table, action = exact_table(list_tables(token, app_token), spec, configured_id)
    if table is None:
        if not write:
            return {"key": spec.key, "action": "would_create", "schema_writes": 0}, ""
        table_id = create_table_once(token, app_token, spec)
        action = "created"
    else:
        table_id = str(table.get("table_id") or "")
    schema = validate_schema(token, app_token, table_id, spec)
    if schema["missing_views"]:
        if not write:
            return {
                "key": spec.key, "action": "would_configure_views", "table_id_sha256": id_hash(table_id),
                "missing_view_count": len(schema["missing_views"]), "schema_writes": 0,
            }, table_id
        spec.ensure_views(token, app_token, table_id)
        schema = validate_schema(token, app_token, table_id, spec)
        if schema["missing_views"]:
            raise ProvisionError(f"{spec.key}_view_readback_mismatch")
    final_action = action if configured_id or action == "created" else "bound_existing"
    return {
        "key": spec.key, "action": final_action, "table_id_sha256": id_hash(table_id),
        "required_field_count": schema["required_field_count"], "view_count": len(spec.views),
        "schema_writes": 0 if not write or final_action in {"configured", "bound_existing"} else 1,
    }, table_id


def quote_env(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def atomic_update_env_file(path: Path, values: dict[str, str]) -> None:
    parent_stat = path.parent.lstat()
    file_stat = path.lstat()
    if not stat.S_ISDIR(parent_stat.st_mode) or path.parent.is_symlink() or parent_stat.st_uid != os.getuid() or parent_stat.st_mode & 0o022:
        raise ProvisionError("staging_env_parent_unsafe")
    if not stat.S_ISREG(file_stat.st_mode) or path.is_symlink() or file_stat.st_uid != os.getuid() or file_stat.st_nlink != 1:
        raise ProvisionError("staging_env_file_unsafe")
    raw = path.read_text(encoding="utf-8")
    lines = raw.splitlines(keepends=True)
    replaced: set[str] = set()
    output: list[str] = []
    for line in lines:
        stripped = line.strip()
        key = line.split("=", 1)[0].strip() if "=" in line and stripped and not stripped.startswith("#") else ""
        if key in values:
            newline = "\r\n" if line.endswith("\r\n") else "\n"
            output.append(f"{key}={quote_env(values[key])}{newline}")
            replaced.add(key)
        else:
            output.append(line)
    if output and not output[-1].endswith(("\n", "\r\n")):
        output[-1] += "\n"
    for key, value in values.items():
        if key not in replaced:
            output.append(f"{key}={quote_env(value)}\n")
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write("".join(output))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
        os.chmod(path, 0o600)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def run(env_file: Path, *, write: bool, environ: dict[str, str] | None = None, production_env_file: Path | None = None) -> dict[str, Any]:
    env = dict(os.environ if environ is None else environ)
    staging = validate_staging_context(env_file, env, production_env_file)
    previous = {key: os.environ.get(key) for key in staging if key.startswith("FEISHU_")}
    try:
        for key, value in staging.items():
            if key.startswith("FEISHU_"):
                os.environ[key] = value
        token = feishu.tenant_token()
        app_token = staging["FEISHU_BASE_APP_TOKEN"]
        results: list[dict[str, Any]] = []
        bindings: dict[str, str] = {}
        for spec in TABLE_SPECS:
            result, table_id = provision_table(token, app_token, spec, staging.get(spec.env_key, ""), write=write)
            results.append(result)
            if table_id:
                bindings[spec.env_key] = table_id
        if write and len(bindings) == len(TABLE_SPECS):
            atomic_update_env_file(env_file, bindings)
        return {
            "ok": True, "mode": "write" if write else "check_only", "environment": "staging",
            "resources": results, "env_keys_bound": sorted(bindings) if write else [],
            "business_record_writes": 0, "tables_managed": len(TABLE_SPECS), "secrets_exposed": False,
        }
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def main() -> int:
    parser = argparse.ArgumentParser(description="Provision dedicated AR-040 staging 01/03 tables only.")
    parser.add_argument("--env-file", required=True)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    try:
        result = run(Path(args.env_file).expanduser().absolute(), write=args.write)
    except (ProvisionError, OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "reason": str(exc), "business_record_writes": 0, "secrets_exposed": False}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
