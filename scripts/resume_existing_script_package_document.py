#!/usr/bin/env python3
"""Resume Feishu document sync for one existing 06 record without generation."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import tempfile
from pathlib import Path
from typing import Any

import push_to_feishu as feishu
from codex_script_package_runner import (
    create_feishu_document,
    format_script_package_record_fields,
    refresh_user_doc_token_if_needed,
)
from local_env import load_local_env
from script_package_shared import all_records, fields_by_name, resolve_script_package_table_ids


ROOT = Path(__file__).resolve().parents[1]
FAILED_STATUS = "飞书文档同步失败"
SUCCESS_STATUSES = {"已创建飞书文档", "已创建飞书文档并同步", "已创建飞书文档，但需确认文件夹对用户可见"}
LINK_TYPE = 15
RUN_ID_PATTERN = re.compile(r"^run_\d{8}_\d{6}$")
RECORD_ID_PATTERN = re.compile(r"^rec[A-Za-z0-9_-]+$")


class ResumeError(RuntimeError):
    pass


def text(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("text") or value.get("link") or "").strip()
    return str(value or "").strip()


def link_url(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("link") or "").strip()
    return str(value or "").strip()


def record_title(fields: dict[str, Any]) -> str:
    return text(fields.get("脚本标题") or fields.get("关联选题") or fields.get("我的选题标题") or fields.get("选题命题"))


def record_run_id(fields: dict[str, Any]) -> str:
    return text(fields.get("运行批次") or fields.get("最近参与运行批次"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_markdown(path: Path, expected_sha256: str, title: str) -> tuple[str, int]:
    if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
        raise ResumeError("expected_sha256_invalid")
    raw_path = path.expanduser().absolute()
    try:
        info = raw_path.lstat()
    except OSError as exc:
        raise ResumeError(f"markdown_unreadable:{type(exc).__name__}") from exc
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or raw_path.is_symlink():
        raise ResumeError("markdown_not_owned_regular_file")
    if info.st_size <= 0:
        raise ResumeError("markdown_empty")
    actual_sha256 = sha256_file(raw_path)
    if actual_sha256 != expected_sha256:
        raise ResumeError("markdown_sha256_mismatch")
    sample = raw_path.read_text(encoding="utf-8")
    if title not in sample:
        raise ResumeError("markdown_topic_title_mismatch")
    return actual_sha256, info.st_size


def require_metadata(metadata: dict[str, dict[str, Any]], needs_folder: bool) -> None:
    document_link = metadata.get("飞书文档链接")
    if not document_link or int(document_link.get("type") or 0) != LINK_TYPE:
        raise ResumeError("document_link_metadata_invalid")
    if needs_folder:
        folder_link = metadata.get("飞书文件夹链接")
        if not folder_link or int(folder_link.get("type") or 0) != LINK_TYPE:
            raise ResumeError("folder_link_metadata_invalid")


def find_exact(records: list[dict[str, Any]], record_id: str, kind: str) -> dict[str, Any]:
    matches = [record for record in records if str(record.get("record_id") or "") == record_id]
    if len(matches) != 1:
        raise ResumeError(f"{kind}_record_count_invalid:{len(matches)}")
    return matches[0]


def recovery_state_path(root: Path, script_record_id: str) -> Path:
    return root / f"{script_record_id}.json"


def write_state(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def load_state(path: Path, expected: dict[str, str]) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        info = path.lstat()
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ResumeError("recovery_state_invalid") from exc
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or path.is_symlink() or not isinstance(payload, dict):
        raise ResumeError("recovery_state_invalid")
    for key, value in expected.items():
        if payload.get(key) != value:
            raise ResumeError(f"recovery_state_{key}_mismatch")
    url = payload.get("document_url")
    if not isinstance(url, str) or not url.startswith("https://"):
        raise ResumeError("recovery_state_document_url_invalid")
    return payload


def validate_records(
    source_record_id: str,
    script_record_id: str,
    source_records: list[dict[str, Any]],
    script_records: list[dict[str, Any]],
    metadata: dict[str, dict[str, Any]],
    markdown_path: Path,
    expected_sha256: str,
) -> dict[str, Any]:
    if not RECORD_ID_PATTERN.fullmatch(source_record_id) or not RECORD_ID_PATTERN.fullmatch(script_record_id):
        raise ResumeError("record_id_invalid")
    source = find_exact(source_records, source_record_id, "source_04")
    script = find_exact(script_records, script_record_id, "script_06")
    source_fields = source.get("fields") if isinstance(source.get("fields"), dict) else {}
    script_fields = script.get("fields") if isinstance(script.get("fields"), dict) else {}
    title = record_title(source_fields)
    script_title = record_title(script_fields)
    run_id = record_run_id(source_fields)
    if not title or title != script_title:
        raise ResumeError("source_script_title_mismatch")
    if not RUN_ID_PATTERN.fullmatch(run_id):
        raise ResumeError("source_run_id_invalid")
    same_title = [record for record in script_records if record_title(record.get("fields") or {}) == title]
    if len(same_title) != 1 or str(same_title[0].get("record_id") or "") != script_record_id:
        raise ResumeError(f"script_06_topic_duplicate:{len(same_title)}")
    if text(source_fields.get("是否已生成脚本稿")) in {"", "否", "未生成"}:
        raise ResumeError("source_04_not_already_generated")
    status = text(script_fields.get("文档同步状态"))
    document_url = link_url(script_fields.get("飞书文档链接")) or text(script_fields.get("飞书文档"))
    folder_url = link_url(script_fields.get("飞书文件夹链接")) or text(script_fields.get("飞书文件夹"))
    if status in SUCCESS_STATUSES and document_url:
        mode = "already_synchronized"
    elif status == FAILED_STATUS and not document_url:
        mode = "resume_required"
    else:
        raise ResumeError("script_06_document_state_conflict")
    require_metadata(metadata, bool(folder_url))
    actual_sha256, size = validate_markdown(markdown_path, expected_sha256, title)
    return {
        "source_04_record_id": source_record_id,
        "existing_06_record_id": script_record_id,
        "run_id": run_id,
        "title": title,
        "markdown_path": str(markdown_path.expanduser().absolute()),
        "markdown_sha256": actual_sha256,
        "markdown_size": size,
        "folder_url": folder_url,
        "document_url": document_url,
        "mode": mode,
    }


def oauth_user_token() -> str:
    token = refresh_user_doc_token_if_needed()
    if not token:
        raise ResumeError("user_oauth_missing")
    payload = feishu.request_json("GET", "/authen/v1/user_info", token=token)
    data = payload.get("data")
    if not isinstance(data, dict) or not (data.get("open_id") or data.get("user_id")):
        raise ResumeError("user_oauth_validation_failed")
    return token


def read_script_record(token: str, app_token: str, table_id: str, record_id: str) -> dict[str, Any]:
    payload = feishu.request_json(
        "GET", f"/bitable/v1/apps/{app_token}/tables/{table_id}/records/{record_id}", token=token
    )
    data = payload.get("data", {})
    return data.get("record", data)


def record_has_document(record: dict[str, Any], expected_url: str) -> bool:
    fields = record.get("fields") if isinstance(record.get("fields"), dict) else {}
    current = link_url(fields.get("飞书文档链接")) or text(fields.get("飞书文档"))
    return current == expected_url and text(fields.get("文档同步状态")) in SUCCESS_STATUSES


def update_existing_record(
    tenant_token: str,
    app_token: str,
    table_id: str,
    record_id: str,
    fields: dict[str, Any],
    expected_url: str,
) -> int:
    path = f"/bitable/v1/apps/{app_token}/tables/{table_id}/records/{record_id}"
    attempts = 0
    for _ in range(2):
        attempts += 1
        try:
            feishu.request_json("PUT", path, token=tenant_token, body={"fields": fields})
            return attempts
        except Exception:
            if record_has_document(read_script_record(tenant_token, app_token, table_id, record_id), expected_url):
                return attempts
    raise ResumeError("existing_06_update_failed")


def run(args: argparse.Namespace) -> dict[str, Any]:
    load_local_env()
    tenant_token = feishu.tenant_token()
    app_token = os.getenv("FEISHU_BASE_APP_TOKEN", "").strip()
    if not app_token:
        raise ResumeError("feishu_app_token_missing")
    table_ids = resolve_script_package_table_ids(tenant_token, app_token)
    source_records = all_records(tenant_token, app_token, table_ids["topic_decision"])
    script_records = all_records(tenant_token, app_token, table_ids["script_package"])
    metadata = fields_by_name(tenant_token, app_token, table_ids["script_package"])
    identity = validate_records(
        args.source_04_record_id,
        args.existing_06_record_id,
        source_records,
        script_records,
        metadata,
        Path(args.markdown_path),
        args.expected_sha256,
    )
    base = {
        "ok": True,
        **identity,
        "codex_calls": 0,
        "script_06_create_calls": 0,
        "source_04_update_calls": 0,
        "queue_actions": 0,
    }
    if args.check_only:
        return {**base, "check_only": True, "would_create_document": identity["mode"] == "resume_required", "would_update_existing_06": identity["mode"] == "resume_required", "business_writes": 0}
    if identity["mode"] == "already_synchronized":
        return {**base, "check_only": False, "document_creates": 0, "existing_06_updates": 0, "business_writes": 0, "idempotent": True}

    oauth_user_token()
    state_root = Path(os.getenv("SCRIPT_PACKAGE_DOC_RESUME_STATE_ROOT", str(ROOT / "output" / "script_package_doc_resume"))).expanduser()
    state_path = recovery_state_path(state_root, args.existing_06_record_id)
    expected_state = {
        "source_04_record_id": args.source_04_record_id,
        "existing_06_record_id": args.existing_06_record_id,
        "markdown_path": identity["markdown_path"],
        "markdown_sha256": identity["markdown_sha256"],
        "run_id": identity["run_id"],
    }
    state = load_state(state_path, expected_state)
    document_creates = 0
    if state:
        document_url = str(state["document_url"])
    else:
        markdown = Path(identity["markdown_path"]).read_text(encoding="utf-8")
        result = create_feishu_document(tenant_token, f"{identity['title']}_完整脚本与制作包", markdown)
        if not result.url or result.status == FAILED_STATUS:
            raise ResumeError("document_create_failed")
        document_url = result.url
        document_creates = 1
        write_state(state_path, {**expected_state, "document_url": document_url})

    update_fields = format_script_package_record_fields(
        {
            "飞书文档": document_url,
            "飞书文件夹": identity["folder_url"],
            "文档同步状态": "已创建飞书文档并同步",
            "文档同步错误": "",
        },
        metadata,
    )
    update_attempts = update_existing_record(
        tenant_token, app_token, table_ids["script_package"], args.existing_06_record_id, update_fields, document_url
    )
    read_back = read_script_record(tenant_token, app_token, table_ids["script_package"], args.existing_06_record_id)
    if not record_has_document(read_back, document_url):
        raise ResumeError("existing_06_readback_mismatch")
    state_path.unlink(missing_ok=True)
    return {**base, "check_only": False, "document_url": document_url, "document_creates": document_creates, "existing_06_updates": 1, "update_attempts": update_attempts, "business_writes": document_creates + 1, "idempotent": False}


def main() -> int:
    parser = argparse.ArgumentParser(description="Resume document sync for one existing Feishu 06 record.")
    parser.add_argument("--existing-06-record-id", required=True)
    parser.add_argument("--source-04-record-id", required=True)
    parser.add_argument("--markdown-path", required=True)
    parser.add_argument("--expected-sha256", required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check-only", action="store_true")
    mode.add_argument("--write", action="store_true")
    args = parser.parse_args()
    try:
        result = run(args)
    except Exception as exc:
        result = {"ok": False, "error": str(exc), "error_type": type(exc).__name__, "codex_calls": 0, "script_06_create_calls": 0, "source_04_update_calls": 0, "queue_actions": 0}
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 4
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
