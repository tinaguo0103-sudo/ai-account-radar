#!/usr/bin/env python3
"""Daily entrypoint for AI account radar.

Daily operation should write to Feishu with --write-feishu. The default remains
local-only for development safety. Platform collection is guarded so risky
sources such as Douyin are collected at most once per day unless explicitly
forced for collection-logic testing.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import csv
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from local_env import load_local_env
from full_account_collection_contract import rejection_payload, validate_account_limit_argv
from source_ingestion_lineage import LineageError, validate_partial_source_artifact

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output"
LOG_DIR = OUT / "logs"
LATEST_DIR = OUT / "latest"
LATEST_DRY_RUN_DIR = OUT / "latest_dry_run"
LATEST_WRITE_DIR = OUT / "latest_write"
DEFAULT_MANUAL = ROOT / "data" / "manual" / "content_items.example.jsonl"
URL_RESOLVED = OUT / "url_content_items.jsonl"
URL_RESOLVED_MANUAL = OUT / "url_content_items_manual.jsonl"
WECHAT_FULLTEXT_RESOLVED_MANUAL = OUT / "wechat_fulltext_provider_items.jsonl"
DOUYIN_CDP_RESOLVED_MANUAL = OUT / "spikes" / "douyin_cdp_source_watch_probe" / "content_items_manual.jsonl"
DOUYIN_CDP_RETRY_DIR = OUT / "spikes" / "douyin_cdp_source_watch_probe_verification_retry"
DOUYIN_CDP_RETRY_MANUAL = DOUYIN_CDP_RETRY_DIR / "content_items_manual.jsonl"
DOUYIN_CDP_RETRY_RESULT = DOUYIN_CDP_RETRY_DIR / "cdp_probe_results.json"
DOUYIN_CDP_RESULT = OUT / "spikes" / "douyin_cdp_source_watch_probe" / "cdp_probe_results.json"
DOUYIN_TRANSCRIPTS_MANUAL = OUT / "spikes" / "douyin_transcripts" / "transcribed_content_items.jsonl"
COMBINED_MANUAL = OUT / "daily_pipeline_manual_combined.jsonl"
DEFAULT_WECHAT_FULLTEXT_PROVIDER_CONFIG = ROOT / "config" / "wechat_fulltext_provider.example.yaml"
SOURCE_CACHE_DIR = OUT / "source_collection_cache"

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


def run_optional_step(name: str, command: list[str], env: dict[str, str] | None = None) -> dict[str, Any]:
    result = run_step(name, command, env=env)
    original_returncode = result["returncode"]
    if original_returncode != 0:
        result["optional_returncode"] = original_returncode
        result["returncode"] = 0
        result["optional_failed"] = True
        result["note"] = "Optional source failed; daily pipeline continues."
    return result


def douyin_probe_allowed(chrome_step: dict[str, Any], preflight_step: dict[str, Any] | None) -> bool:
    return chrome_step.get("returncode") == 0 and preflight_step is not None and preflight_step.get("returncode") == 0


def collection_failure_steps(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        step for step in steps
        if not step.get("deferred") and (step.get("returncode") != 0 or step.get("optional_failed"))
    ]


def deferred_exit_code(steps: list[dict[str, Any]]) -> int:
    return 1 if collection_failure_steps(steps) else 0


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


def sync_enriched_candidate_mirrors(today_path: Path, report_path: Path, write_feishu: bool) -> None:
    """Keep latest pointers aligned after the editorial skill runner mutates CSV."""
    mirror_dirs = [LATEST_DIR, LATEST_WRITE_DIR if write_feishu else LATEST_DRY_RUN_DIR]
    for directory in mirror_dirs:
        directory.mkdir(parents=True, exist_ok=True)
        shutil.copy2(today_path, directory / "today_10_topics.csv")
        if report_path.exists():
            shutil.copy2(report_path, directory / "editorial_skill_report.json")
    if write_feishu:
        shutil.copy2(today_path, OUT / "today_10_topics.csv")


def today10_count(path: Path) -> int:
    if not path.exists() or not path.read_text(encoding="utf-8-sig").strip():
        return 0
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return len(list(csv.DictReader(handle)))


def new_run_id() -> str:
    return f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}"


def today_key() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def douyin_cache_manifest_path() -> Path:
    return SOURCE_CACHE_DIR / today_key() / "douyin_cdp_source_watch.json"


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


def commit_wechat_success_watermark(
    freshness: dict[str, Any], *, downstream_report: dict[str, Any],
    ingestion_closure: dict[str, Any], run_id: str,
) -> Path:
    feishu_identity = ingestion_closure.get("feishu_03_identity") if isinstance(ingestion_closure, dict) else {}
    if not downstream_report.get("downstream_usable") or feishu_identity.get("mode") != "write" or not feishu_identity.get("ok"):
        raise RuntimeError("wechat_watermark_before_downstream_readback")
    if str(freshness.get("run_id") or "") != run_id or not str(freshness.get("refresh_attempt_id") or ""):
        raise RuntimeError("wechat_watermark_owner_identity_mismatch")
    target = Path(str(freshness["state_path"])).expanduser().resolve()
    payload = {
        "schema_version": 2,
        "refresh_revision": int(freshness["refresh_revision"]),
        "refreshed_at_ms": int(freshness["refreshed_at_ms"]),
        "article_publish_watermark": int(freshness["latest_article_publish_time"]),
        "refresh_attempt_id": str(freshness["refresh_attempt_id"]),
        "accepted_run_id": str(freshness["run_id"]),
        "committed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(target)
    return target


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def file_modified_today(path: Path) -> bool:
    if not path.exists():
        return False
    modified = datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d")
    return modified == today_key()


def douyin_cache_ready() -> bool:
    manifest = read_json(douyin_cache_manifest_path())
    if manifest.get("date") != today_key() or manifest.get("status") != "ok":
        return False
    manual_path = Path(str(manifest.get("manual_jsonl") or DOUYIN_CDP_RESOLVED_MANUAL))
    return manual_path.exists() and file_modified_today(manual_path)


def cached_douyin_manual_paths() -> list[Path]:
    manifest = read_json(douyin_cache_manifest_path())
    paths: list[Path] = []
    for key in ("manual_jsonl", "retry_manual_jsonl"):
        value = str(manifest.get(key) or "").strip()
        if value:
            path = Path(value)
            if path.exists() and file_modified_today(path):
                paths.append(path)
    if not paths and DOUYIN_CDP_RESOLVED_MANUAL.exists() and file_modified_today(DOUYIN_CDP_RESOLVED_MANUAL):
        paths.append(DOUYIN_CDP_RESOLVED_MANUAL)
    return paths


def write_douyin_cache_manifest(status: str, run_id: str, steps: list[dict[str, Any]], note: str = "") -> None:
    payload = {
        "date": today_key(),
        "status": status,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "run_id": run_id,
        "manual_jsonl": str(DOUYIN_CDP_RESOLVED_MANUAL) if DOUYIN_CDP_RESOLVED_MANUAL.exists() else "",
        "retry_manual_jsonl": str(DOUYIN_CDP_RETRY_MANUAL) if DOUYIN_CDP_RETRY_MANUAL.exists() and file_modified_today(DOUYIN_CDP_RETRY_MANUAL) else "",
        "note": note,
        "steps": [
            {
                "name": step.get("name"),
                "returncode": step.get("returncode"),
                "optional_failed": step.get("optional_failed", False),
            }
            for step in steps
            if "Douyin" in str(step.get("name", "")) or "douyin" in " ".join(str(part) for part in step.get("command", []))
        ],
    }
    write_json(douyin_cache_manifest_path(), payload)


def cdp_port(cdp_url: str) -> int:
    parsed = urlparse(cdp_url)
    return parsed.port or 9333


def canonical_douyin_cdp(cdp_url: str) -> bool:
    parsed = urlparse(cdp_url)
    return parsed.scheme == "http" and parsed.hostname == "127.0.0.1" and cdp_port(cdp_url) == 9333


def douyin_verification_rows(result_path: Path) -> list[dict[str, Any]]:
    if not result_path.exists():
        return []
    try:
        payload = json.loads(result_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    rows = payload.get("rows") or []
    return [
        row for row in rows
        if row.get("status") == "needs_login_or_verification"
        and (row.get("account_name") or row.get("homepage_url"))
    ]


def successful_step(steps: list[dict[str, Any]], name: str) -> bool:
    return any(step.get("name") == name and step.get("returncode") == 0 for step in steps)


def is_douyin_account_level_partial(step: dict[str, Any], probe: dict[str, Any]) -> bool:
    if step.get("name") != "fetch daily Douyin homepage title/caption samples through Chrome CDP":
        return False
    if not step.get("optional_failed"):
        return False
    return str(probe.get("status") or "") == "completed_with_failures"


def is_candidate_local_partial(step: dict[str, Any], probe: dict[str, Any]) -> bool:
    return is_douyin_account_level_partial(step, probe) or bool(step.get("candidate_local_partial"))


def wechat_candidate_partial_checks(outcome: dict[str, Any]) -> dict[str, bool]:
    return {
        "wechat_candidate_lineage_complete": (
            int(outcome.get("planned") or 0) == int(outcome.get("attempted") or -1)
            and int(outcome.get("attempted") or 0)
            == int(outcome.get("succeeded") or 0) + int(outcome.get("failed") or 0)
        ),
        "wechat_candidate_failures_isolated": bool(outcome.get("downstream_usable")),
        "wechat_truthful_successes_nonempty": int(outcome.get("succeeded") or 0) > 0,
    }


def wechat_watermark_allowed(
    *, write_feishu: bool, downstream_usable: bool,
    freshness: dict[str, Any] | None, read_outcome: dict[str, Any] | None,
) -> bool:
    return bool(
        write_feishu and downstream_usable and freshness
        and freshness.get("state") in {"updated_with_new_items", "updated_no_new_items"}
        and (not read_outcome or read_outcome.get("full_collection_success") is True)
    )


def downstream_usability_report(
    steps: list[dict[str, Any]],
    output_dir: Path,
    today_candidates: int,
    probe_result_path: Path = DOUYIN_CDP_RESULT,
    ingestion_closure: dict[str, Any] | None = None,
    wechat_read_outcome: dict[str, Any] | None = None,
) -> dict[str, Any]:
    probe = read_json(probe_result_path)
    coverage = probe.get("coverage") if isinstance(probe.get("coverage"), dict) else {}
    invariants = coverage.get("invariants") if isinstance(coverage.get("invariants"), dict) else {}
    failed_accounts = coverage.get("failed_accounts") if isinstance(coverage.get("failed_accounts"), list) else []
    per_account_artifacts = coverage.get("per_account_artifact_counts")
    if not isinstance(per_account_artifacts, dict):
        per_account_artifacts = {}

    full_collection_failures = collection_failure_steps(steps)
    hard_failures = [
        step for step in full_collection_failures
        if not is_candidate_local_partial(step, probe)
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

    checks = {
        "canonical_profile_preflight_ok": successful_step(steps, "start/verify canonical Douyin Chrome CDP")
        and successful_step(steps, "verify canonical Douyin profile login session"),
        "planned_equals_attempted": bool(invariants.get("attempted_equals_planned")),
        "success_plus_failed_equals_attempted": bool(invariants.get("success_plus_failed_equals_attempted")),
        "account_lineage_unique_and_complete": bool(invariants.get("account_lineage_unique_and_complete")),
        "failed_accounts_have_zero_artifacts": not failed_artifact_leaks,
        "item_lineage_ok": bool((probe.get("item_lineage") or {}).get("ok")),
        "successful_account_artifacts_nonempty": bool(successful_artifact_accounts),
        "today_candidates_nonempty": today_candidates > 0,
        "no_system_level_failures": not hard_failures,
    }
    successful_items_declared = sum(int(value or 0) for name, value in per_account_artifacts.items() if name not in failure_names)
    if successful_items_declared > 0:
        closure = ingestion_closure if isinstance(ingestion_closure, dict) else {}
        feishu_identity = closure.get("feishu_03_identity") if isinstance(closure.get("feishu_03_identity"), dict) else {}
        checks.update({
            "probe_run_identity_bound": bool(closure.get("run_id") and closure.get("run_id") == probe.get("run_id")),
            "manual_artifact_identity_verified": bool(closure.get("manual_artifact_identity_verified")),
            "combined_input_bijection_ok": bool(closure.get("combined_sha256")),
            "content_items_bijection_ok": bool(closure.get("content_items_sha256")),
            "comparison_universe_inclusion_ok": int(closure.get("comparison_universe_count") or 0) > 0,
            "feishu_03_planned_identity_ok": bool((feishu_identity.get("planned_identity") or {}).get("identity_sha256")),
            "feishu_03_readback_contract_ok": bool(feishu_identity.get("ok")),
        })
    wechat_read = wechat_read_outcome if isinstance(wechat_read_outcome, dict) else {}
    if wechat_read.get("status") == "completed_with_failures":
        checks.update(wechat_candidate_partial_checks(wechat_read))
    blocked_reasons = [name for name, ok in checks.items() if not ok]
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


def select_and_validate_douyin_artifact(
    run_id: str, primary_result: Path, primary_manual: Path,
    retry_result: Path, retry_manual: Path,
) -> tuple[Path, Path, dict[str, Any] | None]:
    result_path, manual_path = (retry_result, retry_manual) if retry_result.exists() else (primary_result, primary_manual)
    probe = read_json(result_path)
    counts = ((probe.get("coverage") or {}).get("per_account_artifact_counts") or {})
    successful_declared = sum(int(value or 0) for value in counts.values())
    if not successful_declared:
        return result_path, manual_path, None
    report = validate_partial_source_artifact(probe, manual_path, expected_run_id=run_id)
    return result_path, manual_path, report


def main() -> int:
    account_gate = validate_account_limit_argv(sys.argv[1:])
    if not account_gate.ok:
        print(json.dumps(rejection_payload("daily_pipeline", account_gate), ensure_ascii=False, indent=2))
        return 2

    parser = argparse.ArgumentParser(description="Run the daily AI account radar pipeline.")
    parser.add_argument("--write-feishu", action="store_true", help="Write Feishu changes for selected steps: URL resolver writes 03/updates 02; 今日候选池 writes 04 and refreshes 00.")
    parser.add_argument("--no-fetch-aihot", action="store_true", help="Skip AIHOT network fetch and use manual samples only.")
    parser.add_argument("--manual", default=str(DEFAULT_MANUAL), help="Path to JSONL manual content items.")
    parser.add_argument("--resolve-url-intake", action="store_true", help="Resolve URLs from Feishu 02 URL投喂入口 into ContentItem rows before sampling.")
    parser.add_argument("--include-resolved-url-intake", action="store_true", help="Testing mode: reuse already parsed Feishu 02 URLs as candidates without changing default intake behavior.")
    parser.add_argument("--url-file", help="Resolve URLs from a local text file into ContentItem rows before sampling.")
    parser.add_argument("--fetch-wechat-fulltext-provider", action="store_true", help="Explicit P1 mode: fetch local WeChat fulltext provider rows into the candidate pool. Default is off.")
    parser.add_argument("--wechat-fulltext-provider-config", default=str(DEFAULT_WECHAT_FULLTEXT_PROVIDER_CONFIG), help="Config for explicit WeChat fulltext provider intake.")
    parser.add_argument("--wechat-fulltext-provider", default="", help="Provider id/name to fetch, e.g. wewe-rss. Only used with explicit WeChat fulltext provider mode.")
    parser.add_argument("--wechat-feed-limit", type=int, default=5, help="Max articles to fetch from the explicit WeChat fulltext provider.")
    parser.add_argument("--fetch-douyin-cdp-source-watch", action="store_true", help="Compatibility flag: Douyin homepage title/caption sampling is now attempted by default unless --no-fetch-douyin is set.")
    parser.add_argument("--no-fetch-douyin-cdp-source-watch", "--no-fetch-douyin", dest="no_fetch_douyin_cdp_source_watch", action="store_true", help="Skip daily Douyin homepage title/caption sampling.")
    parser.add_argument("--douyin-cdp", default=os.getenv("DOUYIN_CDP_URL", "http://127.0.0.1:9333"), help="Chrome DevTools endpoint for explicit Douyin homepage probe.")
    parser.add_argument("--douyin-account-limit", type=int, default=0, help="Max Douyin accounts to probe; 0 means every eligible account.")
    parser.add_argument("--douyin-video-limit", type=int, default=3, help="Max videos per Douyin account when --fetch-douyin-cdp-source-watch is enabled.")
    parser.add_argument("--douyin-retries", type=int, default=2, help="Retries per Douyin account before skipping to the next account.")
    parser.add_argument("--douyin-verification-action", choices=["foreground", "log-only"], default="foreground", help="When a Douyin account needs login/verification, foreground the dedicated Chrome for user handling or only log it.")
    parser.add_argument("--douyin-verification-wait-seconds", type=float, default=60.0, help="Seconds to wait after foregrounding Chrome for Douyin login/verification before retrying affected accounts.")
    parser.add_argument("--force-fetch-douyin", action="store_true", help="Force Douyin homepage collection even if today's source cache exists. Use only when testing collection logic or after manually changing links/login.")
    parser.add_argument("--no-reuse-source-cache", action="store_true", help="Disable same-day source cache reuse. Normally do not use this for Douyin to avoid account risk.")
    parser.add_argument("--include-douyin-transcripts", action="store_true", help="Explicit P1 mode: include already transcribed Douyin ContentItems. Does not call ASR.")
    parser.add_argument(
        "--defer-editorial",
        action="store_true",
        help="Stop after raw candidate generation so the outer Codex automation can apply ai-account-editorial-director without nested codex exec.",
    )
    args = parser.parse_args()
    args.douyin_account_limit = account_gate.value

    load_local_env()

    if args.write_feishu or args.resolve_url_intake or args.include_resolved_url_intake:
        require_feishu_env()

    py = sys.executable
    steps: list[dict[str, Any]] = []
    manual_path = args.manual
    manual_inputs: list[Path] = [Path(args.manual)]
    douyin_source_lineage: dict[str, Any] | None = None
    active_douyin_probe_result = DOUYIN_CDP_RESULT
    wechat_freshness: dict[str, Any] | None = None
    wechat_read_outcome: dict[str, Any] | None = None
    run_id = new_run_id()
    run_started_at_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    step_env = os.environ.copy()
    step_env["RUN_ID"] = run_id
    step_env["AI_ACCOUNT_RADAR_RUN_ID"] = run_id

    if args.resolve_url_intake or args.include_resolved_url_intake or args.url_file:
        resolver_cmd = [py, str(ROOT / "scripts" / "url_content_resolver.py"), "--out", str(URL_RESOLVED)]
        if args.resolve_url_intake or args.include_resolved_url_intake:
            resolver_cmd.append("--feishu-intake")
        if args.include_resolved_url_intake:
            resolver_cmd.append("--include-resolved-url-intake")
        if args.url_file:
            resolver_cmd.extend(["--file", args.url_file])
        if args.write_feishu:
            resolver_cmd.append("--write-feishu")
        steps.append(run_step("resolve URL intake into ContentItem rows", resolver_cmd, env=step_env))
        if steps[-1]["returncode"] != 0:
            log_path = write_run_log(steps, "write-feishu" if args.write_feishu else "dry-run", run_id)
            print(json.dumps({"ok": False, "log": str(log_path)}, ensure_ascii=False, indent=2))
            return steps[-1]["returncode"]
        manual_inputs = [URL_RESOLVED_MANUAL]

    if args.fetch_wechat_fulltext_provider or args.wechat_fulltext_provider:
        refresh_attempt_path = OUT / "provider_health" / run_id / "wewe_refresh_attempt.json"
        refresh_cmd = [py, str(ROOT / "scripts" / "wewe_provider_refresh.py"), "--run-id", run_id, "--run-started-at-ms", str(run_started_at_ms), "--out", str(refresh_attempt_path)]
        if not args.write_feishu:
            refresh_cmd.append("--check-only")
        steps.append(run_step("request fixed wewe-rss provider refresh", refresh_cmd, env=step_env))
        refresh_result = stdout_json(steps[-1])
        if refresh_result.get("status") == "refresh_required":
            log_path = write_run_log(steps, "dry-run", run_id)
            print(json.dumps({"ok": False, "wechat_freshness": {"state": "refresh_required", "check_only": True}, "log": str(log_path)}, ensure_ascii=False, indent=2))
            return 4
        if steps[-1]["returncode"] != 0:
            log_path = write_run_log(steps, "write-feishu" if args.write_feishu else "dry-run", run_id)
            print(json.dumps({"ok": False, "wechat_freshness": {"state": "provider_failed"}, "log": str(log_path)}, ensure_ascii=False, indent=2))
            return steps[-1]["returncode"]
        health_cmd = [py, str(ROOT / "scripts" / "wewe_provider_health.py"), "--check-only", "--run-id", run_id, "--run-started-at-ms", str(run_started_at_ms), "--refresh-result", str(refresh_attempt_path)]
        steps.append(run_step("verify canonical wewe-rss account and refresh freshness", health_cmd, env=step_env))
        wechat_freshness = stdout_json(steps[-1])
        if steps[-1]["returncode"] != 0:
            log_path = write_run_log(steps, "write-feishu" if args.write_feishu else "dry-run", run_id)
            print(json.dumps({"ok": False, "wechat_freshness": wechat_freshness, "log": str(log_path)}, ensure_ascii=False, indent=2))
            return steps[-1]["returncode"]
        if wechat_freshness.get("state") == "updated_no_new_items":
            provider_cmd = []
        else:
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
            if steps[-1]["returncode"] != 0:
                log_path = write_run_log(steps, "write-feishu" if args.write_feishu else "dry-run", run_id)
                print(json.dumps({"ok": False, "log": str(log_path)}, ensure_ascii=False, indent=2))
                return steps[-1]["returncode"]
            if wechat_read_outcome.get("status") == "completed_with_failures":
                steps[-1]["optional_failed"] = True
                steps[-1]["candidate_local_partial"] = True
                steps[-1]["note"] = "WeChat candidate-local read failures were isolated; successful truthful rows continue."
            manual_inputs.append(WECHAT_FULLTEXT_RESOLVED_MANUAL)

    fetch_douyin = not args.no_fetch_douyin_cdp_source_watch or args.fetch_douyin_cdp_source_watch
    if fetch_douyin:
        if not canonical_douyin_cdp(args.douyin_cdp):
            steps.append({
                "name": "verify canonical Douyin CDP endpoint",
                "command": ["canonical-cdp-check", args.douyin_cdp],
                "started_at": datetime.now().isoformat(timespec="seconds"),
                "returncode": 3,
                "stdout": "",
                "stderr": "non_canonical_douyin_cdp: expected http://127.0.0.1:9333",
            })
            write_douyin_cache_manifest("preflight_failed", run_id, steps, note="Non-canonical Douyin CDP endpoint was rejected.")
            chrome_step = steps[-1]
            preflight_step = None
        else:
            chrome_cmd = [
                py,
                str(ROOT / "scripts" / "start_douyin_cdp_chrome.py"),
                "--port",
                str(cdp_port(args.douyin_cdp)),
            ]
            chrome_step = run_step("start/verify canonical Douyin Chrome CDP", chrome_cmd, env=step_env)
            steps.append(chrome_step)
            preflight_step: dict[str, Any] | None = None
            if chrome_step["returncode"] == 0:
                preflight_step = run_step("verify canonical Douyin profile login session", [
                    py,
                    str(ROOT / "scripts" / "check_douyin_session.py"),
                    "--port",
                    str(cdp_port(args.douyin_cdp)),
                ], env=step_env)
                steps.append(preflight_step)

        douyin_gate_ok = douyin_probe_allowed(chrome_step, preflight_step)
        reuse_douyin_cache = douyin_gate_ok and not args.no_reuse_source_cache and not args.force_fetch_douyin and douyin_cache_ready()
        if not douyin_gate_ok:
            write_douyin_cache_manifest(
                "preflight_failed",
                run_id,
                steps,
                note="Canonical Douyin profile identity/login preflight failed; Douyin probe and cache reuse were blocked.",
            )
        elif reuse_douyin_cache:
            cached_paths = cached_douyin_manual_paths()
            manual_inputs.extend(cached_paths)
            cache_step = {
                "name": "reuse today's Douyin source cache",
                "command": ["source-cache", "douyin_cdp_source_watch", "--date", today_key()],
                "started_at": datetime.now().isoformat(timespec="seconds"),
                "returncode": 0,
                "stdout": f"Reused {len(cached_paths)} cached Douyin ContentItem file(s). Use --force-fetch-douyin only when testing collection logic.",
                "stderr": "",
            }
            print("\n== reuse today's Douyin source cache ==")
            print(cache_step["stdout"])
            steps.append(cache_step)
        else:
            douyin_cmd = [
                "node",
                str(ROOT / "scripts" / "douyin_cdp_source_watch_probe.mjs"),
                "--cdp",
                args.douyin_cdp,
                "--account-limit",
                str(args.douyin_account_limit),
                "--video-limit",
                str(args.douyin_video_limit),
                "--retries",
                str(args.douyin_retries),
            ]
            douyin_step = run_optional_step("fetch daily Douyin homepage title/caption samples through Chrome CDP", douyin_cmd, env=step_env)
            steps.append(douyin_step)
            verification_rows = douyin_verification_rows(OUT / "spikes" / "douyin_cdp_source_watch_probe" / "cdp_probe_results.json")
            if verification_rows and args.douyin_verification_action == "foreground":
                first_homepage = str(verification_rows[0].get("homepage_url") or "https://www.douyin.com/")
                foreground_cmd = [
                    py,
                    str(ROOT / "scripts" / "start_douyin_cdp_chrome.py"),
                    "--port",
                    str(cdp_port(args.douyin_cdp)),
                    "--foreground",
                    "--url",
                    first_homepage,
                    "--wait-seconds",
                    str(args.douyin_verification_wait_seconds),
                ]
                steps.append(run_optional_step("foreground Douyin Chrome for login/verification", foreground_cmd, env=step_env))
                retry_cmd = [
                    "node",
                    str(ROOT / "scripts" / "douyin_cdp_source_watch_probe.mjs"),
                    "--cdp",
                    args.douyin_cdp,
                    "--out-dir",
                    str(DOUYIN_CDP_RETRY_DIR),
                    "--account-limit",
                    "0",
                    "--video-limit",
                    str(args.douyin_video_limit),
                    "--retries",
                    str(args.douyin_retries),
                ]
                steps.append(run_optional_step("retry full Douyin account plan after user verification", retry_cmd, env=step_env))
            try:
                selected_result, selected_manual, douyin_source_lineage = select_and_validate_douyin_artifact(
                    run_id, DOUYIN_CDP_RESULT, DOUYIN_CDP_RESOLVED_MANUAL,
                    DOUYIN_CDP_RETRY_RESULT, DOUYIN_CDP_RETRY_MANUAL,
                )
            except LineageError as exc:
                douyin_step["lineage_error"] = str(exc)
                steps.append({"name": "verify Douyin successful-item source artifact", "command": ["lineage-check"], "started_at": datetime.now().isoformat(timespec="seconds"), "returncode": 3, "stdout": "", "stderr": str(exc)})
                log_path = write_run_log(steps, "write-feishu" if args.write_feishu else "dry-run", run_id)
                print(json.dumps({"ok": False, "status": "failed_or_partial", "error": str(exc), "log": str(log_path)}, ensure_ascii=False, indent=2))
                return 3
            active_douyin_probe_result = selected_result
            if douyin_source_lineage:
                manual_inputs.append(selected_manual)
                douyin_step["partial_success_ingested"] = True
                douyin_step["successful_item_count"] = douyin_source_lineage["successful_item_count"]
            if not douyin_step.get("optional_failed") and DOUYIN_CDP_RESOLVED_MANUAL.exists() and file_modified_today(DOUYIN_CDP_RESOLVED_MANUAL):
                write_douyin_cache_manifest("ok", run_id, steps, note="Douyin source collection completed; later same-day runs should reuse this cache.")
            else:
                write_douyin_cache_manifest("failed", run_id, steps, note="Douyin source collection did not produce a fresh manual JSONL; same-day cache will not be reused.")

    if args.include_douyin_transcripts:
        manual_inputs.append(DOUYIN_TRANSCRIPTS_MANUAL)

    if len(manual_inputs) > 1:
        manual_path = str(combine_manual_jsonl(manual_inputs, COMBINED_MANUAL))
    elif manual_inputs:
        manual_path = str(manual_inputs[0])

    sampler_cmd = [py, str(ROOT / "scripts" / "content_sampler.py"), "--manual", manual_path, "--run-id", run_id]
    lineage_manifest_path = OUT / "lineage" / run_id / "douyin_source_lineage.json"
    if douyin_source_lineage:
        write_json(lineage_manifest_path, {"run_id": run_id, "source_report": douyin_source_lineage, "combined_path": str(Path(manual_path).resolve())})
        sampler_cmd.extend(["--source-lineage-manifest", str(lineage_manifest_path)])
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
    sampler_result = read_json(output_dir / "content_sampler_log.json")
    ingestion_lineage = sampler_result.get("source_ingestion_closure") if isinstance(sampler_result.get("source_ingestion_closure"), dict) else None
    if douyin_source_lineage and not ingestion_lineage:
        steps.append({"name": "verify Douyin successful-item downstream lineage", "command": ["lineage-check"], "started_at": datetime.now().isoformat(timespec="seconds"), "returncode": 3, "stdout": "", "stderr": "source_ingestion_closure_missing"})
    today10_path = output_dir / "today_10_topics.csv"
    generated_count = today10_count(today10_path)
    if ingestion_lineage:
        ingestion_lineage.update({"run_id": douyin_source_lineage.get("run_id"), "manual_artifact_identity_verified": douyin_source_lineage.get("manual_artifact_identity_verified")})
    downstream_report = downstream_usability_report(
        steps, output_dir, generated_count, active_douyin_probe_result, ingestion_lineage,
        wechat_read_outcome=wechat_read_outcome,
    )
    downstream_report["douyin_partial_ingestion"] = douyin_source_lineage or {}
    downstream_report["ingestion_bijection"] = ingestion_lineage or {}
    downstream_report["wechat_freshness"] = wechat_freshness or {}
    downstream_report["wechat_read_outcome"] = wechat_read_outcome or {}
    watermark_closure = ingestion_lineage or {
            "feishu_03_identity": {
                "ok": bool(((sampler_result.get("feishu_content_ledger") or {}).get("read_back_identity") or {}).get("ok")),
                "mode": "write",
            }
        }
    watermark_pending = wechat_watermark_allowed(
        write_feishu=args.write_feishu,
        downstream_usable=bool(downstream_report.get("downstream_usable")),
        freshness=wechat_freshness,
        read_outcome=wechat_read_outcome,
    )
    if generated_count == 0:
        failures = collection_failure_steps(steps)
        log_path = write_run_log(
            steps,
            "write-feishu" if args.write_feishu else "dry-run",
            run_id,
            downstream_report=downstream_report,
        )
        if watermark_pending:
            watermark_path = commit_wechat_success_watermark(wechat_freshness or {}, downstream_report=downstream_report, ingestion_closure=watermark_closure, run_id=run_id)
            downstream_report["wechat_freshness"]["watermark_committed"] = True
            downstream_report["wechat_freshness"]["watermark_path"] = str(watermark_path)
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
        downstream_report = downstream_usability_report(steps, output_dir, generated_count, active_douyin_probe_result, ingestion_lineage)
        log_path = write_run_log(
            steps,
            "write-feishu" if args.write_feishu else "dry-run",
            run_id,
            downstream_report=downstream_report,
        )
        if watermark_pending:
            watermark_path = commit_wechat_success_watermark(wechat_freshness or {}, downstream_report=downstream_report, ingestion_closure=watermark_closure, run_id=run_id)
            downstream_report["wechat_freshness"] = dict(wechat_freshness or {})
            downstream_report["wechat_freshness"]["watermark_committed"] = True
            downstream_report["wechat_freshness"]["watermark_path"] = str(watermark_path)
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
        return deferred_exit_code(steps)

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
    sync_enriched_candidate_mirrors(today10_path, editorial_report_path, args.write_feishu)

    dry_run_cmd = [py, str(ROOT / "scripts" / "push_today10_to_feishu.py"), "--input", str(today10_path)]
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

        refresh_cmd = [py, str(ROOT / "scripts" / "refresh_console_daily.py")]
        steps.append(run_step("refresh Feishu 00 主控台", refresh_cmd, env=step_env))

    ok = all(step["returncode"] == 0 for step in steps)
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
    if watermark_pending and ok:
        watermark_path = commit_wechat_success_watermark(wechat_freshness or {}, downstream_report=downstream_report, ingestion_closure=watermark_closure, run_id=run_id)
        final_report["wechat_freshness"]["watermark_committed"] = True
        final_report["wechat_freshness"]["watermark_path"] = str(watermark_path)
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
