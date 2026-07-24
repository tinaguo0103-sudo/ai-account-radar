#!/usr/bin/env python3
"""Finalize one exact daily run after the editorial task enriches its topic CSV.

The 08:00 Codex automation runs inside Codex already. Calling `codex exec`
again from `editorial_skill_runner.py` can fail in that context, so the outer
agent may enrich `today_10_topics.csv` directly using the global editorial
Skill. This script only performs the mechanical tail of the pipeline:

- dry-run and write Feishu 04;
- verify Feishu 04 consistency;
- mark the exact daily pipeline log as finalized.

It does not generate editorial content.
"""
from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import daily_pipeline
import douyin_candidate_lifecycle
from local_env import load_local_env
from scheduled_flow_preflight import evaluate_preflight


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output"
LOG_DIR = OUT / "logs"


def recommended_row_count(path: Path) -> int:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return sum(
            1
            for row in csv.DictReader(handle)
            if str(row.get("今日建议级别") or "").strip() == "推荐制作"
        )


def run_step(name: str, command: list[str]) -> dict[str, Any]:
    started_at = datetime.now().isoformat(timespec="seconds")
    print(f"\n== {name} ==")
    print(" ".join(command))
    output_lines: list[str] = []
    process = subprocess.Popen(
        command,
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    assert process.stdout is not None
    try:
        for line in process.stdout:
            print(line, end="")
            output_lines.append(line)
    except KeyboardInterrupt:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        raise
    returncode = process.wait()
    output = "".join(output_lines)
    return {
        "name": name,
        "command": command,
        "started_at": started_at,
        "returncode": returncode,
        "stdout": output[-4000:],
        "stderr": "",
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


def csv_row_count(path: Path) -> int:
    if not path.exists() or not path.read_text(encoding="utf-8-sig").strip():
        return 0
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return len(list(csv.DictReader(handle)))


def csv_fingerprints(path: Path) -> list[str]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [str(row.get("内容指纹") or "").strip() for row in csv.DictReader(handle) if str(row.get("内容指纹") or "").strip()]


def update_run_sampler_log(run_id: str, today_path: Path) -> None:
    output_dir = OUT / "runs" / run_id
    run_log_path = output_dir / "content_sampler_log.json"
    payload = read_json(run_log_path)
    if not payload:
        raise RuntimeError("exact_run_sampler_log_missing")
    if str(payload.get("run_id") or "") != run_id or str(payload.get("mode") or "") != "write-feishu":
        raise RuntimeError("exact_run_sampler_log_identity_mismatch")
    payload.update({
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "run_id": run_id,
        "mode": "write-feishu",
        "output_dir": str(output_dir),
        "today_candidates": csv_row_count(today_path),
    })
    outputs = payload.get("outputs") if isinstance(payload.get("outputs"), dict) else {}
    outputs.update({
        "today_candidates": str(today_path),
        "content_sampler_log": str(run_log_path),
    })
    payload["outputs"] = outputs
    write_json(run_log_path, payload)


def update_pipeline_log(run_id: str, tail_steps: list[dict[str, Any]], ok: bool) -> Path:
    log_path = LOG_DIR / f"daily_pipeline_{datetime.now().strftime('%Y-%m-%d')}.json"
    payload = read_json(log_path)
    existing_steps = payload.get("steps") if isinstance(payload.get("steps"), list) else []
    full_collection_success = bool(payload.get("full_collection_success", payload.get("ok", False)))
    overall_ok = bool(ok and full_collection_success)
    payload.update({
        "ok": overall_ok,
        "editorial_finalized": ok,
        "finalization_ok": ok,
        "status": "completed" if overall_ok else ("completed_with_failures" if ok else "failed"),
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Finalize a daily run after external editorial enrichment.")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--input", default="")
    parser.add_argument("--write-feishu", action="store_true")
    args = parser.parse_args()

    py = sys.executable
    today_path = Path(args.input) if args.input else default_today_path(args.run_id)
    if not today_path.exists():
        raise SystemExit(f"Missing enriched topic CSV: {today_path}")
    authoritative_path = default_today_path(args.run_id).resolve()
    if today_path.resolve() != authoritative_path:
        raise SystemExit(f"Finalizer input must be exact run-scoped artifact: {authoritative_path}")
    if recommended_row_count(today_path) == 0:
        log_path = update_pipeline_log(args.run_id, [], True)
        print(json.dumps({
            "ok": True,
            "status": "completed_no_recommendation",
            "run_id": args.run_id,
            "input": str(today_path),
            "log": str(log_path),
            "feishu_04_calls": 0,
            "topic_card_calls": 0,
            "generation_06_calls": 0,
        }, ensure_ascii=False, indent=2))
        return 0

    if args.write_feishu:
        try:
            load_local_env(required=True)
        except SystemExit as exc:
            print(json.dumps({
                "ok": False,
                "reason": "environment_not_loaded",
                "detail": str(exc),
                "external_calls": 0,
                "business_writes": 0,
            }, ensure_ascii=False, indent=2))
            return 2

    if args.write_feishu:
        preflight = evaluate_preflight("editorial", check_network=True)
        if not preflight["ok"]:
            print(json.dumps({"ok": False, "reason": "scheduled_flow_preflight_failed", "preflight": preflight}, ensure_ascii=False, indent=2))
            return 2
        try:
            update_run_sampler_log(args.run_id, today_path)
        except RuntimeError as exc:
            print(json.dumps({
                "ok": False,
                "reason": str(exc),
                "external_calls": 0,
                "business_writes": 0,
            }, ensure_ascii=False, indent=2))
            return 2

    steps: list[dict[str, Any]] = []
    dry_run_cmd = [
        py, str(ROOT / "scripts" / "push_today10_to_feishu.py"),
        "--input", str(today_path), "--run-id", args.run_id,
    ]
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
        douyin_candidate_lifecycle.mark_written_04(csv_fingerprints(today_path), run_id=args.run_id)

    ok = daily_pipeline.business_steps_ok(steps)
    log_path = update_pipeline_log(args.run_id, steps, ok)
    print(json.dumps({
        "ok": ok,
        "run_id": args.run_id,
        "input": str(today_path),
        "log": str(log_path),
    }, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
