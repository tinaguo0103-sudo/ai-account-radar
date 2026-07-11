#!/usr/bin/env python3
"""AR-020D current-task editorial state machine.

Python prepares minimized inputs, validates current-task outputs, advances
state, and writes artifacts. It never starts a model process or network call.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from datetime import date, datetime
from pathlib import Path
from typing import Any

import content_sampler
import editorial_skill_runner as runner
import topic_replay_evaluation as deterministic_replay
import topic_skill_replay_evaluation as replay


VERSION = "ar020d_current_task_state_machine_v1"
ACCOUNT_DIRECTIONS = ["AI业务定调", "真实工作流改造", "AI导演工作流", "汽车与内容营销", "AI项目复盘"]
STAGE1_ALLOWLIST = {
    "source_type",
    "platform",
    "source_account",
    "original_title",
    "source_excerpt",
    "source_title_hook",
    "source_weight_label",
    "source_influence_weight",
    "source_composition",
    "aihot_major_news",
    "market_validation",
    "account_directions",
}
STAGE1_FORBIDDEN_MARKERS = {
    "内容指纹", "来源链接", "原始payload", "抓取状态", "失败原因", "内部文件路径",
    "Austin转译角度", "关联母场景", "我的工作流痛点", "我要做的实验", "验证方式", "可沉淀资产",
}


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def hash_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else ""


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def state_path(out_dir: Path) -> Path:
    return out_dir / "editorial_state_machine.json"


def load_state(out_dir: Path) -> dict[str, Any]:
    path = state_path(out_dir)
    if not path.exists():
        raise RuntimeError(f"Missing state machine: {path}. Run prepare-stage1 first.")
    return read_json(path)


def save_state(out_dir: Path, state: dict[str, Any]) -> None:
    state["updated_at"] = now_iso()
    write_json(state_path(out_dir), state)


def stage_record(status: str = "pending", **extra: Any) -> dict[str, Any]:
    return {"status": status, "started_at": "", "completed_at": "", "failed_at": "", "error": "", **extra}


def start_stage(record: dict[str, Any], input_hash: str) -> None:
    record.update({"status": "started", "started_at": now_iso(), "completed_at": "", "failed_at": "", "error": "", "input_hash": input_hash})


def complete_stage(record: dict[str, Any], output_hash: str, **extra: Any) -> None:
    record.update({"status": "completed", "completed_at": now_iso(), "failed_at": "", "error": "", "output_hash": output_hash, **extra})


def fail_stage(record: dict[str, Any], exc: Exception) -> None:
    record.update({"status": "failed", "failed_at": now_iso(), "error": f"{type(exc).__name__}: {exc}"})


def require_completed(state: dict[str, Any], stage_name: str) -> None:
    if state["stages"].get(stage_name, {}).get("status") != "completed":
        raise RuntimeError(f"Stage {stage_name} is not completed; later stages are blocked")


def invalidate_stage(state: dict[str, Any], stage_name: str, reason: str) -> None:
    record = state["stages"].get(stage_name)
    if not record:
        return
    record["status"] = "stale"
    record["stale_at"] = now_iso()
    record["stale_reason"] = reason


def invalidate_downstream(state: dict[str, Any], from_stage: str, reason: str) -> None:
    order = ["stage1", "global_ranking", "stage2", "finalize"]
    start = order.index(from_stage) + 1
    for stage_name in order[start:]:
        invalidate_stage(state, stage_name, reason)


def local_source_trace(row: dict[str, Any], index: int) -> dict[str, Any]:
    return {
        "index": index,
        "content_fingerprint": row.get("内容指纹", ""),
        "source_trace_hash": hash_json({key: str(value or "") for key, value in row.items()}),
    }


def minimized_stage1_row(row: dict[str, Any], index: int) -> dict[str, Any]:
    facts = runner.safe_source_facts({key: str(value or "") for key, value in row.items()})
    result = {
        "source_type": facts["source_type"],
        "platform": str(row.get("平台") or row.get("来源平台") or facts["source_type"] or ""),
        "source_account": facts["source_account"],
        "original_title": replay.extract_original_title(facts["source_title"]),
        "source_excerpt": replay.truncate_on_sentence(facts["source_excerpt"], 280),
        "source_title_hook": facts["source_title_hook"],
        "source_weight_label": facts["source_weight_label"],
        "source_influence_weight": facts["source_influence_weight"],
        "source_composition": facts["source_composition"],
        "aihot_major_news": facts["aihot_major_news"],
        "market_validation": facts["market_validation"],
        "account_directions": ACCOUNT_DIRECTIONS,
    }
    assert set(result) == STAGE1_ALLOWLIST
    text = canonical_json(result)
    leaked = sorted(marker for marker in STAGE1_FORBIDDEN_MARKERS if marker in text)
    if leaked:
        raise RuntimeError(f"Stage 1 minimized row leaked forbidden markers: {leaked}")
    return {"index": index, **result}


def provenance(task_id: str) -> dict[str, Any]:
    base = runner.runtime_provenance(fallback_state="false")
    return {
        **base,
        "state_machine_version": VERSION,
        "execution_surface": "current_codex_task",
        "task_provenance": task_id,
        "nested_model_execution": False,
        "fallback": False,
        "persona_style_embedded": True,
        "persona_style_reference_only": True,
    }


def prepare_stage1(args: argparse.Namespace) -> dict[str, Any]:
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_paths = deterministic_replay.discover_content_csvs(args.content_csv)
    if args.resume and state_path(out_dir).exists():
        state = load_state(out_dir)
        current_hashes = {str(path): file_hash(path) for path in csv_paths}
        stored = read_json(out_dir / "local_source_manifest.json").get("input_file_hashes", {})
        if current_hashes != stored:
            raise RuntimeError("Cannot resume: source CSV hashes changed")
        return {"ok": True, "stage": "prepare_stage1", "resumed": True, "stages": state["stages"]}
    items = deterministic_replay.load_items(csv_paths, date.fromisoformat(args.since))
    pre = replay.build_pre_skill_pool(items, args.max_skill_candidates)
    pool = pre["pre_skill_pool"]
    if not pool:
        raise RuntimeError("No pre-Skill candidates")
    batches = replay.batch_slices(pool, args.batch_size)
    prov = provenance(args.task_id)
    persona_text = runner.load_text(runner.SKILL_REFERENCE)
    persona_path = out_dir / "references" / "persona_style_reference.md"
    persona_path.parent.mkdir(parents=True, exist_ok=True)
    persona_path.write_text(persona_text, encoding="utf-8")
    source_manifest = {
        "input_files": [str(path) for path in csv_paths],
        "input_file_hashes": {str(path): file_hash(path) for path in csv_paths},
        "content_items": len(items),
        "candidate_count": len(pre["candidates"]),
        "pre_skill_pool_count": len(pool),
        "source_traces": [local_source_trace(row, index) for index, row in enumerate(pool)],
    }
    write_json(out_dir / "local_source_manifest.json", source_manifest)
    write_json(out_dir / "provenance_manifest.json", prov)
    replay.write_csv(out_dir / "candidate_universe.csv", pre["candidates"])
    replay.write_csv(out_dir / "pre_skill_candidates.csv", pool)
    replay.append_progress(out_dir, [
        replay.progress_event(
            status="success",
            stage="prepare_stage1",
            note=f"content_items={len(items)}; candidates={len(pre['candidates'])}; pre_skill={len(pool)}",
        )
    ])
    write_json(out_dir / "schemas" / "stage1_output_schema.json", runner.editorial_decision_output_schema())
    batch_state: dict[str, Any] = {}
    for batch_index, start, rows in batches:
        batch_id = replay.batch_id_for(batch_index)
        payload = {
            "protocol": VERSION,
            "execution_surface": "current_codex_task",
            "stage": "editorial_decision",
            "batch_id": batch_id,
            "start_index": start,
            "persona_style_reference": {
                "path": str(persona_path),
                "sha256": file_hash(persona_path),
                "embedded": True,
                "role": "style_reference_only_not_source_evidence",
            },
            "skill": {"path": str(runner.SKILL_MD), "sha256": file_hash(runner.SKILL_MD)},
            "rows": [minimized_stage1_row(row, start + offset) for offset, row in enumerate(rows)],
        }
        batch_dir = out_dir / "stage1" / batch_id
        write_json(batch_dir / "input.json", payload)
        write_json(batch_dir / "schema.json", runner.editorial_decision_output_schema())
        batch_state[batch_id] = stage_record("prepared", input_hash=hash_json(payload), start_index=start, row_count=len(rows))
    state = {
        "protocol": VERSION,
        "execution_surface": "current_codex_task",
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "task_provenance": args.task_id,
        "writes_feishu": False,
        "fallback": False,
        "source_manifest_hash": hash_json(source_manifest),
        "content_csvs": [str(path) for path in csv_paths],
        "since": args.since,
        "batch_size": args.batch_size,
        "max_skill_candidates": args.max_skill_candidates,
        "stages": {
            "prepare_stage1": stage_record("completed", input_hash=hash_json(source_manifest), output_hash=hash_json(batch_state), completed_at=now_iso()),
            "stage1": stage_record("prepared", batches=batch_state),
            "global_ranking": stage_record(),
            "stage2": stage_record(),
            "finalize": stage_record(),
        },
    }
    save_state(out_dir, state)
    return {"ok": True, "stage": "prepare_stage1", "batches": len(batches), "rows": len(pool), "out_dir": str(out_dir)}


def pool_from_state(state: dict[str, Any]) -> tuple[list[content_sampler.ContentItem], dict[str, Any]]:
    paths = [Path(value) for value in state["content_csvs"]]
    items = deterministic_replay.load_items(paths, date.fromisoformat(state["since"]))
    pre = replay.build_pre_skill_pool(items, int(state["max_skill_candidates"]))
    return items, pre


def validate_stage1(args: argparse.Namespace) -> dict[str, Any]:
    out_dir = Path(args.out_dir)
    state = load_state(out_dir)
    require_completed(state, "prepare_stage1")
    record = state["stages"]["stage1"]["batches"].get(args.batch_id)
    if not record:
        raise RuntimeError(f"Unknown Stage 1 batch: {args.batch_id}")
    batch_dir = out_dir / "stage1" / args.batch_id
    input_payload = read_json(batch_dir / "input.json")
    if hash_json(input_payload) != record["input_hash"]:
        raise RuntimeError(f"Stage 1 input hash mismatch for {args.batch_id}")
    output_path = batch_dir / "output.pending.json"
    if not output_path.exists():
        raise RuntimeError(f"Current task must write {output_path}")
    raw_output_hash = file_hash(output_path)
    if record.get("status") == "completed" and record.get("raw_output_hash") == raw_output_hash:
        return {"ok": True, "stage": "validate_stage1", "batch_id": args.batch_id, "status": "completed", "resumed": True}
    previous_output_hash = record.get("raw_output_hash", "")
    _, pre = pool_from_state(state)
    start = int(record["start_index"])
    rows = [{key: str(value or "") for key, value in row.items()} for row in pre["pre_skill_pool"][start:start + int(record["row_count"])]]
    start_stage(record, record["input_hash"])
    try:
        payload = read_json(output_path)
        decisions, meta = runner.validate_stage1_payload(rows, payload, start_index=start)
        if any("case_anchor" in canonical_json(item) or "可调用案例" in canonical_json(item) for item in decisions):
            raise RuntimeError("Stage 1 output exposed a case anchor/citation")
        write_json(batch_dir / "decisions.json", decisions)
        write_json(batch_dir / "meta.json", meta)
        complete_stage(record, hash_json(decisions), decision_count=len(decisions), raw_output_hash=raw_output_hash)
        if previous_output_hash and previous_output_hash != raw_output_hash:
            invalidate_downstream(state, "stage1", f"{args.batch_id} Stage 1 output changed")
    except Exception as exc:
        fail_stage(record, exc)
        save_state(out_dir, state)
        raise
    batches = state["stages"]["stage1"]["batches"]
    if all(item["status"] == "completed" for item in batches.values()):
        state["stages"]["stage1"]["status"] = "completed"
        state["stages"]["stage1"]["completed_at"] = now_iso()
    save_state(out_dir, state)
    return {"ok": True, "stage": "validate_stage1", "batch_id": args.batch_id, "status": record["status"]}


def all_stage1_decisions(out_dir: Path, state: dict[str, Any]) -> list[dict[str, Any]]:
    require_completed(state, "stage1")
    decisions: list[dict[str, Any]] = []
    for batch_id in sorted(state["stages"]["stage1"]["batches"]):
        decisions.extend(read_json(out_dir / "stage1" / batch_id / "decisions.json"))
    return sorted(decisions, key=lambda item: int(item["index"]))


def prepare_ranking(args: argparse.Namespace) -> dict[str, Any]:
    out_dir = Path(args.out_dir)
    state = load_state(out_dir)
    decisions = all_stage1_decisions(out_dir, state)
    payload = {
        "protocol": VERSION,
        "execution_surface": "current_codex_task",
        "stage": "global_daily_ranking",
        "rules": {"strict_bijection": True, "top_today_max": 3, "tradeoff_required_for_select": True},
        "decisions": decisions,
    }
    write_json(out_dir / "global_ranking" / "input.json", payload)
    write_json(out_dir / "global_ranking" / "schema.json", runner.global_ranking_output_schema())
    record = state["stages"]["global_ranking"]
    record.clear()
    record.update(stage_record("prepared", input_hash=hash_json(payload), row_count=len(decisions)))
    save_state(out_dir, state)
    return {"ok": True, "stage": "prepare_global_ranking", "rows": len(decisions)}


def validate_ranking(args: argparse.Namespace) -> dict[str, Any]:
    out_dir = Path(args.out_dir)
    state = load_state(out_dir)
    require_completed(state, "stage1")
    record = state["stages"]["global_ranking"]
    input_payload = read_json(out_dir / "global_ranking" / "input.json")
    if hash_json(input_payload) != record.get("input_hash"):
        raise RuntimeError("Global ranking input hash mismatch")
    output_path = out_dir / "global_ranking" / "output.pending.json"
    if not output_path.exists():
        raise RuntimeError(f"Current task must write {output_path}")
    raw_output_hash = file_hash(output_path)
    if record.get("status") == "completed" and record.get("raw_output_hash") == raw_output_hash:
        return {"ok": True, "stage": "validate_global_ranking", "status": "completed", "resumed": True}
    previous_output_hash = record.get("raw_output_hash", "")
    start_stage(record, record["input_hash"])
    try:
        output = read_json(output_path)
        decisions = all_stage1_decisions(out_dir, state)
        ranked = runner.apply_global_ranking(decisions, output.get("ranking_rows", []))
        write_json(out_dir / "global_ranking" / "ranked_decisions.json", ranked)
        complete_stage(record, hash_json(ranked), raw_output_hash=raw_output_hash, row_count=len(ranked), ranking_bijection_ok=True,
                       top_count=sum(1 for item in ranked if item.get("locked_daily_level") == "今日最值得做"))
        if previous_output_hash and previous_output_hash != raw_output_hash:
            invalidate_downstream(state, "global_ranking", "Global ranking output changed")
    except Exception as exc:
        fail_stage(record, exc)
        save_state(out_dir, state)
        raise
    save_state(out_dir, state)
    return {"ok": True, "stage": "validate_global_ranking", "rows": record["row_count"], "top_count": record["top_count"]}


def prepare_stage2(args: argparse.Namespace) -> dict[str, Any]:
    out_dir = Path(args.out_dir)
    state = load_state(out_dir)
    require_completed(state, "global_ranking")
    ranked = read_json(out_dir / "global_ranking" / "ranked_decisions.json")
    _, pre = pool_from_state(state)
    pool = pre["pre_skill_pool"]
    batch_state: dict[str, Any] = {}
    for batch_index, start, rows in replay.batch_slices(pool, int(state["batch_size"])):
        batch_id = replay.batch_id_for(batch_index)
        decisions = ranked[start:start + len(rows)]
        payload = {
            "protocol": VERSION,
            "execution_surface": "current_codex_task",
            "stage": "field_mapping",
            "batch_id": batch_id,
            "rows": [
                {
                    "index": start + offset,
                    "source_facts": {key: value for key, value in minimized_stage1_row(row, start + offset).items() if key != "index"},
                    "locked_editorial_decision": decisions[offset],
                    "stage2_rule": "operational fields only; owner-field authoring is forbidden",
                }
                for offset, row in enumerate(rows)
            ],
            "allowed_output_fields": runner.STAGE2_OPERATIONAL_FIELDS,
        }
        batch_dir = out_dir / "stage2" / batch_id
        write_json(batch_dir / "input.json", payload)
        write_json(batch_dir / "schema.json", runner.field_mapping_output_schema())
        batch_state[batch_id] = stage_record("prepared", input_hash=hash_json(payload), start_index=start, row_count=len(rows))
    record = state["stages"]["stage2"]
    record.clear()
    record.update(stage_record("prepared", batches=batch_state))
    save_state(out_dir, state)
    return {"ok": True, "stage": "prepare_stage2", "batches": len(batch_state), "rows": len(pool)}


def validate_stage2(args: argparse.Namespace) -> dict[str, Any]:
    out_dir = Path(args.out_dir)
    state = load_state(out_dir)
    require_completed(state, "global_ranking")
    record = state["stages"]["stage2"]["batches"].get(args.batch_id)
    if not record:
        raise RuntimeError(f"Unknown Stage 2 batch: {args.batch_id}")
    batch_dir = out_dir / "stage2" / args.batch_id
    input_payload = read_json(batch_dir / "input.json")
    if hash_json(input_payload) != record["input_hash"]:
        raise RuntimeError(f"Stage 2 input hash mismatch for {args.batch_id}")
    output_path = batch_dir / "output.pending.json"
    if not output_path.exists():
        raise RuntimeError(f"Current task must write {output_path}")
    raw_output_hash = file_hash(output_path)
    if record.get("status") == "completed" and record.get("raw_output_hash") == raw_output_hash:
        return {"ok": True, "stage": "validate_stage2", "batch_id": args.batch_id, "status": "completed", "resumed": True}
    previous_output_hash = record.get("raw_output_hash", "")
    _, pre = pool_from_state(state)
    ranked = read_json(out_dir / "global_ranking" / "ranked_decisions.json")
    start = int(record["start_index"])
    count = int(record["row_count"])
    rows = [{key: str(value or "") for key, value in row.items()} for row in pre["pre_skill_pool"][start:start + count]]
    decisions = ranked[start:start + count]
    start_stage(record, record["input_hash"])
    try:
        payload = read_json(output_path)
        mapped, meta = runner.apply_stage2_payload(rows, decisions, payload, artifact_dir=batch_dir)
        write_csv(batch_dir / "skill_rows.csv", mapped)
        write_json(batch_dir / "meta.json", meta)
        failures = sum(1 for row in mapped if row.get("stage2_invariant_status") == "fail" or row.get("guard_blocked") == "true")
        if failures:
            raise RuntimeError(f"Stage 2 invariant/guard failures: {failures}")
        complete_stage(record, hash_json(mapped), raw_output_hash=raw_output_hash, row_count=len(mapped), raw_drift_count=0, guard_blocked_count=0)
        if previous_output_hash and previous_output_hash != raw_output_hash:
            invalidate_stage(state, "finalize", f"{args.batch_id} Stage 2 output changed")
    except Exception as exc:
        fail_stage(record, exc)
        save_state(out_dir, state)
        raise
    batches = state["stages"]["stage2"]["batches"]
    if all(item["status"] == "completed" for item in batches.values()):
        state["stages"]["stage2"]["status"] = "completed"
        state["stages"]["stage2"]["completed_at"] = now_iso()
    save_state(out_dir, state)
    return {"ok": True, "stage": "validate_stage2", "batch_id": args.batch_id, "status": record["status"]}


def finalize(args: argparse.Namespace) -> dict[str, Any]:
    out_dir = Path(args.out_dir)
    state = load_state(out_dir)
    require_completed(state, "stage2")
    record = state["stages"]["finalize"]
    stage2_hashes = {batch_id: item["output_hash"] for batch_id, item in state["stages"]["stage2"]["batches"].items()}
    start_stage(record, hash_json(stage2_hashes))
    try:
        items, pre = pool_from_state(state)
        rows: list[dict[str, Any]] = []
        batch_meta = []
        for batch_id, item in sorted(state["stages"]["stage2"]["batches"].items()):
            batch_dir = out_dir / "stage2" / batch_id
            batch_rows = replay.read_csv(batch_dir / "skill_rows.csv")
            rows.extend(batch_rows)
            batch_meta.append({
                "batch_id": batch_id,
                "status": "success",
                "row_count": len(batch_rows),
                "outputs": {"meta": str(batch_dir / "meta.json")},
                "engine_meta": {
                    "execution_surface": "current_codex_task",
                    "provenance_manifest": read_json(out_dir / "provenance_manifest.json"),
                },
            })
        ranking_record = state["stages"]["global_ranking"]
        engine_meta = {
            "mode": "current_task_state_machine",
            "execution_surface": "current_codex_task",
            "fallback_only": False,
            "failed_batch_count": 0,
            "batch_count": len(batch_meta),
            "completed_batch_count": len(batch_meta),
            "batches": batch_meta,
            "global_ranking": {
                "status": "success",
                "ranking_bijection_ok": ranking_record.get("ranking_bijection_ok"),
                "stage1_decision_count": ranking_record.get("row_count"),
                "ranking_output_count": ranking_record.get("row_count"),
                "global_top_count": ranking_record.get("top_count"),
                "outputs": {"ranked_decisions": str(out_dir / "global_ranking" / "ranked_decisions.json")},
            },
            "provenance_manifest": read_json(out_dir / "provenance_manifest.json"),
        }
        ns = argparse.Namespace(
            since=state["since"], batch_size=state["batch_size"], batch_timeout_seconds=0, timeout=0,
            max_skill_candidates=state["max_skill_candidates"],
        )
        replay.write_csv(out_dir / "candidate_universe.csv", pre["candidates"])
        replay.write_csv(out_dir / "pre_skill_candidates.csv", pre["pre_skill_pool"])
        summary = replay.aggregate_replay_outputs(
            out_dir, ns, csv_paths=[Path(value) for value in state["content_csvs"]], items=items,
            pre=pre, skill_rows=rows, engine_meta=engine_meta, engine="current_task", completed=True,
        )
        if not summary.get("quality_gate_ok"):
            raise RuntimeError("Final quality gate failed")
        complete_stage(record, hash_json(summary), quality_gate_ok=True)
    except Exception as exc:
        fail_stage(record, exc)
        save_state(out_dir, state)
        raise
    save_state(out_dir, state)
    return summary


def status(args: argparse.Namespace) -> dict[str, Any]:
    state = load_state(Path(args.out_dir))
    return {"protocol": state["protocol"], "execution_surface": state["execution_surface"], "stages": state["stages"], "writes_feishu": state["writes_feishu"], "fallback": state["fallback"]}


def main() -> int:
    parser = argparse.ArgumentParser(description="AR-020D current-task editorial state machine")
    sub = parser.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("prepare-stage1")
    prepare.add_argument("--out-dir", required=True)
    prepare.add_argument("--content-csv", action="append", default=[])
    prepare.add_argument("--since", default="2026-07-01")
    prepare.add_argument("--batch-size", type=int, default=3)
    prepare.add_argument("--max-skill-candidates", type=int, default=content_sampler.MAX_SKILL_REVIEW_CANDIDATES)
    prepare.add_argument("--task-id", default=os.getenv("CODEX_THREAD_ID", "current-codex-task"))
    prepare.add_argument("--resume", action="store_true")
    for name in ["validate-stage1", "validate-stage2"]:
        command = sub.add_parser(name)
        command.add_argument("--out-dir", required=True)
        command.add_argument("--batch-id", required=True)
    for name in ["prepare-ranking", "validate-ranking", "prepare-stage2", "finalize", "status"]:
        command = sub.add_parser(name)
        command.add_argument("--out-dir", required=True)
    args = parser.parse_args()
    handlers = {
        "prepare-stage1": prepare_stage1,
        "validate-stage1": validate_stage1,
        "prepare-ranking": prepare_ranking,
        "validate-ranking": validate_ranking,
        "prepare-stage2": prepare_stage2,
        "validate-stage2": validate_stage2,
        "finalize": finalize,
        "status": status,
    }
    try:
        result = handlers[args.command](args)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "command": args.command, "error_type": type(exc).__name__, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
