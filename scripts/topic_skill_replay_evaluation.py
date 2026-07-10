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
    "knowledge_base": ["Codex", "Obsidian", "知识库", "RAG"],
    "storyboard": ["多宫格", "故事板", "分镜"],
    "codex_ppt": ["Codex", "PPT", "Word Brief", "可编辑PPT"],
    "agent_feishu_desk": ["Claude Cowork", "飞书", "选题台", "执行台", "任务边界"],
    "ai_video_director": ["AIGC", "AI视频", "短剧", "成片", "视频交付"],
    "ai_hot_observe": ["AI Hot", "AIHOT", "MIRA", "Claude", "企业", "增长", "融资", "行业"],
}
SAMPLE_LABELS = {
    "knowledge_base": "知识库 / 信息资产",
    "storyboard": "故事板 / 分镜观察",
    "codex_ppt": "Codex PPT / 方案交付",
    "agent_feishu_desk": "Agent / 飞书执行台",
    "ai_video_director": "AI导演 / 视频交付",
    "ai_hot_observe": "AI Hot / 观察池",
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
) -> tuple[list[dict[str, str]], dict[str, Any], str]:
    rows = [{key: str(value or "") for key, value in row.items()} for row in pool]
    if args.engine == "codex":
        enriched, meta = editorial_skill_runner.run_codex_skill(rows, args.codex_model, timeout or args.timeout)
        return enriched, meta, "codex"
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
    out["execution_note"] = (
        "Codex exec 按嵌入的 ai-account-editorial-director repo mirror/persona/context 执行主编合约；"
        "未在本进程内另行调用全局私有 Skill 工具。最终证据以 guard-applied rows 为准。"
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
            rows, meta, engine = run_skill(batch, args, per_batch_timeout)
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
        elif level in {"暂存观察", "可选候选"}:
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

        samples = sample_rows(skill_rows)
        write_csv(out_dir / "skill_sample_table.csv", samples)
        failed_batch_count = int(engine_meta.get("failed_batch_count", 0) or 0)
        contract_failure_count = len(classified["contract_failures"])
        fallback_row_count = len(classified["fallback_rows"])
        title_quality_failure_count = sum(1 for row in title_rows if row.get("title_quality_status") == "fail")
        title_quality_warning_count = sum(1 for row in title_rows if row.get("title_quality_status") == "warn")
        replay_completed_ok = completed and failed_batch_count == 0
        summary = {
            "ok": replay_completed_ok,
            "completed": replay_completed_ok,
            "stage": "aggregate_success" if replay_completed_ok else "partial_batch_replay",
            "quality_gate_ok": contract_failure_count == 0 and fallback_row_count == 0 and title_quality_failure_count == 0,
            "engine": engine,
            "engine_meta": engine_meta,
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
            },
        }
        write_markdown_report(out_dir, summary, samples)
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
    parser = argparse.ArgumentParser(description="Run AR-020B real editorial Skill replay.")
    parser.add_argument("--since", default="2026-07-01")
    parser.add_argument("--content-csv", action="append", default=[], help="Specific content_items.csv path. Can be repeated.")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT))
    parser.add_argument("--engine", choices=["codex", "deterministic"], default="codex")
    parser.add_argument("--codex-model", default="")
    parser.add_argument("--timeout", type=int, default=900, help="Default timeout seconds for each real codex Skill batch unless --batch-timeout-seconds is set. Error artifacts are written on timeout/failure.")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE, help="Number of pre-skill candidates per real Skill batch. Use 0 to run as one batch.")
    parser.add_argument("--batch-timeout-seconds", type=int, default=0, help="Timeout seconds per real Skill batch. Defaults to --timeout.")
    parser.add_argument("--resume", action="store_true", help="Skip completed batch artifacts and continue remaining batches.")
    parser.add_argument("--aggregate-only", action="store_true", help="Aggregate existing successful batch artifacts without running the Skill.")
    parser.add_argument("--max-skill-candidates", type=int, default=content_sampler.MAX_SKILL_REVIEW_CANDIDATES)
    args = parser.parse_args()

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
