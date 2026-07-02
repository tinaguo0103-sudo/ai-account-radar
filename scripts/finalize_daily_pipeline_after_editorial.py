#!/usr/bin/env python3
"""Finalize a daily run after an outer Codex agent enriches the topic CSV.

The 08:00 Codex automation runs inside Codex already. Calling `codex exec`
again from `editorial_skill_runner.py` can fail in that context, so the outer
agent may enrich `today_10_topics.csv` directly using the global editorial
Skill. This script only performs the mechanical tail of the pipeline:

- sync enriched CSV/report into latest mirrors;
- dry-run and write Feishu 04;
- verify Feishu 04 consistency;
- refresh the console;
- mark the daily pipeline log as recovered.

It does not generate editorial content.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import daily_pipeline


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output"
LOG_DIR = OUT / "logs"


def run_step(name: str, command: list[str]) -> dict[str, Any]:
    started_at = datetime.now().isoformat(timespec="seconds")
    print(f"\n== {name} ==")
    print(" ".join(command))
    result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True)
    if result.stdout:
        print(result.stdout)
    if result.stderr:
        print(result.stderr, file=sys.stderr)
    return {
        "name": name,
        "command": command,
        "started_at": started_at,
        "returncode": result.returncode,
        "stdout": result.stdout[-4000:],
        "stderr": result.stderr[-4000:],
    }


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def default_today_path(run_id: str) -> Path:
    return OUT / "runs" / run_id / "today_10_topics.csv"


def update_pipeline_log(run_id: str, tail_steps: list[dict[str, Any]], ok: bool) -> Path:
    log_path = LOG_DIR / f"daily_pipeline_{datetime.now().strftime('%Y-%m-%d')}.json"
    payload = read_json(log_path)
    existing_steps = payload.get("steps") if isinstance(payload.get("steps"), list) else []
    payload.update({
        "ok": ok,
        "recovered_ok": ok,
        "recovered_from": "external_editorial_finalizer",
        "run_id": run_id or payload.get("run_id", ""),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "steps": existing_steps + tail_steps,
    })
    outputs = payload.get("outputs") if isinstance(payload.get("outputs"), dict) else {}
    output_dir = OUT / "runs" / run_id
    outputs.update({
        "run_output_dir": str(output_dir),
        "today_10_topics": str(output_dir / "today_10_topics.csv"),
        "today_10_markdown": str(output_dir / f"today_10_topics_{datetime.now().strftime('%Y-%m-%d')}.md"),
    })
    payload["outputs"] = outputs
    write_json(log_path, payload)
    return log_path


def update_scheduled_log(tail_steps: list[dict[str, Any]], ok: bool) -> Path:
    log_path = LOG_DIR / f"scheduled_daily_collection_{datetime.now().strftime('%Y-%m-%d')}.json"
    payload = read_json(log_path)
    if not payload:
        return log_path
    existing_steps = payload.get("steps") if isinstance(payload.get("steps"), list) else []
    payload.update({
        "ok": ok,
        "recovered_ok": ok,
        "recovered_from": "external_editorial_finalizer",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "steps": existing_steps + tail_steps,
    })
    write_json(log_path, payload)
    return log_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Finalize a daily run after external editorial enrichment.")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--input", default="")
    parser.add_argument("--write-feishu", action="store_true")
    parser.add_argument("--update-scheduled-log", action="store_true")
    args = parser.parse_args()

    py = sys.executable
    today_path = Path(args.input) if args.input else default_today_path(args.run_id)
    if not today_path.exists():
        raise SystemExit(f"Missing enriched topic CSV: {today_path}")

    editorial_report = today_path.parent / "editorial_skill_report.json"
    daily_pipeline.sync_enriched_candidate_mirrors(today_path, editorial_report, args.write_feishu)

    steps: list[dict[str, Any]] = []
    dry_run_cmd = [py, str(ROOT / "scripts" / "push_today10_to_feishu.py"), "--input", str(today_path)]
    steps.append(run_step("dry-run 今日候选池 Feishu write after external editorial", dry_run_cmd))
    if steps[-1]["returncode"] != 0:
        log_path = update_pipeline_log(args.run_id, steps, False)
        print(json.dumps({"ok": False, "log": str(log_path)}, ensure_ascii=False, indent=2))
        return steps[-1]["returncode"]

    if args.write_feishu:
        write_cmd = [
            py,
            str(ROOT / "scripts" / "push_today10_to_feishu.py"),
            "--input",
            str(today_path),
            "--write",
            "--run-id",
            args.run_id,
        ]
        steps.append(run_step("write 今日候选池 to Feishu 04 after external editorial", write_cmd))
        if steps[-1]["returncode"] != 0:
            log_path = update_pipeline_log(args.run_id, steps, False)
            print(json.dumps({"ok": False, "log": str(log_path)}, ensure_ascii=False, indent=2))
            return steps[-1]["returncode"]

        verify_cmd = [
            py,
            str(ROOT / "scripts" / "verify_today10_feishu_consistency.py"),
            "--input",
            str(today_path),
            "--run-id",
            args.run_id,
        ]
        steps.append(run_step("verify Feishu 04 after external editorial", verify_cmd))
        if steps[-1]["returncode"] != 0:
            log_path = update_pipeline_log(args.run_id, steps, False)
            print(json.dumps({"ok": False, "log": str(log_path)}, ensure_ascii=False, indent=2))
            return steps[-1]["returncode"]

        refresh_cmd = [py, str(ROOT / "scripts" / "refresh_console_daily.py")]
        steps.append(run_step("refresh Feishu 00 主控台 after external editorial", refresh_cmd))

    ok = all(step["returncode"] == 0 for step in steps)
    log_path = update_pipeline_log(args.run_id, steps, ok)
    scheduled_log = update_scheduled_log(steps, ok) if args.update_scheduled_log else ""
    print(json.dumps({
        "ok": ok,
        "run_id": args.run_id,
        "input": str(today_path),
        "log": str(log_path),
        "scheduled_log": str(scheduled_log) if scheduled_log else "",
    }, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
