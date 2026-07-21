from __future__ import annotations

import csv
import hashlib
import json
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest import mock

import scheduled_flow_preflight as preflight
import content_sampler
import daily_pipeline
import run_daily_collection_job
import topic_editorial_state_machine as machine
import verify_today10_feishu_consistency as verifier
import apply_operational_stage2_mapping as stage2_mapping
import exact_candidate_input
import push_today10_to_feishu as topic_writer


FIELDS = [
    "来源链接", "内容指纹", "来源类型", "来源内容", "原始来源标题",
    "原始发布文案", "原始来源账号", "平台",
]


class AR040ScheduledFlowTests(unittest.TestCase):
    @staticmethod
    def topic_row(run_id: str, source_title: str, topic_title: str, url: str = "") -> dict[str, str]:
        return {
            "运行批次": run_id,
            "原始来源标题": source_title,
            "选题标题": topic_title,
            "来源链接": url,
            "推荐日期": "2026-07-21",
        }

    def test_topic_write_plan_is_url_independent_and_rerun_stable(self) -> None:
        run_id = "run_20260721_160000_ar040_devproof"
        rows = [
            self.topic_row(run_id, "source-nonempty", "candidate-nonempty", "https://unsupported.invalid/item"),
            self.topic_row(run_id, "source-empty", "candidate-empty"),
            self.topic_row(run_id, "source-survivor", "candidate-survivor", "https://example.com/item"),
        ]
        updates, creates = topic_writer.plan_topic_writes([], rows)
        self.assertEqual(updates, [])
        self.assertEqual(creates, rows)
        remote = [
            {"record_id": f"rec-{index}", "fields": dict(row)}
            for index, row in enumerate(rows, start=1)
        ]
        second_updates, second_creates = topic_writer.plan_topic_writes(remote, rows)
        self.assertEqual(second_creates, [])
        self.assertEqual([record["record_id"] for record, _row in second_updates], ["rec-1", "rec-2", "rec-3"])
        empty_record = next(record for record, row in second_updates if not row["来源链接"])
        self.assertEqual(empty_record["record_id"], "rec-2")
        self.assertEqual(
            [topic_writer.topic_candidate_business_identity(row) for row in rows],
            [topic_writer.topic_candidate_business_identity(record["fields"]) for record in remote],
        )

    def test_topic_write_plan_distinguishes_empty_urls_and_exact_runs(self) -> None:
        run_a = "run_20260721_160001_ar040_devproof"
        run_b = "run_20260721_160002_ar040_devproof"
        empty_a = self.topic_row(run_a, "source-a", "same title")
        empty_b = self.topic_row(run_a, "source-b", "same title")
        remote = [{"record_id": "rec-a", "fields": dict(empty_a)}]
        updates, creates = topic_writer.plan_topic_writes(remote, [empty_a, empty_b])
        self.assertEqual([record["record_id"] for record, _row in updates], ["rec-a"])
        self.assertEqual(creates, [empty_b])

        same_title_other_run = self.topic_row(run_b, "source-a", "same title")
        same_url_other_run = self.topic_row(run_b, "source-c", "different title", "https://example.com/shared")
        same_url_run_a = self.topic_row(run_a, "source-c", "different title", "https://example.com/shared")
        updates, creates = topic_writer.plan_topic_writes(
            remote + [{"record_id": "rec-url-a", "fields": dict(same_url_run_a)}],
            [same_title_other_run, same_url_other_run],
        )
        self.assertEqual(updates, [])
        self.assertEqual(creates, [same_title_other_run, same_url_other_run])

    def test_topic_write_plan_stops_on_duplicate_remote_identity_before_writes(self) -> None:
        row = self.topic_row("run_20260721_160003_ar040_devproof", "source", "candidate")
        records = [
            {"record_id": "rec-1", "fields": dict(row)},
            {"record_id": "rec-2", "fields": dict(row)},
        ]
        with mock.patch.object(topic_writer, "update_existing_top10") as update, \
                mock.patch.object(topic_writer, "batch_create") as create:
            with self.assertRaisesRegex(RuntimeError, "topic_candidate_remote_identity_ambiguous"):
                topic_writer.plan_topic_writes(records, [row])
        update.assert_not_called()
        create.assert_not_called()

    def test_owned_collection_artifact_keeps_display_url_without_network_open(self) -> None:
        row = {
            "来源类型": "公众号文章",
            "平台": "Web",
            "账号名/公众号名": "controlled",
            "内容标题": "trusted artifact",
            "内容链接": "https://unsupported.invalid/ordinary",
            "正文/字幕/简介片段": "trusted collected text",
            "抓取方式": "owned_staging_input",
            "抓取状态": "success",
            "内容指纹": "controlled-fingerprint",
        }
        with mock.patch.object(content_sampler, "load_json", return_value={"sources": []}), \
                mock.patch.object(content_sampler, "load_manual_items", return_value=[row]), \
                mock.patch.object(content_sampler, "extract_article") as extract_article:
            items, _logs = content_sampler.collect_items(False, Path("/tmp/owned.jsonl"))
        extract_article.assert_not_called()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].url, row["内容链接"])
        self.assertEqual(items[0].fetch_method, "owned_staging_input")
        self.assertEqual(items[0].fetch_status, "success")

    def test_owned_ordinary_url_presence_does_not_change_skill_pool_eligibility(self) -> None:
        base = {
            "来源类型": "公众号文章",
            "平台": "Web",
            "账号名/公众号名": "controlled",
            "正文/字幕/简介片段": "内容团队把 AI Agent 放进真实流程，包含输入、执行、复核、失败恢复和交付验收。",
            "抓取方式": "owned_staging_input",
            "抓取状态": "success",
        }
        rows = [
            {**base, "内容标题": "paired trusted artifact A", "内容链接": "https://unsupported.invalid/ordinary", "内容指纹": "paired-nonempty"},
            {**base, "内容标题": "paired trusted artifact B", "内容链接": "", "内容指纹": "paired-empty"},
        ]
        with mock.patch.object(content_sampler, "load_json", return_value={"sources": []}), \
                mock.patch.object(content_sampler, "load_manual_items", return_value=rows), \
                mock.patch.object(content_sampler, "extract_article") as extract_article:
            items, _logs = content_sampler.collect_items(False, Path("/tmp/paired.jsonl"))
        extract_article.assert_not_called()
        run_id = "run_20260721_123700_ar040_devproof"
        read_back = [
            {"fields": {"内容指纹": item.fingerprint, "运行批次": run_id}}
            for item in items
        ]
        ledger_identity = content_sampler.verify_content_ledger_readback(items, read_back, run_id)
        self.assertEqual(ledger_identity["planned_count"], 2)
        self.assertEqual(ledger_identity["matched_count"], 2)
        self.assertEqual(ledger_identity["run_id"], run_id)
        candidates = []
        for item in items:
            candidate = content_sampler.topic_from_breakdown(content_sampler.breakdown(item), item)
            content_sampler.ensure_publish_metadata(candidate, item)
            content_sampler.editorial_judgement(candidate, item)
            candidates.append(candidate)
        selected = content_sampler.select_skill_review_candidates(candidates)
        self.assertEqual({row["内容指纹"] for row in selected}, {"paired-nonempty", "paired-empty"})
        by_fp = {row["内容指纹"]: row for row in selected}
        self.assertEqual(by_fp["paired-nonempty"]["来源链接"], "https://unsupported.invalid/ordinary")
        self.assertEqual(by_fp["paired-empty"]["来源链接"], "")
        for row in selected:
            self.assertEqual(row["候选来源方式"], "可信采集产物")
            self.assertEqual(row["抓取方式"], "owned_staging_input")
            self.assertNotEqual(row["是否有足够内容支撑"], "不足")

        state_rows = [
            {field: row.get(field, "") for field in FIELDS}
            for row in selected
        ]
        temp, _root, result, state = self.prepare(state_rows)
        self.addCleanup(temp.cleanup)
        self.assertEqual(result["candidates"], 2)
        self.assertEqual(result["source_open_calls"], 0)
        self.assertEqual(len(machine.candidate_rows_from_state(state)), 2)

    def test_exact_candidate_input_accepts_owned_ar040_devproof_run(self) -> None:
        self.assertIsNotNone(
            exact_candidate_input.RUN_ID_RE.fullmatch("run_20260721_123700_ar040_devproof")
        )

    def test_supplied_current_run_artifact_satisfies_browser_source_readiness(self) -> None:
        probe = {
            "run_id": "run_20260721_123700_ar040_devproof",
            "coverage": {
                "invariants": {
                    "attempted_equals_planned": True,
                    "success_plus_failed_equals_attempted": True,
                    "account_lineage_unique_and_complete": True,
                },
                "failed_accounts": [],
                "per_account_artifact_counts": {"account-hash": 1},
            },
            "item_lineage": {"ok": True},
        }
        steps = [{
            "name": "validate supplied current-run Douyin source artifact",
            "returncode": 0,
        }]
        with tempfile.TemporaryDirectory() as tmp:
            probe_path = Path(tmp) / "probe.json"
            probe_path.write_text(json.dumps(probe), encoding="utf-8")
            report = daily_pipeline.downstream_usability_report(
                steps, Path(tmp), 1, probe_path,
                ingestion_closure={
                    "run_id": probe["run_id"],
                    "manual_artifact_identity_verified": True,
                    "combined_sha256": "a",
                    "content_items_sha256": "b",
                    "comparison_universe_count": 1,
                    "feishu_03_identity": {
                        "ok": True,
                        "planned_identity": {"identity_sha256": "c"},
                    },
                },
            )
        self.assertTrue(report["downstream_usable"])
        self.assertTrue(report["downstream_usable_checks"]["canonical_profile_preflight_ok"])

    def test_formal_wrapper_owned_input_builds_public_pipeline_command(self) -> None:
        commands: list[list[str]] = []

        def fake_step(name: str, command: list[str]):
            commands.append(command)
            return {"name": name, "command": command, "returncode": 0, "stdout": "", "stderr": ""}

        argv = [
            "run_daily_collection_job.py", "--allow-non-production-worktree", "--no-notify",
            "--defer-editorial", "--owned-source-input-only",
            "--run-id", "run_20260721_123700_ar040_devproof",
            "--manual", "/tmp/controlled.jsonl",
            "--douyin-artifact-result", "/tmp/result.json",
            "--douyin-artifact-manual", "/tmp/douyin.jsonl",
        ]
        with mock.patch.object(Path, "is_file", return_value=True), \
                mock.patch.object(run_daily_collection_job.sys, "argv", argv), \
                mock.patch.object(run_daily_collection_job, "load_local_env"), \
                mock.patch.object(run_daily_collection_job, "evaluate_preflight", return_value={"ok": True}), \
                mock.patch.object(run_daily_collection_job, "check_automation_worktree", return_value=mock.Mock(ok=True)), \
                mock.patch.object(run_daily_collection_job, "run_step", side_effect=fake_step), \
                mock.patch.object(run_daily_collection_job, "write_job_log", return_value=Path("/tmp/log.json")):
            self.assertEqual(run_daily_collection_job.main(), 0)
        pipeline = commands[-1]
        self.assertIn("daily_pipeline.py", " ".join(pipeline))
        self.assertIn("--no-fetch-aihot", pipeline)
        self.assertIn("--defer-editorial", pipeline)
        self.assertIn("--douyin-artifact-result", pipeline)
        self.assertNotIn("--resolve-url-intake", pipeline)
        self.assertNotIn("--fetch-wechat-fulltext-provider", pipeline)

    def test_staging_verifier_uses_explicit_topic_table_id(self) -> None:
        source = Path(verifier.__file__).read_text(encoding="utf-8")
        self.assertIn("configured_table_id(tables_by_name, TARGET_TABLE_KEY)", source)
        self.assertNotIn("resolve_table_id(tables_by_name, TARGET_TABLE_KEY)", source)

    def test_stage2_asset_is_candidate_specific(self) -> None:
        fields = stage2_mapping.mapping({
            "selected_visible_title": "真实任务标题", "locked_decision": "select",
            "state_or_gap": "补证", "source_title_hook": "来源钩子",
            "source_hook_usage": "改写理由", "audience_hook": "公开钩子",
            "why_i_would_choose": "选择理由", "natural_austin_angle": "自然角度",
            "public_decision_summary": "主编摘要", "research_evidence_ids": "artifact:1",
            "rejected_common_take": "常见误区", "proposed_content_structure": "结构",
            "why_i_would_not_choose": "事实边界", "research_confidence": "low",
        }, "真实工作流改造")
        self.assertIn("真实任务标题", fields["可沉淀资产"])
        self.assertIn("失败样例", fields["可沉淀资产"])

    def test_topic_projection_preserves_trusted_artifact_lineage(self) -> None:
        item = content_sampler.ContentItem(
            source_type="对标视频", platform="抖音", account_name="staging-account",
            title="真实作品标题", url="https://www.douyin.com/video/90000000001",
            content_shape="short_video", cover_text="", body_snippet="真实页面自有响应作品文案",
            published_at="", comment_questions="", ocr_text="",
            fetch_method="douyin_cdp_homepage_card", fetch_status="success",
            failure_reason="", fingerprint="fixture-fingerprint",
        )
        row = content_sampler.breakdown(item)
        topic = content_sampler.topic_from_breakdown(row, item)
        self.assertEqual(topic["原始来源账号"], "staging-account")
        self.assertEqual(topic["原始来源标题"], "真实作品标题")
        self.assertEqual(topic["原始发布文案"], "真实页面自有响应作品文案")
        self.assertEqual(topic["平台"], "抖音")

    def test_content_ledger_readback_carries_verified_run_identity(self) -> None:
        item = content_sampler.ContentItem(
            source_type="AI热点", platform="Web", account_name="source",
            title="title", url="https://example.com/item", content_shape="article",
            cover_text="", body_snippet="body", published_at="", comment_questions="",
            ocr_text="", fetch_method="owned", fetch_status="success", failure_reason="",
            fingerprint="fp-1",
        )
        read_back = [{"fields": {"内容指纹": "fp-1", "运行批次": "run_20260721_123700_ar040_devproof"}}]
        result = content_sampler.verify_content_ledger_readback(
            [item], read_back, "run_20260721_123700_ar040_devproof",
        )
        self.assertEqual(result["run_id"], "run_20260721_123700_ar040_devproof")

    def prepare(self, rows: list[dict[str, str]], *, assert_no_open: bool = True):
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        run_id = "run_20260721_080000"
        path = root / "output" / "runs" / run_id / "today_10_topics.csv"
        path.parent.mkdir(parents=True)
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDS)
            writer.writeheader()
            writer.writerows(rows)
        args = Namespace(
            out_dir=str(root / "state"), content_csv=[], since="2026-07-21", batch_size=3,
            max_skill_candidates=20, task_id="ar040-public-path", persona_docx=str(root / "persona.docx"),
            exact_input_csv=str(path), exact_input_sha256=hashlib.sha256(path.read_bytes()).hexdigest(), run_id=run_id,
        )
        adapter_patch = mock.patch.object(machine.source_adapter, "primary_adapter_for_url", side_effect=AssertionError("ordinary candidate opened network")) if assert_no_open else mock.patch.object(machine.source_adapter, "primary_adapter_for_url", side_effect=machine.source_adapter.AdapterContractError("unsupported_high_risk_source"))
        identity_patch = mock.patch.object(machine.source_adapter, "expected_identity", side_effect=AssertionError("ordinary candidate resolved URL"))
        with mock.patch.object(machine.persona_builder, "build_bundle", return_value={"authority_sha256": "a" * 64, "manifest_hash": "b" * 64}), adapter_patch, identity_patch:
            result = machine.prepare_source_open(args)
        return temp, root, result, machine.load_state(root / "state")

    def test_trusted_ordinary_artifacts_skip_source_open_for_nonempty_and_empty_urls(self) -> None:
        rows = [
            {"来源链接": "https://claude.com/blog/article/unsupported-slug", "内容指纹": "fp-nonempty", "来源类型": "AI热点", "来源内容": "A practical workflow opinion without hard facts", "原始来源标题": "Workflow opinion", "原始发布文案": "", "原始来源账号": "AIHOT", "平台": "Web"},
            {"来源链接": "", "内容指纹": "fp-empty", "来源类型": "对标视频", "来源内容": "A useful content structure and personal judgment", "原始来源标题": "Structure", "原始发布文案": "", "原始来源账号": "creator", "平台": "抖音"},
        ]
        temp, root, result, state = self.prepare(rows)
        self.addCleanup(temp.cleanup)
        self.assertEqual(result["candidates"], 2)
        self.assertEqual(result["source_open_calls"], 0)
        self.assertNotIn("Trusted collection artifact", json.dumps(state, ensure_ascii=False))
        self.assertEqual(state["stages"]["source_open"]["status"], "completed")
        for candidate in machine.candidate_rows_from_state(state):
            validated = json.loads((root / "state" / "source_open" / candidate["candidate_id"] / "validated.json").read_text())
            self.assertEqual(validated["open_status"], "artifact_only")
            self.assertTrue(validated["eligible"])

    def test_high_risk_unsupported_url_is_candidate_local(self) -> None:
        rows = [
            {"来源链接": "https://claude.com/blog/article/unsupported-slug", "内容指纹": "fp-risk", "来源类型": "AI热点", "来源内容": "官方宣布融资10亿元", "原始来源标题": "Funding", "原始发布文案": "", "原始来源账号": "AIHOT", "平台": "Web"},
            {"来源链接": "https://example.com/opinion", "内容指纹": "fp-safe", "来源类型": "AI热点", "来源内容": "An ordinary workflow opinion", "原始来源标题": "Opinion", "原始发布文案": "", "原始来源账号": "AIHOT", "平台": "Web"},
        ]
        temp, root, result, state = self.prepare(rows, assert_no_open=False)
        self.addCleanup(temp.cleanup)
        self.assertEqual(result["source_open_calls"], 0)
        self.assertEqual(state["stages"]["source_open"]["status"], "completed_with_failures")
        failures = [record for record in state["stages"]["source_open"]["candidates"].values() if record["status"] == "failed"]
        successes = [record for record in state["stages"]["source_open"]["candidates"].values() if record["status"] == "completed"]
        self.assertEqual((len(failures), len(successes)), (1, 1))

    def test_preflight_classifies_blockers_and_optional_telemetry(self) -> None:
        env = {
            "AI_ACCOUNT_RADAR_ENV": "staging",
            "FEISHU_APP_ID": "id", "FEISHU_APP_SECRET": "secret", "FEISHU_BASE_APP_TOKEN": "base",
            "FEISHU_TOPIC_TABLE_ID": "topic", "FEISHU_CARD_RECEIVE_TARGETS": "open_id:test",
        }
        result = preflight.evaluate_preflight(
            "card", environ=env, check_network=True,
            resolver=lambda *_args, **_kwargs: [(None, None, None, None, None)],
            path_probe=lambda path: "logs" not in str(path),
        )
        self.assertTrue(result["ok"])
        self.assertIn("optional_telemetry_unavailable", result["classifications"])
        blocked = preflight.evaluate_preflight(
            "card", environ=env, check_network=True,
            resolver=mock.Mock(side_effect=OSError("dns down")), path_probe=lambda _path: True,
        )
        self.assertFalse(blocked["ok"])
        self.assertIn("core_external_write_unavailable", blocked["blocking_reasons"])
        self.assertEqual(blocked["external_calls"], 0)

    def test_staging_requires_explicit_resources(self) -> None:
        result = preflight.evaluate_preflight(
            "collection",
            environ={"AI_ACCOUNT_RADAR_ENV": "staging", "FEISHU_APP_ID": "id", "FEISHU_APP_SECRET": "secret", "FEISHU_BASE_APP_TOKEN": "base"},
            path_probe=lambda _path: True,
        )
        self.assertFalse(result["ok"])
        self.assertIn("FEISHU_SOURCE_TABLE_ID", result["missing_environment_keys"])
        self.assertIn("FEISHU_CONTENT_TABLE_ID", result["missing_environment_keys"])


if __name__ == "__main__":
    unittest.main()
