#!/usr/bin/env python3
"""Run local Codex to generate 06 script/execution packages for pending 04 topics.

This is the unattended local path. It uses Feishu only for queue state and
write-back, then invokes `codex exec` for the actual writing step so the
generated package is LLM-authored rather than a pure template renderer.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote

from local_env import load_local_env

import push_to_feishu as feishu
from script_package_shared import (
    SCRIPT_PACKAGE_FIELDS,
    TOPIC_MARK_FIELD,
    ensure_text_fields,
    feishu_ready_topics,
    filter_topics,
    load_austin_module,
    normalize_topics,
    require_app_token,
)


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "config" / "codex_script_package_schema.json"
PROJECT_SCRIPT_PACKAGE_ROOT = ROOT.parent / "06 完整脚本与制作包"
DEFAULT_OUTPUT_ROOT = (
    PROJECT_SCRIPT_PACKAGE_ROOT
    if PROJECT_SCRIPT_PACKAGE_ROOT.exists() or PROJECT_SCRIPT_PACKAGE_ROOT.is_symlink()
    else ROOT / "output" / "script_execution_packages"
)
LOG_DIR = ROOT / "output" / "logs"
LOCK_FILE = ROOT / ".runtime" / "codex_script_package_runner.lock"
RUNNER_VERSION = "codex-local-script-package-runner-v0.1"
DEFAULT_CODEX_BIN = "/Applications/Codex.app/Contents/Resources/codex"
TEST_TITLE_PREFIXES = ("【测试】", "【流程测试】", "【部署后测试】", "【模拟测试】", "[测试]", "测试：", "测试:")
TEST_TITLE_TAG_RE = re.compile(r"^(【[^】]*(测试|测速)[^】]*】|\[[^\]]*(测试|测速)[^\]]*\])")
DOC_SYNC_MAX_PARAGRAPHS = 180


@dataclass
class FeishuDocSyncResult:
    url: str = ""
    folder_url: str = ""
    status: str = "未配置飞书文档同步"
    error: str = ""


def now_stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def date_slug() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def slugify(text: str, fallback: str = "topic") -> str:
    cleaned = re.sub(r"[\\/:*?\"<>|\s：，。！？；、（）()【】《》「」『』]+", "_", str(text).strip())
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    return cleaned[:64] or fallback


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    for index in range(2, 1000):
        candidate = path.with_name(f"{path.stem}_{index}{path.suffix}")
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"Could not find a unique output path for {path}")


def compact(value: Any, limit: int = 500) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[:limit].rstrip() + "..."


def inline_items(items: list[str], fallback: str = "无", limit: int = 6) -> str:
    clean = [str(item).strip() for item in items if str(item).strip()]
    return "；".join(clean[:limit]) if clean else fallback


def visible_script_package_folder_token() -> str:
    return os.getenv("FEISHU_SCRIPT_PACKAGE_VISIBLE_FOLDER_TOKEN", "").strip()


def legacy_script_package_folder_token() -> str:
    return os.getenv("FEISHU_SCRIPT_PACKAGE_FOLDER_TOKEN", "").strip()


def script_package_folder_token() -> str:
    return visible_script_package_folder_token() or legacy_script_package_folder_token()


def script_package_folder_url() -> str:
    return os.getenv("FEISHU_SCRIPT_PACKAGE_VISIBLE_FOLDER_URL", "").strip()


def feishu_doc_token(default_tenant_token: str) -> str:
    """Prefer user identity for user-visible Drive folders.

    Tenant tokens can create documents in app-owned space, but they often cannot
    write into a folder the user can browse in normal Feishu Drive. When a user
    access token is configured, use it only for docx creation.
    """
    return (
        os.getenv("FEISHU_SCRIPT_PACKAGE_USER_ACCESS_TOKEN", "").strip()
        or os.getenv("FEISHU_USER_ACCESS_TOKEN", "").strip()
        or default_tenant_token
    )


def feishu_docs_enabled() -> bool:
    value = os.getenv("FEISHU_SCRIPT_PACKAGE_DOCS_ENABLED", "").strip().lower()
    if value in {"0", "false", "no", "off"}:
        return False
    return bool(script_package_folder_token())


def doc_sync_preflight_status() -> FeishuDocSyncResult | None:
    if feishu_docs_enabled():
        return None
    if os.getenv("FEISHU_SCRIPT_PACKAGE_DOCS_ENABLED", "").strip().lower() in {"0", "false", "no", "off"}:
        return FeishuDocSyncResult(status="已关闭飞书文档同步")
    return FeishuDocSyncResult(
        status="未配置用户可见飞书文件夹",
        error="缺少 FEISHU_SCRIPT_PACKAGE_VISIBLE_FOLDER_TOKEN；旧 FEISHU_SCRIPT_PACKAGE_FOLDER_TOKEN 只适合作为应用空间兼容路径。",
    )


def doc_sync_success_status() -> str:
    if visible_script_package_folder_token():
        if os.getenv("FEISHU_SCRIPT_PACKAGE_USER_ACCESS_TOKEN", "").strip() or os.getenv("FEISHU_USER_ACCESS_TOKEN", "").strip():
            return "已同步到用户可见飞书文件夹"
        return "已创建飞书文档，但需确认文件夹对用户可见"
    return "已创建飞书文档：应用空间兼容路径，非正常用户文件夹入口"


def record_day(value: Any) -> date | None:
    if isinstance(value, (int, float)):
        timestamp = float(value)
        if timestamp > 10_000_000_000:
            timestamp = timestamp / 1000
        return datetime.fromtimestamp(timestamp).date()
    text = str(value or "").strip()
    match = re.search(r"\d{4}-\d{2}-\d{2}", text)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(0), "%Y-%m-%d").date()
    except ValueError:
        return None


def record_date(value: Any) -> str:
    day = record_day(value)
    return day.isoformat() if day else ""


def within_recent_days(value: Any, max_age_days: int) -> bool:
    if max_age_days <= 0:
        return True
    day = record_day(value)
    if not day:
        return False
    today = date.today().isoformat()
    oldest = date.today() - timedelta(days=max_age_days - 1)
    return oldest <= day <= date.fromisoformat(today)


def filter_recent(records: list[dict[str, Any]], topic_cards: list[dict[str, Any]], max_age_days: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    kept_records: list[dict[str, Any]] = []
    kept_topics: list[dict[str, Any]] = []
    for record, topic in zip(records, topic_cards):
        fields = record.get("fields", {})
        if within_recent_days(fields.get("推荐日期"), max_age_days):
            kept_records.append(record)
            kept_topics.append(topic)
    return kept_records, kept_topics


def is_test_topic(record: dict[str, Any], topic: dict[str, Any]) -> bool:
    fields = record.get("fields", {})
    title = str(
        topic.get("topic_title")
        or fields.get("选题标题")
        or fields.get("选题命题")
        or fields.get("一句话Brief")
        or ""
    ).strip()
    return title.startswith(TEST_TITLE_PREFIXES) or bool(TEST_TITLE_TAG_RE.match(title))


def filter_test_records(records: list[dict[str, Any]], topic_cards: list[dict[str, Any]], include_test_records: bool) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if include_test_records:
        return records, topic_cards
    kept_records: list[dict[str, Any]] = []
    kept_topics: list[dict[str, Any]] = []
    for record, topic in zip(records, topic_cards):
        if not is_test_topic(record, topic):
            kept_records.append(record)
            kept_topics.append(topic)
    return kept_records, kept_topics


def log(message: str) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    line = f"[{now_stamp()}] {message}"
    print(line, flush=True)
    with (LOG_DIR / f"codex_script_package_runner_{date_slug()}.log").open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def acquire_lock() -> Any:
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    handle = LOCK_FILE.open("w", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit("Another codex_script_package_runner is already running.")
    handle.write(str(os.getpid()))
    handle.flush()
    return handle


def codex_bin() -> str:
    return os.getenv("CODEX_BIN", DEFAULT_CODEX_BIN)


def configured_path(env_name: str, default: Path) -> Path:
    return Path(os.getenv(env_name, str(default))).expanduser()


def output_root() -> Path:
    return configured_path("SCRIPT_PACKAGE_OUTPUT_ROOT", DEFAULT_OUTPUT_ROOT)


def display_output_root() -> Path:
    return configured_path("SCRIPT_PACKAGE_DISPLAY_OUTPUT_ROOT", output_root())


def display_path_for(actual_path: Path) -> Path:
    root = output_root()
    try:
        return display_output_root() / actual_path.relative_to(root)
    except ValueError:
        return actual_path


def topic_prompt(topic: dict[str, Any]) -> str:
    return f"""你是 Austin AI账号的本地定时脚本生成器。

请使用本机全局 Skill `$austin-no-overtime-scripting` 和 `$austin-voice-scriptwriter` 的方法，基于下面 Topic Card 生成一份完整的 `06 完整脚本与制作包`。

硬性要求：
- 只生成内容，不修改代码、不提交 Git、不调用外部采集。
- 口播全文要像真人实战分享，不要像课程讲义。
- 必须保留主观判断、真实犹豫、失败或不完美结果、人工修正点、边界提醒。
- 不要强行指定用户没有提供的真实案例；可以写“建议用某类案例”，但不要写成已经发生。
- 素材、事实核验、发布前回看原文属于提醒；不要因为这些提醒就把可用稿全部判死。
- 输出必须严格符合 JSON Schema，不要输出 Markdown 代码块，不要输出解释。
- `full_markdown` 必须是一份完整 Markdown，至少包含：先看结论、核心观点、开头钩子候选、视频结构、口播全文、录屏与素材清单、剪辑交接、发布包草稿、QA 报告。

Topic Card JSON：
{json.dumps(topic, ensure_ascii=False, indent=2)}
"""


def run_codex_for_topic(topic: dict[str, Any], timeout_seconds: int) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="austin-codex-script-package-") as tmpdir:
        output_file = Path(tmpdir) / "package.json"
        command = [
            codex_bin(),
            "exec",
            "-C",
            str(ROOT),
            "--skip-git-repo-check",
            "--sandbox",
            "workspace-write",
            "--output-schema",
            str(SCHEMA),
            "--output-last-message",
            str(output_file),
            "-",
        ]
        log(f"starting codex exec for {topic.get('topic_title')}")
        result = subprocess.run(
            command,
            input=topic_prompt(topic),
            text=True,
            cwd=str(ROOT),
            capture_output=True,
            timeout=timeout_seconds,
        )
        if result.stdout.strip():
            log("codex stdout: " + compact(result.stdout, 1200))
        if result.stderr.strip():
            log("codex stderr: " + compact(result.stderr, 1200))
        if result.returncode != 0:
            raise RuntimeError(f"codex exec failed with exit code {result.returncode}")
        if not output_file.exists():
            raise RuntimeError("codex exec did not write output JSON")
        try:
            package = json.loads(output_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"codex output was not valid JSON: {exc}") from exc
        return package


def write_package_markdown(topic: dict[str, Any], package: dict[str, Any]) -> Path:
    title = str(topic.get("topic_title") or package.get("topic_title") or "未命名选题")
    root = output_root()
    root.mkdir(parents=True, exist_ok=True)
    filename = f"{date_slug()}_{slugify(title)}_完整脚本与制作包.md"
    path = unique_path(root / filename)
    path.write_text(str(package["full_markdown"]).rstrip() + "\n", encoding="utf-8")
    return display_path_for(path)


def feishu_doc_url(document_id: str) -> str:
    return f"https://my.feishu.cn/docx/{quote(document_id)}"


def markdown_blocks(markdown: str) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for raw_line in markdown.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#"):
            line = line.lstrip("#").strip()
        if line.startswith(("- [ ] ", "- [x] ", "- [X] ", "- ")):
            line = line[2:].strip()
        if line:
            blocks.append({
                "block_type": 2,
                "text": {
                    "elements": [{
                        "text_run": {
                            "content": line[:1800],
                            "text_element_style": {},
                        },
                    }],
                    "style": {},
                },
            })
        if len(blocks) >= DOC_SYNC_MAX_PARAGRAPHS:
            blocks.append({
                "block_type": 2,
                "text": {
                    "elements": [{
                        "text_run": {
                            "content": "（后续内容见本地 Markdown 备份）",
                            "text_element_style": {},
                        },
                    }],
                    "style": {},
                },
            })
            break
    return blocks


def create_feishu_document(token: str, title: str, markdown: str) -> FeishuDocSyncResult:
    preflight = doc_sync_preflight_status()
    if preflight:
        return preflight
    folder_token = script_package_folder_token()
    doc_token = feishu_doc_token(token)
    payload = feishu.request_json(
        "POST",
        "/docx/v1/documents",
        token=doc_token,
        body={"folder_token": folder_token, "title": title[:250]},
    )
    data = payload.get("data", {})
    document = data.get("document", data)
    document_id = str(document.get("document_id") or document.get("token") or data.get("document_id") or "")
    if not document_id:
        raise RuntimeError(f"Could not find document_id in create document response: {payload}")

    blocks = markdown_blocks(markdown)
    for start in range(0, len(blocks), 50):
        chunk = blocks[start:start + 50]
        feishu.request_json(
            "POST",
            f"/docx/v1/documents/{document_id}/blocks/{document_id}/children",
            token=doc_token,
            body={"children": chunk},
        )
        time.sleep(0.15)
    return FeishuDocSyncResult(
        url=feishu_doc_url(document_id),
        folder_url=script_package_folder_url(),
        status=doc_sync_success_status(),
    )


def try_create_feishu_document(token: str, title: str, package: dict[str, Any]) -> FeishuDocSyncResult:
    preflight = doc_sync_preflight_status()
    if preflight:
        return preflight
    try:
        return create_feishu_document(token, f"{date_slug()}_{title}_完整脚本与制作包", str(package["full_markdown"]))
    except Exception as exc:
        message = compact(exc, 1000)
        log("feishu document sync failed: " + message)
        return FeishuDocSyncResult(
            folder_url=script_package_folder_url(),
            status="飞书文档同步失败",
            error=message,
        )


def script_status(qa_status: str) -> str:
    if qa_status == "blocked":
        return "完整脚本包-阻塞"
    if qa_status == "revise":
        return "完整脚本包-待修订"
    return "已生成完整脚本包"


def package_row(topic: dict[str, Any], package: dict[str, Any], document_path: Path, doc_sync: FeishuDocSyncResult | None = None) -> dict[str, str]:
    qa_status = str(package.get("qa_status") or "revise")
    qa_result = str(package.get("qa_result") or "待人工确认")
    doc_sync = doc_sync or FeishuDocSyncResult()
    return {
        "关联选题": str(topic.get("topic_title") or package.get("topic_title") or ""),
        "脚本状态": script_status(qa_status),
        "推荐模板": str(package.get("recommended_template") or ""),
        "核心观点": str(package.get("core_viewpoint") or "")[:5000],
        "开头钩子": str(package.get("opening_hook") or "")[:500],
        "飞书文档": doc_sync.url,
        "飞书文件夹": doc_sync.folder_url,
        "文档同步状态": doc_sync.status,
        "文档同步错误": doc_sync.error[:1000],
        "本地文档": str(document_path),
        "素材提醒": inline_items([str(item) for item in package.get("material_reminders", [])], "无P0素材缺口"),
        "发布前核验": inline_items([str(item) for item in package.get("release_checks", [])], "无额外事实核验点"),
        "QA结果": f"{qa_status}｜{qa_result}"[:1000],
        "是否可拍": str(package.get("can_shoot") or ("是：可拍；按素材提醒和发布前核验处理" if qa_status == "pass" else "否：先人工确认")),
        "版本": RUNNER_VERSION,
    }


def create_script_package_record(token: str, app_token: str, table_id: str, row: dict[str, str]) -> str:
    payload = feishu.request_json(
        "POST",
        f"/bitable/v1/apps/{app_token}/tables/{table_id}/records",
        token=token,
        body={"fields": row},
    )
    data = payload.get("data", {})
    record = data.get("record", data)
    return str(record.get("record_id", ""))


def mark_topic_generated(token: str, app_token: str, table_id: str, record_id: str) -> None:
    feishu.request_json(
        "PUT",
        f"/bitable/v1/apps/{app_token}/tables/{table_id}/records/{record_id}",
        token=token,
        body={"fields": {TOPIC_MARK_FIELD: "是"}},
    )


def load_ready_topics(record_id: str, limit: int) -> tuple[str, str, dict[str, str], list[dict[str, Any]], list[dict[str, Any]]]:
    app_token = require_app_token()
    token = feishu.tenant_token()
    table_ids, records = feishu_ready_topics(token, app_token)
    records = filter_topics(records, record_id=record_id, limit=limit)
    austin, _skill_dir = load_austin_module()
    topic_cards = normalize_topics(records, austin)
    return token, app_token, table_ids, records, topic_cards


def main() -> int:
    load_local_env()
    parser = argparse.ArgumentParser()
    parser.add_argument("--write-feishu", action="store_true", help="Write generated packages to Feishu and mark 04 records.")
    parser.add_argument("--record-id", default="", help="Only process specific 04 record_id. Comma-separated ids are supported.")
    parser.add_argument("--limit", type=int, default=int(os.getenv("CODEX_SCRIPT_PACKAGE_LIMIT", "2")), help="Max topics per run.")
    parser.add_argument("--timeout-seconds", type=int, default=int(os.getenv("CODEX_SCRIPT_PACKAGE_TIMEOUT", "900")), help="Timeout per Codex topic.")
    parser.add_argument("--max-age-days", type=int, default=int(os.getenv("CODEX_SCRIPT_PACKAGE_MAX_AGE_DAYS", "0")), help="Only auto-process records whose 推荐日期 is within this many days. 0 means no date filter.")
    parser.add_argument("--include-test-records", action="store_true", help="Allow obvious test-titled topics to be processed.")
    parser.add_argument("--skip-codex", action="store_true", help="Only list ready topics. Useful for scheduler health checks.")
    args = parser.parse_args()

    _lock = acquire_lock()
    max_age_days = max(0, args.max_age_days)
    initial_limit = 0 if max_age_days > 0 and not args.record_id else args.limit
    token, app_token, table_ids, records, topic_cards = load_ready_topics(args.record_id, initial_limit)
    if max_age_days > 0 and not args.record_id:
        records, topic_cards = filter_recent(records, topic_cards, max_age_days)
    if not args.record_id:
        records, topic_cards = filter_test_records(records, topic_cards, args.include_test_records)
    if (max_age_days > 0 or not args.include_test_records) and not args.record_id:
        if args.limit > 0:
            records = records[:args.limit]
            topic_cards = topic_cards[:args.limit]
    log(json.dumps({
        "event": "ready_topics",
        "count": len(topic_cards),
        "write_feishu": args.write_feishu,
        "skip_codex": args.skip_codex,
        "max_age_days": max_age_days,
        "include_test_records": args.include_test_records,
        "topics": [{"record_id": record.get("record_id"), "title": topic.get("topic_title")} for record, topic in zip(records, topic_cards)],
    }, ensure_ascii=False))

    if args.skip_codex or not topic_cards:
        return 0

    if args.write_feishu:
        ensure_text_fields(token, app_token, table_ids["topic_decision"], [TOPIC_MARK_FIELD])
        ensure_text_fields(token, app_token, table_ids["script_package"], SCRIPT_PACKAGE_FIELDS)

    results: list[dict[str, Any]] = []
    for record, topic in zip(records, topic_cards):
        package = run_codex_for_topic(topic, args.timeout_seconds)
        document_path = write_package_markdown(topic, package)
        title = str(topic.get("topic_title") or package.get("topic_title") or "未命名选题")
        doc_sync = try_create_feishu_document(token, title, package) if args.write_feishu else FeishuDocSyncResult(status="未写入飞书")
        row = package_row(topic, package, document_path, doc_sync)
        created_id = ""
        if args.write_feishu:
            created_id = create_script_package_record(token, app_token, table_ids["script_package"], row)
            mark_topic_generated(token, app_token, table_ids["topic_decision"], str(record["record_id"]))
        result = {
            "record_id": record.get("record_id"),
            "topic_title": topic.get("topic_title"),
            "document_path": str(document_path),
            "feishu_document_url": doc_sync.url,
            "doc_sync_status": doc_sync.status,
            "qa_status": package.get("qa_status"),
            "created_script_package_id": created_id,
            "marked_topic": bool(args.write_feishu),
        }
        results.append(result)
        log(json.dumps({"event": "topic_done", **result}, ensure_ascii=False))
        time.sleep(0.2)

    print(json.dumps({"ok": True, "version": RUNNER_VERSION, "results": results}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
