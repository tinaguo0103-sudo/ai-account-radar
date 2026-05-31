#!/usr/bin/env python3
"""Daily entrypoint for AI account radar.

Default mode is dry-run: generate content objects, breakdowns and 今日10,
then print the rows that would be written to Feishu. Use --write-feishu to
write only 今日10 to 03 分析与选题 and refresh 00 主控台.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output"
LOG_DIR = OUT / "logs"
DEFAULT_MANUAL = ROOT / "data" / "manual" / "content_items.example.jsonl"


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


def write_run_log(steps: list[dict[str, Any]], mode: str) -> Path:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    path = LOG_DIR / f"daily_pipeline_{datetime.now().strftime('%Y-%m-%d')}.json"
    payload = {
        "ok": all(step["returncode"] == 0 for step in steps),
        "mode": mode,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "steps": steps,
        "outputs": {
            "today_10_topics": str(OUT / "today_10_topics.csv"),
            "today_10_markdown": str(OUT / "daily_reports" / f"today_10_topics_{datetime.now().strftime('%Y-%m-%d')}.md"),
        },
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the daily AI account radar pipeline.")
    parser.add_argument("--write-feishu", action="store_true", help="Write only 今日10 to Feishu and refresh 00 主控台.")
    parser.add_argument("--no-fetch-aihot", action="store_true", help="Skip AIHOT network fetch and use manual samples only.")
    parser.add_argument("--manual", default=str(DEFAULT_MANUAL), help="Path to JSONL manual content items.")
    args = parser.parse_args()

    if args.write_feishu:
        require_feishu_env()

    py = sys.executable
    steps: list[dict[str, Any]] = []

    sampler_cmd = [py, str(ROOT / "scripts" / "content_sampler.py"), "--manual", args.manual]
    if args.no_fetch_aihot:
        sampler_cmd.append("--no-fetch-aihot")
    steps.append(run_step("generate content breakdowns and 今日10", sampler_cmd))
    if steps[-1]["returncode"] != 0:
        log_path = write_run_log(steps, "write-feishu" if args.write_feishu else "dry-run")
        print(json.dumps({"ok": False, "log": str(log_path)}, ensure_ascii=False, indent=2))
        return steps[-1]["returncode"]

    dry_run_cmd = [py, str(ROOT / "scripts" / "push_today10_to_feishu.py")]
    steps.append(run_step("dry-run 今日10 Feishu write", dry_run_cmd))
    if steps[-1]["returncode"] != 0:
        log_path = write_run_log(steps, "write-feishu" if args.write_feishu else "dry-run")
        print(json.dumps({"ok": False, "log": str(log_path)}, ensure_ascii=False, indent=2))
        return steps[-1]["returncode"]

    if args.write_feishu:
        write_cmd = [py, str(ROOT / "scripts" / "push_today10_to_feishu.py"), "--write"]
        steps.append(run_step("write 今日10 to Feishu 03 分析与选题", write_cmd, env=os.environ.copy()))
        if steps[-1]["returncode"] != 0:
            log_path = write_run_log(steps, "write-feishu")
            print(json.dumps({"ok": False, "log": str(log_path)}, ensure_ascii=False, indent=2))
            return steps[-1]["returncode"]

        refresh_cmd = [py, str(ROOT / "scripts" / "refresh_console_daily.py")]
        steps.append(run_step("refresh Feishu 00 主控台", refresh_cmd, env=os.environ.copy()))

    log_path = write_run_log(steps, "write-feishu" if args.write_feishu else "dry-run")
    ok = all(step["returncode"] == 0 for step in steps)
    print(json.dumps({
        "ok": ok,
        "mode": "write-feishu" if args.write_feishu else "dry-run",
        "log": str(log_path),
        "wrote_feishu": bool(args.write_feishu and ok),
    }, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
