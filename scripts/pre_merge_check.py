#!/usr/bin/env python3
"""Run pre-merge checks for the AI account radar dev worktree."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import hashlib
import re
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
    "scripts/full_account_collection_contract.py",
    "scripts/douyin_chrome_runtime.py",
    "scripts/start_douyin_cdp_chrome.py",
    "scripts/check_douyin_session.py",
    "scripts/run_topic_card_if_fresh.py",
    "scripts/watch_script_package_queue.py",
    "scripts/local_env.py",
    "scripts/check_feishu_card_cloud_receiver.py",
    "scripts/topic_flow_rework.py",
    "scripts/topic_field_contract.py",
    "scripts/topic_replay_evaluation.py",
    "scripts/topic_skill_replay_evaluation.py",
    "scripts/editorial_expression_policy.py",
    "scripts/ar020e_expression_calibration.py",
    "scripts/ar020e_daily_editorial_entrypoint.py",
    "scripts/exact_candidate_input.py",
    "scripts/ar020e_schema_readiness.py",
    "scripts/semantic_owner_dataflow.py",
    "scripts/ar020d_semantic_owner_gate.py",
    "scripts/install_production_keepawake.py",
)
DEFAULT_FEISHU_READ_TABLE_KEYS = ("topic_decision", "script_package")
SMOKE_MANUAL = "data/manual/content_items.example.jsonl"


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
    first_line = status["stdout"].splitlines()[0] if status["stdout"].splitlines() else ""
    ok = status["returncode"] == 0 and (
        "feature/next-production-flow" in first_line
        or "release/ar020e-rc" in first_line
    )
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


def configured_production_root() -> Path:
    raw = str(os.getenv("AI_ACCOUNT_RADAR_PRODUCTION_DIR") or "").strip()
    return Path(raw).expanduser().resolve() if raw else PRODUCTION_ROOT.resolve()


def git_text(args: list[str], *, cwd: Path | None = None) -> tuple[int, str, str]:
    result = run(["git", *args], cwd=cwd or ROOT)
    return result["returncode"], result["stdout"].strip(), result["stderr"].strip()


def check_git_production_release(expected_head: str) -> dict[str, Any]:
    expected_root = configured_production_root()
    expected = expected_head.strip()
    branch_rc, branch, branch_err = git_text(["rev-parse", "--abbrev-ref", "HEAD"])
    local_rc, local_head, local_err = git_text(["rev-parse", "HEAD"])
    remote_rc, remote_head, remote_err = git_text(["rev-parse", "origin/main"])
    status_rc, status, status_err = git_text(["status", "--porcelain"])
    reasons = []
    if not expected:
        reasons.append("missing_expected_head")
    elif not re.fullmatch(r"[0-9a-f]{40}", expected):
        reasons.append("invalid_expected_head")
    if ROOT.resolve() != expected_root:
        reasons.append("not_configured_production_root")
    if branch_rc != 0 or branch != "main":
        reasons.append("not_main_branch")
    if status_rc != 0 or status:
        reasons.append("dirty_worktree")
    if local_rc != 0 or remote_rc != 0:
        reasons.append("head_read_failed")
    elif expected and (local_head != expected or remote_head != expected or local_head != remote_head):
        reasons.append("head_mismatch")
    stderr = "\n".join(part for part in [branch_err, local_err, remote_err, status_err] if part)
    return {
        "ok": not reasons,
        "name": "production release worktree/head contract",
        "returncode": 0 if not reasons else 2,
        "stdout": json.dumps({
            "mode": "production_release_check",
            "root": str(ROOT.resolve()),
            "configured_production_root": str(expected_root),
            "branch": branch,
            "expected_head": expected,
            "local_head": local_head,
            "remote_head": remote_head,
            "clean": not bool(status),
            "reasons": reasons,
        }, ensure_ascii=False),
        "stderr": stderr,
        "command": ["git", "status/rev-parse"],
        "cwd": str(ROOT),
        "expected_head": expected,
        "local_head": local_head,
        "remote_head": remote_head,
        "reasons": reasons,
    }


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


def check_ar020d_semantic_owner_gate() -> dict[str, Any]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "scripts")
    result = run([sys.executable, "scripts/ar020d_semantic_owner_gate.py"], env=env)
    return {
        "ok": result["returncode"] == 0,
        "name": "AR-020D semantic owner dataflow and sentinel gate",
        **result,
    }


def check_exact_candidate_input_gate() -> dict[str, Any]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "scripts")
    result = run([
        sys.executable,
        "-m",
        "unittest",
        "scripts.test_exact_candidate_input",
    ], env=env)
    return {
        "ok": result["returncode"] == 0,
        "name": "AR-033B exact same-day candidate input gate",
        **result,
    }


def check_douyin_runtime_gate() -> dict[str, Any]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "scripts")
    python_result = run([
        sys.executable,
        "-m",
        "unittest",
        "scripts/test_start_douyin_cdp_chrome.py",
        "scripts/test_douyin_chrome_runtime.py",
        "scripts/test_ar031_douyin_preflight_gate.py",
        "scripts/test_ar026_account_limit_gate.py",
    ], env=env)
    if python_result["returncode"] != 0:
        return {"ok": False, "name": "Douyin canonical profile/session/full-account gate", **python_result}
    node_result = run(["node", "scripts/test_douyin_login_dom_probe.mjs"])
    source_probe_result = run(["node", "scripts/test_douyin_cdp_source_watch_probe.mjs"])
    combined = {
        **source_probe_result,
        "stdout": (python_result["stdout"] + "\n" + node_result["stdout"] + "\n" + source_probe_result["stdout"])[-4000:],
        "stderr": (python_result["stderr"] + "\n" + node_result["stderr"] + "\n" + source_probe_result["stderr"])[-4000:],
    }
    return {
        "ok": node_result["returncode"] == 0 and source_probe_result["returncode"] == 0,
        "name": "Douyin canonical profile/session/full-account gate",
        **combined,
    }


def check_feishu_receiver_node_tests() -> dict[str, Any]:
    result = run(["node", "--test", "cloud_functions/feishu-card-receiver/test/receiver.test.mjs", "cloud_functions/feishu-card-receiver/test/tencent-scf-entry.test.mjs"])
    return {"ok": result["returncode"] == 0, "name": "Feishu card receiver Node tests", **result}


def check_topic_card_guard() -> dict[str, Any]:
    if ROOT.resolve() == PRODUCTION_ROOT.resolve():
        return {
            "ok": False,
            "name": "topic card production guard in dev",
            "returncode": 2,
            "stdout": "",
            "stderr": "Refusing to run Topic Card guard probe from the production worktree.",
            "command": [sys.executable, "scripts/run_topic_card_if_fresh.py", "--check-only"],
            "cwd": str(ROOT),
        }
    result = run([sys.executable, "scripts/run_topic_card_if_fresh.py", "--check-only"])
    ok = result["returncode"] == 2 and (
        "running_from_development_worktree" in result["stdout"]
        or "running_from_unexpected_directory" in result["stdout"]
        or "running_from_non_production_branch" in result["stdout"]
    )
    return {"ok": ok, "name": "topic card production guard in dev", **result}


def topic_card_artifact_snapshot() -> dict[str, str]:
    paths: list[Path] = []
    decision_dir = ROOT / "output" / "decision_cards"
    if decision_dir.exists():
        paths.extend(path for path in decision_dir.rglob("*") if path.is_file())
    for path in (ROOT / "output").glob("*topic*decision*card*.json"):
        if path.is_file():
            paths.append(path)
    snapshot = {}
    for path in sorted(set(paths)):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        snapshot[str(path.relative_to(ROOT))] = f"{path.stat().st_size}:{path.stat().st_mtime_ns}:{digest}"
    return snapshot


def parse_single_json_output(stdout: str) -> dict[str, Any]:
    value = json.loads(stdout)
    if not isinstance(value, dict):
        raise ValueError("Topic Card check-only output must be a JSON object")
    return value


def check_topic_card_production_release() -> dict[str, Any]:
    command = [sys.executable, "scripts/run_topic_card_if_fresh.py", "--check-only", "--no-notify"]
    before = topic_card_artifact_snapshot()
    result = run(command)
    after = topic_card_artifact_snapshot()
    payload: dict[str, Any] = {}
    parse_error = ""
    try:
        payload = parse_single_json_output(result["stdout"])
    except (json.JSONDecodeError, ValueError) as exc:
        parse_error = f"{type(exc).__name__}: {exc}"
    artifacts_unchanged = before == after
    ok = (
        result["returncode"] == 0
        and not parse_error
        and payload.get("check_only") is True
        and payload.get("sent") is False
        and payload.get("wrote_feishu") is not True
        and payload.get("writes_feishu") is not True
        and payload.get("notified") is not True
        and payload.get("notification_sent") is not True
        and artifacts_unchanged
    )
    return {
        "ok": ok,
        "name": "production Topic Card check-only/no-notify guard",
        **result,
        "stdout": json.dumps({
            "payload": payload,
            "parse_error": parse_error,
            "artifacts_unchanged": artifacts_unchanged,
            "writes_feishu": False,
            "sent": payload.get("sent") if payload else None,
        }, ensure_ascii=False),
        "payload": payload,
        "parse_error": parse_error,
        "artifacts_unchanged": artifacts_unchanged,
    }


def check_feishu_read(env_file: str, table_keys: list[str]) -> dict[str, Any]:
    env = os.environ.copy()
    env["AI_ACCOUNT_RADAR_ENV_FILE"] = env_file
    results = []
    ok = True
    for table_key in table_keys:
        result = run([
            sys.executable,
            "scripts/check_feishu_card_cloud_receiver.py",
            "--skip-receiver",
            "--table-key",
            table_key,
        ], env=env)
        results.append({"table_key": table_key, **result})
        ok = ok and result["returncode"] == 0
    return {
        "ok": ok,
        "name": "staging/test Feishu read-only check",
        "returncode": 0 if ok else 1,
        "stdout": json.dumps(results, ensure_ascii=False, indent=2),
        "stderr": "\n".join(str(result.get("stderr") or "") for result in results if result.get("stderr")),
        "command": ["check_feishu_card_cloud_receiver.py", "--skip-receiver", "--table-key", ",".join(table_keys)],
        "cwd": str(ROOT),
    }


def check_daily_pipeline_full_smoke() -> dict[str, Any]:
    env = os.environ.copy()
    env["EDITORIAL_SKILL_ENGINE"] = "deterministic"
    result = run([
        sys.executable,
        "scripts/daily_pipeline.py",
        "--no-fetch-aihot",
        "--no-fetch-douyin",
        "--manual",
        SMOKE_MANUAL,
    ], env=env)
    ok = (
        result["returncode"] == 0
        and '"ok": true' in result["stdout"]
        and '"mode": "dry-run"' in result["stdout"]
        and '"wrote_feishu": false' in result["stdout"]
    )
    return {"ok": ok, "name": "full local deterministic pipeline smoke without Feishu writes", **result}


def check_qa_notification_smoke(env_file: str) -> dict[str, Any]:
    env = os.environ.copy()
    env["AI_ACCOUNT_RADAR_ENV_FILE"] = env_file
    qa_result = run([
        sys.executable,
        "scripts/automation_failure_qa.py",
        "--reason",
        "latest_write_not_generated_today",
        "--run-id",
        "premerge_smoke",
    ], env=env)
    if qa_result["returncode"] != 0:
        return {"ok": False, "name": "failure QA notification smoke", **qa_result}

    body = "【测试】失败 QA 预合并通知链路测试\n不会写入业务表，不会发送选题卡。\n\n" + qa_result["stdout"]
    notify_result = run([
        sys.executable,
        "scripts/feishu_automation_notify.py",
        "--title",
        "【测试】AI账号雷达失败QA预合并测试",
        "--body",
        body,
    ], env=env)
    return {"ok": notify_result["returncode"] == 0, "name": "failure QA notification smoke", **notify_result}


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
    parser.add_argument("--production-release-check", action="store_true", help="Run the explicit production release gate from the configured clean production main worktree.")
    parser.add_argument("--expected-head", default="", help="Required exact local/origin main SHA for --production-release-check.")
    parser.add_argument("--env-file", default="", help="Optional staging/test env file for read-only Feishu check.")
    parser.add_argument("--feishu-read", action="store_true", help="Run a read-only Feishu check using --env-file or AI_ACCOUNT_RADAR_ENV_FILE.")
    parser.add_argument("--table-key", action="append", default=[], help="Feishu table key to read during --feishu-read. Defaults to 04 and 06.")
    parser.add_argument("--full-smoke", action="store_true", help="Run a full local dry-run pipeline smoke. This can take a few minutes.")
    parser.add_argument("--notify-smoke", action="store_true", help="Send a clearly labeled test QA notification using --env-file.")
    args = parser.parse_args()

    if args.production_release_check:
        incompatible = args.feishu_read or args.full_smoke or args.notify_smoke or bool(args.table_key) or bool(args.env_file)
        checks = [check_git_production_release(args.expected_head)]
        if incompatible:
            checks.append({
                "ok": False,
                "name": "production release mode arguments",
                "returncode": 2,
                "stdout": "",
                "stderr": "Production release mode does not accept smoke, notification, or external read options.",
            })
        checks.extend([
            check_py_compile(),
            check_ar020d_semantic_owner_gate(),
            check_exact_candidate_input_gate(),
            check_failure_qa_rules(),
            check_douyin_runtime_gate(),
            check_feishu_receiver_node_tests(),
        ])
        if checks[0]["ok"] and not incompatible:
            checks.append(check_topic_card_production_release())
        else:
            checks.append({
                "ok": False,
                "name": "production Topic Card check-only/no-notify guard",
                "returncode": 2,
                "stdout": "",
                "stderr": "Skipped because the production worktree/head contract failed.",
            })
        summary = summarize(checks)
        summary.update({
            "mode": "production_release_check",
            "expected_head": args.expected_head,
            "local_head": checks[0].get("local_head", ""),
            "remote_head": checks[0].get("remote_head", ""),
            "check_only": True,
            "writes_feishu": False,
            "sent": False,
        })
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0 if summary["ok"] else 1

    checks = [
        check_git_dev(),
        check_git_production(),
        check_py_compile(),
        check_ar020d_semantic_owner_gate(),
        check_exact_candidate_input_gate(),
        check_failure_qa_rules(),
        check_douyin_runtime_gate(),
        check_feishu_receiver_node_tests(),
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
            table_keys = args.table_key or list(DEFAULT_FEISHU_READ_TABLE_KEYS)
            checks.append(check_feishu_read(env_file, table_keys))

    if args.full_smoke:
        checks.append(check_daily_pipeline_full_smoke())

    if args.notify_smoke:
        if not env_file:
            checks.append({
                "ok": False,
                "name": "failure QA notification smoke",
                "returncode": 2,
                "stdout": "",
                "stderr": "Pass --env-file .env.staging.local or set AI_ACCOUNT_RADAR_ENV_FILE.",
            })
        else:
            checks.append(check_qa_notification_smoke(env_file))

    summary = summarize(checks)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
