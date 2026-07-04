#!/usr/bin/env python3
"""Run local Codex to generate 06 script/execution packages for pending 04 topics.

This is the unattended local path. It uses Feishu only for queue state and
write-back, then invokes `codex exec` for the actual writing step so the
generated package is LLM-authored rather than a pure template renderer.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
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
from feishu_user_oauth_store import sync_user_tokens

import push_to_feishu as feishu
from script_package_shared import (
    SCRIPT_PACKAGE_FIELDS,
    TOPIC_MARK_FIELD,
    ensure_text_fields,
    feishu_ready_topics,
    fields_by_name,
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
DEFAULT_SCRIPT_PACKAGE_SKILL_NAME = "austin-no-overtime-scripting"
DEFAULT_SCRIPT_PACKAGE_VOICE_SKILL_NAME = "austin-voice-scriptwriter"
LOG_DIR = ROOT / "output" / "logs"
LOCK_FILE = ROOT / ".runtime" / "codex_script_package_runner.lock"
RUNNER_VERSION = "codex-local-script-package-runner-v0.2"
MAX_REVISE_ATTEMPTS = 2
BITABLE_TEXT_FIELD_TYPE = 1
BITABLE_URL_FIELD_TYPE = 15
CLICKABLE_LINK_FIELDS = {
    "飞书文档": {"label": "打开飞书文档", "mirror_field": "飞书文档链接"},
    "飞书文件夹": {"label": "打开飞书文件夹", "mirror_field": "飞书文件夹链接"},
}
RETRY_QA_PATTERNS = (
    "需要重写",
    "必须重写",
    "请重写",
    "重写口播",
    "结构不可用",
    "脚本不可用",
    "缺少关键输入",
    "缺关键输入",
    "事实无法成立",
    "内部状态边界进入",
    "进入用户可见",
)
USER_VISIBLE_RETRY_SECTIONS = (
    "开头钩子候选",
    "视频结构",
    "口播全文",
    "分段执行方案",
    "录屏与素材清单",
    "剪辑交接",
    "发布包草稿",
)
USER_VISIBLE_BOUNDARY_PATTERNS = (
    "如果当天还没生成06",
    "如果当天没有生成 06",
    "如果当天没有生成06",
    "如果今天没有完整生成到最后一步",
    "没有完整生成到最后一步",
    "选题系统复盘",
    "沉淀资产",
)
DEFAULT_CODEX_BIN = "/Applications/Codex.app/Contents/Resources/codex"
TEST_TITLE_PREFIXES = ("【测试】", "【流程测试】", "【部署后测试】", "【模拟测试】", "[测试]", "测试：", "测试:")
TEST_TITLE_TAG_RE = re.compile(r"^(【[^】]*(测试|测速)[^】]*】|\[[^\]]*(测试|测速)[^\]]*\])")
DOC_SYNC_MAX_PARAGRAPHS = 180
USER_TOKEN_REFRESH_SAFETY_SECONDS = 300
SCRIPT_PACKAGE_QUALITY_ACTION = "submit_script_package_quality_feedback"
SCRIPT_PACKAGE_QUALITY_OPTIONS = ["直接可拍", "小修可拍", "需要重写", "暂不采用"]
SCRIPT_PACKAGE_ISSUE_OPTIONS = [
    "不像我",
    "太 AI 味",
    "太泛",
    "旧流程痛点不准",
    "AI 介入点不清",
    "结构散",
    "标题弱",
    "证据不可拍",
    "口播不顺",
    "太长",
    "过度承诺",
    "需要补真实案例",
]


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


def parse_card_targets() -> list[tuple[str, str]]:
    raw = os.getenv("FEISHU_SCRIPT_PACKAGE_FEEDBACK_RECEIVE_TARGETS", "").strip()
    targets: list[tuple[str, str]] = []
    for part in raw.split(","):
        item = part.strip()
        if not item or ":" not in item:
            continue
        receive_id_type, receive_id = item.split(":", 1)
        receive_id_type = receive_id_type.strip()
        receive_id = receive_id.strip()
        if receive_id_type and receive_id:
            targets.append((receive_id_type, receive_id))
    return targets


def card_uuid(prefix: str, *parts: str) -> str:
    seed = "|".join(str(part) for part in parts if str(part))
    digest = hashlib.sha1((seed or prefix).encode("utf-8")).hexdigest()[:16]
    return f"{prefix}-{digest}"[:50]


def card_option(value: str) -> dict[str, Any]:
    return {"text": {"tag": "plain_text", "content": value}, "value": value}


def quality_select(record_id: str) -> dict[str, Any]:
    return {
        "tag": "select_static",
        "name": f"script_quality__{record_id}",
        "required": False,
        "width": "fill",
        "placeholder": {"tag": "plain_text", "content": "选择质量结论"},
        "options": [card_option(value) for value in SCRIPT_PACKAGE_QUALITY_OPTIONS],
    }


def issue_select(record_id: str) -> dict[str, Any]:
    return {
        "tag": "multi_select_static",
        "name": f"script_issues__{record_id}",
        "required": False,
        "width": "fill",
        "placeholder": {"tag": "plain_text", "content": "选择主要问题，可多选"},
        "options": [card_option(value) for value in SCRIPT_PACKAGE_ISSUE_OPTIONS],
    }


def feedback_inputs(record_id: str) -> list[dict[str, Any]]:
    placeholders = [
        "修改意见 1：哪里不像、哪段要改",
        "修改意见 2：应该补哪个真实场景或证据",
        "修改意见 3：其他边界、标题或口播问题",
    ]
    names = [f"script_note__{record_id}", f"script_note__{record_id}__2", f"script_note__{record_id}__3"]
    return [
        {
            "tag": "input",
            "name": name,
            "required": False,
            "width": "fill",
            "placeholder": {"tag": "plain_text", "content": placeholder},
            "default_value": "",
        }
        for name, placeholder in zip(names, placeholders)
    ]


def env_int(name: str) -> int:
    try:
        return int(float(os.getenv(name, "0").strip() or "0"))
    except ValueError:
        return 0


def user_refresh_token() -> str:
    return (
        os.getenv("FEISHU_SCRIPT_PACKAGE_USER_REFRESH_TOKEN", "").strip()
        or os.getenv("FEISHU_USER_REFRESH_TOKEN", "").strip()
    )


def exchange_user_refresh_token(refresh_token: str) -> dict[str, Any]:
    app_id = os.getenv("FEISHU_APP_ID", "").strip()
    app_secret = os.getenv("FEISHU_APP_SECRET", "").strip()
    if not app_id or not app_secret:
        raise RuntimeError("Missing FEISHU_APP_ID or FEISHU_APP_SECRET for user token refresh")
    payload = feishu.request_json(
        "POST",
        "/authen/v2/oauth/token",
        body={
            "grant_type": "refresh_token",
            "client_id": app_id,
            "client_secret": app_secret,
            "refresh_token": refresh_token,
        },
    )
    return payload.get("data", payload)


def refresh_user_doc_token_if_needed() -> str:
    access_token = (
        os.getenv("FEISHU_SCRIPT_PACKAGE_USER_ACCESS_TOKEN", "").strip()
        or os.getenv("FEISHU_USER_ACCESS_TOKEN", "").strip()
    )
    refresh_token = user_refresh_token()
    expires_at = max(
        env_int("FEISHU_SCRIPT_PACKAGE_USER_ACCESS_TOKEN_EXPIRES_AT"),
        env_int("FEISHU_USER_ACCESS_TOKEN_EXPIRES_AT"),
    )
    if access_token and (not expires_at or time.time() < expires_at - USER_TOKEN_REFRESH_SAFETY_SECONDS):
        return access_token
    if not refresh_token:
        return access_token

    data = exchange_user_refresh_token(refresh_token)
    new_access_token = str(data.get("access_token") or data.get("user_access_token") or "").strip()
    new_refresh_token = str(data.get("refresh_token") or refresh_token).strip()
    expires_in = int(data.get("expires_in") or data.get("access_token_expires_in") or 0)
    refresh_expires_in = int(data.get("refresh_expires_in") or data.get("refresh_token_expires_in") or 0)
    if not new_access_token:
        raise RuntimeError(f"Feishu OAuth refresh did not return user access token: {payload_public_keys(data)}")
    now = int(time.time())
    values = {
        "FEISHU_SCRIPT_PACKAGE_USER_ACCESS_TOKEN": new_access_token,
        "FEISHU_SCRIPT_PACKAGE_USER_REFRESH_TOKEN": new_refresh_token,
    }
    if expires_in:
        values["FEISHU_SCRIPT_PACKAGE_USER_ACCESS_TOKEN_EXPIRES_AT"] = str(now + expires_in)
    if refresh_expires_in:
        values["FEISHU_SCRIPT_PACKAGE_USER_REFRESH_TOKEN_EXPIRES_AT"] = str(now + refresh_expires_in)
    sync_user_tokens(values)
    os.environ.update(values)
    return new_access_token


def payload_public_keys(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: "<set>" if "token" in key.lower() else value for key, value in payload.items()}


def feishu_doc_token(default_tenant_token: str) -> str:
    """Prefer user identity for user-visible Drive folders.

    Tenant tokens can create documents in app-owned space, but they often cannot
    write into a folder the user can browse in normal Feishu Drive. When a user
    access token is configured, use it only for docx creation.
    """
    return refresh_user_doc_token_if_needed() or default_tenant_token


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
        if (
            os.getenv("FEISHU_SCRIPT_PACKAGE_USER_ACCESS_TOKEN", "").strip()
            or os.getenv("FEISHU_USER_ACCESS_TOKEN", "").strip()
            or user_refresh_token()
        ):
            return "已同步到用户可见飞书文件夹"
        return "已创建飞书文档，但需确认文件夹对用户可见"
    return "已创建飞书文档：应用空间兼容路径，非正常用户文件夹入口"


def is_user_oauth_error(message: str) -> bool:
    lowered = message.lower()
    needles = (
        "invalid_grant",
        "refresh token",
        "token has been revoked",
        "offline_access",
        "user access token",
    )
    return any(needle in lowered for needle in needles)


def notify_doc_sync_oauth_failure(title: str, message: str) -> None:
    try:
        from feishu_automation_notify import notify

        body = (
            "06 飞书文档同步失败，但本地 Markdown 和 06 记录会继续保留。\n"
            f"选题：{title}\n"
            f"原因：{compact(message, 600)}\n"
            "处理：重新运行 `python3 scripts/feishu_user_oauth.py` 授权用户身份，"
            "再运行 `python3 scripts/install_script_package_watcher_launch_agent.py --sync-runtime-only` 同步 runtime。"
        )
        notify("【AI账号信息雷达】飞书文档同步授权失效", body)
    except Exception as exc:
        log("feishu oauth failure notification failed: " + compact(exc, 500))


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


def script_package_skill_name() -> str:
    return os.getenv("SCRIPT_PACKAGE_SKILL_NAME", DEFAULT_SCRIPT_PACKAGE_SKILL_NAME).strip() or DEFAULT_SCRIPT_PACKAGE_SKILL_NAME


def script_package_voice_skill_name() -> str:
    return os.getenv("SCRIPT_PACKAGE_VOICE_SKILL_NAME", DEFAULT_SCRIPT_PACKAGE_VOICE_SKILL_NAME).strip() or DEFAULT_SCRIPT_PACKAGE_VOICE_SKILL_NAME


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


def qa_status_of(package: dict[str, Any]) -> str:
    status = str(package.get("qa_status") or "revise").strip().lower()
    return status if status in {"pass", "revise", "blocked"} else "revise"


def heading_text(line: str) -> tuple[int, str] | None:
    match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line.strip())
    if not match:
        return None
    return len(match.group(1)), match.group(2).strip()


def markdown_named_sections(markdown: str, section_names: tuple[str, ...]) -> dict[str, str]:
    sections: dict[str, list[str]] = {}
    active_name = ""
    active_level = 0
    for line in markdown.splitlines():
        heading = heading_text(line)
        if heading:
            level, name = heading
            clean_name = name.strip("：: ")
            if active_name and level <= active_level:
                active_name = ""
                active_level = 0
            for section_name in section_names:
                if clean_name == section_name or clean_name.startswith(section_name):
                    active_name = section_name
                    active_level = level
                    sections.setdefault(section_name, [])
                    break
            else:
                if active_name and level <= active_level:
                    active_name = ""
                    active_level = 0
            continue
        if active_name:
            sections.setdefault(active_name, []).append(line)
    return {name: "\n".join(lines) for name, lines in sections.items()}


def visible_boundary_issues(markdown: str) -> list[str]:
    issues: list[str] = []
    sections = markdown_named_sections(markdown, USER_VISIBLE_RETRY_SECTIONS)
    for section_name, body in sections.items():
        for pattern in USER_VISIBLE_BOUNDARY_PATTERNS:
            if pattern in body:
                issues.append(f"{section_name}:{pattern}")
    return issues


def should_retry_package(package: dict[str, Any]) -> tuple[bool, str]:
    status = qa_status_of(package)
    if status != "revise":
        return False, f"qa_status={status}"
    qa_result = str(package.get("qa_result") or "")
    for pattern in RETRY_QA_PATTERNS:
        if pattern in qa_result:
            return True, f"qa_result:{pattern}"
    boundary_issues = visible_boundary_issues(str(package.get("full_markdown") or ""))
    if boundary_issues:
        return True, "visible_boundary:" + ",".join(boundary_issues[:3])
    return False, "revise_waiting_external_qa"


def topic_prompt(topic: dict[str, Any], previous_package: dict[str, Any] | None = None, attempt: int = 1) -> str:
    script_skill = script_package_skill_name()
    voice_skill = script_package_voice_skill_name()
    retry_block = ""
    if previous_package:
        retry_block = f"""

这是第 {attempt} 轮生成。上一轮 QA 没有通过，必须针对下面问题重写，不要只是换同义词：
- 上一轮 QA 状态：{qa_status_of(previous_package)}
- 上一轮 QA 原因：{compact(previous_package.get("qa_result"), 1200)}
- 上一轮开头钩子：{compact(previous_package.get("opening_hook"), 500)}
- 上一轮核心观点：{compact(previous_package.get("core_viewpoint"), 1200)}

第二轮硬性修正要求：
- 如果上一轮是 `revise`，优先重写口播全文、开头钩子和关键判断，让它更像 Austin 的真人实战分享。
- 不要把普通素材提醒、发布前核验当成 `revise` 原因。
- 只有缺少关键输入、事实无法成立、或脚本结构本身不可用时，才输出 `revise` 或 `blocked`。
"""
    return f"""你是 Austin AI账号的本地定时脚本生成器。

请使用本机全局 Skill `${script_skill}` 和 `${voice_skill}` 的方法，基于下面 Topic Card 生成一份完整的 `06 完整脚本与制作包`。

硬性要求：
- 只生成内容，不修改代码、不提交 Git、不调用外部采集。
- 口播全文要像真人实战分享，不要像课程讲义。
- 必须保留主观判断、真实犹豫、失败或不完美结果、人工修正点、边界提醒。
- 不要强行指定用户没有提供的真实案例；可以写“建议用某类案例”，但不要写成已经发生。
- 保护现有 Austin 口播风格基线：先真实痛点、旧流程、新动作、人工判断和边界，不要另起一套新风格体系。
- 生成前要围绕当前选题做 2-4 个当前/同类信息检索：同类内容怎么开场、怎么解释、制造什么冲突、哪些产品事实需要核验。检索不到或无法联网时，必须在 `full_markdown` 里说明失败原因和待人工补的来源，不得编造来源、视频内容或产品能力。
- 对标/同类内容只作为素材：必须写出“搜索来源摘要、表达模式拆解、保留什么、丢弃什么、如何融合进 Austin 账号风格”，不要照搬对标表达。
- 遇到知识库、RAG、Agent、TTS、Voice Agent、工作流系统、AI 工具等概念/工具型选题，先做生成前判断：用户原来怎么解决、旧方式卡在哪里、为什么现在需要它、用人话怎么解释、当前工具/热点只是哪个落地案例、最后怎么回到用户自己的工作流和证据。
- 上一条只作为素材组织方式，不是口播模板；不要写成固定段落顺序、固定开头、固定句式或逐条念出来的清单，也不要把用户举例写成固定规则。
- `口播全文` 和 `分段执行方案` 不能套统一六段、统一“三个动作”或固定章节名；段落标题和推进方式要跟随当前选题的真实旧流程、卡点、证据和拍摄现场变化。
- 仓库 deterministic fallback 只用于格式、安全和字段兜底，不代表最终 Austin 风格质量验收；真实内容质量以本机测试 Skill / 私有 Skill 生成结果和人工样例为准。
- 内部状态边界只能留在 `发布前核验`、`QA 风险与防错` 或 `发布前提醒`：例如“如果当天/今天没有生成 06”“没有完整生成到最后一步”“选题系统复盘”。这些句子不得进入开头钩子、拍摄前待办、视频结构、口播全文、分段执行方案、录屏与素材清单、剪辑交接、发布包草稿。
- `沉淀资产` 是内部抽象词，不得出现在用户可见创作内容中，包括开头钩子、标题/封面、简介、置顶评论、口播、素材清单、分段方案和 QA 通过原因。改成人话：有没有留下来、下次还能不能用、路径有没有串起来、后面能不能复用、资料有没有变成下次能用的东西。
- 当前仍是测试/返修阶段，`qa_status` 不要自评为 `pass`；应标为草稿、待 PM 验收、待 QA，不能写“可进入拍摄准备”。
- `qa_status=revise` 可以表示待 PM/QA 人工验收，不等于自动重试；只有 `qa_result` 明确要求重写，或用户可见内容混入内部边界时，runner 才会再生成。
- 素材、事实核验、发布前回看原文属于提醒；不要因为这些提醒就把可用稿全部判死。
- 输出必须严格符合 JSON Schema，不要输出 Markdown 代码块，不要输出解释。
- `full_markdown` 必须是一份完整 Markdown，至少包含：先看结论、核心观点、开头钩子候选、视频结构、搜索与表达融合、口播全文、录屏与素材清单、剪辑交接、发布包草稿、QA 报告。
{retry_block}

Topic Card JSON：
{json.dumps(topic, ensure_ascii=False, indent=2)}
"""


def run_codex_for_topic(topic: dict[str, Any], timeout_seconds: int, previous_package: dict[str, Any] | None = None, attempt: int = 1) -> dict[str, Any]:
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
        log(f"starting codex exec attempt {attempt} for {topic.get('topic_title')}")
        result = subprocess.run(
            command,
            input=topic_prompt(topic, previous_package=previous_package, attempt=attempt),
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


def generate_package_with_retry(topic: dict[str, Any], timeout_seconds: int) -> tuple[dict[str, Any], int, list[dict[str, str]]]:
    attempts: list[dict[str, str]] = []
    previous_package: dict[str, Any] | None = None
    for attempt in range(1, MAX_REVISE_ATTEMPTS + 1):
        try:
            package = run_codex_for_topic(topic, timeout_seconds, previous_package=previous_package, attempt=attempt)
        except Exception as exc:
            retry_error = compact(exc, 1000)
            attempts.append({
                "attempt": str(attempt),
                "qa_status": "error",
                "qa_result": retry_error,
                "retry": str(attempt < MAX_REVISE_ATTEMPTS).lower(),
                "retry_reason": "codex_exec_error",
            })
            if attempt < MAX_REVISE_ATTEMPTS:
                log(json.dumps({
                    "event": "codex_exec_retry",
                    "topic_title": topic.get("topic_title"),
                    "attempt": attempt,
                    "error": retry_error,
                }, ensure_ascii=False))
                previous_package = {
                    "qa_status": "revise",
                    "qa_result": f"上一轮 codex exec 失败，需要重试：{retry_error}",
                }
                continue
            raise
        status = qa_status_of(package)
        should_retry, retry_reason = should_retry_package(package)
        will_retry = should_retry and attempt < MAX_REVISE_ATTEMPTS
        history_retry_reason = retry_reason if will_retry or not should_retry else f"max_attempts_reached:{retry_reason}"
        attempts.append({
            "attempt": str(attempt),
            "qa_status": status,
            "qa_result": compact(package.get("qa_result"), 1000),
            "retry": str(will_retry).lower(),
            "retry_reason": history_retry_reason,
        })
        if not should_retry:
            return package, attempt, attempts
        if will_retry:
            log(json.dumps({
                "event": "qa_revise_retry",
                "topic_title": topic.get("topic_title"),
                "attempt": attempt,
                "retry_reason": retry_reason,
                "qa_result": package.get("qa_result"),
            }, ensure_ascii=False))
            previous_package = package
            continue
        return package, attempt, attempts
    raise RuntimeError("unreachable retry state")


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
        if is_user_oauth_error(message):
            notify_doc_sync_oauth_failure(title, message)
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


def package_row(topic: dict[str, Any], package: dict[str, Any], document_path: Path, doc_sync: FeishuDocSyncResult | None = None, attempts: int = 1) -> dict[str, Any]:
    qa_status = qa_status_of(package)
    qa_result = str(package.get("qa_result") or "待人工确认")
    doc_sync = doc_sync or FeishuDocSyncResult()
    title = str(topic.get("topic_title") or package.get("topic_title") or "")
    return {
        "脚本标题": title,
        "关联选题": title,
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
        "QA结果": f"{qa_status}｜生成轮次:{attempts}｜{qa_result}"[:1000],
        "是否可拍": str(package.get("can_shoot") or ("是：可拍；按素材提醒和发布前核验处理" if qa_status == "pass" else "否：先人工确认")),
        "版本": RUNNER_VERSION,
    }


def clickable_link_value(url: str, label: str, field_type: Any) -> Any:
    clean_url = str(url or "").strip()
    if not clean_url:
        return ""
    try:
        normalized_type = int(field_type or 0)
    except (TypeError, ValueError):
        normalized_type = 0
    if normalized_type == BITABLE_URL_FIELD_TYPE:
        return {"link": clean_url, "text": label}
    return clean_url


def format_script_package_record_fields(row: dict[str, Any], field_meta: dict[str, dict[str, Any]]) -> dict[str, Any]:
    fields = dict(row)
    for field_name, spec in CLICKABLE_LINK_FIELDS.items():
        label = spec["label"]
        value = str(fields.get(field_name) or "").strip()
        fields[field_name] = clickable_link_value(value, label, field_meta.get(field_name, {}).get("type"))
        mirror_field = spec["mirror_field"]
        if mirror_field in field_meta:
            fields[mirror_field] = clickable_link_value(value, label, field_meta.get(mirror_field, {}).get("type"))
    return fields


def create_script_package_record(token: str, app_token: str, table_id: str, row: dict[str, Any]) -> str:
    payload_fields = format_script_package_record_fields(row, fields_by_name(token, app_token, table_id))
    payload = feishu.request_json(
        "POST",
        f"/bitable/v1/apps/{app_token}/tables/{table_id}/records",
        token=token,
        body={"fields": payload_fields},
    )
    data = payload.get("data", {})
    record = data.get("record", data)
    return str(record.get("record_id", ""))


def result_link_markdown(result: dict[str, Any]) -> str:
    url = str(result.get("feishu_document_url") or "").strip()
    if url:
        return f"[打开飞书文档]({url})"
    return "飞书文档未生成"


def build_completion_card(results: list[dict[str, Any]]) -> dict[str, Any]:
    feedback_results = [
        item for item in results
        if str(item.get("created_script_package_id") or "").strip()
        and str(item.get("feishu_document_url") or "").strip()
    ]
    issued_at = datetime.utcnow().replace(microsecond=0)
    expires_at = issued_at + timedelta(days=7)
    record_ids = [str(item["created_script_package_id"]) for item in feedback_results]
    elements: list[dict[str, Any]] = [
        {
            "tag": "markdown",
            "content": (
                f"本轮已生成 {len(feedback_results)} 份 `06 完整脚本与制作包`。"
                "所有交付文档集中在这张卡里；可以先打开文档阅读，再在下方留下质量反馈。"
            ),
        }
    ]
    form_elements: list[dict[str, Any]] = []
    for index, result in enumerate(feedback_results, start=1):
        record_id = str(result["created_script_package_id"])
        title = compact(result.get("topic_title"), 60) or f"脚本包 {index}"
        core = compact(result.get("core_viewpoint"), 140)
        qa_status = compact(result.get("qa_status"), 16)
        can_shoot = compact(result.get("can_shoot"), 80)
        lines = [f"**{index}. {title}**", result_link_markdown(result)]
        meta = "｜".join(part for part in [f"QA：{qa_status}" if qa_status else "", f"可拍：{can_shoot}" if can_shoot else ""] if part)
        if meta:
            lines.append(meta)
        if core:
            lines.append(f"核心观点：{core}")
        form_elements.extend([
            {"tag": "markdown", "content": "\n".join(lines)},
            quality_select(record_id),
            issue_select(record_id),
        ])
        form_elements.extend(feedback_inputs(record_id))
        if index != len(feedback_results):
            form_elements.append({"tag": "hr"})

    form_elements.append({
        "tag": "column_set",
        "columns": [
            {
                "tag": "column",
                "width": "auto",
                "elements": [
                    {
                        "tag": "button",
                        "type": "primary",
                        "width": "default",
                        "text": {"tag": "plain_text", "content": "保存质量反馈"},
                        "form_action_type": "submit",
                        "name": "submit_script_package_quality_feedback",
                        "behaviors": [
                            {
                                "type": "callback",
                                "value": {
                                    "action": SCRIPT_PACKAGE_QUALITY_ACTION,
                                    "candidate_ids": record_ids,
                                    "card_issued_at": issued_at.isoformat() + "Z",
                                    "card_expires_at": expires_at.isoformat() + "Z",
                                    "card_ttl_days": 7,
                                },
                            },
                        ],
                    },
                ],
            },
            {
                "tag": "column",
                "width": "auto",
                "elements": [
                    {
                        "tag": "button",
                        "type": "default",
                        "width": "default",
                        "text": {"tag": "plain_text", "content": "重置"},
                        "form_action_type": "reset",
                        "name": "reset_script_package_quality_feedback",
                    },
                ],
            },
        ],
    })
    elements.append({
        "tag": "form",
        "name": "script_package_quality_batch",
        "padding": "8px 0px 0px 0px",
        "vertical_spacing": "8px",
        "elements": form_elements,
    })
    return {
        "schema": "2.0",
        "config": {
            "update_multi": True,
            "enable_forward": False,
            "width_mode": "fill",
        },
        "header": {
            "template": "blue",
            "title": {"tag": "plain_text", "content": "06 完整脚本与制作包已生成"},
        },
        "body": {"elements": elements},
    }


def send_interactive_card(token: str, card: dict[str, Any], uuid_base: str) -> dict[str, Any]:
    targets = parse_card_targets()
    if not targets:
        return {"sent_count": 0, "skipped": "missing_receive_targets"}
    sends = []
    for receive_id_type, receive_id in targets:
        payload = feishu.request_json(
            "POST",
            f"/im/v1/messages?receive_id_type={quote(receive_id_type)}",
            token=token,
            body={
                "receive_id": receive_id,
                "msg_type": "interactive",
                "content": json.dumps(card, ensure_ascii=False),
                "uuid": card_uuid("script-package-card", uuid_base, receive_id_type, receive_id),
            },
        )
        sends.append({"receive_id_type": receive_id_type, "receive_id": receive_id, "message_id": payload.get("data", {}).get("message_id", "")})
    return {"sent_count": len(sends), "sends": sends}


def send_completion_card(token: str, results: list[dict[str, Any]]) -> dict[str, Any]:
    created_results = [item for item in results if str(item.get("created_script_package_id") or "").strip()]
    feedback_results = [
        item for item in created_results
        if str(item.get("feishu_document_url") or "").strip()
    ]
    missing_doc_links = len(created_results) - len(feedback_results)
    if not feedback_results:
        return {"sent_count": 0, "skipped": "missing_feishu_document_urls", "missing_doc_links": missing_doc_links}
    card = build_completion_card(feedback_results)
    uuid_base = "|".join(str(item.get("created_script_package_id") or "") for item in feedback_results)
    result = send_interactive_card(token, card, uuid_base)
    result["missing_doc_links"] = missing_doc_links
    return result


def mark_topic_generated(token: str, app_token: str, table_id: str, record_id: str, marker: str = "是") -> None:
    feishu.request_json(
        "PUT",
        f"/bitable/v1/apps/{app_token}/tables/{table_id}/records/{record_id}",
        token=token,
        body={"fields": {TOPIC_MARK_FIELD: marker}},
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
    parser.add_argument("--no-completion-card", action="store_true", help="Do not send the 06 completion feedback card after writing Feishu records.")
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
        package, attempt_count, attempt_history = generate_package_with_retry(topic, args.timeout_seconds)
        qa_status = qa_status_of(package)
        document_path = write_package_markdown(topic, package)
        title = str(topic.get("topic_title") or package.get("topic_title") or "未命名选题")
        doc_sync = try_create_feishu_document(token, title, package) if args.write_feishu else FeishuDocSyncResult(status="未写入飞书")
        row = package_row(topic, package, document_path, doc_sync, attempts=attempt_count)
        created_id = ""
        topic_marker = "是" if qa_status == "pass" else "需人工处理"
        if args.write_feishu:
            created_id = create_script_package_record(token, app_token, table_ids["script_package"], row)
            mark_topic_generated(token, app_token, table_ids["topic_decision"], str(record["record_id"]), marker=topic_marker)
        result = {
            "record_id": record.get("record_id"),
            "topic_title": topic.get("topic_title"),
            "document_path": str(document_path),
            "feishu_document_url": doc_sync.url,
            "doc_sync_status": doc_sync.status,
            "qa_status": qa_status,
            "qa_result": str(package.get("qa_result") or ""),
            "core_viewpoint": str(package.get("core_viewpoint") or ""),
            "opening_hook": str(package.get("opening_hook") or ""),
            "can_shoot": str(package.get("can_shoot") or ""),
            "attempts": attempt_count,
            "attempt_history": attempt_history,
            "created_script_package_id": created_id,
            "topic_marker": topic_marker if args.write_feishu else "",
            "marked_topic": bool(args.write_feishu),
        }
        results.append(result)
        log(json.dumps({"event": "topic_done", **result}, ensure_ascii=False))
        time.sleep(0.2)

    completion_card: dict[str, Any] = {"sent_count": 0, "skipped": "disabled_or_not_write_feishu"}
    if args.write_feishu and not args.no_completion_card:
        try:
            completion_card = send_completion_card(token, results)
        except Exception as exc:  # noqa: BLE001 - docs are already generated; surface card failure without rollback.
            completion_card = {"sent_count": 0, "error": compact(exc, 1000)}
        log(json.dumps({"event": "completion_card", **completion_card}, ensure_ascii=False))

    print(json.dumps({"ok": True, "version": RUNNER_VERSION, "results": results, "completion_card": completion_card}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
