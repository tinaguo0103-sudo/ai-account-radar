#!/usr/bin/env python3
"""Rule-based QA summaries for scheduled automation failures."""
from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class FailureRule:
    category: str
    patterns: tuple[str, ...]
    conclusion: str
    likely_cause: str
    impact: str
    actions: tuple[str, ...]
    severity: str = "P1"


RULES: tuple[FailureRule, ...] = (
    FailureRule(
        category="worktree_guard",
        patterns=("running_from_development_worktree", "running_from_non_production_branch", "running_from_unexpected_directory"),
        conclusion="自动化入口被 worktree 守卫拦截。",
        likely_cause="定时任务可能运行在开发目录、功能分支或非预期目录。",
        impact="系统已在写飞书、采集或发卡前停止，避免开发分支污染生产数据。",
        actions=(
            "确认 Codex automation 的工作区仍指向生产目录 ai_account_radar/。",
            "确认生产目录分支是 main，且不要把 automation 指到 ai_account_radar_dev/。",
            "如果只是开发演练，使用 --allow-non-production-worktree --no-notify。",
        ),
    ),
    FailureRule(
        category="missing_feishu_env",
        patterns=("FEISHU_APP_ID", "FEISHU_APP_SECRET", "FEISHU_BASE_APP_TOKEN", "--write-feishu requires environment variables"),
        conclusion="飞书写入所需环境变量缺失。",
        likely_cause="运行环境没有加载 .env.local，或 Codex automation 缺少飞书 app/base 配置。",
        impact="无法读取或写入飞书表，今日采集、候选写入或发卡会停止。",
        actions=(
            "检查生产目录 .env.local 是否包含 FEISHU_APP_ID、FEISHU_APP_SECRET、FEISHU_BASE_APP_TOKEN。",
            "在生产目录手动跑一次只读健康检查：python3 scripts/check_feishu_card_cloud_receiver.py。",
            "如果是新 automation 环境，先确认 load_local_env() 能读取到同一份本机配置。",
        ),
    ),
    FailureRule(
        category="notify_target_missing",
        patterns=("Missing notify target", "FEISHU_AUTOMATION_NOTIFY_TARGETS", "FEISHU_CARD_RECEIVE_TARGETS"),
        conclusion="异常通知目标缺失。",
        likely_cause="没有配置 FEISHU_AUTOMATION_NOTIFY_TARGETS，且 FEISHU_CARD_RECEIVE_TARGETS 也不可用。",
        impact="主任务可能已经失败，但系统无法把失败原因发到飞书。",
        actions=(
            "在 .env.local 配置 FEISHU_AUTOMATION_NOTIFY_TARGETS=chat_id:oc_xxx。",
            "如果暂时复用选题卡接收群，确认 FEISHU_CARD_RECEIVE_TARGETS 仍有效。",
            "配置后单独运行 python3 scripts/feishu_automation_notify.py --title 测试 --body 测试。",
        ),
    ),
    FailureRule(
        category="feishu_forbidden_base",
        patterns=("91403", "Forbidden"),
        conclusion="飞书多维表权限不足。",
        likely_cause="自建应用不是目标 Base 协作者，或 app/base token 指向了无权限的表。",
        impact="候选、状态或学习字段无法写入飞书，生产链路会在写表阶段失败。",
        actions=(
            "确认 FEISHU_BASE_APP_TOKEN 指向当前生产 Base。",
            "把飞书自建应用加入该 Base 的协作者，或改用应用自己创建/授权过的 Base。",
            "修复后重跑失败阶段，不要直接跳过一致性校验。",
        ),
    ),
    FailureRule(
        category="feishu_doc_folder_permission",
        patterns=("1770040", "no folder permission", "文档同步失败", "飞书文档同步失败"),
        conclusion="飞书用户可见文件夹无写入权限。",
        likely_cause="tenant/app token 不能写普通云盘文件夹，缺少用户 access token 或文件夹授权。",
        impact="06 本地 Markdown 和 06 表记录可能成功，但飞书文档链接不可用或同步状态异常。",
        actions=(
            "检查 FEISHU_SCRIPT_PACKAGE_VISIBLE_FOLDER_TOKEN 和 FEISHU_SCRIPT_PACKAGE_VISIBLE_FOLDER_URL 是否正确。",
            "运行 python3 scripts/feishu_user_oauth.py --timeout-seconds 240 重新授权用户身份。",
            "确认 .env.local 存在 FEISHU_SCRIPT_PACKAGE_USER_ACCESS_TOKEN 或 FEISHU_USER_ACCESS_TOKEN。",
        ),
    ),
    FailureRule(
        category="feishu_token_invalid",
        patterns=("tenant_access_token", "access token", "Invalid token", "999916", "999914", "unauthorized"),
        conclusion="飞书 token 或授权状态异常。",
        likely_cause="app secret 错误、token 过期、权限范围缺失，或用户 token refresh 失败。",
        impact="飞书读取、写入、发消息或创建文档会失败。",
        actions=(
            "确认 FEISHU_APP_ID / FEISHU_APP_SECRET 未被替换或过期。",
            "如果失败发生在 06 文档创建，重新跑 feishu_user_oauth.py 刷新用户授权。",
            "如果失败发生在消息发送，检查机器人和 im/v1/messages 权限。",
        ),
    ),
    FailureRule(
        category="network_or_dns",
        patterns=("ReadTimeout", "ConnectTimeout", "ConnectionError", "NameResolutionError", "Temporary failure", "nodename nor servname", "ECONNRESET", "ETIMEDOUT"),
        conclusion="网络连接或 DNS 临时失败。",
        likely_cause="本机网络、DNS、代理/VPN、飞书/API 服务或目标站点短时不可达。",
        impact="当次采集或写入没有完成；10:00 守卫会阻止复用旧候选。",
        actions=(
            "确认本机联网、代理/VPN 状态和 DNS 正常。",
            "优先重跑失败阶段；如果只是单个可选来源失败，确认主链路是否已降级继续。",
            "如果连续两次失败，再检查飞书开放平台、AIHOT、Jina Reader 或目标源服务状态。",
        ),
    ),
    FailureRule(
        category="local_service_refused",
        patterns=("Connection refused", "ECONNREFUSED", "127.0.0.1:4000", "127.0.0.1:9333"),
        conclusion="本机依赖服务未启动或端口不可用。",
        likely_cause="wewe-rss、专用 Chrome CDP、Docker Desktop 或本地服务端口没有成功启动。",
        impact="公众号全文或抖音主页采样可能失败；如果这是必需步骤，今日采集会停止。",
        actions=(
            "公众号全文失败：运行 python3 scripts/start_wewe_rss.py 检查 Docker/wewe-rss。",
            "抖音 CDP 失败：运行 python3 scripts/start_douyin_cdp_chrome.py --port 9333。",
            "如果本机服务短期不可用，可临时跳过对应来源，确保 AIHOT/URL 主链路先跑通。",
        ),
    ),
    FailureRule(
        category="docker_wewe_rss",
        patterns=("Docker Desktop", "Cannot connect to the Docker daemon", "WEWE_RSS_AUTH_CODE", "wewe-rss", "ai-radar-wewe-rss"),
        conclusion="公众号全文 provider 启动或登录状态异常。",
        likely_cause="Docker Desktop 未启动、wewe-rss 容器未运行、首次创建缺少 WEWE_RSS_AUTH_CODE，或微信登录态失效。",
        impact="公众号全文源不可用；如果当前任务要求全文源，采集会失败或缺少公众号内容。",
        actions=(
            "先运行 python3 scripts/start_wewe_rss.py 查看容器状态。",
            "如果提示缺少 WEWE_RSS_AUTH_CODE，补齐本机 .env.local 后再创建容器。",
            "如果是登录态失效，打开 http://127.0.0.1:4000 处理登录后再重跑。",
        ),
    ),
    FailureRule(
        category="douyin_verification",
        patterns=("needs_login_or_verification", "登录", "验证码", "verification", "captcha", "Chrome CDP", "douyin_cdp"),
        conclusion="抖音采样遇到登录、验证或 CDP 访问问题。",
        likely_cause="专用 Chrome 登录态失效、抖音触发验证，或 CDP 探针未能读取主页数据。",
        impact="抖音对标账号浅层采样可能缺失；主链路通常应继续处理 AIHOT、公众号和 URL 投喂。",
        actions=(
            "如果通知只是可选来源失败，先观察今日候选是否仍生成，不要急着反复重试。",
            "需要恢复抖音采样时，运行 python3 scripts/start_douyin_cdp_chrome.py --foreground --port 9333 并处理登录/验证。",
            "重试时使用 --force-fetch-douyin；不要在无人值守时频繁强制采样。",
        ),
    ),
    FailureRule(
        category="aihot_fetch",
        patterns=("AIHOT", "403", "non-json", "HTML", "skipped_missing_url"),
        conclusion="AIHOT 来源抓取异常或被目标站点拒绝。",
        likely_cause="AIHOT API/页面短时变化、返回 HTML/空内容，或请求被 403 拦截。",
        impact="热点源可能缺失；如果其他来源正常，系统可继续生成较窄候选池。",
        actions=(
            "确认脚本仍带浏览器 User-Agent；不要改成裸 requests 默认 UA。",
            "如果单个 AIHOT 源失败，先看主控台是否是“部分源失败”而不是全链路失败。",
            "需要补救时，把 AIHOT 页面/日报内容粘贴到手动样例或 URL 投喂入口。",
        ),
    ),
    FailureRule(
        category="codex_or_skill",
        patterns=("codex exec failed", "command not found: codex", "No such file or directory: 'codex'", "Skill", "EDITORIAL_SKILL_DIR", "AUSTIN_SCRIPT_SKILL_DIR", "timed out", "TimeoutExpired"),
        conclusion="Codex 或私有 Skill 执行失败。",
        likely_cause="本机 Codex CLI 不可用、全局私有 Skill 缺失、脚本超时，或生成上下文过重。",
        impact="候选主编判断或 06 完整脚本包无法完成；不应把半成品写成成功状态。",
        actions=(
            "确认本机 codex CLI 可运行，且全局私有 Skill 目录存在。",
            "如果是超时，优先缩小 limit 或从干净候选池重跑，避免复用过重的 enriched CSV。",
            "如果是 Skill 缺失，恢复全局私有 Skill；不要默认降级到仓库脱敏镜像。",
        ),
    ),
    FailureRule(
        category="no_candidates",
        patterns=("today_10_topics.csv 为空", "today_10_topics_csv_empty", "today_candidates", "No daily topic candidates generated", "no_today_candidates_in_sampler_log"),
        conclusion="今日没有可发送候选。",
        likely_cause="采集源为空、内容都被主编层过滤，或今日 exact run 没有生成候选 CSV。",
        impact="10:00 不会发选题卡，避免把历史候选当成今天候选发送。",
        actions=(
            "先看 output/runs/<run_id>/content_sampler_log.json 的 today_candidates 和 run_id。",
            "检查 output/runs/<run_id>/debug_today10_generation.csv，确认是来源为空还是全部被过滤。",
            "需要临时补救时，使用 02 URL 投喂入口或手动样例补充内容后重跑采集。",
        ),
    ),
    FailureRule(
        category="stale_or_mismatched_run",
        patterns=("today_daily_pipeline_log_not_ok", "exact_run_artifact_not_generated_today", "pipeline_and_exact_artifact_run_id_mismatch", "exact_run_artifact_is_not_write_feishu_mode"),
        conclusion="今日候选新鲜度守卫阻止发卡。",
        likely_cause="08:00 采集未成功、exact run artifact 不是今天生成、run_id 不一致，或候选不是正式写飞书模式。",
        impact="10:00 不会发卡，避免误发旧候选或 dry-run 候选。",
        actions=(
            "先检查 output/logs/daily_pipeline_今天.json 和 output/runs/<run_id>/content_sampler_log.json。",
            "如果 08:00 采集失败，先处理采集失败，不要绕过新鲜度守卫。",
            "如果只是手动补跑成功，再运行 10:00 发卡入口或 run_topic_decision_card_session.py。",
        ),
    ),
    FailureRule(
        category="feishu_message_send",
        patterns=("run_topic_decision_card_session", "im/v1/messages", "FEISHU_CARD_RECEIVE_TARGETS", "receive_id_type", "机器人", "chat_id"),
        conclusion="飞书选题卡消息发送失败。",
        likely_cause="接收目标配置错误、机器人不在群里、消息 API 权限不足，或 receive_id_type 不匹配。",
        impact="候选可能已经生成并写入 04，但你没有收到交互式选题卡。",
        actions=(
            "检查 FEISHU_CARD_RECEIVE_TARGETS 是否是 type:id 格式，例如 chat_id:oc_xxx。",
            "确认机器人仍在目标群，且应用拥有 im/v1/messages 发送权限。",
            "必要时先从飞书 04 今日挑选卡片视图兜底选择。",
        ),
    ),
    FailureRule(
        category="feishu_consistency",
        patterns=("verify Feishu 04", "consistency", "mismatch", "not found after write", "一致性"),
        conclusion="飞书写入后一致性校验失败。",
        likely_cause="候选写入 04 后读回记录不完整、字段名不匹配、表结构变化，或飞书 API 有延迟/失败。",
        impact="不要直接相信本地 CSV；飞书前台候选可能缺行或字段异常。",
        actions=(
            "先重跑 verify_today10_feishu_consistency.py 复查是否为短暂延迟。",
            "如果持续失败，检查 04 字段是否被手动改名或删除。",
            "修复字段契约后再发选题卡。",
        ),
    ),
)


def compact(value: Any, limit: int = 700) -> str:
    text = str(value or "").strip()
    text = re.sub(r"\n{3,}", "\n\n", text)
    if len(text) <= limit:
        return text
    return text[-limit:]


def step_text(step: dict[str, Any]) -> str:
    command = " ".join(str(part) for part in step.get("command", []))
    return "\n".join([
        str(step.get("name") or ""),
        command,
        str(step.get("stdout") or ""),
        str(step.get("stderr") or ""),
        str(step.get("reason") or ""),
    ])


def match_rule(text: str) -> FailureRule:
    lowered = text.lower()
    for rule in RULES:
        if any(pattern.lower() in lowered for pattern in rule.patterns):
            return rule
    return FailureRule(
        category="unknown",
        patterns=(),
        conclusion="自动化失败，当前规则未能归类。",
        likely_cause="可能是新的异常类型、外部服务变化、脚本 bug 或日志信息不足。",
        impact="本次任务没有可靠完成；下游任务应继续依赖守卫，避免复用旧数据。",
        actions=(
            "先打开通知中的日志路径，查看失败阶段的 stderr/stdout。",
            "用失败阶段命令在生产目录单独重跑一次，确认是否可复现。",
            "如果连续复现，把新错误加入 automation_failure_qa.py 的规则库。",
        ),
        severity="P2",
    )


def failed_step(steps: list[dict[str, Any]]) -> dict[str, Any]:
    return next((step for step in steps if int(step.get("returncode") or 0) != 0), steps[-1] if steps else {})


def optional_warnings(steps: list[dict[str, Any]]) -> list[str]:
    warnings: list[str] = []
    for step in steps:
        if step.get("optional_failed"):
            rule = match_rule(step_text(step))
            warnings.append(f"{step.get('name', 'optional step')}：{rule.conclusion}")
    return warnings[:4]


def evidence_lines(step: dict[str, Any], log_path: str = "", extra: str = "") -> list[str]:
    lines = []
    if step.get("name"):
        lines.append(f"失败阶段：{step.get('name')}")
    if step.get("returncode") not in (None, ""):
        lines.append(f"退出码：{step.get('returncode')}")
    if log_path:
        lines.append(f"日志：{log_path}")
    command = " ".join(str(part) for part in step.get("command", []))
    if command:
        lines.append(f"命令：{compact(command, 220)}")
    detail = compact(str(step.get("stderr") or step.get("stdout") or extra or ""), 800)
    if detail:
        lines.append(f"关键证据：{detail}")
    return lines


def format_qa(task_name: str, rule: FailureRule, step: dict[str, Any], *, log_path: str = "", extra: str = "", warnings: list[str] | None = None) -> str:
    warning_lines = warnings or []
    lines = [
        f"任务：{task_name}",
        f"QA结论：{rule.conclusion}",
        f"严重级别：{rule.severity}",
        f"影响：{rule.impact}",
        f"可能原因：{rule.likely_cause}",
        "建议处理：",
    ]
    lines.extend(f"{idx}. {action}" for idx, action in enumerate(rule.actions, start=1))
    if warning_lines:
        lines.extend(["可选来源提醒：", *[f"- {warning}" for warning in warning_lines]])
    evidence = evidence_lines(step, log_path=log_path, extra=extra)
    if evidence:
        lines.extend(["证据：", *[f"- {line}" for line in evidence]])
    return "\n".join(lines)


def qa_for_steps(task_name: str, steps: list[dict[str, Any]], *, log_path: str = "") -> str:
    step = failed_step(steps)
    text = step_text(step)
    rule = match_rule(text)
    return format_qa(task_name, rule, step, log_path=log_path, warnings=optional_warnings(steps))


def qa_for_topic_skip(reason: str, run_id: str = "") -> str:
    step = {
        "name": "topic card freshness guard",
        "returncode": "",
        "reason": reason,
        "stdout": f"run_id={run_id or '无'}",
        "stderr": "",
    }
    rule = match_rule(reason)
    return format_qa("10:00 每日选题卡发送", rule, step, extra=reason)


def qa_for_command_failure(task_name: str, command: list[str], returncode: int, stdout: str = "", stderr: str = "", run_id: str = "") -> str:
    step = {
        "name": task_name,
        "command": command,
        "returncode": returncode,
        "stdout": stdout or f"run_id={run_id or '无'}",
        "stderr": stderr,
    }
    return format_qa(task_name, match_rule(step_text(step)), step)


def load_steps_from_log(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    steps = payload.get("steps", [])
    return steps if isinstance(steps, list) else []


def main() -> int:
    parser = argparse.ArgumentParser(description="Diagnose AI account radar automation failures with rule-based QA.")
    parser.add_argument("--task", default="自动化任务")
    parser.add_argument("--log", help="Path to a scheduled job JSON log containing steps.")
    parser.add_argument("--reason", default="", help="Optional guard/skip reason to diagnose without a log.")
    parser.add_argument("--run-id", default="")
    args = parser.parse_args()

    if args.log:
        log_path = Path(args.log)
        print(qa_for_steps(args.task, load_steps_from_log(log_path), log_path=str(log_path)))
        return 0
    if args.reason:
        print(qa_for_topic_skip(args.reason, args.run_id))
        return 0
    raise SystemExit("Provide --log or --reason.")


if __name__ == "__main__":
    raise SystemExit(main())
