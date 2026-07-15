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
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from local_env import load_local_env
from full_account_collection_contract import rejection_payload, validate_account_limit_argv

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


def write_run_log(steps: list[dict[str, Any]], mode: str, run_id: str = "") -> Path:
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
    parser.add_argument("--no-auto-start-wewe-rss", action="store_true", help="Do not auto-start the local wewe-rss Docker provider before WeChat fulltext fetch.")
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
    run_id = new_run_id()
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
        provider_hint = str(args.wechat_fulltext_provider or "wewe_rss_local").lower()
        should_start_wewe = not args.no_auto_start_wewe_rss and ("wewe" in provider_hint or not args.wechat_fulltext_provider)
        if should_start_wewe:
            start_wewe_cmd = [
                py,
                str(ROOT / "scripts" / "start_wewe_rss.py"),
            ]
            steps.append(run_step("start/check local wewe-rss fulltext provider", start_wewe_cmd, env=step_env))
            if steps[-1]["returncode"] != 0:
                log_path = write_run_log(steps, "write-feishu" if args.write_feishu else "dry-run", run_id)
                print(json.dumps({"ok": False, "log": str(log_path)}, ensure_ascii=False, indent=2))
                return steps[-1]["returncode"]

        provider_cmd = [
            py,
            str(ROOT / "scripts" / "wechat_fulltext_provider_probe.py"),
            "--config",
            args.wechat_fulltext_provider_config,
            "--out",
            str(WECHAT_FULLTEXT_RESOLVED_MANUAL),
            "--csv",
            str(OUT / "wechat_fulltext_provider_items.csv"),
            "--dry-run",
        ]
        if args.wechat_fulltext_provider:
            provider_cmd.extend(["--provider-id", args.wechat_fulltext_provider])
        if args.wechat_feed_limit:
            provider_cmd.extend(["--limit", str(args.wechat_feed_limit)])
        steps.append(run_step("fetch explicit WeChat fulltext provider into ContentItem rows", provider_cmd, env=step_env))
        if steps[-1]["returncode"] != 0:
            log_path = write_run_log(steps, "write-feishu" if args.write_feishu else "dry-run", run_id)
            print(json.dumps({"ok": False, "log": str(log_path)}, ensure_ascii=False, indent=2))
            return steps[-1]["returncode"]
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
            if not douyin_step.get("optional_failed") and DOUYIN_CDP_RESOLVED_MANUAL.exists():
                manual_inputs.append(DOUYIN_CDP_RESOLVED_MANUAL)
            if DOUYIN_CDP_RETRY_MANUAL.exists() and file_modified_today(DOUYIN_CDP_RETRY_MANUAL):
                manual_inputs.append(DOUYIN_CDP_RETRY_MANUAL)
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
    if generated_count == 0:
        failures = collection_failure_steps(steps)
        log_path = write_run_log(steps, "write-feishu" if args.write_feishu else "dry-run", run_id)
        print(json.dumps({
            "ok": not failures,
            "status": "failed_or_partial" if failures else "completed_empty",
            "mode": "write-feishu" if args.write_feishu else "dry-run",
            "today_10_topics": 0,
            "wrote_feishu": False,
            "log": str(log_path),
            "run_output_dir": str(output_dir),
            "note": f"No daily topic candidates generated. Check URL parsing failures in {output_dir / 'content_items.csv'} and {output_dir / 'content_breakdowns.csv'}.",
        }, ensure_ascii=False, indent=2))
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
        log_path = write_run_log(steps, "write-feishu" if args.write_feishu else "dry-run", run_id)
        print(json.dumps({
            "ok": False,
            "deferred_editorial": True,
            "collection_ok": collection_ok,
            "collection_status": "deferred_editorial" if collection_ok else "failed_or_partial",
            "source_failure_steps": [str(step.get("name") or "") for step in source_failures],
            "mode": "write-feishu" if args.write_feishu else "dry-run",
            "run_id": run_id,
            "run_output_dir": str(output_dir),
            "today_10_topics": str(today10_path),
            "log": str(log_path),
            "note": "Outer Codex automation must apply ai-account-editorial-director and finalize the run before 10:00 card sending.",
        }, ensure_ascii=False, indent=2))
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

    log_path = write_run_log(steps, "write-feishu" if args.write_feishu else "dry-run", run_id)
    ok = all(step["returncode"] == 0 for step in steps)
    print(json.dumps({
        "ok": ok,
        "mode": "write-feishu" if args.write_feishu else "dry-run",
        "run_id": run_id,
        "run_output_dir": str(output_dir),
        "today_10_topics": str(today10_path),
        "log": str(log_path),
        "wrote_feishu": bool(args.write_feishu and ok),
    }, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
