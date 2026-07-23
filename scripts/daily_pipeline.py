#!/usr/bin/env python3
"""Daily entrypoint for AI account radar.

Daily operation should write to Feishu with --write-feishu. Each configured
source is attempted once and only current-run successful rows enter planning.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import csv
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from local_env import load_local_env
from full_account_collection_contract import rejection_payload, validate_account_limit_argv

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output"
LOG_DIR = OUT / "logs"
URL_RESOLVED = OUT / "url_content_items.jsonl"
URL_RESOLVED_MANUAL = OUT / "url_content_items_manual.jsonl"
WECHAT_FULLTEXT_RESOLVED_MANUAL = OUT / "wechat_fulltext_provider_items.jsonl"
DOUYIN_CDP_RESULT = OUT / "spikes" / "douyin_cdp_source_watch_probe" / "cdp_probe_results.json"
DEFAULT_WECHAT_FULLTEXT_PROVIDER_CONFIG = ROOT / "config" / "wechat_fulltext_provider.example.yaml"


def douyin_run_artifact_paths(run_id: str) -> dict[str, Path]:
    root = OUT / "runs" / run_id / "sources" / "douyin"
    return {
        "dir": root,
        "result": root / "cdp_probe_results.json",
        "manual": root / "content_items_manual.jsonl",
    }

def run_step(name: str, command: list[str], env: dict[str, str] | None = None) -> dict[str, Any]:
    print(f"\n== {name} ==")
    print(" ".join(command))
    started = datetime.now().isoformat(timespec="seconds")
    result = subprocess.run(command, cwd=ROOT, env=env, text=True, capture_output=True)
    if result.stdout:
        print(result.stdout)
    if result.stderr:
        print(result.stderr, file=sys.stderr)
    return {
        "name": name,
        "command": command,
        "started_at": started,
        "returncode": result.returncode,
        "stdout": result.stdout[-4000:],
        "stderr": result.stderr[-4000:],
    }


def isolate_source_failure(
    step: dict[str, Any],
    *,
    source: str,
    state: str,
    reason: str = "",
) -> dict[str, Any]:
    original_returncode = int(step.get("returncode") or 1)
    step.update({
        "returncode": 0,
        "source_returncode": original_returncode,
        "source_local_failure": True,
        "source": source,
        "source_outcome": state,
        "source_rows": 0,
        "note": f"{source} source failed and was isolated; unrelated sources continue.",
    })
    if reason:
        step["source_failure_reason"] = reason
    return step


def machine_failure_reason(payload: dict[str, Any], step: dict[str, Any], fallback: str) -> str:
    return str(payload.get("reason") or payload.get("error") or step.get("stderr") or fallback)


def collection_failure_steps(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        step for step in steps
        if not step.get("deferred") and (
            step.get("returncode") != 0
            or step.get("source_local_failure")
            or step.get("candidate_local_partial")
        )
    ]


def deferred_exit_code(steps: list[dict[str, Any]]) -> int:
    return 1 if collection_failure_steps(steps) else 0


def business_steps_ok(steps: list[dict[str, Any]]) -> bool:
    return all(step.get("returncode") == 0 for step in steps)


def require_feishu_env() -> None:
    missing = [name for name in ["FEISHU_APP_ID", "FEISHU_APP_SECRET", "FEISHU_BASE_APP_TOKEN"] if not os.getenv(name)]
    if missing:
        raise SystemExit(f"--write-feishu requires environment variables: {', '.join(missing)}")


def write_run_log(
    steps: list[dict[str, Any]],
    mode: str,
    run_id: str = "",
    downstream_report: dict[str, Any] | None = None,
) -> Path:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    path = LOG_DIR / f"daily_pipeline_{datetime.now().strftime('%Y-%m-%d')}.json"
    output_dir = pipeline_output_dir(run_id, mode == "write-feishu") if run_id else OUT
    payload = {
        "ok": all(step["returncode"] == 0 for step in steps),
        "mode": mode,
        "run_id": run_id,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "steps": steps,
        "outputs": {
            "run_output_dir": str(output_dir),
            "today_10_topics": str(output_dir / "today_10_topics.csv"),
            "today_10_markdown": str(output_dir / f"today_10_topics_{datetime.now().strftime('%Y-%m-%d')}.md"),
        },
    }
    if downstream_report is not None:
        payload.update(downstream_report)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def pipeline_output_dir(run_id: str, write_feishu: bool) -> Path:
    return OUT / ("runs" if write_feishu else "dry_runs") / run_id


def today10_count(path: Path) -> int:
    if not path.exists() or not path.read_text(encoding="utf-8-sig").strip():
        return 0
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return len(list(csv.DictReader(handle)))


def new_run_id() -> str:
    return f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}"


def today_key() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def stdout_json(step: dict[str, Any]) -> dict[str, Any]:
    try:
        value = json.loads(str(step.get("stdout") or ""))
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def cdp_port(cdp_url: str) -> int:
    parsed = urlparse(cdp_url)
    return parsed.port or 9333


def canonical_douyin_cdp(cdp_url: str) -> bool:
    parsed = urlparse(cdp_url)
    return parsed.scheme == "http" and parsed.hostname == "127.0.0.1" and cdp_port(cdp_url) == 9333


def is_douyin_account_level_partial(step: dict[str, Any], probe: dict[str, Any]) -> bool:
    if step.get("name") != "fetch daily Douyin homepage title/caption samples through Chrome CDP":
        return False
    return str(probe.get("status") or "") == "completed_with_failures"


def is_candidate_local_partial(step: dict[str, Any], probe: dict[str, Any]) -> bool:
    return (
        is_douyin_account_level_partial(step, probe)
        or bool(step.get("candidate_local_partial"))
        or bool(step.get("source_local_failure"))
    )


def downstream_usability_report(
    steps: list[dict[str, Any]],
    output_dir: Path,
    today_candidates: int,
    probe_result_path: Path = DOUYIN_CDP_RESULT,
    wechat_read_outcome: dict[str, Any] | None = None,
) -> dict[str, Any]:
    probe = read_json(probe_result_path)
    coverage = probe.get("coverage") if isinstance(probe.get("coverage"), dict) else {}
    failed_accounts = coverage.get("failed_accounts") if isinstance(coverage.get("failed_accounts"), list) else []
    per_account_artifacts = coverage.get("per_account_artifact_counts")
    if not isinstance(per_account_artifacts, dict):
        per_account_artifacts = {}

    full_collection_failures = collection_failure_steps(steps)
    hard_failures = [
        step for step in full_collection_failures
        if not is_candidate_local_partial(step, probe)
        and step.get("external_status_unknown_after_reconciliation")
    ]
    failed_artifact_leaks = [
        str(row.get("account_name") or "")
        for row in failed_accounts
        if int(row.get("artifact_count") or 0) != 0
    ]
    failure_names = {str(row.get("account_name") or "") for row in failed_accounts}
    successful_artifact_accounts = [
        name for name, count in per_account_artifacts.items()
        if name not in failure_names and int(count or 0) > 0
    ]

    checks = {"today_candidates_nonempty": today_candidates > 0}
    blocking_checks = {
        "today_candidates_nonempty": today_candidates > 0,
        "no_unreconciled_external_failures": not hard_failures,
    }
    douyin_isolated = any(
        step.get("source_local_failure") and step.get("source") == "douyin"
        for step in steps
    )
    if not douyin_isolated:
        source_checks = {"failed_accounts_have_zero_artifacts": not failed_artifact_leaks}
        checks.update(source_checks)
        blocking_checks["failed_accounts_have_zero_artifacts"] = not failed_artifact_leaks
    wechat_read = wechat_read_outcome if isinstance(wechat_read_outcome, dict) else {}
    checks["no_unreconciled_external_failures"] = not hard_failures
    blocked_reasons = [name for name, ok in blocking_checks.items() if not ok]
    downstream_usable = not blocked_reasons
    full_collection_success = not full_collection_failures
    return {
        "full_collection_success": full_collection_success,
        "collection_status": "completed" if full_collection_success else "completed_with_failures",
        "downstream_usable": downstream_usable,
        "downstream_usable_reason": "full_collection_success" if full_collection_success else (
            "account_failures_isolated" if downstream_usable else "blocked"
        ),
        "downstream_usable_checks": checks,
        "downstream_blocked_reasons": blocked_reasons,
        "source_failure_count": len(full_collection_failures),
        "system_failure_count": len(hard_failures),
        "isolated_source_failures": [
            {
                "source": str(step.get("source") or ""),
                "state": str(step.get("source_outcome") or ""),
                "reason": str(step.get("source_failure_reason") or ""),
                "rows": int(step.get("source_rows") or 0),
            }
            for step in full_collection_failures
            if step.get("source_local_failure")
        ],
        "isolated_failed_account_count": len(failed_accounts),
        "isolated_failed_wechat_item_count": int(wechat_read.get("failed") or 0),
        "isolated_failed_accounts": [
            {
                "account_name": row.get("account_name", ""),
                "status": row.get("status", ""),
                "failure_reason": row.get("failure_reason", ""),
                "artifact_count": int(row.get("artifact_count") or 0),
            }
            for row in failed_accounts
        ],
        "probe_result_path": str(probe_result_path),
        "run_output_dir": str(output_dir),
        "today_candidates": today_candidates,
    }


def row_key(row: dict[str, Any]) -> str:
    return str(row.get("内容指纹") or row.get("内容链接") or row.get("内容标题") or "")


def combine_manual_jsonl(paths: list[Path], output: Path) -> Path:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for path in paths:
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            key = row_key(row)
            if key and key in seen:
                continue
            if key:
                seen.add(key)
            rows.append(row)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + ("\n" if rows else ""), encoding="utf-8")
    return output


def current_douyin_rows(result_path: Path, manual_path: Path, run_id: str) -> int:
    result = read_json(result_path)
    if str(result.get("run_id") or "") != run_id or not manual_path.is_file():
        return 0
    try:
        rows = [json.loads(line) for line in manual_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except (OSError, json.JSONDecodeError, TypeError):
        return 0
    failed = [
        row for row in (result.get("rows") or [])
        if str(row.get("status") or "").startswith(("failed", "needs_"))
    ]
    if any(int(row.get("artifact_count") or 0) != 0 for row in failed):
        return 0
    return len(rows)


def main() -> int:
    account_gate = validate_account_limit_argv(sys.argv[1:])
    if not account_gate.ok:
        print(json.dumps(rejection_payload("daily_pipeline", account_gate), ensure_ascii=False, indent=2))
        return 2

    parser = argparse.ArgumentParser(description="Run the daily AI account radar pipeline.")
    parser.add_argument("--write-feishu", action="store_true", help="Write Feishu changes for selected steps: URL resolver writes 03/updates 02; 今日候选池 writes 04 and refreshes 00.")
    parser.add_argument("--no-fetch-aihot", action="store_true", help="Skip AIHOT network fetch for an isolated test run.")
    parser.add_argument("--run-id", default="", help="Use an explicit run id across source, 03, editorial, 04, and card state.")
    parser.add_argument("--resolve-url-intake", action="store_true", help="Resolve URLs from Feishu 02 URL投喂入口 into ContentItem rows before sampling.")
    parser.add_argument("--fetch-wechat-fulltext-provider", action="store_true", help="Explicit P1 mode: fetch local WeChat fulltext provider rows into the candidate pool. Default is off.")
    parser.add_argument("--wechat-fulltext-provider-config", default=str(DEFAULT_WECHAT_FULLTEXT_PROVIDER_CONFIG), help="Config for explicit WeChat fulltext provider intake.")
    parser.add_argument("--wechat-fulltext-provider", default="", help="Provider id/name to fetch, e.g. wewe-rss. Only used with explicit WeChat fulltext provider mode.")
    parser.add_argument("--wechat-feed-limit", type=int, default=5, help="Max articles to fetch from the explicit WeChat fulltext provider.")
    parser.add_argument("--fetch-douyin-cdp-source-watch", action="store_true", help="Compatibility flag: Douyin homepage title/caption sampling is now attempted by default unless --no-fetch-douyin is set.")
    parser.add_argument("--no-fetch-douyin-cdp-source-watch", "--no-fetch-douyin", dest="no_fetch_douyin_cdp_source_watch", action="store_true", help="Skip daily Douyin homepage title/caption sampling.")
    parser.add_argument("--douyin-cdp", default=os.getenv("DOUYIN_CDP_URL", "http://127.0.0.1:9333"), help="Chrome DevTools endpoint for explicit Douyin homepage probe.")
    parser.add_argument("--douyin-account-limit", type=int, default=0, help="Max Douyin accounts to probe; 0 means every eligible account.")
    parser.add_argument("--douyin-video-limit", type=int, default=3, help="Max videos per Douyin account when --fetch-douyin-cdp-source-watch is enabled.")
    parser.add_argument(
        "--defer-editorial",
        action="store_true",
        help="Stop after raw candidate generation so the outer Codex automation can apply ai-account-editorial-director without nested codex exec.",
    )
    args = parser.parse_args()
    args.douyin_account_limit = account_gate.value

    load_local_env()

    if args.write_feishu or args.resolve_url_intake:
        require_feishu_env()

    py = sys.executable
    steps: list[dict[str, Any]] = []
    manual_inputs: list[Path] = []
    active_douyin_probe_result = DOUYIN_CDP_RESULT
    wechat_freshness: dict[str, Any] | None = None
    wechat_read_outcome: dict[str, Any] | None = None
    run_id = args.run_id or new_run_id()
    douyin_artifacts = douyin_run_artifact_paths(run_id)
    run_started_at_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    step_env = os.environ.copy()
    step_env["RUN_ID"] = run_id
    step_env["AI_ACCOUNT_RADAR_RUN_ID"] = run_id

    if args.resolve_url_intake:
        resolver_cmd = [py, str(ROOT / "scripts" / "url_content_resolver.py"), "--out", str(URL_RESOLVED)]
        resolver_cmd.append("--feishu-intake")
        if args.write_feishu:
            resolver_cmd.append("--write-feishu")
        steps.append(run_step("resolve URL intake into ContentItem rows", resolver_cmd, env=step_env))
        if steps[-1]["returncode"] != 0:
            isolate_source_failure(
                steps[-1],
                source="url_intake",
                state="resolver_failed",
                reason=str(steps[-1].get("stderr") or "url_intake_failed"),
            )
            manual_inputs = []
        else:
            manual_inputs = [URL_RESOLVED_MANUAL]

    if args.fetch_wechat_fulltext_provider or args.wechat_fulltext_provider:
        refresh_attempt_path = OUT / "provider_health" / run_id / "wewe_refresh_attempt.json"
        refresh_cmd = [py, str(ROOT / "scripts" / "wewe_provider_refresh.py"), "--run-id", run_id, "--run-started-at-ms", str(run_started_at_ms), "--out", str(refresh_attempt_path)]
        if not args.write_feishu:
            refresh_cmd.append("--check-only")
        steps.append(run_step("request fixed wewe-rss provider refresh", refresh_cmd, env=step_env))
        refresh_result = stdout_json(steps[-1])
        provider_cmd: list[str] = []
        if refresh_result.get("status") == "refresh_required" or steps[-1]["returncode"] != 0:
            state = str(refresh_result.get("status") or "provider_failed")
            isolate_source_failure(
                steps[-1],
                source="wechat",
                state=state,
                reason=machine_failure_reason(refresh_result, steps[-1], state),
            )
            wechat_freshness = {
                "ok": False,
                "state": state,
                "run_id": run_id,
                "source_rows": 0,
            }
        else:
            health_cmd = [py, str(ROOT / "scripts" / "wewe_provider_health.py"), "--check-only", "--run-id", run_id, "--run-started-at-ms", str(run_started_at_ms), "--refresh-result", str(refresh_attempt_path)]
            steps.append(run_step("verify canonical wewe-rss account and refresh freshness", health_cmd, env=step_env))
            wechat_freshness = stdout_json(steps[-1])
            if steps[-1]["returncode"] != 0:
                isolate_source_failure(
                    steps[-1],
                    source="wechat",
                    state=str(wechat_freshness.get("state") or "provider_failed"),
                    reason=machine_failure_reason(wechat_freshness, steps[-1], "wechat_health_failed"),
                )
            elif wechat_freshness.get("state") != "updated_no_new_items":
                provider_cmd = [
                    py,
                    str(ROOT / "scripts" / "wewe_current_feed_reader.py"),
                    "--refresh-result",
                    str(refresh_attempt_path),
                    "--run-id",
                    run_id,
                    "--run-started-at-ms",
                    str(run_started_at_ms),
                    "--out",
                    str(WECHAT_FULLTEXT_RESOLVED_MANUAL),
                    "--csv",
                    str(OUT / "wechat_fulltext_provider_items.csv"),
                    "--report",
                    str(OUT / "provider_health" / run_id / "wewe_current_feed_read_report.json"),
                ]
        if provider_cmd:
            steps.append(run_step("fetch refreshed WeChat fulltext provider into ContentItem rows", provider_cmd, env=step_env))
            wechat_read_outcome = stdout_json(steps[-1])
            candidate_partial = (
                wechat_read_outcome.get("status") == "completed_with_failures"
                and bool(wechat_read_outcome.get("downstream_usable"))
            )
            if steps[-1]["returncode"] != 0 and not candidate_partial:
                isolate_source_failure(
                    steps[-1],
                    source="wechat",
                    state=str(wechat_read_outcome.get("status") or "provider_failed"),
                    reason=str(wechat_read_outcome.get("error") or steps[-1].get("stderr") or "wechat_read_failed"),
                )
            else:
                if steps[-1]["returncode"] != 0:
                    steps[-1]["candidate_returncode"] = steps[-1]["returncode"]
                    steps[-1]["returncode"] = 0
                manual_inputs.append(WECHAT_FULLTEXT_RESOLVED_MANUAL)
            if candidate_partial:
                steps[-1]["candidate_local_partial"] = True
                steps[-1]["note"] = "WeChat candidate-local read failures were isolated; successful truthful rows continue."

    fetch_douyin = not args.no_fetch_douyin_cdp_source_watch or args.fetch_douyin_cdp_source_watch
    if fetch_douyin:
        if not canonical_douyin_cdp(args.douyin_cdp):
            failed = {
                "name": "verify canonical Douyin CDP endpoint",
                "command": ["canonical-cdp-check", args.douyin_cdp],
                "started_at": datetime.now().isoformat(timespec="seconds"),
                "returncode": 3,
                "stdout": "",
                "stderr": "non_canonical_douyin_cdp: expected http://127.0.0.1:9333",
            }
            isolate_source_failure(failed, source="douyin", state="cdp_invalid", reason=failed["stderr"])
            steps.append(failed)
        else:
            douyin_cmd = [
                "node",
                str(ROOT / "scripts" / "douyin_cdp_source_watch_probe.mjs"),
                "--cdp",
                args.douyin_cdp,
                "--out-dir",
                str(douyin_artifacts["dir"]),
                "--account-limit",
                str(args.douyin_account_limit),
                "--video-limit",
                str(args.douyin_video_limit),
            ]
            douyin_step = run_step("fetch daily Douyin homepage title/caption samples through Chrome CDP", douyin_cmd, env=step_env)
            steps.append(douyin_step)
            active_douyin_probe_result = douyin_artifacts["result"]
            row_count = current_douyin_rows(douyin_artifacts["result"], douyin_artifacts["manual"], run_id)
            if douyin_step["returncode"] == 0 and row_count > 0:
                manual_inputs.append(douyin_artifacts["manual"])
                douyin_step["source_rows"] = row_count
            else:
                isolate_source_failure(
                    douyin_step,
                    source="douyin",
                    state="collection_failed",
                    reason=str(douyin_step.get("stderr") or "douyin_current_run_rows_missing"),
                )

    manual_path = str(combine_manual_jsonl(
        manual_inputs,
        OUT / "runs" / run_id / "sources" / "current_run_rows.jsonl",
    ))

    sampler_cmd = [py, str(ROOT / "scripts" / "content_sampler.py"), "--manual", manual_path, "--run-id", run_id]
    if args.no_fetch_aihot:
        sampler_cmd.append("--no-fetch-aihot")
    if args.write_feishu:
        sampler_cmd.append("--write-feishu")
    steps.append(run_step("generate content breakdowns and 今日候选池", sampler_cmd, env=step_env if args.write_feishu else None))
    if steps[-1]["returncode"] != 0:
        log_path = write_run_log(steps, "write-feishu" if args.write_feishu else "dry-run", run_id)
        print(json.dumps({"ok": False, "log": str(log_path)}, ensure_ascii=False, indent=2))
        return steps[-1]["returncode"]

    output_dir = pipeline_output_dir(run_id, args.write_feishu)
    today10_path = output_dir / "today_10_topics.csv"
    generated_count = today10_count(today10_path)
    downstream_report = downstream_usability_report(
        steps, output_dir, generated_count, active_douyin_probe_result,
        wechat_read_outcome=wechat_read_outcome,
    )
    if generated_count == 0:
        failures = collection_failure_steps(steps)
        log_path = write_run_log(
            steps,
            "write-feishu" if args.write_feishu else "dry-run",
            run_id,
            downstream_report=downstream_report,
        )
        payload = {
            "ok": not failures,
            "status": "failed_or_partial" if failures else "completed_empty",
            "mode": "write-feishu" if args.write_feishu else "dry-run",
            "today_10_topics": 0,
            "wrote_feishu": False,
            "log": str(log_path),
            "run_output_dir": str(output_dir),
            "note": f"No daily topic candidates generated. Check URL parsing failures in {output_dir / 'content_items.csv'} and {output_dir / 'content_breakdowns.csv'}.",
        }
        payload.update(downstream_report)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 1 if failures else 0

    if args.defer_editorial:
        defer_step = {
            "name": "defer ai-account-editorial-director fields to outer Codex",
            "command": ["outer-codex", "apply-ai-account-editorial-director"],
            "started_at": datetime.now().isoformat(timespec="seconds"),
            "returncode": 75,
            "stdout": (
                "Raw candidates generated. The outer Codex automation must enrich "
                "today_10_topics.csv, then run finalize_daily_pipeline_after_editorial.py."
            ),
            "stderr": "",
            "deferred": True,
        }
        steps.append(defer_step)
        source_failures = collection_failure_steps(steps)
        collection_ok = not source_failures
        downstream_report = downstream_usability_report(
            steps,
            output_dir,
            generated_count,
            active_douyin_probe_result,
            wechat_read_outcome=wechat_read_outcome,
        )
        log_path = write_run_log(
            steps,
            "write-feishu" if args.write_feishu else "dry-run",
            run_id,
            downstream_report=downstream_report,
        )
        payload = {
            "ok": False,
            "deferred_editorial": True,
            "collection_ok": collection_ok,
            "collection_status": "deferred_editorial" if collection_ok else downstream_report["collection_status"],
            "source_failure_steps": [str(step.get("name") or "") for step in source_failures],
            "mode": "write-feishu" if args.write_feishu else "dry-run",
            "run_id": run_id,
            "run_output_dir": str(output_dir),
            "today_10_topics": str(today10_path),
            "log": str(log_path),
            "note": "Outer Codex automation must apply ai-account-editorial-director and finalize the run before 10:00 card sending.",
        }
        payload.update(downstream_report)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if downstream_report.get("downstream_usable") else deferred_exit_code(steps)

    editorial_report_path = output_dir / "editorial_skill_report.json"
    editorial_cmd = [
        py,
        str(ROOT / "scripts" / "editorial_skill_runner.py"),
        "--input",
        str(today10_path),
        "--output",
        str(today10_path),
        "--report",
        str(editorial_report_path),
    ]
    steps.append(run_step("apply ai-account-editorial-director fields", editorial_cmd, env=step_env))
    if steps[-1]["returncode"] != 0:
        log_path = write_run_log(steps, "write-feishu" if args.write_feishu else "dry-run", run_id)
        print(json.dumps({"ok": False, "log": str(log_path)}, ensure_ascii=False, indent=2))
        return steps[-1]["returncode"]
    dry_run_cmd = [
        py, str(ROOT / "scripts" / "push_today10_to_feishu.py"),
        "--input", str(today10_path), "--run-id", run_id,
    ]
    steps.append(run_step("dry-run 今日候选池 Feishu write", dry_run_cmd))
    if steps[-1]["returncode"] != 0:
        log_path = write_run_log(steps, "write-feishu" if args.write_feishu else "dry-run", run_id)
        print(json.dumps({"ok": False, "log": str(log_path)}, ensure_ascii=False, indent=2))
        return steps[-1]["returncode"]

    if args.write_feishu:
        write_cmd = [py, str(ROOT / "scripts" / "push_today10_to_feishu.py"), "--input", str(today10_path), "--write", "--run-id", run_id]
        steps.append(run_step("write 今日候选池 to Feishu 04 分析与选题", write_cmd, env=step_env))
        if steps[-1]["returncode"] != 0:
            log_path = write_run_log(steps, "write-feishu", run_id)
            print(json.dumps({"ok": False, "log": str(log_path)}, ensure_ascii=False, indent=2))
            return steps[-1]["returncode"]

        verify_cmd = [py, str(ROOT / "scripts" / "verify_today10_feishu_consistency.py"), "--input", str(today10_path), "--run-id", run_id]
        steps.append(run_step("verify Feishu 04 今日候选池 consistency", verify_cmd, env=step_env))
        if steps[-1]["returncode"] != 0:
            log_path = write_run_log(steps, "write-feishu", run_id)
            print(json.dumps({"ok": False, "log": str(log_path)}, ensure_ascii=False, indent=2))
            return steps[-1]["returncode"]

    ok = business_steps_ok(steps)
    final_report = dict(downstream_report)
    final_report.update({
        "editorial_finalized": ok,
        "finalization_ok": ok,
        "status": "completed" if ok and downstream_report.get("full_collection_success") else (
            "completed_with_failures" if ok else "failed"
        ),
    })
    log_path = write_run_log(
        steps,
        "write-feishu" if args.write_feishu else "dry-run",
        run_id,
        downstream_report=final_report,
    )
    payload = {
        "ok": ok,
        "mode": "write-feishu" if args.write_feishu else "dry-run",
        "run_id": run_id,
        "run_output_dir": str(output_dir),
        "today_10_topics": str(today10_path),
        "log": str(log_path),
        "wrote_feishu": bool(args.write_feishu and ok),
    }
    payload.update(final_report)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
