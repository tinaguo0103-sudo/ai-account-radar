#!/usr/bin/env python3
"""Daily entrypoint for AI account radar.

Daily operation should write to Feishu with --write-feishu. Each configured
source is attempted once and only current-run successful rows enter planning.
"""
from __future__ import annotations

import argparse
import hashlib
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
DEFAULT_WECHAT_PUBLIC_CONFIG = ROOT / "config" / "wechat_public_fulltext_sources.json"
DOUYIN_CDP_RESULT = OUT / "spikes" / "douyin_cdp_source_watch_probe" / "cdp_probe_results.json"


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


def current_douyin_artifact(result_path: Path, manual_path: Path, run_id: str) -> dict[str, Any]:
    result = read_json(result_path)
    status = str(result.get("status") or "")
    if str(result.get("run_id") or "") != run_id:
        return {"ok": False, "row_count": 0, "reason": "douyin_run_mismatch"}
    if status not in {"completed", "completed_with_failures"}:
        return {"ok": False, "row_count": 0, "reason": "douyin_status_not_usable"}
    if result.get("source_runtime_failure"):
        return {"ok": False, "row_count": 0, "reason": "douyin_shared_runtime_failure"}
    lineage = result.get("item_lineage")
    if not isinstance(lineage, dict) or lineage.get("ok") is not True:
        return {"ok": False, "row_count": 0, "reason": "douyin_item_lineage_invalid"}
    if not manual_path.is_file():
        return {"ok": False, "row_count": 0, "reason": "douyin_manual_artifact_missing"}
    try:
        rows = [json.loads(line) for line in manual_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except (OSError, json.JSONDecodeError, TypeError):
        return {"ok": False, "row_count": 0, "reason": "douyin_manual_artifact_unreadable"}
    artifact = result.get("manual_artifact")
    if not isinstance(artifact, dict):
        return {"ok": False, "row_count": 0, "reason": "douyin_manual_artifact_identity_missing"}
    raw = manual_path.read_bytes()
    if (
        str(artifact.get("run_id") or "") != run_id
        or str(artifact.get("path") or "") != str(manual_path.resolve())
        or int(artifact.get("row_count") or -1) != len(rows)
        or str(artifact.get("sha256") or "") != hashlib.sha256(raw).hexdigest()
    ):
        return {"ok": False, "row_count": 0, "reason": "douyin_manual_artifact_identity_mismatch"}
    coverage = result.get("coverage")
    if not isinstance(coverage, dict):
        return {"ok": False, "row_count": 0, "reason": "douyin_coverage_missing"}
    failed_accounts = coverage.get("failed_accounts")
    if not isinstance(failed_accounts, list):
        return {"ok": False, "row_count": 0, "reason": "douyin_failed_accounts_missing"}
    if any(int(row.get("artifact_count") or 0) != 0 for row in failed_accounts if isinstance(row, dict)):
        return {"ok": False, "row_count": 0, "reason": "douyin_failed_account_artifact_pollution"}
    failed_names = {
        str(row.get("account_name") or "")
        for row in failed_accounts if isinstance(row, dict)
    }
    if any(str(row.get("运行批次") or "") != run_id for row in rows):
        return {"ok": False, "row_count": 0, "reason": "douyin_manual_row_run_mismatch"}
    if any(str(row.get("账号名/公众号名") or "") in failed_names for row in rows):
        return {"ok": False, "row_count": 0, "reason": "douyin_failed_account_row_pollution"}
    temporal_counts = {
        "today_new": sum(str(row.get("候选时态") or "") == "today_new" for row in rows),
        "historical_unreviewed": sum(str(row.get("候选时态") or "") == "historical_unreviewed" for row in rows),
    }
    if sum(temporal_counts.values()) != len(rows):
        return {"ok": False, "row_count": 0, "reason": "douyin_candidate_temporal_state_invalid"}
    lifecycle = result.get("candidate_lifecycle")
    if not isinstance(lifecycle, dict) or (
        int(lifecycle.get("today_new_count") or 0) != temporal_counts["today_new"]
        or int(lifecycle.get("historical_unreviewed_count") or 0) != temporal_counts["historical_unreviewed"]
    ):
        return {"ok": False, "row_count": 0, "reason": "douyin_candidate_temporal_count_mismatch"}
    if not rows:
        return {"ok": False, "row_count": 0, "reason": "douyin_safe_rows_empty"}
    return {
        "ok": True,
        "row_count": len(rows),
        "status": status,
        "partial": status == "completed_with_failures",
        "failed_account_count": len(failed_accounts),
        **temporal_counts,
    }


def current_douyin_rows(result_path: Path, manual_path: Path, run_id: str) -> int:
    return int(current_douyin_artifact(result_path, manual_path, run_id).get("row_count") or 0)


def attach_current_douyin_artifact(
    step: dict[str, Any],
    artifact: dict[str, Any],
    manual_path: Path,
    manual_inputs: list[Path],
) -> bool:
    if artifact.get("ok") is not True:
        return False
    manual_inputs.append(manual_path)
    step["source_rows"] = int(artifact.get("row_count") or 0)
    step["source_outcome"] = str(artifact["status"])
    step["today_new_rows"] = int(artifact["today_new"])
    step["historical_unreviewed_rows"] = int(artifact["historical_unreviewed"])
    if artifact.get("partial"):
        step["source_returncode"] = step["returncode"]
        step["returncode"] = 0
        step["candidate_local_partial"] = True
        step["source"] = "douyin"
        step["source_failure_reason"] = (
            f"{int(artifact['failed_account_count'])} account failures isolated"
        )
    return True


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
    parser.add_argument("--fetch-wechat-public-fulltext", action="store_true", help="Discover exact public WeChat article URLs and parse their full text.")
    parser.add_argument("--wechat-public-config", default=str(DEFAULT_WECHAT_PUBLIC_CONFIG))
    parser.add_argument("--wechat-article-limit", type=int, default=1)
    parser.add_argument("--no-fetch-douyin", action="store_true", help="Skip the daily Douyin collection attempt.")
    parser.add_argument("--douyin-cdp", default=os.getenv("DOUYIN_CDP_URL", "http://127.0.0.1:9333"), help="Chrome DevTools endpoint for explicit Douyin homepage probe.")
    parser.add_argument("--douyin-account-limit", type=int, default=0, help="Max Douyin accounts to probe; 0 means every eligible account.")
    parser.add_argument("--douyin-video-limit", type=int, default=3, help="Max videos per Douyin account.")
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

    if args.fetch_wechat_public_fulltext:
        wechat_dir = OUT / "runs" / run_id / "sources" / "wechat"
        provider_cmd = [
            py,
            str(ROOT / "scripts" / "wechat_public_fulltext_source.py"),
            "--config",
            args.wechat_public_config,
            "--run-id",
            run_id,
            "--out-dir",
            str(wechat_dir),
            "--limit",
            str(args.wechat_article_limit),
        ]
        steps.append(run_step("discover exact public WeChat article and parse full text", provider_cmd, env=step_env))
        wechat_read_outcome = stdout_json(steps[-1])
        wechat_freshness = {
            "ok": wechat_read_outcome.get("ok") is True,
            "state": str(wechat_read_outcome.get("status") or "provider_failed"),
            "run_id": run_id,
            "source_rows": int(wechat_read_outcome.get("rows") or 0),
        }
        if steps[-1]["returncode"] != 0:
            isolate_source_failure(
                steps[-1],
                source="wechat",
                state=wechat_freshness["state"],
                reason=str(steps[-1].get("stderr") or "wechat_public_fulltext_failed"),
            )
        else:
            manual_inputs.append(wechat_dir / "content_items_manual.jsonl")
            steps[-1]["source"] = "wechat"
            steps[-1]["source_rows"] = int(wechat_read_outcome.get("rows") or 0)
            steps[-1]["source_outcome"] = wechat_freshness["state"]
            if wechat_freshness["state"] == "completed_with_failures":
                steps[-1]["candidate_local_partial"] = True
                steps[-1]["source_failure_reason"] = "one or more WeChat accounts failed with zero substitute rows"

    fetch_douyin = not args.no_fetch_douyin
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
            douyin_artifact = current_douyin_artifact(
                douyin_artifacts["result"], douyin_artifacts["manual"], run_id,
            )
            if not attach_current_douyin_artifact(
                douyin_step, douyin_artifact, douyin_artifacts["manual"], manual_inputs,
            ):
                isolate_source_failure(
                    douyin_step,
                    source="douyin",
                    state="collection_failed",
                    reason=str(douyin_artifact.get("reason") or douyin_step.get("stderr") or "douyin_current_run_rows_missing"),
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
