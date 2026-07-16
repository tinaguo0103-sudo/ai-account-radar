#!/usr/bin/env python3
"""Active static and behavioral zero-substitution gate for AR-020D."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any, Callable

import editorial_skill_runner as runner
import feishu_topic_decision_card as card
import push_today10_to_feishu as writer
import semantic_owner_dataflow as dataflow
import topic_editorial_state_machine as machine
import topic_skill_replay_evaluation as replay
import validate_ar020d_visible_closure as closure


def _case(name: str, check: Callable[[], None], results: list[dict[str, Any]]) -> None:
    try:
        check()
        results.append({"name": name, "ok": True})
    except Exception as exc:  # the gate must preserve the exact failing transformation
        results.append({"name": name, "ok": False, "error": f"{type(exc).__name__}: {exc}"})


def _assert_equal(actual: Any, expected: Any, label: str) -> None:
    if actual != expected:
        raise AssertionError(f"{label}: {actual!r} != {expected!r}")


def _assert_not_in(needle: str, haystack: str, label: str) -> None:
    if needle in haystack:
        raise AssertionError(f"{label}: leaked {needle!r}")


def sentinel_row() -> dict[str, str]:
    row = {
        field: f"SENTINEL__{group}__{field}"
        for group, fields in dataflow.OWNER_GROUPS.items()
        for field in fields
    }
    row.update({
        "来源链接": "https://example.com/exact",
        "原始来源账号": "Source account",
        "平台": "article",
        "来源类型": "competitor",
        "内容指纹": "sentinel-fingerprint",
        "选题命题": "Canonical proposition",
        "选题标题": "Canonical card title",
        "我的切入": "Canonical natural angle",
        "研究置信度": "medium",
        "内容结构": "1. fact 2. conflict 3. decision",
        "需要补的证据": "none",
        "推荐动作": "生成脚本包",
        "今日建议级别": "推荐制作",
    })
    return row


def behavioral_sentinel_matrix() -> dict[str, Any]:
    results: list[dict[str, Any]] = []

    def report_source_identity() -> None:
        row = sentinel_row(); row["原始来源标题"] = ""; row["选题命题"] = "Codex联动Obsidian"
        _assert_equal(replay.progress_rows([row], "ready")[0]["原始来源标题"], "", "progress title")
        _assert_equal(replay.progress_event(status="ready", stage="x", row=row)["原始来源标题"], "", "event title")
        _assert_equal(replay.title_body_check_rows([row])[0]["原始来源标题"], "", "title check title")
        _assert_equal(replay.decision_rank_final_trace_rows([row])[0]["原始来源标题"], "", "trace title")
        sample = replay.sample_rows([row])[0]
        _assert_equal(sample["source_title"], "", "sample title")
        _assert_equal(sample["source_publication_copy"], row["原始发布文案"], "sample publication copy")
    _case("replay_source_identity_owner", report_source_identity, results)

    def report_rationale() -> None:
        row = sentinel_row(); row["原始来源标题"] = "Codex联动Obsidian"; row["Austin改写理由"] = ""
        sample = replay.sample_rows([row])[0]
        _assert_equal(sample["austin_rewrite_reason"], "", "Austin rewrite reason")
        _assert_equal(sample["title_thinking"], row["标题思路"], "title thinking remains independent")
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp)
            replay.write_ar020d_self_acceptance_report(out, {}, [sample])
            text = (out / "AR020D_DEV_SELF_ACCEPTANCE.md").read_text(encoding="utf-8")
            _assert_not_in(f"Austin rewrite reason: {row['标题思路']}", text, "self acceptance rationale")
    _case("replay_rationale_owner", report_rationale, results)

    def source_open_title() -> None:
        row = sentinel_row(); row["原始来源标题"] = ""
        _assert_equal(machine.source_open_candidate(row, 0)["csv_title"], "", "source-open expected title")
    _case("source_open_title_owner", source_open_title, results)

    def writer_all_groups() -> None:
        row = sentinel_row()
        canonical = ["原始来源标题", "原始发布文案", "研究摘要", "受众钩子", "主编判断摘要", "标题思路", "我的切入"]
        for field in canonical:
            probe = dict(row); probe[field] = ""
            mapped = writer.map_row(probe, 1, "2026-07-14", "sentinel")
            _assert_equal(mapped[field], "", f"writer {field}")
        probe = dict(row); probe["选题命题"] = ""
        _assert_equal(writer.map_row(probe, 1, "2026-07-14", "sentinel")["选题标题"], "", "writer proposition")
    _case("writer_owner_matrix", writer_all_groups, results)

    def visible_snapshot() -> None:
        row = sentinel_row(); row["选题命题"] = ""
        try:
            closure.expected_staging_rows_from_original([row], "[TEST] ")
        except closure.VisibleClosureError as exc:
            if "选题命题" not in str(exc):
                raise
        else:
            raise AssertionError("missing proposition did not fail closed")
        row = sentinel_row(); row["我的切入"] = ""
        try:
            closure.expected_staging_rows_from_original([row], "[TEST] ")
        except closure.VisibleClosureError as exc:
            if "我的切入" not in str(exc):
                raise
        else:
            raise AssertionError("missing natural angle did not fail closed")
    _case("visible_snapshot_required_owners", visible_snapshot, results)

    def card_owner_matrix() -> None:
        row = sentinel_row(); row["选题标题"] = ""
        _assert_equal(card.candidate_title(row), "", "card title")
        row = sentinel_row(); row["原始来源标题"] = ""
        markdown = card.card_markdown_for_candidate(1, row)
        if "原始标题：平台未提供独立标题" not in markdown:
            raise AssertionError("card did not render title unavailable placeholder")
        row = sentinel_row(); row["研究摘要"] = ""
        markdown = card.card_markdown_for_candidate(1, row)
        _assert_not_in(f"来源摘要：{row['受众钩子']}", markdown, "card research summary")
        row = sentinel_row(); row["我的切入"] = ""
        markdown = card.card_markdown_for_candidate(1, row)
        _assert_not_in(f"Austin 角度：{row['natural_austin_angle']}", markdown, "card natural angle")
    _case("card_owner_matrix", card_owner_matrix, results)

    def stage_lock_owner_wins() -> None:
        row = sentinel_row()
        decision = {
            "locked_decision": "select", "locked_recommendation_status": "生成脚本包",
            "locked_daily_level": "推荐制作", "locked_should_produce": "是",
            "locked_title_permission": "可发布标题", "locked_global_rank_position": "1",
            "locked_global_tradeoff_reason": "tradeoff", "selected_visible_title": "Decision title",
            "natural_austin_angle": "Decision angle", "title_rationale": "Decision rationale",
            "public_decision_summary": "Decision summary",
            "aihot_significance_rationale": "Evidence-bound AIHOT significance",
        }
        mapped = runner.reapply_locked_stage2_fields(row, decision)
        _assert_equal(mapped["选题命题"], "Decision title", "stage title")
        _assert_equal(mapped["我的切入"], "Decision angle", "stage angle")
        _assert_equal(mapped["主编判断摘要"], "Decision summary", "stage summary")
        _assert_equal(mapped["标题思路"], "Decision rationale", "stage rationale")
        _assert_equal(mapped["AIHOT重大性说明"], "Evidence-bound AIHOT significance", "stage AIHOT significance")
        if not runner.raw_stage2_drift_issues(decision, {"AIHOT重大性说明": "Stage2-authored value"}):
            raise AssertionError("Stage2 AIHOT owner mutation was not blocked")
    _case("stage_locked_owner_mapping", stage_lock_owner_wins, results)

    failures = [row for row in results if not row["ok"]]
    return {"ok": not failures, "case_count": len(results), "failure_count": len(failures), "cases": results}


def run_gate() -> dict[str, Any]:
    static_violations = dataflow.audit_active_paths()
    behavioral = behavioral_sentinel_matrix()
    return {
        "ok": not static_violations and behavioral["ok"],
        "static": {"ok": not static_violations, "violation_count": len(static_violations), "violations": static_violations},
        "behavioral": behavioral,
    }


def main() -> int:
    result = run_gate()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
