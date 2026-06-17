#!/usr/bin/env python3
"""Daily entrypoint for AI account radar.

Default mode is dry-run: generate content objects, breakdowns and 今日候选池,
then print the rows that would be written to Feishu. Use --write-feishu to
write only the daily candidate pool to 04 分析与选题 and refresh 00 主控台.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import csv
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from local_env import load_local_env

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output"
LOG_DIR = OUT / "logs"
DEFAULT_MANUAL = ROOT / "data" / "manual" / "content_items.example.jsonl"
URL_RESOLVED = OUT / "url_content_items.jsonl"
URL_RESOLVED_MANUAL = OUT / "url_content_items_manual.jsonl"
WECHAT_FEED_RESOLVED = OUT / "wechat_feed_content_items.jsonl"
WECHAT_FEED_RESOLVED_MANUAL = OUT / "wechat_feed_content_items_manual.jsonl"
WECHAT_FULLTEXT_RESOLVED_MANUAL = OUT / "wechat_fulltext_provider_items.jsonl"
DOUYIN_CDP_RESOLVED_MANUAL = OUT / "spikes" / "douyin_cdp_source_watch_probe" / "content_items_manual.jsonl"
DOUYIN_CDP_RETRY_DIR = OUT / "spikes" / "douyin_cdp_source_watch_probe_verification_retry"
DOUYIN_CDP_RETRY_MANUAL = DOUYIN_CDP_RETRY_DIR / "content_items_manual.jsonl"
DOUYIN_TRANSCRIPTS_MANUAL = OUT / "spikes" / "douyin_transcripts" / "transcribed_content_items.jsonl"
COMBINED_MANUAL = OUT / "daily_pipeline_manual_combined.jsonl"
DEFAULT_WECHAT_FEED_CONFIG = ROOT / "config" / "wechat_feed_candidates.yaml"
DEFAULT_WECHAT_FULLTEXT_PROVIDER_CONFIG = ROOT / "config" / "wechat_fulltext_provider.example.yaml"

load_local_env()


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


def today10_count(path: Path) -> int:
    if not path.exists() or not path.read_text(encoding="utf-8-sig").strip():
        return 0
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return len(list(csv.DictReader(handle)))


def new_run_id() -> str:
    return f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}"


def cdp_port(cdp_url: str) -> int:
    parsed = urlparse(cdp_url)
    return parsed.port or 9333


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


def csv_names(rows: list[dict[str, Any]]) -> str:
    names = []
    for row in rows:
        name = str(row.get("account_name") or "").strip()
        if name and name not in names:
            names.append(name)
    return ",".join(names)


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
    parser = argparse.ArgumentParser(description="Run the daily AI account radar pipeline.")
    parser.add_argument("--write-feishu", action="store_true", help="Write Feishu changes for selected steps: URL resolver writes 03/updates 02; 今日候选池 writes 04 and refreshes 00.")
    parser.add_argument("--no-fetch-aihot", action="store_true", help="Skip AIHOT network fetch and use manual samples only.")
    parser.add_argument("--manual", default=str(DEFAULT_MANUAL), help="Path to JSONL manual content items.")
    parser.add_argument("--resolve-url-intake", action="store_true", help="Resolve URLs from Feishu 02 URL投喂入口 into ContentItem rows before sampling.")
    parser.add_argument("--include-resolved-url-intake", action="store_true", help="Testing mode: reuse already parsed Feishu 02 URLs as candidates without changing default intake behavior.")
    parser.add_argument("--url-file", help="Resolve URLs from a local text file into ContentItem rows before sampling.")
    parser.add_argument("--fetch-wechat-feed", action="store_true", help="Deprecated: public WeChat feed is discovery-only and no longer enters the candidate pool. Use --fetch-wechat-fulltext-provider.")
    parser.add_argument("--fetch-wechat-fulltext-provider", action="store_true", help="Explicit P1 mode: fetch local WeChat fulltext provider rows into the candidate pool. Default is off.")
    parser.add_argument("--wechat-feed-config", default=str(DEFAULT_WECHAT_FEED_CONFIG), help="Config for explicit WeChat feed intake.")
    parser.add_argument("--wechat-fulltext-provider-config", default=str(DEFAULT_WECHAT_FULLTEXT_PROVIDER_CONFIG), help="Config for explicit WeChat fulltext provider intake.")
    parser.add_argument("--wechat-fulltext-provider", default="", help="Provider id/name to fetch, e.g. wewe-rss. Only used with explicit WeChat fulltext provider mode.")
    parser.add_argument("--wechat-feed-limit", type=int, default=5, help="Max articles to fetch per WeChat feed when --fetch-wechat-feed is enabled.")
    parser.add_argument("--fetch-douyin-cdp-source-watch", action="store_true", help="Compatibility flag: Douyin homepage title/caption sampling is now attempted by default unless --no-fetch-douyin is set.")
    parser.add_argument("--no-fetch-douyin-cdp-source-watch", "--no-fetch-douyin", dest="no_fetch_douyin_cdp_source_watch", action="store_true", help="Skip daily Douyin homepage title/caption sampling.")
    parser.add_argument("--douyin-cdp", default=os.getenv("DOUYIN_CDP_URL", "http://127.0.0.1:9333"), help="Chrome DevTools endpoint for explicit Douyin homepage probe.")
    parser.add_argument("--douyin-account-limit", type=int, default=12, help="Max Douyin accounts to probe in daily source-watch sampling.")
    parser.add_argument("--douyin-video-limit", type=int, default=3, help="Max videos per Douyin account when --fetch-douyin-cdp-source-watch is enabled.")
    parser.add_argument("--douyin-retries", type=int, default=2, help="Retries per Douyin account before skipping to the next account.")
    parser.add_argument("--douyin-verification-action", choices=["foreground", "log-only"], default="foreground", help="When a Douyin account needs login/verification, foreground the dedicated Chrome for user handling or only log it.")
    parser.add_argument("--douyin-verification-wait-seconds", type=float, default=60.0, help="Seconds to wait after foregrounding Chrome for Douyin login/verification before retrying affected accounts.")
    parser.add_argument("--include-douyin-transcripts", action="store_true", help="Explicit P1 mode: include already transcribed Douyin ContentItems. Does not call ASR.")
    args = parser.parse_args()

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

    if args.fetch_wechat_feed:
        steps.append({
            "name": "skip deprecated WeChat public feed",
            "command": ["--fetch-wechat-feed"],
            "started_at": datetime.now().isoformat(timespec="seconds"),
            "returncode": 0,
            "stdout": "Wechat2RSS public feed is discovery-only and no longer enters the daily candidate pool. Use --fetch-wechat-fulltext-provider / --wechat-fulltext-provider wewe-rss for fulltext.",
            "stderr": "",
        })
        print("\n== skip deprecated WeChat public feed ==")
        print("Wechat2RSS public feed is discovery-only and no longer enters the daily candidate pool. Use --fetch-wechat-fulltext-provider / --wechat-fulltext-provider wewe-rss for fulltext.")

    if args.fetch_wechat_fulltext_provider or args.wechat_fulltext_provider:
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
        chrome_cmd = [
            py,
            str(ROOT / "scripts" / "start_douyin_cdp_chrome.py"),
            "--port",
            str(cdp_port(args.douyin_cdp)),
        ]
        chrome_step = run_optional_step("start/reuse background Douyin Chrome CDP", chrome_cmd, env=step_env)
        steps.append(chrome_step)
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
            retry_names = csv_names(verification_rows)
            retry_cmd = [
                "node",
                str(ROOT / "scripts" / "douyin_cdp_source_watch_probe.mjs"),
                "--cdp",
                args.douyin_cdp,
                "--out-dir",
                str(DOUYIN_CDP_RETRY_DIR),
                "--account-limit",
                str(max(len(verification_rows), 1)),
                "--video-limit",
                str(args.douyin_video_limit),
                "--retries",
                str(args.douyin_retries),
                "--only-account-names",
                retry_names,
            ]
            steps.append(run_optional_step("retry Douyin accounts after user verification", retry_cmd, env=step_env))
        if not douyin_step.get("optional_failed") and DOUYIN_CDP_RESOLVED_MANUAL.exists():
            manual_inputs.append(DOUYIN_CDP_RESOLVED_MANUAL)
        if DOUYIN_CDP_RETRY_MANUAL.exists():
            manual_inputs.append(DOUYIN_CDP_RETRY_MANUAL)

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
        log_path = write_run_log(steps, "write-feishu" if args.write_feishu else "dry-run", run_id)
        print(json.dumps({
            "ok": True,
            "mode": "write-feishu" if args.write_feishu else "dry-run",
            "today_10_topics": 0,
            "wrote_feishu": False,
            "log": str(log_path),
            "run_output_dir": str(output_dir),
            "note": f"No daily topic candidates generated. Check URL parsing failures in {output_dir / 'content_items.csv'} and {output_dir / 'content_breakdowns.csv'}.",
        }, ensure_ascii=False, indent=2))
        return 0

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
