#!/usr/bin/env python3
"""Daily entrypoint for AI account radar.

Default mode is dry-run: generate content objects, breakdowns and 今日10,
then print the rows that would be written to Feishu. Use --write-feishu to
write only 今日10 to 04 分析与选题 and refresh 00 主控台.
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


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output"
LOG_DIR = OUT / "logs"
DEFAULT_MANUAL = ROOT / "data" / "manual" / "content_items.example.jsonl"
URL_RESOLVED = OUT / "url_content_items.jsonl"
URL_RESOLVED_MANUAL = OUT / "url_content_items_manual.jsonl"


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


def require_feishu_env() -> None:
    missing = [name for name in ["FEISHU_APP_ID", "FEISHU_APP_SECRET", "FEISHU_BASE_APP_TOKEN"] if not os.getenv(name)]
    if missing:
        raise SystemExit(f"--write-feishu requires environment variables: {', '.join(missing)}")


def write_run_log(steps: list[dict[str, Any]], mode: str, run_id: str = "") -> Path:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    path = LOG_DIR / f"daily_pipeline_{datetime.now().strftime('%Y-%m-%d')}.json"
    payload = {
        "ok": all(step["returncode"] == 0 for step in steps),
        "mode": mode,
        "run_id": run_id,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "steps": steps,
        "outputs": {
            "today_10_topics": str(OUT / "today_10_topics.csv"),
            "today_10_markdown": str(OUT / "daily_reports" / f"today_10_topics_{datetime.now().strftime('%Y-%m-%d')}.md"),
        },
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def today10_count() -> int:
    path = OUT / "today_10_topics.csv"
    if not path.exists() or not path.read_text(encoding="utf-8-sig").strip():
        return 0
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return len(list(csv.DictReader(handle)))


def new_run_id() -> str:
    return f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the daily AI account radar pipeline.")
    parser.add_argument("--write-feishu", action="store_true", help="Write Feishu changes for selected steps: URL resolver writes 03/updates 02; 今日10 writes 04 and refreshes 00.")
    parser.add_argument("--no-fetch-aihot", action="store_true", help="Skip AIHOT network fetch and use manual samples only.")
    parser.add_argument("--manual", default=str(DEFAULT_MANUAL), help="Path to JSONL manual content items.")
    parser.add_argument("--resolve-url-intake", action="store_true", help="Resolve URLs from Feishu 02 URL投喂入口 into ContentItem rows before sampling.")
    parser.add_argument("--include-resolved-url-intake", action="store_true", help="Testing mode: reuse already parsed Feishu 02 URLs as candidates without changing default intake behavior.")
    parser.add_argument("--url-file", help="Resolve URLs from a local text file into ContentItem rows before sampling.")
    args = parser.parse_args()

    if args.write_feishu or args.resolve_url_intake or args.include_resolved_url_intake:
        require_feishu_env()

    py = sys.executable
    steps: list[dict[str, Any]] = []
    manual_path = args.manual
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
        manual_path = str(URL_RESOLVED_MANUAL)

    sampler_cmd = [py, str(ROOT / "scripts" / "content_sampler.py"), "--manual", manual_path, "--run-id", run_id]
    if args.no_fetch_aihot:
        sampler_cmd.append("--no-fetch-aihot")
    if args.write_feishu:
        sampler_cmd.append("--write-feishu")
    steps.append(run_step("generate content breakdowns and 今日10", sampler_cmd, env=step_env if args.write_feishu else None))
    if steps[-1]["returncode"] != 0:
        log_path = write_run_log(steps, "write-feishu" if args.write_feishu else "dry-run", run_id)
        print(json.dumps({"ok": False, "log": str(log_path)}, ensure_ascii=False, indent=2))
        return steps[-1]["returncode"]

    generated_count = today10_count()
    if generated_count == 0:
        log_path = write_run_log(steps, "write-feishu" if args.write_feishu else "dry-run", run_id)
        print(json.dumps({
            "ok": True,
            "mode": "write-feishu" if args.write_feishu else "dry-run",
            "today_10_topics": 0,
            "wrote_feishu": False,
            "log": str(log_path),
            "note": "No 今日10 topics generated. Check URL parsing failures in output/content_items.csv and output/content_breakdowns.csv.",
        }, ensure_ascii=False, indent=2))
        return 0

    dry_run_cmd = [py, str(ROOT / "scripts" / "push_today10_to_feishu.py")]
    steps.append(run_step("dry-run 今日10 Feishu write", dry_run_cmd))
    if steps[-1]["returncode"] != 0:
        log_path = write_run_log(steps, "write-feishu" if args.write_feishu else "dry-run", run_id)
        print(json.dumps({"ok": False, "log": str(log_path)}, ensure_ascii=False, indent=2))
        return steps[-1]["returncode"]

    if args.write_feishu:
        write_cmd = [py, str(ROOT / "scripts" / "push_today10_to_feishu.py"), "--write", "--run-id", run_id]
        steps.append(run_step("write 今日10 to Feishu 04 分析与选题", write_cmd, env=step_env))
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
        "log": str(log_path),
        "wrote_feishu": bool(args.write_feishu and ok),
    }, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
