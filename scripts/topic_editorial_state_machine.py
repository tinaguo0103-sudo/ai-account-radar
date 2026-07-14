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
import topic_research_contract as research_contract
import persona_reference_builder as persona_builder
import trusted_exact_source_adapter as source_adapter


VERSION = "ar020d_research_grounded_state_machine_v2"
ACCOUNT_DIRECTIONS = ["AI业务定调", "真实工作流改造", "汽车与内容营销", "AI导演工作流"]


def infer_judgment_operations(dossier: dict[str, Any]) -> list[str]:
    """Infer the editorial operation from evidence shape, not source keywords."""
    hook = dossier.get("hook_analysis", {}) or {}
    hook_types = set(hook.get("hook_type") or [])
    claims = list(dossier.get("claim_evidence") or [])
    confidence = str(dossier.get("confidence") or "").lower()
    operations = {"natural_voice"}
    if hook_types & {"story", "social_proof", "story_or_social_proof"}:
        operations.add("story_or_social_proof")
    if hook_types & {"result_promise", "audience_benefit"}:
        operations.add("result_promise")
    if dossier.get("conflicts") or hook_types & {"contradiction", "conflict", "public_contradiction"}:
        operations.add("public_contradiction")
    if confidence in {"low", "弱"} or any(
        str(item.get("strength") or "").lower() in {"low", "weak", "弱"} for item in claims
    ):
        operations.add("evidence_skepticism")
    if "decision_tradeoff" in hook_types:
        operations.add("decision_tradeoff")
    if "shallow_take_rejection" in hook_types:
        operations.add("shallow_take_rejection")
    if not (operations & {"story_or_social_proof", "result_promise", "public_contradiction", "evidence_skepticism"}):
        operations.add("shallow_take_rejection")
    return [name for name in persona_builder.OPERATION_PATTERNS if name in operations]
STAGE1_ALLOWLIST = {
    "candidate_id", "source", "research", "hook_analysis", "persona_facts",
    "judgment_and_style_examples",
    "account_directions",
}
STAGE1_FORBIDDEN_MARKERS = {
    "原始payload", "内部文件路径", "Austin转译角度", "关联母场景",
    "我的工作流痛点", "我要做的实验", "验证方式", "可沉淀资产",
    "experience_archive", "可调用案例", "真实/相邻案例",
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
    order = ["source_open", "research", "stage1", "global_ranking", "stage2", "finalize"]
    start = order.index(from_stage) + 1
    for stage_name in order[start:]:
        invalidate_stage(state, stage_name, reason)


def local_source_trace(row: dict[str, Any], index: int) -> dict[str, Any]:
    return {
        "index": index,
        "content_fingerprint": row.get("内容指纹", ""),
        "source_trace_hash": hash_json({key: str(value or "") for key, value in row.items()}),
    }


def minimized_stage1_row(
    candidate: dict[str, Any],
    dossier: dict[str, Any],
    persona_facts: dict[str, Any],
    style_examples: list[dict[str, Any]],
) -> dict[str, Any]:
    result = {
        "candidate_id": candidate["candidate_id"],
        "source": dossier["source"],
        "research": {
            "research_summary": dossier["research_summary"],
            "results": dossier["results"],
            "conflicts": dossier.get("conflicts", []),
            "confidence": dossier.get("confidence", ""),
            "dossier_hash": dossier["dossier_hash"],
        },
        "hook_analysis": dossier["hook_analysis"],
        "persona_facts": persona_facts,
        "judgment_and_style_examples": style_examples,
        "account_directions": ACCOUNT_DIRECTIONS,
    }
    assert set(result) == STAGE1_ALLOWLIST
    text = canonical_json(result)
    leaked = sorted(marker for marker in STAGE1_FORBIDDEN_MARKERS if marker in text)
    if leaked:
        raise RuntimeError(f"Stage 1 minimized row leaked forbidden markers: {leaked}")
    return result


def provenance(task_id: str) -> dict[str, Any]:
    base = runner.runtime_provenance()
    return {
        **base,
        "state_machine_version": VERSION,
        "execution_surface": "current_codex_task",
        "task_provenance": task_id,
        "nested_model_execution": False,
        "strict_fail_closed": True,
        "persona_style_embedded": True,
        "persona_style_reference_only": True,
    }


def candidate_id(row: dict[str, Any], index: int) -> str:
    fingerprint = str(row.get("内容指纹") or "").strip()
    return f"candidate_{index:03d}_{fingerprint or hash_json(row)[:12]}"


def shortlist(args: argparse.Namespace) -> tuple[list[Path], list[content_sampler.ContentItem], dict[str, Any]]:
    csv_paths = deterministic_replay.discover_content_csvs(args.content_csv)
    items = deterministic_replay.load_items(csv_paths, date.fromisoformat(args.since))
    pre = replay.build_pre_skill_pool(items, args.max_skill_candidates)
    if not pre["pre_skill_pool"]:
        raise RuntimeError("No pre-Skill candidates")
    return csv_paths, items, pre


def candidate_rows_from_state(state: dict[str, Any]) -> list[dict[str, Any]]:
    return read_json(Path(state["out_dir"]) / "shortlist_candidates.json")


def update_terminal_stage(state: dict[str, Any], stage_name: str) -> None:
    batches = state["stages"][stage_name]["candidates"]
    terminal = {"completed", "failed"}
    if batches and all(item.get("status") in terminal for item in batches.values()):
        failures = sum(1 for item in batches.values() if item.get("status") == "failed")
        state["stages"][stage_name]["status"] = "completed_with_failures" if failures else "completed"
        state["stages"][stage_name]["completed_at"] = now_iso()
        state["stages"][stage_name]["failure_count"] = failures


def require_terminal(state: dict[str, Any], stage_name: str) -> None:
    status_value = state["stages"].get(stage_name, {}).get("status")
    if status_value not in {"completed", "completed_with_failures"}:
        raise RuntimeError(f"Stage {stage_name} is not terminal; later stages are blocked")


def completed_candidate_ids(state: dict[str, Any], stage_name: str) -> set[str]:
    return {
        candidate_id for candidate_id, record in state["stages"][stage_name].get("candidates", {}).items()
        if record.get("status") == "completed"
    }


def source_open_candidate(row: dict[str, Any], index: int) -> dict[str, Any]:
    exact_url = str(row.get("来源链接") or "").strip()
    return {
        "index": index,
        "candidate_id": candidate_id(row, index),
        "exact_url": exact_url,
        "csv_title": str(row.get("原始来源标题") or ""),
        "source_account": str(row.get("原始来源账号") or ""),
        "source_type": str(row.get("来源类型") or ""),
        "platform": str(row.get("平台") or ""),
        "local_trace_hash": hash_json(row),
        "primary_adapter": source_adapter.primary_adapter_for_url(exact_url),
        "expected_page_identity": source_adapter.expected_identity(exact_url),
    }


def prepare_source_open(args: argparse.Namespace) -> dict[str, Any]:
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if state_path(out_dir).exists():
        raise RuntimeError("Output directory already contains a run; use a fresh out-dir or explicit stage resume")
    csv_paths, items, pre = shortlist(args)
    pool = pre["pre_skill_pool"]
    candidates: list[dict[str, Any]] = []
    source_state: dict[str, Any] = {}
    for index, row in enumerate(pool):
        item = source_open_candidate(row, index)
        candidates.append(item)
        path = out_dir / "source_open" / item["candidate_id"]
        write_json(path / "input.json", {
            "protocol": VERSION,
            "stage": "exact_source_open",
            "candidate": item,
            "rules": {
                "must_open_exact_url": True,
                "no_csv_or_search_snippet_substitution": True,
                "no_account_home_or_search_page": True,
                "one_primary_adapter_only": True,
                "no_failover_after_adapter_failure": True,
            },
        })
        source_state[item["candidate_id"]] = stage_record("prepared", input_hash=file_hash(path / "input.json"), index=index)
    write_json(out_dir / "shortlist_candidates.json", candidates)
    prov = provenance(args.task_id)
    persona_manifest = persona_builder.build_bundle(Path(args.persona_docx), out_dir / "private_persona")
    prov.update({
        "raw_persona_authority_sha256": persona_manifest["authority_sha256"],
        "persona_manifest_hash": persona_manifest["manifest_hash"],
        "experience_archive_runtime": "excluded",
        "prohibited_paths": [],
    })
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
            stage="prepare_source_open",
            note=f"content_items={len(items)}; candidates={len(pre['candidates'])}; pre_skill={len(pool)}",
        )
    ])
    state = {
        "protocol": VERSION,
        "execution_surface": "current_codex_task",
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "task_provenance": args.task_id,
        "out_dir": str(out_dir),
        "writes_feishu": False,
        "strict_fail_closed": True,
        "source_manifest_hash": hash_json(source_manifest),
        "content_csvs": [str(path) for path in csv_paths],
        "since": args.since,
        "batch_size": args.batch_size,
        "max_skill_candidates": args.max_skill_candidates,
        "stages": {
            "source_open": stage_record("prepared", candidates=source_state),
            "research": stage_record("pending", candidates={}),
            "prepare_stage1": stage_record("pending"),
            "stage1": stage_record("pending", batches={}),
            "global_ranking": stage_record(),
            "stage2": stage_record(),
            "finalize": stage_record(),
        },
    }
    save_state(out_dir, state)
    return {"ok": True, "stage": "prepare_source_open", "candidates": len(candidates), "out_dir": str(out_dir)}


def validate_source_open(args: argparse.Namespace) -> dict[str, Any]:
    out_dir = Path(args.out_dir)
    state = load_state(out_dir)
    record = state["stages"]["source_open"]["candidates"].get(args.candidate_id)
    if not record:
        raise RuntimeError(f"Unknown candidate: {args.candidate_id}")
    candidate = next(item for item in candidate_rows_from_state(state) if item["candidate_id"] == args.candidate_id)
    output_path = Path(args.evidence_json) if getattr(args, "evidence_json", "") else out_dir / "source_open" / args.candidate_id / "output.pending.json"
    if not output_path.exists():
        raise RuntimeError(f"Current task must write {output_path}")
    record["primary_adapter"] = candidate["primary_adapter"]
    try:
        validated = research_contract.validate_source_open(candidate, read_json(output_path))
        write_json(out_dir / "source_open" / args.candidate_id / "validated.json", validated)
        if validated.get("eligible"):
            complete_stage(record, hash_json(validated), source_hash=validated["captured_content_hash"])
        else:
            raise research_contract.ContractError(validated.get("failure_reason") or "source_open_failed")
    except Exception as exc:
        fail_stage(record, exc)
    update_terminal_stage(state, "source_open")
    save_state(out_dir, state)
    return {"ok": record["status"] == "completed", "candidate_id": args.candidate_id, "status": record["status"]}


def prepare_research(args: argparse.Namespace) -> dict[str, Any]:
    out_dir = Path(args.out_dir)
    state = load_state(out_dir)
    require_terminal(state, "source_open")
    records: dict[str, Any] = {}
    for candidate in candidate_rows_from_state(state):
        cid = candidate["candidate_id"]
        source_record = state["stages"]["source_open"]["candidates"][cid]
        if source_record["status"] != "completed":
            records[cid] = stage_record("failed", error="source_open_failed", index=candidate["index"])
            continue
        source = read_json(out_dir / "source_open" / cid / "validated.json")
        payload = {
            "protocol": VERSION,
            "stage": "web_research_and_hook_analysis",
            "candidate": candidate,
            "source": source,
            "rules": {
                "research_every_opened_source": True,
                "official_then_credible_independent": True,
                "search_snippet_is_discovery_only": True,
                "product_name_is_not_hook": True,
                "persona_is_not_evidence": True,
            },
        }
        path = out_dir / "research" / cid
        write_json(path / "input.json", payload)
        records[cid] = stage_record("prepared", input_hash=hash_json(payload), index=candidate["index"])
    state["stages"]["research"] = stage_record("prepared", candidates=records)
    update_terminal_stage(state, "research")
    save_state(out_dir, state)
    return {"ok": True, "stage": "prepare_research", "researchable": sum(1 for item in records.values() if item["status"] == "prepared")}


def validate_research(args: argparse.Namespace) -> dict[str, Any]:
    out_dir = Path(args.out_dir)
    state = load_state(out_dir)
    record = state["stages"]["research"]["candidates"].get(args.candidate_id)
    if not record:
        raise RuntimeError(f"Candidate is not researchable: {args.candidate_id}")
    if record["status"] == "failed":
        record.update(stage_record("prepared", input_hash=record.get("input_hash", ""), index=record.get("index")))
    candidate = next(item for item in candidate_rows_from_state(state) if item["candidate_id"] == args.candidate_id)
    source = read_json(out_dir / "source_open" / args.candidate_id / "validated.json")
    output_path = out_dir / "research" / args.candidate_id / "output.pending.json"
    if not output_path.exists():
        raise RuntimeError(f"Current task must write {output_path}")
    try:
        validated = research_contract.validate_research_dossier(candidate, source, read_json(output_path))
        validated = {
            **validated,
            "source": source,
            "research_summary": validated.get("research_summary", ""),
        }
        write_json(out_dir / "research" / args.candidate_id / "validated.json", validated)
        if validated.get("eligible"):
            complete_stage(record, validated["dossier_hash"], dossier_hash=validated["dossier_hash"])
        else:
            raise research_contract.ContractError(validated.get("failure_reason") or "research_failed")
    except Exception as exc:
        fail_stage(record, exc)
    update_terminal_stage(state, "research")
    save_state(out_dir, state)
    return {"ok": record["status"] == "completed", "candidate_id": args.candidate_id, "status": record["status"]}


def prepare_stage1(args: argparse.Namespace) -> dict[str, Any]:
    out_dir = Path(args.out_dir)
    state = load_state(out_dir)
    require_terminal(state, "research")
    facts = read_json(out_dir / "private_persona" / "persona_facts.private.json")
    examples = read_json(out_dir / "private_persona" / "judgment_and_style_examples.private.json")
    candidates = candidate_rows_from_state(state)
    eligible_ids = completed_candidate_ids(state, "research")
    eligible = [item for item in candidates if item["candidate_id"] in eligible_ids]
    eligible = [{**item, "eligible_index": index} for index, item in enumerate(eligible)]
    if not eligible:
        raise RuntimeError("No fully researched candidates; Stage 1 is blocked")
    write_json(out_dir / "eligible_candidates.json", eligible)
    batch_state: dict[str, Any] = {}
    for batch_index, start, rows in replay.batch_slices(eligible, int(state["batch_size"])):
        batch_id = replay.batch_id_for(batch_index)
        stage_rows = []
        for offset, item in enumerate(rows):
            dossier = read_json(out_dir / "research" / item["candidate_id"] / "validated.json")
            operations = infer_judgment_operations(dossier)
            retrieved = persona_builder.retrieve_style_examples(
                examples, operations, candidate_id=item["candidate_id"], limit=min(6, len(examples))
            )
            write_json(out_dir / "persona_retrieval" / f"{item['candidate_id']}.json", {
                "candidate_id": item["candidate_id"],
                "requested_operations": operations,
                "example_ids": [example["example_id"] for example in retrieved],
                "source_hashes": [example["source_hash"] for example in retrieved],
                "retrieval_reasons": [{
                    "example_id": example["example_id"],
                    "matched_operations": sorted(set(example["judgment_operations"]) & set(operations)),
                    "reason": "该原文片段展示了本候选所需的判断动作，而非复用案例事实或句式。",
                } for example in retrieved],
            })
            stage_rows.append({"index": start + offset, **minimized_stage1_row(item, dossier, facts, retrieved)})
        payload = {
            "protocol": VERSION,
            "execution_surface": "current_codex_task",
            "stage": "persona_native_editorial_decision",
            "batch_id": batch_id,
            "rows": stage_rows,
            "skill": {"path": str(runner.SKILL_MD), "sha256": file_hash(runner.SKILL_MD)},
            "persona_manifest_hash": read_json(out_dir / "private_persona" / "persona_provenance_manifest.json")["manifest_hash"],
        }
        path = out_dir / "stage1" / batch_id
        write_json(path / "input.json", payload)
        write_json(path / "schema.json", runner.editorial_decision_output_schema())
        batch_state[batch_id] = stage_record("prepared", input_hash=hash_json(payload), start_index=start, row_count=len(rows))
    state["stages"]["prepare_stage1"] = stage_record("completed", completed_at=now_iso(), output_hash=hash_json(batch_state))
    state["stages"]["stage1"] = stage_record("prepared", batches=batch_state)
    save_state(out_dir, state)
    return {"ok": True, "stage": "prepare_stage1", "batches": len(batch_state), "eligible_rows": len(eligible), "research_failures": state["stages"]["research"].get("failure_count", 0)}


def pool_from_state(state: dict[str, Any]) -> tuple[list[content_sampler.ContentItem], dict[str, Any]]:
    paths = [Path(value) for value in state["content_csvs"]]
    items = deterministic_replay.load_items(paths, date.fromisoformat(state["since"]))
    pre = replay.build_pre_skill_pool(items, int(state["max_skill_candidates"]))
    return items, pre


def eligible_source_rows(out_dir: Path, state: dict[str, Any]) -> list[dict[str, Any]]:
    _, pre = pool_from_state(state)
    eligible = read_json(out_dir / "eligible_candidates.json")
    rows: list[dict[str, Any]] = []
    for item in eligible:
        row = dict(pre["pre_skill_pool"][int(item["index"])])
        source = read_json(out_dir / "source_open" / item["candidate_id"] / "validated.json")
        is_video = str(source.get("platform") or "").lower() in {"douyin", "抖音", "x"} or source.get("page_identity", {}).get("kind") in {"douyin_video", "x_status"}
        has_independent_title = bool(source.get("independent_title_verified"))
        row["原始来源标题"] = str(source.get("exact_title") or "") if (not is_video or has_independent_title) else ""
        row["原始发布文案"] = str(source.get("caption_body") or "") if is_video else ""
        row["来源链接"] = str(source.get("final_url") or source.get("exact_url") or "")
        rows.append(row)
    return rows


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
    start = int(record["start_index"])
    eligible_rows = eligible_source_rows(out_dir, state)
    rows = [{key: str(value or "") for key, value in row.items()} for row in eligible_rows[start:start + int(record["row_count"])]]
    start_stage(record, record["input_hash"])
    try:
        payload = read_json(output_path)
        decisions, meta = runner.validate_stage1_payload(rows, payload, start_index=start)
        batch_candidates = read_json(out_dir / "eligible_candidates.json")[start:start + int(record["row_count"])]
        for decision, candidate in zip(decisions, batch_candidates):
            serialized = canonical_json(decision)
            if any(marker in serialized for marker in ["case_anchor", "case_id", "可调用案例", "案例证明"]):
                raise RuntimeError("Stage 1 output exposed a case anchor/citation")
            dossier = read_json(out_dir / "research" / candidate["candidate_id"] / "validated.json")
            if decision.get("research_dossier_hash") != dossier.get("dossier_hash"):
                raise RuntimeError(f"Stage 1 dossier hash mismatch for {candidate['candidate_id']}")
            known_ids = research_contract.evidence_ids(dossier)
            used_ids = research_contract.parse_evidence_ids(
                f"{decision.get('research_evidence_ids', '')},{decision.get('hook_evidence_ids', '')}"
            )
            unknown_ids = sorted(used_ids - known_ids)
            if unknown_ids:
                raise RuntimeError(f"Stage 1 used unknown evidence IDs: {unknown_ids}")
            research_contract.validate_claim_trace(decision, dossier)
            research_contract.validate_recommendation_research_eligibility(decision, dossier)
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
        "rules": {
            "strict_bijection": True,
            "ranking_is_order_only": True,
            "selection_cap": None,
            "no_truncation_or_eligibility_downgrade": True,
            "tradeoff_required_for_every_row": True,
        },
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
        complete_stage(
            record,
            hash_json(ranked),
            raw_output_hash=raw_output_hash,
            row_count=len(ranked),
            ranking_bijection_ok=True,
            recommended_count=sum(1 for item in ranked if item.get("locked_decision") == "select"),
        )
        if previous_output_hash and previous_output_hash != raw_output_hash:
            invalidate_downstream(state, "global_ranking", "Global ranking output changed")
    except Exception as exc:
        fail_stage(record, exc)
        save_state(out_dir, state)
        raise
    save_state(out_dir, state)
    return {
        "ok": True,
        "stage": "validate_global_ranking",
        "rows": record["row_count"],
        "recommended_count": record["recommended_count"],
    }


def prepare_stage2(args: argparse.Namespace) -> dict[str, Any]:
    out_dir = Path(args.out_dir)
    state = load_state(out_dir)
    require_completed(state, "global_ranking")
    ranked = read_json(out_dir / "global_ranking" / "ranked_decisions.json")
    ranked_by_index = {int(item["index"]): item for item in ranked}
    pool = eligible_source_rows(out_dir, state)
    expected_indices = set(range(len(pool)))
    if set(ranked_by_index) != expected_indices:
        raise RuntimeError("Global ranking indices do not match the eligible Stage 2 pool")
    batch_state: dict[str, Any] = {}
    for batch_index, start, rows in replay.batch_slices(pool, int(state["batch_size"])):
        batch_id = replay.batch_id_for(batch_index)
        decisions = [ranked_by_index[start + offset] for offset in range(len(rows))]
        payload = {
            "protocol": VERSION,
            "execution_surface": "current_codex_task",
            "stage": "field_mapping",
            "batch_id": batch_id,
            "rows": [
                {
                    "index": start + offset,
                    "source_facts": {
                        "candidate_id": read_json(out_dir / "eligible_candidates.json")[start + offset]["candidate_id"],
                        "source": read_json(out_dir / "research" / read_json(out_dir / "eligible_candidates.json")[start + offset]["candidate_id"] / "validated.json")["source"],
                        "research_dossier_hash": read_json(out_dir / "research" / read_json(out_dir / "eligible_candidates.json")[start + offset]["candidate_id"] / "validated.json")["dossier_hash"],
                    },
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
    ranked = read_json(out_dir / "global_ranking" / "ranked_decisions.json")
    ranked_by_index = {int(item["index"]): item for item in ranked}
    start = int(record["start_index"])
    count = int(record["row_count"])
    eligible_rows = eligible_source_rows(out_dir, state)
    rows = [{key: str(value or "") for key, value in row.items()} for row in eligible_rows[start:start + count]]
    decisions = [ranked_by_index[start + offset] for offset in range(count)]
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
            "strict_fail_closed": True,
            "prohibited_path_count": 0,
            "failed_batch_count": 0,
            "batch_count": len(batch_meta),
            "completed_batch_count": len(batch_meta),
            "batches": batch_meta,
            "global_ranking": {
                "status": "success",
                "ranking_bijection_ok": ranking_record.get("ranking_bijection_ok"),
                "stage1_decision_count": ranking_record.get("row_count"),
                "ranking_output_count": ranking_record.get("row_count"),
                "recommended_count": ranking_record.get("recommended_count"),
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
        visual_warning_paths = []
        for candidate in candidate_rows_from_state(state):
            source_path = out_dir / "source_open" / candidate["candidate_id"] / "validated.json"
            if source_path.exists() and read_json(source_path).get("visual_capture_status") == "failed":
                visual_warning_paths.append(str(source_path))
        summary["visual_capture_warning_count"] = len(visual_warning_paths)
        summary["visual_capture_warning_paths"] = visual_warning_paths
        provenance_path = out_dir / "ar020d_provenance_manifest.json"
        provenance = read_json(provenance_path)
        provenance["visual_capture_warning_count"] = len(visual_warning_paths)
        provenance["visual_capture_warning_paths"] = visual_warning_paths
        write_json(provenance_path, provenance)
        if not summary.get("quality_gate_ok"):
            raise RuntimeError("Final quality gate failed")
        source_failures = int(state["stages"]["source_open"].get("failure_count", 0) or 0)
        research_failures = int(state["stages"]["research"].get("failure_count", 0) or 0)
        failed_candidate_ids = {
            candidate_id
            for stage_name in ("source_open", "research")
            for candidate_id, item in state["stages"][stage_name].get("candidates", {}).items()
            if item.get("status") == "failed"
        }
        candidate_failures = len(failed_candidate_ids)
        if candidate_failures:
            summary.update({
                "ok": False,
                "full_run_success": False,
                "completed": True,
                "stage": "completed_with_failures",
                "survivor_quality_gate_ok": True,
                "quality_gate_ok": False,
                "candidate_failure_count": candidate_failures,
                "source_open_failure_count": source_failures,
                "research_failure_count": research_failures,
                "failure_semantics": "failed candidates were excluded before editorial decision and cards",
            })
            replay.write_json(out_dir / "skill_replay_summary.json", summary)
            replay.write_markdown_report(out_dir, summary, replay.sample_rows(rows))
            replay.write_ar020d_self_acceptance_report(out_dir, summary, replay.sample_rows(rows))
        if not candidate_failures:
            summary["full_run_success"] = True
            summary["survivor_quality_gate_ok"] = bool(summary.get("quality_gate_ok"))
        complete_stage(
            record,
            hash_json(summary),
            quality_gate_ok=not candidate_failures and bool(summary.get("quality_gate_ok")),
            survivor_quality_gate_ok=bool(summary.get("survivor_quality_gate_ok")),
            candidate_failure_count=candidate_failures,
        )
        if candidate_failures:
            record["status"] = "completed_with_failures"
    except Exception as exc:
        fail_stage(record, exc)
        save_state(out_dir, state)
        raise
    save_state(out_dir, state)
    return summary


def status(args: argparse.Namespace) -> dict[str, Any]:
    state = load_state(Path(args.out_dir))
    return {"protocol": state["protocol"], "execution_surface": state["execution_surface"], "stages": state["stages"], "writes_feishu": state["writes_feishu"], "strict_fail_closed": state["strict_fail_closed"]}


def main() -> int:
    parser = argparse.ArgumentParser(description="AR-020D current-task editorial state machine")
    sub = parser.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("prepare-source-open")
    prepare.add_argument("--out-dir", required=True)
    prepare.add_argument("--content-csv", action="append", default=[])
    prepare.add_argument("--since", default="2026-07-01")
    prepare.add_argument("--batch-size", type=int, default=3)
    prepare.add_argument("--max-skill-candidates", type=int, default=content_sampler.MAX_SKILL_REVIEW_CANDIDATES)
    prepare.add_argument("--task-id", default=os.getenv("CODEX_THREAD_ID", "current-codex-task"))
    prepare.add_argument("--persona-docx", required=True)
    for name in ["validate-source-open", "validate-research"]:
        command = sub.add_parser(name)
        command.add_argument("--out-dir", required=True)
        command.add_argument("--candidate-id", required=True)
        if name == "validate-source-open":
            command.add_argument("--evidence-json", default="")
    for name in ["prepare-research", "prepare-stage1"]:
        command = sub.add_parser(name)
        command.add_argument("--out-dir", required=True)
    for name in ["validate-stage1", "validate-stage2"]:
        command = sub.add_parser(name)
        command.add_argument("--out-dir", required=True)
        command.add_argument("--batch-id", required=True)
    for name in ["prepare-ranking", "validate-ranking", "prepare-stage2", "finalize", "status"]:
        command = sub.add_parser(name)
        command.add_argument("--out-dir", required=True)
    args = parser.parse_args()
    handlers = {
        "prepare-source-open": prepare_source_open,
        "validate-source-open": validate_source_open,
        "prepare-research": prepare_research,
        "validate-research": validate_research,
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
