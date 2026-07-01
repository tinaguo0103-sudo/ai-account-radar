#!/usr/bin/env python3
"""Run pre-merge checks for the AI account radar dev worktree."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from automation_failure_qa import qa_for_command_failure


ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = ROOT.parent
PRODUCTION_ROOT = PROJECT_ROOT / "ai_account_radar"
PY_COMPILE_TARGETS = (
    "scripts/automation_failure_qa.py",
    "scripts/automation_worktree_guard.py",
    "scripts/run_daily_collection_job.py",
    "scripts/run_topic_card_if_fresh.py",
    "scripts/watch_script_package_queue.py",
    "scripts/local_env.py",
    "scripts/check_feishu_card_cloud_receiver.py",
)


def run(command: list[str], *, cwd: Path = ROOT, env: dict[str, str] | None = None) -> dict[str, Any]:
    result = subprocess.run(command, cwd=cwd, text=True, capture_output=True, env=env)
    return {
        "command": command,
        "cwd": str(cwd),
        "returncode": result.returncode,
        "stdout": result.stdout[-4000:],
        "stderr": result.stderr[-4000:],
    }


def check_git_dev() -> dict[str, Any]:
    status = run(["git", "status", "--short", "--branch"])
    ok = status["returncode"] == 0 and "feature/next-production-flow" in status["stdout"]
    return {"ok": ok, "name": "dev worktree branch/status", **status}


def check_git_production() -> dict[str, Any]:
    status = run(["git", "status", "--short", "--branch"], cwd=PRODUCTION_ROOT)
    lines = [line for line in status["stdout"].splitlines() if line.strip()]
    ok = (
        status["returncode"] == 0
        and lines
        and lines[0].startswith("## main")
        and len(lines) == 1
    )
    return {"ok": ok, "name": "production worktree clean main", **status}


def check_py_compile() -> dict[str, Any]:
    env = os.environ.copy()
    env["PYTHONPYCACHEPREFIX"] = "/private/tmp/ai-radar-pycache"
    result = run([sys.executable, "-m", "py_compile", *PY_COMPILE_TARGETS], env=env)
    return {
        "ok": result["returncode"] == 0,
        "name": "python syntax compile",
        **result,
    }


def check_failure_qa_rules() -> dict[str, Any]:
    cases = [
        ("HTTP 403 {\"code\":91403,\"msg\":\"Forbidden\"}", "飞书多维表权限不足"),
        ("HTTP 403 {\"code\":1770040,\"msg\":\"no folder permission\"}", "飞书用户可见文件夹无写入权限"),
        ("ReadTimeout ConnectionError", "网络连接或 DNS 临时失败"),
        ("Cannot connect to the Docker daemon ai-radar-wewe-rss", "公众号全文 provider 启动或登录状态异常"),
        ("needs_login_or_verification Chrome CDP", "抖音采样遇到登录、验证或 CDP 访问问题"),
        ("codex exec failed with exit code 1", "Codex 或私有 Skill 执行失败"),
    ]
    failures: list[str] = []
    for stderr, expected in cases:
        report = qa_for_command_failure("pre-merge synthetic case", ["synthetic"], 1, stderr=stderr)
        if expected not in report:
            failures.append(f"expected {expected} for {stderr}")
    return {
        "ok": not failures,
        "name": "failure QA synthetic rules",
        "returncode": 0 if not failures else 1,
        "stdout": "all synthetic QA cases passed" if not failures else "\n".join(failures),
        "stderr": "",
    }


def check_topic_card_guard() -> dict[str, Any]:
    result = run([sys.executable, "scripts/run_topic_card_if_fresh.py", "--no-notify"])
    ok = result["returncode"] == 2 and "running_from_development_worktree" in result["stdout"]
    return {"ok": ok, "name": "topic card production guard in dev", **result}


def check_feishu_read(env_file: str) -> dict[str, Any]:
    env = os.environ.copy()
    env["AI_ACCOUNT_RADAR_ENV_FILE"] = env_file
    result = run([
        sys.executable,
        "scripts/check_feishu_card_cloud_receiver.py",
        "--skip-receiver",
        "--table-key",
        "topic_decision",
    ], env=env)
    return {"ok": result["returncode"] == 0, "name": "staging/test Feishu read-only check", **result}


def summarize(checks: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "ok": all(check.get("ok") for check in checks),
        "checks": [
            {
                "name": check["name"],
                "ok": bool(check.get("ok")),
                "returncode": check.get("returncode"),
                "stdout": check.get("stdout", "")[-1000:],
                "stderr": check.get("stderr", "")[-1000:],
            }
            for check in checks
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run safe pre-merge checks from the dev worktree.")
    parser.add_argument("--env-file", default="", help="Optional staging/test env file for read-only Feishu check.")
    parser.add_argument("--feishu-read", action="store_true", help="Run a read-only Feishu check using --env-file or AI_ACCOUNT_RADAR_ENV_FILE.")
    args = parser.parse_args()

    checks = [
        check_git_dev(),
        check_git_production(),
        check_py_compile(),
        check_failure_qa_rules(),
        check_topic_card_guard(),
    ]

    env_file = args.env_file or os.getenv("AI_ACCOUNT_RADAR_ENV_FILE") or os.getenv("ENV_FILE") or ""
    if args.feishu_read:
        if not env_file:
            checks.append({
                "ok": False,
                "name": "staging/test Feishu read-only check",
                "returncode": 2,
                "stdout": "",
                "stderr": "Pass --env-file .env.staging.local or set AI_ACCOUNT_RADAR_ENV_FILE.",
            })
        else:
            checks.append(check_feishu_read(env_file))

    summary = summarize(checks)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
