#!/usr/bin/env python3
"""AR-020B real Skill replay for 2026-07-01+ content libraries.

This tool does not write Feishu. Deterministic code only builds a broad review
pool and source-evidence context; the ai-account-editorial-director Skill owns
the user-visible topic fields.
"""
from __future__ import annotations

import argparse
import collections
import csv
import json
import re
import traceback
from datetime import date, datetime
from pathlib import Path
from time import monotonic
from typing import Any

import content_sampler
import editorial_skill_runner
import topic_field_contract as field_contract
import topic_flow_rework as flow
import topic_replay_evaluation as deterministic_replay


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = Path("/private/tmp/ar020b_skill_replay")
DEFAULT_BATCH_SIZE = 5
SAMPLE_KEYWORDS = {
    "codex_obsidian": ["Codex联动Obsidian", "Obsidian", "知识库"],
    "storyboard": ["多宫格故事板", "故事板2.0", "多宫格"],
    "codex_ppt": ["Codex生成可编辑PPT", "可编辑 PPT", "ai生成ppt"],
    "claude_cowork": ["Claude Cowork"],
    "ai_video_director": ["AIGC", "AI视频导演", "AI视频", "短剧", "成片", "视频交付"],
    "mira_world_model": ["MIRA", "实时世界模型", "20 FPS"],
    "agent_execution": ["我们到底在用 agent 的什么能力", "Agent真正有用", "企业用 Agent，真正买的不是聊天能力"],
}
SAMPLE_LABELS = {
    "codex_obsidian": "知识库 / 信息资产",
    "storyboard": "故事板 / 分镜观察",
    "codex_ppt": "Codex PPT / 方案交付",
    "claude_cowork": "Agent / 飞书执行台",
    "ai_video_director": "AI导演 / 视频交付",
    "mira_world_model": "AI Hot / 观察池",
    "agent_execution": "Agent / 业务执行边界",
}
HASHTAG_PATTERN = re.compile(r"\s*#[^\s#]+")


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def clean_source_text(value: Any) -> str:
    text = str(value or "")
    text = text.replace("\u200b", " ").replace("\ufeff", " ")
    text = re.sub(r"https?://\S+", "", text)
    text = HASHTAG_PATTERN.sub("", text)
    return re.sub(r"\s+", " ", text).strip(" ，,。；;：:|-")


def truncate_on_sentence(value: str, limit: int) -> str:
    text = clean_source_text(value)
    if len(text) <= limit:
        return text
    window = text[:limit]
    cut = max(window.rfind(mark) for mark in ["。", "！", "？", "；", ";", "，", ","])
    if cut >= max(18, limit // 2):
        return window[: cut + 1].rstrip()
    return window.rstrip(" ，,。；;：:") + "..."


def extract_original_title(value: Any) -> str:
    text = clean_source_text(value)
    if not text:
        return ""
    first_sentence = re.split(r"[。！？!?]\s*", text, maxsplit=1)[0].strip()
    if 8 <= len(first_sentence) <= 56:
        return first_sentence
    first_chunk = first_sentence.split(" ", 1)[0].strip()
    if 8 <= len(first_chunk) <= 42:
        return first_chunk
    return truncate_on_sentence(first_sentence or text, 56)


def original_title_hook(row: dict[str, Any]) -> str:
    if row.get("原始标题钩子"):
        return clean_source_text(row.get("原始标题钩子"))
    source = row.get("原始来源标题") or row.get("来源内容") or row.get("来源标题")
    title = extract_original_title(source)
    if not title:
        return ""
    hook_terms: list[str] = []
    if any(term in title for term in ["Codex", "Obsidian", "PPT", "Mx-Shell", "Skill", "Claude", "Agent", "MIRA"]):
        hook_terms.append("工具组合")
    if any(term in title for term in ["知识库", "可编辑", "一键", "简单", "无需", "开放公测", "联动", "搭建"]):
        hook_terms.append("结果承诺")
    if any(term in title for term in ["教程", "实战", "手把手", "5步", "必备"]):
        hook_terms.append("学习入口")
    label = " / ".join(hook_terms) if hook_terms else "来源表达"
    return f"{label}：{title}"


def append_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    existing_fieldnames: list[str] = []
    existing_rows: list[dict[str, Any]] = []
    if path.exists():
        with path.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            existing_fieldnames = list(reader.fieldnames or [])
            existing_rows = list(reader)
    fieldnames = list(existing_fieldnames)
    for row in existing_rows + rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    all_rows = existing_rows + rows
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(all_rows)


def progress_rows(pool: list[dict[str, Any]], status: str, note: str = "") -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, row in enumerate(pool):
        rows.append({
            "candidate_index": index,
            "status": status,
            "updated_at": now_iso(),
            "note": note,
            "内容指纹": row.get("内容指纹", ""),
            "原始来源标题": row.get("原始来源标题") or row.get("来源内容", ""),
            "原始来源账号": row.get("原始来源账号") or row.get("账号名/公众号名", ""),
            "来源权重类型": row.get("来源权重类型", ""),
            "主题簇": row.get("主题簇", ""),
        })
    return rows


def write_progress(out_dir: Path, pool: list[dict[str, Any]], status: str, note: str = "") -> None:
    write_csv(out_dir / "skill_replay_progress.csv", progress_rows(pool, status, note))


def append_progress(out_dir: Path, rows: list[dict[str, Any]]) -> None:
    append_csv(out_dir / "skill_replay_progress.csv", rows)


def progress_event(
    *,
    status: str,
    stage: str,
    note: str = "",
    batch_index: int | None = None,
    batch_id: str = "",
    candidate_index: int | None = None,
    row: dict[str, Any] | None = None,
) -> dict[str, Any]:
    event: dict[str, Any] = {
        "updated_at": now_iso(),
        "status": status,
        "stage": stage,
        "note": note,
        "batch_index": "" if batch_index is None else batch_index,
        "batch_id": batch_id,
        "candidate_index": "" if candidate_index is None else candidate_index,
    }
    if row:
        event.update({
            "内容指纹": row.get("内容指纹", ""),
            "原始来源标题": row.get("原始来源标题") or row.get("来源内容", ""),
            "原始来源账号": row.get("原始来源账号") or row.get("账号名/公众号名", ""),
            "来源权重类型": row.get("来源权重类型", ""),
            "主题簇": row.get("主题簇", ""),
        })
    return event


def batch_progress_events(
    batch: list[dict[str, Any]],
    *,
    status: str,
    stage: str,
    note: str,
    batch_index: int,
    batch_id: str,
    start_candidate_index: int,
) -> list[dict[str, Any]]:
    return [
        progress_event(
            status=status,
            stage=stage,
            note=note,
            batch_index=batch_index,
            batch_id=batch_id,
            candidate_index=start_candidate_index + offset,
            row=row,
        )
        for offset, row in enumerate(batch)
    ]


def reset_progress(out_dir: Path, *, resume: bool, aggregate_only: bool) -> None:
    if resume or aggregate_only:
        return
    progress_path = out_dir / "skill_replay_progress.csv"
    if progress_path.exists():
        progress_path.unlink()


def timeout_seconds(args: argparse.Namespace) -> int:
    return int(getattr(args, "batch_timeout_seconds", 0) or getattr(args, "timeout", 0) or 0)


def write_error_artifacts(
    out_dir: Path,
    args: argparse.Namespace,
    stage: str,
    error: BaseException,
    *,
    csv_paths: list[Path],
    content_items: int = 0,
    candidate_count: int = 0,
    pre_skill_pool: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    error_text = str(error)
    payload = {
        "ok": False,
        "completed": False,
        "stage": stage,
        "error_type": type(error).__name__,
        "error": error_text,
        "traceback_tail": traceback.format_exc()[-4000:],
        "generated_at": now_iso(),
        "engine": args.engine,
        "since": args.since,
        "timeout_seconds": timeout_seconds(args),
        "input_files": [str(path) for path in csv_paths if path.exists()],
        "content_items": content_items,
        "candidate_count": candidate_count,
        "pre_skill_pool_count": len(pre_skill_pool or []),
        "writes_feishu": False,
        "outputs": {
            "candidate_universe": str(out_dir / "candidate_universe.csv"),
            "pre_skill_candidates": str(out_dir / "pre_skill_candidates.csv"),
            "skill_replay_progress": str(out_dir / "skill_replay_progress.csv"),
            "skill_replay_error": str(out_dir / "skill_replay_error.json"),
            "skill_replay_error_report": str(out_dir / "skill_replay_error.md"),
            "skill_replay_summary": str(out_dir / "skill_replay_summary.json"),
        },
    }
    write_json(out_dir / "skill_replay_error.json", payload)
    write_json(out_dir / "skill_replay_summary.json", payload)
    lines = [
        "# AR-020C Skill Replay Error",
        "",
        "本报告表示 replay 未完整完成；不写飞书、不发 Topic Card、不触发 06。",
        "",
        f"- stage: {stage}",
        f"- error_type: {type(error).__name__}",
        f"- error: {error_text}",
        f"- timeout_seconds: {timeout_seconds(args)}",
        f"- content_items: {content_items}",
        f"- candidate_count: {candidate_count}",
        f"- pre_skill_pool_count: {len(pre_skill_pool or [])}",
        "",
        "## Action",
        "- 检查 `skill_replay_progress.csv` 判断是否卡在 Skill 执行前、执行中或输出后。",
        "- 如为 timeout，先用较小 `--max-skill-candidates` 复现，再回到完整候选池。",
        "- 不要用 deterministic fallback 代替 real Skill replay 作为内容质量证据。",
    ]
    (out_dir / "skill_replay_error.md").write_text("\n".join(lines), encoding="utf-8")
    return payload


def build_pre_skill_pool(items: list[content_sampler.ContentItem], max_candidates: int) -> dict[str, Any]:
    item_by_fp = {item.fingerprint: item for item in items}
    breakdown_rows = [content_sampler.breakdown(item) for item in items]
    candidates = [
        content_sampler.topic_from_breakdown(row, item_by_fp[row["内容指纹"]])
        for row in breakdown_rows
        if row["是否进入候选初筛"] == "是"
    ]
    candidates = content_sampler.apply_editorial_judgement(candidates, item_by_fp)
    selected = content_sampler.select_skill_review_candidates(candidates)[:max_candidates]
    return {
        "items": [content_sampler.item_row(item) for item in items],
        "breakdowns": breakdown_rows,
        "candidates": candidates,
        "pre_skill_pool": selected,
        "item_by_fp": item_by_fp,
    }


def run_skill(
    pool: list[dict[str, Any]],
    args: argparse.Namespace,
    timeout: int | None = None,
    artifact_dir: Path | None = None,
) -> tuple[list[dict[str, str]], dict[str, Any], str]:
    rows = [{key: str(value or "") for key, value in row.items()} for row in pool]
    if args.engine == "codex":
        raise RuntimeError(
            "Legacy nested Codex replay is disabled for AR-020D. Use "
            "scripts/topic_editorial_state_machine.py so the current Codex task "
            "writes Stage 1, global ranking, and Stage 2 artifacts directly."
        )
    enriched = editorial_skill_runner.normalize_batch([editorial_skill_runner.enrich(row) for row in rows])
    return enriched, {"mode": "explicit_deterministic", "fallback_only": True, "not_editorial_quality": True}, "deterministic"


def batch_id_for(index: int) -> str:
    return f"batch_{index:03d}"


def batch_path(out_dir: Path, batch_id: str) -> Path:
    return out_dir / "batches" / batch_id


def batch_meta_path(out_dir: Path, batch_id: str) -> Path:
    return batch_path(out_dir, batch_id) / "meta.json"


def batch_output_path(out_dir: Path, batch_id: str) -> Path:
    return batch_path(out_dir, batch_id) / "skill_rows.csv"


def completed_batch_rows(out_dir: Path, batch_id: str) -> list[dict[str, str]]:
    meta_path = batch_meta_path(out_dir, batch_id)
    rows_path = batch_output_path(out_dir, batch_id)
    if not meta_path.exists() or not rows_path.exists():
        return []
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if meta.get("status") != "success":
        return []
    return read_csv(rows_path)


def completed_rows_match_rank_lock(rows: list[dict[str, str]], decisions: list[dict[str, Any]]) -> bool:
    if len(rows) != len(decisions):
        return False
    for row, decision in zip(rows, decisions):
        expected = {
            "global_rank_hash": decision.get("global_rank_hash", ""),
            "locked_daily_level": decision.get("locked_daily_level", ""),
            "locked_recommendation_status": decision.get("locked_recommendation_status", ""),
            "locked_should_produce": decision.get("locked_should_produce", ""),
        }
        for field, value in expected.items():
            if str(row.get(field, "") or "") != str(value or ""):
                return False
    return True


def stage1_decisions_path(out_dir: Path, batch_id: str) -> Path:
    return batch_path(out_dir, batch_id) / "stage1_decisions.json"


def completed_stage1_decisions(out_dir: Path, batch_id: str) -> list[dict[str, Any]]:
    path = stage1_decisions_path(out_dir, batch_id)
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return payload
    return []


def write_batch_meta(out_dir: Path, batch_id: str, payload: dict[str, Any]) -> None:
    write_json(batch_meta_path(out_dir, batch_id), payload)


def batch_slices(pool: list[dict[str, Any]], batch_size: int) -> list[tuple[int, int, list[dict[str, Any]]]]:
    size = max(1, batch_size)
    batches: list[tuple[int, int, list[dict[str, Any]]]] = []
    for batch_index, start in enumerate(range(0, len(pool), size)):
        batches.append((batch_index, start, pool[start:start + size]))
    return batches


def final_batch_notes(rows: list[dict[str, Any]]) -> str:
    """Summarize final guard-applied row state for PM-facing batch evidence."""
    level_counts = collections.Counter(str(row.get("今日建议级别") or row.get("候选状态") or "未知") for row in rows)
    action_counts = collections.Counter(str(row.get("推荐动作") or "未知") for row in rows)
    generated_titles = [
        str(row.get("选题命题") or row.get("可发布标题") or row.get("原始来源标题") or "")[:40]
        for row in rows
        if str(row.get("推荐动作") or "") == "生成脚本包"
    ]
    failures = sum(1 for row in rows if row.get("field_contract_status") == "fail")
    title_failures = sum(1 for row in rows if row.get("title_quality_status") == "fail")
    title_warnings = sum(1 for row in rows if row.get("title_quality_status") == "warn")
    level_text = "，".join(f"{key}={value}" for key, value in sorted(level_counts.items())) or "无"
    action_text = "，".join(f"{key}={value}" for key, value in sorted(action_counts.items())) or "无"
    generated_text = "；".join(title for title in generated_titles if title) or "无"
    return (
        "final_guard_state："
        f"建议级别[{level_text}]；推荐动作[{action_text}]；"
        f"生成脚本包候选[{generated_text}]；"
        f"field_contract_fail={failures}；title_quality_fail={title_failures}；title_quality_warn={title_warnings}。"
    )


def final_engine_meta(meta: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Keep model metadata but make PM-facing notes reflect final row state."""
    out = dict(meta or {})
    raw_notes = str(out.get("batch_notes") or "")
    if raw_notes and not out.get("pre_guard_batch_notes"):
        out["pre_guard_batch_notes"] = raw_notes.replace(
            "未调用外部 Skill",
            "未额外调用外部工具；本批由 Codex 按嵌入的 ai-account-editorial-director 合约执行",
        )
    out["batch_notes"] = final_batch_notes(rows)
    execution_surface = str(out.get("execution_surface") or "")
    if execution_surface == "current_codex_task":
        out["execution_note"] = (
            "当前 Codex 任务按 repo mirror/persona/context 直接执行主编状态机；"
            "未启动 nested codex exec、API 或子代理。最终证据以 guard-applied rows 为准。"
        )
    else:
        out["execution_note"] = (
            "Legacy nested replay artifact；该执行路径已禁用，不得作为当前主编质量证据。"
            "新证据必须来自 execution_surface=current_codex_task 状态机。"
        )
    return out


def run_skill_batches(
    pool: list[dict[str, Any]],
    args: argparse.Namespace,
    out_dir: Path,
) -> tuple[list[dict[str, str]], dict[str, Any], str, bool]:
    """Run real Skill replay in auditable batches.

    Returns skill rows, batch meta, engine name, and whether all batches
    completed successfully. Completed batch outputs are durable artifacts, so a
    later ``--resume`` can skip them and continue the remaining batches.
    """
    if args.engine == "codex":
        raise RuntimeError(
            "Nested Codex replay is legacy-disabled. Use topic_editorial_state_machine.py "
            "with execution_surface=current_codex_task."
        )

    batch_size = int(getattr(args, "batch_size", 0) or len(pool) or 1)
    per_batch_timeout = timeout_seconds(args)
    all_rows: list[dict[str, str]] = []
    batch_meta: list[dict[str, Any]] = []
    engine = args.engine
    all_ok = True
    for batch_index, start_index, batch in batch_slices(pool, batch_size):
        batch_id = batch_id_for(batch_index)
        directory = batch_path(out_dir, batch_id)
        directory.mkdir(parents=True, exist_ok=True)
        write_csv(directory / "input.csv", batch)

        if getattr(args, "resume", False):
            completed_rows = completed_batch_rows(out_dir, batch_id)
            if completed_rows:
                all_rows.extend(completed_rows)
                meta = json.loads(batch_meta_path(out_dir, batch_id).read_text(encoding="utf-8"))
                meta = {**meta, "resume_status": "skipped_completed", "resume_checked_at": now_iso()}
                batch_meta.append(meta)
                append_progress(out_dir, batch_progress_events(
                    batch,
                    status="batch_skip_completed",
                    stage="real_skill_replay",
                    note="resume skipped completed batch",
                    batch_index=batch_index,
                    batch_id=batch_id,
                    start_candidate_index=start_index,
                ))
                continue

        started_at = now_iso()
        started_monotonic = monotonic()
        append_progress(out_dir, batch_progress_events(
            batch,
            status="batch_start",
            stage="real_skill_replay",
            note=f"engine={args.engine}; batch_timeout={per_batch_timeout}s; rows={len(batch)}",
            batch_index=batch_index,
            batch_id=batch_id,
            start_candidate_index=start_index,
        ))
        try:
            rows, meta, engine = run_skill(batch, args, per_batch_timeout, artifact_dir=directory)
            meta = final_engine_meta(meta, rows)
            duration_ms = int((monotonic() - started_monotonic) * 1000)
            write_csv(batch_output_path(out_dir, batch_id), rows)
            payload = {
                "batch_id": batch_id,
                "batch_index": batch_index,
                "status": "success",
                "started_at": started_at,
                "finished_at": now_iso(),
                "duration_ms": duration_ms,
                "row_count": len(rows),
                "input_count": len(batch),
                "engine": engine,
                "engine_meta": meta,
                "timeout_seconds": per_batch_timeout,
                "outputs": {
                    "input": str(directory / "input.csv"),
                    "skill_rows": str(batch_output_path(out_dir, batch_id)),
                    "meta": str(batch_meta_path(out_dir, batch_id)),
                },
            }
            write_batch_meta(out_dir, batch_id, payload)
            batch_meta.append(payload)
            all_rows.extend(rows)
            append_progress(out_dir, batch_progress_events(
                batch,
                status="batch_success",
                stage="real_skill_replay",
                note=f"rows={len(rows)}; duration_ms={duration_ms}",
                batch_index=batch_index,
                batch_id=batch_id,
                start_candidate_index=start_index,
            ))
        except Exception as exc:  # Keep later batches and existing outputs recoverable.
            all_ok = False
            duration_ms = int((monotonic() - started_monotonic) * 1000)
            payload = {
                "batch_id": batch_id,
                "batch_index": batch_index,
                "status": "failed",
                "started_at": started_at,
                "finished_at": now_iso(),
                "duration_ms": duration_ms,
                "input_count": len(batch),
                "engine": args.engine,
                "timeout_seconds": per_batch_timeout,
                "error_type": type(exc).__name__,
                "error": str(exc),
                "traceback_tail": traceback.format_exc()[-4000:],
                "outputs": {
                    "input": str(directory / "input.csv"),
                    "meta": str(batch_meta_path(out_dir, batch_id)),
                    "error": str(directory / "error.json"),
                },
            }
            write_batch_meta(out_dir, batch_id, payload)
            write_json(directory / "error.json", payload)
            batch_meta.append(payload)
            append_progress(out_dir, batch_progress_events(
                batch,
                status="batch_failed",
                stage="real_skill_replay",
                note=f"{type(exc).__name__}: {str(exc)[:240]}",
                batch_index=batch_index,
                batch_id=batch_id,
                start_candidate_index=start_index,
            ))
    engine_meta = {
        "mode": "batched_skill_replay",
        "batch_size": batch_size,
        "batch_timeout_seconds": per_batch_timeout,
        "batch_count": len(batch_meta),
        "completed_batch_count": sum(1 for meta in batch_meta if meta.get("status") == "success"),
        "failed_batch_count": sum(1 for meta in batch_meta if meta.get("status") == "failed"),
        "batches": batch_meta,
    }
    write_json(out_dir / "skill_replay_batches.json", engine_meta)
    return all_rows, engine_meta, engine, all_ok


def run_codex_skill_replay_batches(
    pool: list[dict[str, Any]],
    args: argparse.Namespace,
    out_dir: Path,
) -> tuple[list[dict[str, str]], dict[str, Any], str, bool]:
    """Run AR-020D as Stage 1 batches -> global ranking -> Stage 2 batches."""
    batch_size = int(getattr(args, "batch_size", 0) or len(pool) or 1)
    per_batch_timeout = timeout_seconds(args)
    all_rows_for_skill = [{key: str(value or "") for key, value in row.items()} for row in pool]
    all_decisions: list[dict[str, Any]] = []
    stage1_meta: list[dict[str, Any]] = []
    all_ok = True

    for batch_index, start_index, batch in batch_slices(all_rows_for_skill, batch_size):
        batch_id = batch_id_for(batch_index)
        directory = batch_path(out_dir, batch_id)
        directory.mkdir(parents=True, exist_ok=True)
        write_csv(directory / "input.csv", batch)
        if getattr(args, "resume", False):
            completed = completed_stage1_decisions(out_dir, batch_id)
            if completed:
                all_decisions.extend(completed)
                stage1_meta.append({
                    "batch_id": batch_id,
                    "batch_index": batch_index,
                    "status": "stage1_success",
                    "resume_status": "skipped_completed_stage1",
                    "row_count": len(completed),
                    "input_count": len(batch),
                    "outputs": {
                        "input": str(directory / "input.csv"),
                        "stage1_decisions": str(stage1_decisions_path(out_dir, batch_id)),
                    },
                })
                append_progress(out_dir, batch_progress_events(
                    batch,
                    status="stage1_skip_completed",
                    stage="editorial_decision",
                    note="resume skipped completed Stage 1 decisions",
                    batch_index=batch_index,
                    batch_id=batch_id,
                    start_candidate_index=start_index,
                ))
                continue
        started_at = now_iso()
        started_monotonic = monotonic()
        append_progress(out_dir, batch_progress_events(
            batch,
            status="stage1_start",
            stage="editorial_decision",
            note=f"engine=codex; timeout={per_batch_timeout}s; rows={len(batch)}",
            batch_index=batch_index,
            batch_id=batch_id,
            start_candidate_index=start_index,
        ))
        try:
            decisions, meta = editorial_skill_runner.run_codex_stage1(
                batch,
                args.codex_model,
                per_batch_timeout,
                artifact_dir=directory,
                start_index=start_index,
            )
            duration_ms = int((monotonic() - started_monotonic) * 1000)
            write_json(stage1_decisions_path(out_dir, batch_id), decisions)
            payload = {
                "batch_id": batch_id,
                "batch_index": batch_index,
                "status": "stage1_success",
                "started_at": started_at,
                "finished_at": now_iso(),
                "duration_ms": duration_ms,
                "row_count": len(decisions),
                "input_count": len(batch),
                "engine": "codex",
                "engine_meta": meta,
                "timeout_seconds": per_batch_timeout,
                "outputs": {
                    "input": str(directory / "input.csv"),
                    "stage1_decisions": str(stage1_decisions_path(out_dir, batch_id)),
                    "stage1_input_sanitized": str(directory / "stage1_input_sanitized.json"),
                    "stage1_output": str(directory / "stage1_editorial_decision_output.json"),
                },
            }
            stage1_meta.append(payload)
            all_decisions.extend(decisions)
            append_progress(out_dir, batch_progress_events(
                batch,
                status="stage1_success",
                stage="editorial_decision",
                note=f"decisions={len(decisions)}; duration_ms={duration_ms}",
                batch_index=batch_index,
                batch_id=batch_id,
                start_candidate_index=start_index,
            ))
        except Exception as exc:
            all_ok = False
            duration_ms = int((monotonic() - started_monotonic) * 1000)
            payload = {
                "batch_id": batch_id,
                "batch_index": batch_index,
                "status": "stage1_failed",
                "started_at": started_at,
                "finished_at": now_iso(),
                "duration_ms": duration_ms,
                "input_count": len(batch),
                "engine": "codex",
                "timeout_seconds": per_batch_timeout,
                "error_type": type(exc).__name__,
                "error": str(exc),
                "traceback_tail": traceback.format_exc()[-4000:],
                "outputs": {
                    "input": str(directory / "input.csv"),
                    "error": str(directory / "stage1_error.json"),
                },
            }
            write_json(directory / "stage1_error.json", payload)
            stage1_meta.append(payload)
            append_progress(out_dir, batch_progress_events(
                batch,
                status="stage1_failed",
                stage="editorial_decision",
                note=f"{type(exc).__name__}: {str(exc)[:240]}",
                batch_index=batch_index,
                batch_id=batch_id,
                start_candidate_index=start_index,
            ))
    if not all_ok or len(all_decisions) != len(all_rows_for_skill):
        engine_meta = {
            "mode": "ar020d_stage1_global_rank_stage2",
            "batch_size": batch_size,
            "batch_timeout_seconds": per_batch_timeout,
            "stage1_batch_count": len(stage1_meta),
            "completed_batch_count": 0,
            "failed_batch_count": sum(1 for meta in stage1_meta if meta.get("status") == "stage1_failed"),
            "batches": stage1_meta,
        }
        write_json(out_dir / "skill_replay_batches.json", engine_meta)
        return [], engine_meta, "codex", False

    ranking_dir = out_dir / "global_ranking"
    ranking_started = now_iso()
    ranking_monotonic = monotonic()
    append_progress(out_dir, [progress_event(
        status="global_ranking_start",
        stage="global_daily_ranking",
        note=f"stage1_decisions={len(all_decisions)}; select_count={sum(1 for d in all_decisions if d.get('decision') == 'select')}",
    )])
    try:
        ranked_decisions, ranking_meta = editorial_skill_runner.run_codex_global_ranking(
            all_rows_for_skill,
            all_decisions,
            args.codex_model,
            per_batch_timeout,
            artifact_dir=ranking_dir,
        )
        ranking_meta = {
            **ranking_meta,
            "status": "success",
            "started_at": ranking_started,
            "finished_at": now_iso(),
            "duration_ms": int((monotonic() - ranking_monotonic) * 1000),
            "outputs": {
                "global_ranking_input": str(ranking_dir / "global_ranking_input.json"),
                "global_ranking_output": str(ranking_dir / "global_ranking_output.json"),
                "global_ranked_decisions": str(ranking_dir / "global_ranked_decisions.json"),
            },
        }
        append_progress(out_dir, [progress_event(
            status="global_ranking_success",
            stage="global_daily_ranking",
            note=f"recommended={ranking_meta.get('recommended_count')}; rows={len(ranked_decisions)}",
        )])
    except Exception as exc:
        all_ok = False
        ranking_meta = {
            "status": "failed",
            "started_at": ranking_started,
            "finished_at": now_iso(),
            "duration_ms": int((monotonic() - ranking_monotonic) * 1000),
            "error_type": type(exc).__name__,
            "error": str(exc),
            "traceback_tail": traceback.format_exc()[-4000:],
            "outputs": {"error": str(ranking_dir / "error.json")},
        }
        write_json(ranking_dir / "error.json", ranking_meta)
        append_progress(out_dir, [progress_event(
            status="global_ranking_failed",
            stage="global_daily_ranking",
            note=f"{type(exc).__name__}: {str(exc)[:240]}",
        )])
        engine_meta = {
            "mode": "ar020d_stage1_global_rank_stage2",
            "batch_size": batch_size,
            "batch_timeout_seconds": per_batch_timeout,
            "stage1_batches": stage1_meta,
            "global_ranking": ranking_meta,
            "completed_batch_count": 0,
            "failed_batch_count": 1,
            "batches": stage1_meta,
        }
        write_json(out_dir / "skill_replay_batches.json", engine_meta)
        return [], engine_meta, "codex", False

    all_rows: list[dict[str, str]] = []
    final_batch_meta: list[dict[str, Any]] = []
    for batch_index, start_index, batch in batch_slices(all_rows_for_skill, batch_size):
        batch_id = batch_id_for(batch_index)
        directory = batch_path(out_dir, batch_id)
        decisions = ranked_decisions[start_index:start_index + len(batch)]
        if getattr(args, "resume", False):
            completed_rows = completed_batch_rows(out_dir, batch_id)
            if completed_rows and completed_rows_match_rank_lock(completed_rows, decisions):
                all_rows.extend(completed_rows)
                meta = json.loads(batch_meta_path(out_dir, batch_id).read_text(encoding="utf-8"))
                final_batch_meta.append({**meta, "resume_status": "skipped_completed_stage2", "resume_checked_at": now_iso()})
                append_progress(out_dir, batch_progress_events(
                    batch,
                    status="stage2_skip_completed",
                    stage="field_mapping",
                    note="resume skipped completed Stage 2 rows",
                    batch_index=batch_index,
                    batch_id=batch_id,
                    start_candidate_index=start_index,
                ))
                continue
            if completed_rows:
                append_progress(out_dir, batch_progress_events(
                    batch,
                    status="stage2_rerun_rank_lock_changed",
                    stage="field_mapping",
                    note="resume found completed rows but global rank lock changed; rerunning Stage 2",
                    batch_index=batch_index,
                    batch_id=batch_id,
                    start_candidate_index=start_index,
                ))
        started_at = now_iso()
        started_monotonic = monotonic()
        append_progress(out_dir, batch_progress_events(
            batch,
            status="stage2_start",
            stage="field_mapping",
            note=f"engine=codex; timeout={per_batch_timeout}s; rows={len(batch)}",
            batch_index=batch_index,
            batch_id=batch_id,
            start_candidate_index=start_index,
        ))
        try:
            rows, meta = editorial_skill_runner.run_codex_stage2(
                batch,
                decisions,
                args.codex_model,
                per_batch_timeout,
                artifact_dir=directory,
            )
            meta = final_engine_meta({**meta, "global_ranking_meta": ranking_meta}, rows)
            duration_ms = int((monotonic() - started_monotonic) * 1000)
            write_csv(batch_output_path(out_dir, batch_id), rows)
            payload = {
                "batch_id": batch_id,
                "batch_index": batch_index,
                "status": "success",
                "started_at": started_at,
                "finished_at": now_iso(),
                "duration_ms": duration_ms,
                "row_count": len(rows),
                "input_count": len(batch),
                "engine": "codex",
                "engine_meta": meta,
                "timeout_seconds": per_batch_timeout,
                "outputs": {
                    "input": str(directory / "input.csv"),
                    "stage1_decisions": str(stage1_decisions_path(out_dir, batch_id)),
                    "skill_rows": str(batch_output_path(out_dir, batch_id)),
                    "meta": str(batch_meta_path(out_dir, batch_id)),
                },
            }
            write_batch_meta(out_dir, batch_id, payload)
            final_batch_meta.append(payload)
            all_rows.extend(rows)
            append_progress(out_dir, batch_progress_events(
                batch,
                status="stage2_success",
                stage="field_mapping",
                note=f"rows={len(rows)}; duration_ms={duration_ms}",
                batch_index=batch_index,
                batch_id=batch_id,
                start_candidate_index=start_index,
            ))
        except Exception as exc:
            all_ok = False
            duration_ms = int((monotonic() - started_monotonic) * 1000)
            payload = {
                "batch_id": batch_id,
                "batch_index": batch_index,
                "status": "failed",
                "started_at": started_at,
                "finished_at": now_iso(),
                "duration_ms": duration_ms,
                "input_count": len(batch),
                "engine": "codex",
                "timeout_seconds": per_batch_timeout,
                "error_type": type(exc).__name__,
                "error": str(exc),
                "traceback_tail": traceback.format_exc()[-4000:],
                "outputs": {
                    "input": str(directory / "input.csv"),
                    "meta": str(batch_meta_path(out_dir, batch_id)),
                    "error": str(directory / "stage2_error.json"),
                },
            }
            write_batch_meta(out_dir, batch_id, payload)
            write_json(directory / "stage2_error.json", payload)
            final_batch_meta.append(payload)
            append_progress(out_dir, batch_progress_events(
                batch,
                status="stage2_failed",
                stage="field_mapping",
                note=f"{type(exc).__name__}: {str(exc)[:240]}",
                batch_index=batch_index,
                batch_id=batch_id,
                start_candidate_index=start_index,
            ))
    engine_meta = {
        "mode": "ar020d_stage1_global_rank_stage2",
        "batch_size": batch_size,
        "batch_timeout_seconds": per_batch_timeout,
        "stage1_batches": stage1_meta,
        "global_ranking": ranking_meta,
        "batch_count": len(final_batch_meta),
        "completed_batch_count": sum(1 for meta in final_batch_meta if meta.get("status") == "success"),
        "failed_batch_count": sum(1 for meta in final_batch_meta if meta.get("status") == "failed"),
        "batches": final_batch_meta,
    }
    write_json(out_dir / "skill_replay_batches.json", engine_meta)
    return all_rows, engine_meta, "codex", all_ok


def load_completed_batch_outputs(out_dir: Path) -> tuple[list[dict[str, str]], dict[str, Any]]:
    batch_root = out_dir / "batches"
    rows: list[dict[str, str]] = []
    metas: list[dict[str, Any]] = []
    for meta_path in sorted(batch_root.glob("batch_*/meta.json")):
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        metas.append(meta)
        if meta.get("status") == "success":
            batch_rows = read_csv(meta_path.parent / "skill_rows.csv")
            rows.extend(batch_rows)
            meta = {**meta, "engine_meta": final_engine_meta(meta.get("engine_meta", {}), batch_rows)}
            metas[-1] = meta
    return rows, {
        "mode": "aggregate_existing_batches",
        "batch_count": len(metas),
        "completed_batch_count": sum(1 for meta in metas if meta.get("status") == "success"),
        "failed_batch_count": sum(1 for meta in metas if meta.get("status") == "failed"),
        "batches": metas,
    }


def refresh_engine_meta_with_final_rows(engine_meta: dict[str, Any], final_rows: list[dict[str, Any]], out_dir: Path) -> dict[str, Any]:
    """Rewrite batch notes from aggregate-final rows, preserving raw notes."""
    refreshed = dict(engine_meta or {})
    batches: list[dict[str, Any]] = []
    cursor = 0
    for meta in refreshed.get("batches", []) or []:
        next_meta = dict(meta)
        row_count = int(next_meta.get("row_count") or next_meta.get("input_count") or 0)
        batch_rows = final_rows[cursor: cursor + row_count] if row_count else []
        if next_meta.get("status") == "success" and batch_rows:
            next_meta["engine_meta"] = final_engine_meta(next_meta.get("engine_meta", {}), batch_rows)
            meta_path = batch_meta_path(out_dir, str(next_meta.get("batch_id") or ""))
            if meta_path.exists():
                write_json(meta_path, next_meta)
            cursor += row_count
        batches.append(next_meta)
    refreshed["batches"] = batches
    write_json(out_dir / "skill_replay_batches.json", refreshed)
    return refreshed


def aggregate_provenance(engine_meta: dict[str, Any], engine: str) -> dict[str, Any]:
    manifests: list[dict[str, Any]] = []
    for meta in engine_meta.get("batches", []) or []:
        manifest = ((meta.get("engine_meta") or {}).get("provenance_manifest") or {})
        if manifest:
            manifests.append(manifest)
    explicit = engine_meta.get("provenance_manifest") or {}
    if explicit and not manifests:
        manifests.append(explicit)
    real_skill_engine = engine in {"codex", "current_task"}
    first = manifests[0] if manifests else editorial_skill_runner.runtime_provenance(
        fallback_state="false" if real_skill_engine else "true"
    )
    return {
        **first,
        "engine": engine,
        "batch_manifest_count": len(manifests),
        "all_batches_same_skill_hash": len({m.get("skill_md_sha256") for m in manifests if m.get("skill_md_sha256")}) <= 1,
        "all_batches_same_persona_style_hash": len({m.get("persona_style_sha256") for m in manifests if m.get("persona_style_sha256")}) <= 1,
        "case_anchor_policy": "persona/style reference only; no row may cite cases as source evidence",
    }


def classify_rows(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    outputs = {
        "actionable": [],
        "observe": [],
        "rejected": [],
        "contract_failures": [],
        "fallback_rows": [],
    }
    for row in rows:
        row_with_status = dict(row)
        if not row_with_status.get("field_contract_status"):
            issues = field_contract.validate_field_contract(row_with_status)
            row_with_status = field_contract.mark_contract_result(row_with_status, issues)
        if row_with_status.get("fallback_only") == "true" or row_with_status.get("not_editorial_quality") == "true":
            outputs["fallback_rows"].append(row_with_status)
        if row_with_status.get("field_contract_status") == "fail":
            outputs["contract_failures"].append(row_with_status)
        level = row_with_status.get("今日建议级别") or row_with_status.get("候选状态")
        if (
            str(row_with_status.get("推荐动作") or "") in field_contract.ACTIONABLE_ACTIONS
            and row_with_status.get("field_contract_status") != "fail"
            and row_with_status.get("fallback_only") != "true"
            and row_with_status.get("not_editorial_quality") != "true"
        ):
            outputs["actionable"].append(row_with_status)
        elif level == "暂存观察":
            outputs["observe"].append(row_with_status)
        else:
            outputs["rejected"].append(row_with_status)
    return outputs


def title_body_check_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    checked = rows if any(row.get("title_quality_status") or row.get("field_contract_status") for row in rows) else field_contract.apply_batch_quality_guards(rows)
    out: list[dict[str, Any]] = []
    for row in checked:
        out.append({
            "原始来源标题": row.get("原始来源标题") or row.get("来源内容", ""),
            "原始来源账号": row.get("原始来源账号") or row.get("账号名/公众号名", ""),
            "推荐动作": row.get("推荐动作", ""),
            "今日建议级别": row.get("今日建议级别") or row.get("候选状态", ""),
            "可发布标题": row.get("可发布标题", ""),
            "选题命题": row.get("选题命题") or row.get("选题标题", ""),
            "title_pattern_family": row.get("title_pattern_family", ""),
            "title_quality_status": row.get("title_quality_status", ""),
            "title_quality_issues": row.get("title_quality_issues", ""),
            "主编判断摘要": row.get("主编判断摘要", ""),
            "标题思路": row.get("标题思路", ""),
            "field_contract_status": row.get("field_contract_status", ""),
            "field_contract_issues": row.get("field_contract_issues", ""),
        })
    return out


def decision_rank_final_trace_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    trace: list[dict[str, Any]] = []
    for row in rows:
        decision: dict[str, Any] = {}
        try:
            decision = json.loads(str(row.get("editorial_decision_json") or "{}"))
        except json.JSONDecodeError:
            decision = {}
        trace.append({
            "index": decision.get("index", ""),
            "原始来源标题": row.get("原始来源标题") or row.get("来源内容", ""),
            "Stage1_decision": decision.get("decision", ""),
            "Stage1_recommendation_status": decision.get("recommendation_status", ""),
            "Stage1_title": decision.get("selected_visible_title", ""),
            "Stage1_angle": decision.get("natural_austin_angle", ""),
            "Stage1_rationale": decision.get("title_rationale", ""),
            "global_daily_level": decision.get("locked_daily_level", ""),
            "global_should_produce": decision.get("locked_should_produce", ""),
            "global_rank_position": decision.get("locked_global_rank_position", ""),
            "global_tradeoff_reason": decision.get("locked_global_tradeoff_reason", ""),
            "ranking_complete": bool(decision.get("global_rank_id") and decision.get("global_rank_hash")),
            "raw_stage2_drift_status": row.get("raw_stage2_drift_status", ""),
            "raw_stage2_drift_issues": row.get("raw_stage2_drift_issues", ""),
            "final_今日建议级别": row.get("今日建议级别", ""),
            "final_推荐动作": row.get("推荐动作", ""),
            "final_是否建议进入制作": row.get("是否建议进入制作", ""),
            "final_选题命题": row.get("选题命题", ""),
            "final_主编判断摘要": row.get("主编判断摘要", ""),
            "stage2_invariant_status": row.get("stage2_invariant_status", ""),
            "stage2_invariant_issues": row.get("stage2_invariant_issues", ""),
            "guard_blocked": row.get("guard_blocked", ""),
            "guard_blocked_reason": row.get("guard_blocked_reason", ""),
            "field_contract_status": row.get("field_contract_status", ""),
            "title_quality_status": row.get("title_quality_status", ""),
        })
    return trace


def near_miss_rows(reverse_rows: list[Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in reverse_rows:
        if getattr(row, "potentially_better", False):
            rows.append(row.__dict__)
    return rows


def sample_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    seen: set[str] = set()
    for sample_key, keywords in SAMPLE_KEYWORDS.items():
        for row in rows:
            text = "\n".join(str(row.get(field, "")) for field in [
                "原始来源标题",
                "来源内容",
                "选题标题",
                "选题命题",
                "一句话Brief",
                "我要做的实验",
                "我的工作流痛点",
                "重点体现",
            ])
            if any(keyword.lower() in text.lower() or keyword in text for keyword in keywords):
                fp = str(row.get("内容指纹") or row.get("原始来源标题") or row.get("选题标题"))
                if fp in seen:
                    continue
                seen.add(fp)
                source_raw = row.get("原始来源标题") or row.get("来源内容", "")
                source_title = extract_original_title(source_raw)
                source_excerpt = truncate_on_sentence(source_raw, 70)
                samples.append({
                    "sample_key": sample_key,
                    "sample_label": SAMPLE_LABELS.get(sample_key, sample_key),
                    "source_title": source_title,
                    "source_excerpt": source_excerpt,
                    "source_title_hook": original_title_hook(row),
                    "austin_rewrite_reason": row.get("Austin改写理由") or row.get("标题思路", ""),
                    "source_account": row.get("原始来源账号") or row.get("账号名/公众号名", ""),
                    "skill_decision": row.get("主编筛选") or row.get("主编判断", ""),
                    "editorial_trace": row.get("主编判断摘要", ""),
                    "title_thinking": row.get("标题思路", ""),
                    "publish_title": row.get("可发布标题", ""),
                    "topic": row.get("选题命题") or row.get("选题标题", ""),
                    "brief": row.get("一句话Brief", ""),
                    "experiment": row.get("我要做的实验", ""),
                    "pain": row.get("我的工作流痛点", ""),
                    "direction": row.get("对应方向", ""),
                    "action": row.get("推荐动作", ""),
                    "status": row.get("今日建议级别") or row.get("候选状态", ""),
                    "contract_status": row.get("field_contract_status", ""),
                    "contract_issues": row.get("field_contract_issues", ""),
                    "fallback_only": row.get("fallback_only", ""),
                })
                break
    return samples


def sample_stage_artifacts(sample: dict[str, Any], batches: list[dict[str, Any]]) -> dict[str, str]:
    source_title = clean_source_text(sample.get("source_title") or "")
    if not source_title:
        return {}
    for batch in batches:
        outputs = batch.get("outputs") or {}
        input_value = outputs.get("input") or ""
        if not input_value:
            continue
        input_path = Path(input_value)
        if not input_path.exists():
            continue
        input_text = input_path.read_text(encoding="utf-8-sig")
        if source_title in input_text:
            batch_dir = Path(outputs.get("meta", "")).parent if outputs.get("meta") else input_path.parent
            return {
                "batch_id": str(batch.get("batch_id", "")),
                "stage1_input_sanitized": str(batch_dir / "stage1_input_sanitized.json"),
                "stage1_output": str(batch_dir / "stage1_editorial_decision_output.json"),
                "stage2_input_sanitized": str(batch_dir / "stage2_input_sanitized.json"),
                "stage2_output": str(batch_dir / "stage2_field_mapping_output.json"),
            }
    return {}


def write_markdown_report(out_dir: Path, summary: dict[str, Any], samples: list[dict[str, Any]]) -> None:
    lines = [
        "# AR-020C Skill Thinking Replay Report",
        "",
        "本报告只读生产内容 CSV，不写飞书、不发 Topic Card、不触发 06。",
        "",
        "## Summary",
    ]
    for key in [
        "engine",
        "content_items",
        "candidate_count",
        "pre_skill_pool_count",
        "skill_rows",
        "actionable_count",
        "observe_count",
        "rejected_count",
        "contract_failure_count",
        "fallback_row_count",
        "title_quality_failure_count",
        "near_miss_count",
    ]:
        lines.append(f"- {key}: {summary.get(key)}")
    lines.extend(["", "## PM Sample Rows"])
    for row in samples:
        lines.extend([
            f"### 内部分类：{row.get('sample_label') or row['sample_key']}",
            f"- 原始账号：{row['source_account']}",
            f"- 原始标题：{row.get('source_title') or '（无可用标题）'}",
            f"- 原始来源摘录：{row.get('source_excerpt') or '（无摘录）'}",
            f"- 原始标题钩子：{row.get('source_title_hook') or '（未识别）'}",
            f"  - status/action: {row['status']} / {row['action']}",
            f"  - direction: {row['direction']}",
            f"  - editorial trace: {row.get('editorial_trace', '')}",
            f"  - title thinking: {row.get('title_thinking', '')}",
            f"  - Austin rewrite reason: {row.get('austin_rewrite_reason', '')}",
            f"  - publish title: {row.get('publish_title', '')}",
            f"  - topic: {row['topic']}",
            f"  - experiment: {row['experiment']}",
            f"  - contract: {row['contract_status']} {row['contract_issues']}",
            "",
        ])
    lines.extend([
        "",
        "## Output Tables",
        f"- actionable: {summary.get('outputs', {}).get('skill_actionable')}",
        f"- observe: {summary.get('outputs', {}).get('skill_observe')}",
        f"- near_miss_high_fit_unselected: {summary.get('outputs', {}).get('near_miss_high_fit_unselected')}",
        f"- title_body_check: {summary.get('outputs', {}).get('title_body_check')}",
    ])
    (out_dir / "ar020c_user_sample_summary.md").write_text("\n".join(lines), encoding="utf-8")


def write_ar020d_self_acceptance_report(out_dir: Path, summary: dict[str, Any], samples: list[dict[str, Any]]) -> None:
    provenance = summary.get("provenance_manifest", {}) or {}
    engine_meta = summary.get("engine_meta", {}) or {}
    batches = engine_meta.get("batches", []) or []
    lines = [
        "# AR-020D Developer Architecture Self-Validation",
        "",
        "本报告由 replay aggregate 自动生成，只读 content_items.csv；不写飞书、不发 Topic Card、不触发 06。",
        "",
        "## Required Result",
        f"- ok: {summary.get('ok')}",
        f"- stage: {summary.get('stage')}",
        f"- completed: {summary.get('completed')}",
        f"- quality_gate_ok: {summary.get('quality_gate_ok')}",
        f"- writes_feishu: {summary.get('writes_feishu')}",
        f"- fallback_row_count: {summary.get('fallback_row_count')}",
        f"- contract_failure_count: {summary.get('contract_failure_count')}",
        f"- title_quality_failure_count: {summary.get('title_quality_failure_count')}",
        f"- title_quality_warning_count: {summary.get('title_quality_warning_count')}",
        f"- recommended_count: {summary.get('recommended_count')}",
        f"- stage2_selection_drift_count: {summary.get('stage2_selection_drift_count')}",
        f"- raw_stage2_drift_count: {summary.get('raw_stage2_drift_count')}",
        f"- guard_blocked_count: {summary.get('guard_blocked_count')}",
        f"- skill_rows: {summary.get('skill_rows')}",
        f"- actionable_count: {summary.get('actionable_count')}",
        f"- observe_count: {summary.get('observe_count')}",
        f"- candidate_failure_count: {summary.get('candidate_failure_count', 0)}",
        f"- source_open_failure_count: {summary.get('source_open_failure_count', 0)}",
        f"- research_failure_count: {summary.get('research_failure_count', 0)}",
        "",
        "## Provenance",
        f"- execution_surface: {provenance.get('execution_surface')}",
        f"- nested_model_execution: {provenance.get('nested_model_execution')}",
        f"- fallback_state: {provenance.get('fallback_state')}",
        f"- runner_version: {provenance.get('runner_version')}",
        f"- skill_dir: {provenance.get('skill_dir')}",
        f"- skill_md_sha256: {provenance.get('skill_md_sha256')}",
        f"- persona_style_path: {provenance.get('persona_style_path')}",
        f"- persona_style_sha256: {provenance.get('persona_style_sha256')}",
        f"- persona_style_embedded: {provenance.get('persona_style_embedded')}",
        f"- persona_style_reference_only: {provenance.get('persona_style_reference_only')}",
        f"- persona_style_role: {provenance.get('persona_style_role')}",
        f"- case_anchor_policy: {provenance.get('case_anchor_policy')}",
        "",
        "## Global Ranking",
        f"- status: {(engine_meta.get('global_ranking') or {}).get('status')}",
        f"- ranking_bijection_ok: {summary.get('ranking_bijection_ok')}",
        f"- stage1_decision_count: {(engine_meta.get('global_ranking') or {}).get('stage1_decision_count')}",
        f"- ranking_output_count: {(engine_meta.get('global_ranking') or {}).get('ranking_output_count')}",
        f"- recommended_count: {(engine_meta.get('global_ranking') or {}).get('recommended_count')}",
        f"- artifacts: {(engine_meta.get('global_ranking') or {}).get('outputs')}",
        f"- trace table: {out_dir / 'ar020d_decision_rank_final_trace.csv'}",
        "",
        "## Stage Artifact Evidence",
    ]
    for batch in batches:
        if batch.get("status") != "success":
            continue
        outputs = batch.get("outputs") or {}
        batch_id = str(batch.get("batch_id", ""))
        current_task = (batch.get("engine_meta") or {}).get("execution_surface") == "current_codex_task"
        batch_dir = Path(outputs.get("meta", "")).parent if outputs.get("meta") else out_dir / "batches" / batch_id
        lines.extend([
            f"- {batch_id}: status={batch.get('status')}, rows={batch.get('row_count')}, duration_ms={batch.get('duration_ms')}",
            f"  - stage1_input_sanitized: {(out_dir / 'stage1' / batch_id / 'input.json') if current_task else (batch_dir / 'stage1_input_sanitized.json')}",
            f"  - stage1_output: {(out_dir / 'stage1' / batch_id / 'output.pending.json') if current_task else (batch_dir / 'stage1_editorial_decision_output.json')}",
            f"  - stage2_input_sanitized: {(out_dir / 'stage2' / batch_id / 'input.json') if current_task else (batch_dir / 'stage2_input_sanitized.json')}",
            f"  - stage2_output: {(out_dir / 'stage2' / batch_id / 'output.pending.json') if current_task else (batch_dir / 'stage2_field_mapping_output.json')}",
        ])
    lines.extend([
        "",
        "## Proof Checklist",
        "- Stage 1 payloads are sanitized artifacts and do not include existing 04 visible fields, experiment, validation, assets, mother-scene conclusions, or deterministic title hints.",
        "- Stage 2 receives locked Stage 1 decisions and records decision hash/id; `stage2_invariant_status` must be pass in final rows.",
        "- persona-and-cases is embedded as style reference, not source evidence; rows must not expose case anchor/citation.",
        "- deterministic fallback rows are not content-quality evidence.",
        "",
        "## Six Review Categories",
    ])
    for row in samples:
        artifacts = sample_stage_artifacts(row, batches)
        if provenance.get("execution_surface") == "current_codex_task":
            batch_id = ""
            source_title = editorial_skill_runner.normalize_space(row.get("source_title"))
            for candidate_batch in batches:
                candidate_id = str(candidate_batch.get("batch_id") or "")
                input_path = out_dir / "stage1" / candidate_id / "input.json"
                if not input_path.exists():
                    continue
                payload = json.loads(input_path.read_text(encoding="utf-8"))
                if any(
                    editorial_skill_runner.normalize_space(
                        (item.get("source") or {}).get("exact_title") or item.get("original_title")
                    ) == source_title
                    for item in payload.get("rows", [])
                ):
                    batch_id = candidate_id
                    break
            if batch_id:
                artifacts = {
                    "batch_id": batch_id,
                    "stage1_input_sanitized": str(out_dir / "stage1" / batch_id / "input.json"),
                    "stage1_output": str(out_dir / "stage1" / batch_id / "output.pending.json"),
                    "stage2_input_sanitized": str(out_dir / "stage2" / batch_id / "input.json"),
                    "stage2_output": str(out_dir / "stage2" / batch_id / "output.pending.json"),
                }
        lines.extend([
            f"### {row.get('sample_label') or row.get('sample_key')}",
            f"- source_title: {row.get('source_title') or '（无可用标题）'}",
            f"- source_title_hook: {row.get('source_title_hook')}",
            f"- Austin rewrite reason: {row.get('austin_rewrite_reason')}",
            f"- visible title/proposition: {row.get('publish_title') or row.get('topic')}",
            f"- title thinking: {row.get('title_thinking')}",
            f"- decision/status/action: {row.get('status')} / {row.get('action')}",
            f"- contract: {row.get('contract_status')} {row.get('contract_issues')}",
            f"- stage artifacts: {artifacts.get('batch_id', 'not_found')}",
            f"  - stage1_input_sanitized: {artifacts.get('stage1_input_sanitized', '')}",
            f"  - stage1_output: {artifacts.get('stage1_output', '')}",
            f"  - stage2_input_sanitized: {artifacts.get('stage2_input_sanitized', '')}",
            f"  - stage2_output: {artifacts.get('stage2_output', '')}",
            "",
        ])
    lines.extend([
        "## Every Recommended Candidate",
    ])
    actionable_path = out_dir / "skill_actionable.csv"
    for row in read_csv(actionable_path) if actionable_path.exists() else []:
        lines.extend([
            f"- [{row.get('global_rank_position') or row.get('locked_global_rank_position') or '?'}] {row.get('选题命题') or row.get('可发布标题')}",
            f"  - source: {row.get('原始来源标题') or row.get('来源标题')}",
            f"  - exact_url: {row.get('来源链接')}",
            f"  - hook: {row.get('受众钩子')}",
            f"  - structure: {row.get('内容结构')}",
        ])
    lines.extend([
        "",
        "## Output Paths",
        f"- summary: {out_dir / 'skill_replay_summary.json'}",
        f"- rows: {out_dir / 'skill_replay_rows.csv'}",
        f"- title check: {out_dir / 'title_body_check.csv'}",
        f"- user sample summary: {out_dir / 'ar020c_user_sample_summary.md'}",
        f"- provenance manifest: {out_dir / 'ar020d_provenance_manifest.json'}",
        f"- decision rank final trace: {out_dir / 'ar020d_decision_rank_final_trace.csv'}",
    ])
    (out_dir / "AR020D_DEV_SELF_ACCEPTANCE.md").write_text("\n".join(lines), encoding="utf-8")


def aggregate_replay_outputs(
    out_dir: Path,
    args: argparse.Namespace,
    *,
    csv_paths: list[Path],
    items: list[content_sampler.ContentItem],
    pre: dict[str, Any],
    skill_rows: list[dict[str, Any]],
    engine_meta: dict[str, Any],
    engine: str,
    completed: bool,
) -> dict[str, Any]:
    append_progress(out_dir, [progress_event(
        status="aggregate_start",
        stage="aggregate",
        note=f"skill_rows={len(skill_rows)}; completed={str(completed).lower()}",
    )])
    try:
        skill_rows = field_contract.apply_batch_quality_guards(skill_rows)
        engine_meta = refresh_engine_meta_with_final_rows(engine_meta, skill_rows, out_dir)
        classified = classify_rows(skill_rows)
        for name, rows in classified.items():
            write_csv(out_dir / f"skill_{name}.csv", rows)
        write_csv(out_dir / "skill_replay_rows.csv", skill_rows)

        reverse_rows = flow.reverse_evaluation_rows(
            skill_rows,
            pre["candidates"],
            pre["item_by_fp"],
            max_selected=args.max_skill_candidates,
        )
        flow.write_reverse_evaluation(out_dir / "skill_reverse_evaluation.csv", reverse_rows)
        near_misses = near_miss_rows(reverse_rows)
        write_csv(out_dir / "near_miss_high_fit_unselected.csv", near_misses)
        title_rows = title_body_check_rows(skill_rows)
        write_csv(out_dir / "title_body_check.csv", title_rows)
        trace_rows = decision_rank_final_trace_rows(skill_rows)
        write_csv(out_dir / "ar020d_decision_rank_final_trace.csv", trace_rows)

        samples = sample_rows(skill_rows)
        write_csv(out_dir / "skill_sample_table.csv", samples)
        provenance = aggregate_provenance(engine_meta, engine)
        write_json(out_dir / "ar020d_provenance_manifest.json", provenance)
        failed_batch_count = int(engine_meta.get("failed_batch_count", 0) or 0)
        contract_failure_count = len(classified["contract_failures"])
        fallback_row_count = len(classified["fallback_rows"])
        title_quality_failure_count = sum(1 for row in title_rows if row.get("title_quality_status") == "fail")
        title_quality_warning_count = sum(1 for row in title_rows if row.get("title_quality_status") == "warn")
        recommended_count = sum(1 for row in skill_rows if row.get("今日建议级别") == "推荐制作")
        stage2_selection_drift_count = sum(1 for row in skill_rows if row.get("stage2_invariant_status") == "fail")
        raw_stage2_drift_count = sum(1 for row in skill_rows if row.get("raw_stage2_drift_status") == "fail")
        guard_blocked_count = sum(1 for row in skill_rows if str(row.get("guard_blocked") or "").lower() == "true")
        global_ranking_meta = engine_meta.get("global_ranking") or {}
        ranking_bijection_ok = bool(global_ranking_meta.get("ranking_bijection_ok"))
        replay_completed_ok = completed and failed_batch_count == 0
        summary = {
            "ok": replay_completed_ok,
            "completed": replay_completed_ok,
            "stage": "aggregate_success" if replay_completed_ok else "partial_batch_replay",
            "quality_gate_ok": (
                contract_failure_count == 0
                and fallback_row_count == 0
                and title_quality_failure_count == 0
                and stage2_selection_drift_count == 0
                and raw_stage2_drift_count == 0
                and guard_blocked_count == 0
                and ranking_bijection_ok
            ),
            "engine": engine,
            "engine_meta": engine_meta,
            "provenance_manifest": provenance,
            "since": args.since,
            "batch_size": getattr(args, "batch_size", ""),
            "batch_timeout_seconds": timeout_seconds(args),
            "input_files": [str(path) for path in csv_paths if path.exists()],
            "content_items": len(items),
            "candidate_count": len(pre["candidates"]),
            "pre_skill_pool_count": len(pre["pre_skill_pool"]),
            "skill_rows": len(skill_rows),
            "actionable_count": len(classified["actionable"]),
            "observe_count": len(classified["observe"]),
            "rejected_count": len(classified["rejected"]),
            "contract_failure_count": contract_failure_count,
            "fallback_row_count": fallback_row_count,
            "reverse_flags": sum(1 for row in reverse_rows if row.potentially_better),
            "near_miss_count": len(near_misses),
            "title_quality_failure_count": title_quality_failure_count,
            "title_quality_warning_count": title_quality_warning_count,
            "recommended_count": recommended_count,
            "stage2_selection_drift_count": stage2_selection_drift_count,
            "raw_stage2_drift_count": raw_stage2_drift_count,
            "guard_blocked_count": guard_blocked_count,
            "ranking_bijection_ok": ranking_bijection_ok,
            "writes_feishu": False,
            "outputs": {
                "candidate_universe": str(out_dir / "candidate_universe.csv"),
                "pre_skill_candidates": str(out_dir / "pre_skill_candidates.csv"),
                "skill_replay_batches": str(out_dir / "skill_replay_batches.json"),
                "skill_replay_progress": str(out_dir / "skill_replay_progress.csv"),
                "skill_replay_rows": str(out_dir / "skill_replay_rows.csv"),
                "skill_actionable": str(out_dir / "skill_actionable.csv"),
                "skill_observe": str(out_dir / "skill_observe.csv"),
                "skill_rejected": str(out_dir / "skill_rejected.csv"),
                "skill_contract_failures": str(out_dir / "skill_contract_failures.csv"),
                "skill_reverse_evaluation": str(out_dir / "skill_reverse_evaluation.csv"),
                "near_miss_high_fit_unselected": str(out_dir / "near_miss_high_fit_unselected.csv"),
                "title_body_check": str(out_dir / "title_body_check.csv"),
                "skill_sample_table": str(out_dir / "skill_sample_table.csv"),
                "user_sample_summary": str(out_dir / "ar020c_user_sample_summary.md"),
                "ar020d_self_acceptance": str(out_dir / "AR020D_DEV_SELF_ACCEPTANCE.md"),
                "ar020d_provenance_manifest": str(out_dir / "ar020d_provenance_manifest.json"),
                "ar020d_decision_rank_final_trace": str(out_dir / "ar020d_decision_rank_final_trace.csv"),
            },
        }
        write_markdown_report(out_dir, summary, samples)
        write_ar020d_self_acceptance_report(out_dir, summary, samples)
        write_json(out_dir / "skill_replay_summary.json", summary)
        append_progress(out_dir, [progress_event(
            status="aggregate_success" if summary["ok"] else "aggregate_partial_success",
            stage="aggregate",
            note=f"skill_rows={len(skill_rows)}; failed_batches={failed_batch_count}",
        )])
        return summary
    except Exception as exc:
        append_progress(out_dir, [progress_event(
            status="aggregate_failed",
            stage="aggregate",
            note=f"{type(exc).__name__}: {str(exc)[:240]}",
        )])
        raise


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Legacy replay aggregator. Current-task editorial replay uses topic_editorial_state_machine.py."
    )
    parser.add_argument("--since", default="2026-07-01")
    parser.add_argument("--content-csv", action="append", default=[], help="Specific content_items.csv path. Can be repeated.")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT))
    parser.add_argument("--engine", choices=["state-machine", "codex", "deterministic"], default="state-machine")
    parser.add_argument("--codex-model", default="")
    parser.add_argument("--timeout", type=int, default=900, help="Default timeout seconds for each real codex Skill batch unless --batch-timeout-seconds is set. Error artifacts are written on timeout/failure.")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE, help="Number of pre-skill candidates per real Skill batch. Use 0 to run as one batch.")
    parser.add_argument("--batch-timeout-seconds", type=int, default=0, help="Timeout seconds per real Skill batch. Defaults to --timeout.")
    parser.add_argument("--resume", action="store_true", help="Skip completed batch artifacts and continue remaining batches.")
    parser.add_argument("--aggregate-only", action="store_true", help="Aggregate existing successful batch artifacts without running the Skill.")
    parser.add_argument("--max-skill-candidates", type=int, default=content_sampler.MAX_SKILL_REVIEW_CANDIDATES)
    args = parser.parse_args()

    parser.error(
        "This legacy replay CLI is disabled before business I/O. Use "
        "topic_editorial_state_machine.py prepare-source-open. Old aggregate, deterministic, and nested model paths "
        "cannot produce AR-020D runtime or QA evidence."
    )

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    reset_progress(out_dir, resume=args.resume, aggregate_only=args.aggregate_only)
    since = date.fromisoformat(args.since)
    csv_paths = deterministic_replay.discover_content_csvs(args.content_csv)
    items: list[content_sampler.ContentItem] = []
    pre: dict[str, Any] = {"candidates": [], "pre_skill_pool": [], "item_by_fp": {}}
    try:
        items = deterministic_replay.load_items(csv_paths, since)
        pre = build_pre_skill_pool(items, args.max_skill_candidates)
        write_csv(out_dir / "pre_skill_candidates.csv", pre["pre_skill_pool"])
        write_csv(out_dir / "candidate_universe.csv", pre["candidates"])
        append_progress(out_dir, [
            progress_event(
                status="candidate_universe_built",
                stage="candidate_universe",
                note=f"content_items={len(items)}; candidate_count={len(pre['candidates'])}",
            ),
            progress_event(
                status="pre_skill_selection_built",
                stage="pre_skill_selection",
                note=f"pre_skill_pool_count={len(pre['pre_skill_pool'])}; max_skill_candidates={args.max_skill_candidates}",
            ),
        ])

        if args.aggregate_only:
            skill_rows, engine_meta = load_completed_batch_outputs(out_dir)
            engine = args.engine
            completed = bool(skill_rows) and int(engine_meta.get("failed_batch_count", 0) or 0) == 0
        else:
            skill_rows, engine_meta, engine, completed = run_skill_batches(pre["pre_skill_pool"], args, out_dir)
    except Exception as exc:
        if pre.get("pre_skill_pool"):
            append_progress(out_dir, batch_progress_events(
                pre["pre_skill_pool"],
                status="skill_replay_failed",
                stage="real_skill_replay",
                note=f"{type(exc).__name__}: {str(exc)[:240]}",
                batch_index=0,
                batch_id="unbatched",
                start_candidate_index=0,
            ))
        payload = write_error_artifacts(
            out_dir,
            args,
            "real_skill_replay" if args.engine == "codex" else "deterministic_replay",
            exc,
            csv_paths=csv_paths,
            content_items=len(items),
            candidate_count=len(pre.get("candidates", [])),
            pre_skill_pool=pre.get("pre_skill_pool", []),
        )
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 1
    if not skill_rows:
        payload = write_error_artifacts(
            out_dir,
            args,
            "real_skill_replay_batches",
            RuntimeError("No completed Skill batch outputs to aggregate."),
            csv_paths=csv_paths,
            content_items=len(items),
            candidate_count=len(pre.get("candidates", [])),
            pre_skill_pool=pre.get("pre_skill_pool", []),
        )
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 1

    try:
        summary = aggregate_replay_outputs(
            out_dir,
            args,
            csv_paths=csv_paths,
            items=items,
            pre=pre,
            skill_rows=skill_rows,
            engine_meta=engine_meta,
            engine=engine,
            completed=completed,
        )
    except Exception as exc:
        payload = write_error_artifacts(
            out_dir,
            args,
            "aggregate",
            exc,
            csv_paths=csv_paths,
            content_items=len(items),
            candidate_count=len(pre.get("candidates", [])),
            pre_skill_pool=pre.get("pre_skill_pool", []),
        )
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
