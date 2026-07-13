from __future__ import annotations

import hashlib
import inspect
import json
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from datetime import datetime, timezone
from pathlib import Path

import editorial_skill_runner as runner
import feishu_topic_decision_card as card
import push_today10_to_feishu as topic_writer
import persona_reference_builder as persona
import persona_counterfactual_audit as persona_audit
import topic_research_contract as research
import topic_research_dossier_builder as dossier_builder
import topic_research_cache_revalidator as cache_revalidator
import run_douyin_exact_source_stage as douyin_stage
import topic_editorial_state_machine as machine
import trusted_exact_source_adapter as exact_adapter
import trusted_exact_source_evidence as exact_evidence


def decision(index: int, action: str = "生成脚本包") -> dict:
    row = {
        "index": index,
        "decision": "select",
        "recommendation_status": action,
        "natural_austin_angle": f"angle-{index}",
        "selected_visible_title": f"title-{index}",
        "title_rationale": f"rationale-{index}",
        "public_decision_summary": f"summary-{index}",
    }
    row["editorial_decision_hash"] = runner.editorial_decision_hash(row)
    row["editorial_decision_id"] = runner.editorial_decision_id(index, row["editorial_decision_hash"])
    row["locked_decision"] = "select"
    row["locked_recommendation_status"] = action
    row["locked_daily_level"] = "推荐制作"
    row["locked_should_produce"] = "是"
    row["locked_title_permission"] = "可发布标题"
    row["locked_global_rank_position"] = ""
    row["locked_global_tradeoff_reason"] = ""
    row["global_rank_hash"] = runner.global_rank_hash(row)
    return row


def ranking_row(row: dict, position: int) -> dict:
    return {
        "index": row["index"],
        "editorial_decision_id": row["editorial_decision_id"],
        "editorial_decision_hash": row["editorial_decision_hash"],
        "input_global_rank_hash": row["global_rank_hash"],
        "global_daily_level": "推荐制作",
        "final_recommendation_status": row["locked_recommendation_status"],
        "global_rank_position": str(position),
        "global_tradeoff_reason": f"公开取舍 {position}",
    }


class ResearchContractTests(unittest.TestCase):
    def test_douyin_retry_is_same_adapter_bounded_to_two_attempts(self) -> None:
        self.assertTrue(douyin_stage.should_attempt({"source_attempt_count": 0}, None))
        self.assertTrue(douyin_stage.should_attempt({"source_attempt_count": 1}, {"open_status": "failed"}))
        self.assertFalse(douyin_stage.should_attempt({"source_attempt_count": 2}, {"open_status": "failed"}))
        self.assertFalse(douyin_stage.should_attempt({"source_attempt_count": 1}, {"open_status": "opened"}))

    def test_source_attempt_is_reserved_before_browser_process(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            state_path = Path(temp) / "editorial_state_machine.json"
            state_path.write_text(json.dumps({"stages": {"source_open": {"candidates": {
                "candidate": {"status": "failed", "source_attempt_count": 1},
            }}}}), encoding="utf-8")
            self.assertEqual(douyin_stage.reserve_attempt(state_path, "candidate"), 2)
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["stages"]["source_open"]["candidates"]["candidate"]["source_attempt_count"], 2)
            with self.assertRaisesRegex(RuntimeError, "retry bound exceeded"):
                douyin_stage.reserve_attempt(state_path, "candidate")
    def test_research_cache_requires_fresh_exact_source_identity(self) -> None:
        now = datetime(2026, 7, 12, 8, tzinfo=timezone.utc)
        source = {"exact_url": "https://example.com/a", "exact_title": "A", "author": "Austin", "captured_content_hash": "a" * 64}
        cached = {"protocol": "x", "status": "completed", "source_content_hash": "a" * 64, "source_type": "evergreen", "queries": ["q"], "results": [], "research_summary": "s", "hook_analysis": {}, "claim_evidence": [], "completed_at": "2026-07-12T07:00:00+00:00", "dossier_hash": "old"}
        current = {**source, "captured_content_hash": "b" * 64}
        result = cache_revalidator.revalidate(source, current, cached, now=now)
        self.assertEqual(result["source_content_hash"], "b" * 64)
        self.assertEqual(result["cache_revalidation"]["status"], "revalidated")
        with self.assertRaisesRegex(ValueError, "identity changed"):
            cache_revalidator.revalidate(source, {**current, "exact_title": "Changed"}, cached, now=now)
    def test_domain_routes_to_exactly_one_primary_adapter(self) -> None:
        self.assertEqual(exact_adapter.primary_adapter_for_url("https://www.douyin.com/video/1"), exact_adapter.DOUYIN_ADAPTER)
        self.assertEqual(exact_adapter.primary_adapter_for_url("https://x.com/a/status/123"), exact_adapter.TRUSTED_BROWSER_ADAPTER)
        self.assertEqual(exact_adapter.primary_adapter_for_url("https://claude.com/blog/how-people-are-using-claude-cowork"), exact_adapter.TRUSTED_BROWSER_ADAPTER)
        self.assertEqual(exact_adapter.primary_adapter_for_url("https://example.com/article/1"), exact_adapter.TRUSTED_WEB_ADAPTER)

    def test_primary_adapter_rejects_failover_or_identity_mismatch(self) -> None:
        candidate = {"exact_url": "https://x.com/a/status/123", "primary_adapter": exact_adapter.TRUSTED_BROWSER_ADAPTER}
        base = {"primary_adapter": exact_adapter.TRUSTED_BROWSER_ADAPTER, "attempted_adapters": [exact_adapter.TRUSTED_BROWSER_ADAPTER],
                "page_identity": {"kind": "x_status", "status_id": "123"}, "page_state": "exact_post",
                "browser_surface": "iab", "browser_session_boundary": "current_task", "dom_text_path": "/tmp/dom",
                "screenshot_path": "/tmp/shot", "visual_capture_status": "completed"}
        exact_adapter.validate_primary_adapter(candidate, base)
        with self.assertRaisesRegex(exact_adapter.AdapterContractError, "failover"):
            exact_adapter.validate_primary_adapter(candidate, {**base, "attempted_adapters": [exact_adapter.TRUSTED_BROWSER_ADAPTER, exact_adapter.TRUSTED_WEB_ADAPTER]})
        with self.assertRaisesRegex(exact_adapter.AdapterContractError, "status ID"):
            exact_adapter.validate_primary_adapter(candidate, {**base, "page_identity": {"kind": "x_status", "status_id": "999"}})

    def test_trusted_browser_rejects_login_blank_or_generic_home(self) -> None:
        x_candidate = {"exact_url": "https://x.com/a/status/123", "primary_adapter": exact_adapter.TRUSTED_BROWSER_ADAPTER}
        x_output = {"primary_adapter": exact_adapter.TRUSTED_BROWSER_ADAPTER, "attempted_adapters": [exact_adapter.TRUSTED_BROWSER_ADAPTER],
                    "page_identity": {"kind": "x_status", "status_id": "123"}, "page_state": "login_wall",
                    "browser_surface": "iab", "browser_session_boundary": "current_task", "dom_text_path": "/tmp/dom",
                    "screenshot_path": "/tmp/shot", "visual_capture_status": "completed"}
        with self.assertRaisesRegex(exact_adapter.AdapterContractError, "not visibly open"):
            exact_adapter.validate_primary_adapter(x_candidate, x_output)
        claude_candidate = {"exact_url": "https://claude.com/blog/how-people-are-using-claude-cowork", "primary_adapter": exact_adapter.TRUSTED_BROWSER_ADAPTER}
        claude_output = {**x_output, "page_identity": {"kind": "claude_blog", "path": "/blog/how-people-are-using-claude-cowork"}, "page_state": "generic_home"}
        with self.assertRaisesRegex(exact_adapter.AdapterContractError, "not visibly open"):
            exact_adapter.validate_primary_adapter(claude_candidate, claude_output)

    def test_exact_evidence_builder_keeps_one_primary_adapter(self) -> None:
        candidate = {"candidate_id": "c1", "exact_url": "https://x.com/a/status/123", "primary_adapter": exact_adapter.TRUSTED_BROWSER_ADAPTER}
        output = exact_evidence.build_output(candidate, {
            "final_url": candidate["exact_url"], "page_identity": {"kind": "x_status", "status_id": "123"},
            "page_state": "exact_post", "exact_title": "Visible post", "visible_body": "Complete visible post body",
            "author": "@a", "browser_surface": "iab", "browser_session_boundary": "current_task",
            "dom_text_path": "/tmp/dom", "screenshot_path": "", "visual_capture_error": "timeout",
        })
        self.assertEqual(output["attempted_adapters"], [exact_adapter.TRUSTED_BROWSER_ADAPTER])
        self.assertEqual(len(output["content_evidence"]), 1)
        self.assertEqual(output["visual_capture_status"], "failed")
        self.assertEqual(output["screenshot_path"], "")
        self.assertTrue(output["audit_warnings"])

    def test_screenshot_timeout_with_complete_dom_is_opened_with_warning(self) -> None:
        candidate = {"candidate_id": "c1", "exact_url": "https://x.com/a/status/123", "primary_adapter": exact_adapter.TRUSTED_BROWSER_ADAPTER}
        output = exact_evidence.build_output(candidate, {
            "final_url": candidate["exact_url"], "page_identity": {"kind": "x_status", "status_id": "123"},
            "page_state": "exact_post", "exact_title": "Exact post", "visible_body": "Complete post body",
            "author": "@a", "browser_surface": "iab", "browser_session_boundary": "current_task",
            "dom_text_path": "/tmp/fresh-dom", "screenshot_path": "", "visual_capture_error": "timeout",
        })
        self.assertEqual(output["open_status"], "opened")
        self.assertEqual(output["visual_capture_status"], "failed")
        self.assertEqual(output["screenshot_path"], "")

    def test_screenshot_timeout_does_not_relax_missing_body(self) -> None:
        candidate = {"candidate_id": "c1", "exact_url": "https://x.com/a/status/123", "primary_adapter": exact_adapter.TRUSTED_BROWSER_ADAPTER}
        with self.assertRaisesRegex(exact_adapter.AdapterContractError, "title/body/author"):
            exact_evidence.build_output(candidate, {
                "final_url": candidate["exact_url"], "page_identity": {"kind": "x_status", "status_id": "123"},
                "page_state": "exact_post", "exact_title": "Exact post", "visible_body": "", "author": "@a",
                "browser_surface": "iab", "browser_session_boundary": "current_task", "dom_text_path": "/tmp/dom",
                "screenshot_path": "", "visual_capture_error": "timeout",
            })

    def test_screenshot_success_keeps_real_path(self) -> None:
        candidate = {"candidate_id": "c1", "exact_url": "https://x.com/a/status/123", "primary_adapter": exact_adapter.TRUSTED_BROWSER_ADAPTER}
        with tempfile.TemporaryDirectory() as temp:
            screenshot = Path(temp) / "page.png"
            screenshot.write_bytes(b"png")
            output = exact_evidence.build_output(candidate, {
                "final_url": candidate["exact_url"], "page_identity": {"kind": "x_status", "status_id": "123"},
                "page_state": "exact_post", "exact_title": "Exact post", "visible_body": "Complete post body", "author": "@a",
                "browser_surface": "iab", "browser_session_boundary": "current_task", "dom_text_path": "/tmp/dom",
                "screenshot_path": str(screenshot),
            })
        self.assertEqual(output["visual_capture_status"], "completed")
        self.assertEqual(output["screenshot_path"], str(screenshot))
    def test_dossier_builder_produces_contract_hash(self) -> None:
        spec = {
            "source_content_hash": "a" * 64,
            "queries": ["query"],
            "results": [],
            "external_corroboration_state": "no_accessible_corroboration",
            "confidence": "low",
            "corroboration_gap": "No accessible independent page.",
            "research_summary": "Source-only summary.",
            "hook_analysis": {
                "audience_hook": "A concrete result promise",
                "why_unfamiliar_audience_clicks": "The result is understandable without knowing the product.",
                "hook_evidence_ids": ["source:1"],
                "product_name_is_not_hook": True,
                "hook_type": ["result_promise"],
            },
            "claim_evidence": [],
            "completed_at": "2026-07-12T00:00:00+00:00",
        }
        dossier = dossier_builder.build_dossier(spec)
        clean = {key: value for key, value in dossier.items() if key != "dossier_hash"}
        self.assertEqual(dossier["dossier_hash"], research.hash_json(clean))

    def test_source_open_fails_closed_without_exact_open(self) -> None:
        candidate = {
            "exact_url": "https://example.com/article/123",
            "primary_adapter": exact_adapter.TRUSTED_WEB_ADAPTER,
        }
        result = research.validate_source_open(candidate, {
            "open_status": "failed",
            "primary_adapter": exact_adapter.TRUSTED_WEB_ADAPTER,
            "attempted_adapters": [exact_adapter.TRUSTED_WEB_ADAPTER],
        })
        self.assertFalse(result["eligible"])
        self.assertEqual(result["failure_reason"], "exact_source_not_opened")

    def test_source_open_rejects_home_page_substitution(self) -> None:
        candidate = {
            "exact_url": "https://example.com/article/123",
            "primary_adapter": exact_adapter.TRUSTED_WEB_ADAPTER,
        }
        payload = {
            "primary_adapter": exact_adapter.TRUSTED_WEB_ADAPTER,
            "attempted_adapters": [exact_adapter.TRUSTED_WEB_ADAPTER],
            "open_status": "opened", "exact_url": candidate["exact_url"],
            "final_url": "https://example.com/profile", "exact_title": "x", "platform": "web",
            "author": "a", "opened_at": "2026-07-11T00:00:00Z",
            "captured_content_hash": "a" * 64, "source_type": "article", "source_summary": "s",
            "content_evidence": [{"evidence_id": "src-1", "text": "e"}],
        }
        with self.assertRaises(research.ContractError):
            research.validate_source_open(candidate, payload)

    def test_persona_only_material_claim_is_rejected(self) -> None:
        dossier = {
            "source": {"content_evidence": [{"evidence_id": "src-1"}]},
            "results": [{"evidence_id": "web-1"}],
        }
        with self.assertRaises(research.ContractError):
            research.validate_claim_trace({
                "audience_hook": "这个工具已经能完成商业交付",
                "natural_austin_angle": "", "selected_visible_title": "", "public_decision_summary": "",
                "research_evidence_ids": "", "hook_evidence_ids": "",
            }, dossier)

    def test_research_hash_mutation_fails_closed(self) -> None:
        source = {
            "open_status": "opened", "eligible": True, "captured_content_hash": "a" * 64,
            "content_evidence": [{"evidence_id": "src-1", "text": "verified source"}],
        }
        spec = {
            "source_content_hash": "a" * 64, "queries": ["query"], "results": [],
            "external_corroboration_state": "no_accessible_corroboration", "confidence": "low",
            "corroboration_gap": "No accessible corroboration.", "research_summary": "summary",
            "hook_analysis": {"audience_hook": "result promise", "why_unfamiliar_audience_clicks": "clear result",
                "hook_evidence_ids": ["src-1"], "product_name_is_not_hook": True, "hook_type": ["result"]},
            "claim_evidence": [{"claim_id": "c1", "claim": "claim", "evidence_ids": ["src-1"], "persona_only": False}],
            "completed_at": "2026-07-12T00:00:00+00:00",
        }
        dossier = dossier_builder.build_dossier(spec)
        dossier["research_summary"] = "mutated after hashing"
        with self.assertRaisesRegex(research.ContractError, "hash mismatch"):
            research.validate_research_dossier({}, source, dossier)

    def test_exact_source_only_cannot_be_recommended(self) -> None:
        decision = {
            "decision": "select", "recommendation_status": "生成脚本包",
            "research_evidence_ids": "src-1", "hook_evidence_ids": "src-1",
        }
        dossier = {"source": {"content_evidence": [{"evidence_id": "src-1"}]}, "results": []}
        with self.assertRaisesRegex(research.ContractError, "no freshly opened"):
            research.validate_recommendation_research_eligibility(decision, dossier)

    def test_query_only_cannot_be_recommended(self) -> None:
        decision = {
            "decision": "select", "recommendation_status": "生成脚本包",
            "research_evidence_ids": "src-1", "hook_evidence_ids": "src-1",
        }
        dossier = {
            "queries": [{"query": "topic and claim"}],
            "source": {"content_evidence": [{"evidence_id": "src-1"}]},
            "results": [{"evidence_id": "web-1", "open_status": "failed"}],
        }
        with self.assertRaisesRegex(research.ContractError, "no freshly opened"):
            research.validate_recommendation_research_eligibility(decision, dossier)

    def test_exact_url_recheck_is_not_web_research(self) -> None:
        source = {
            "open_status": "opened", "eligible": True, "captured_content_hash": "a" * 64,
            "content_evidence": [{"evidence_id": "src-1", "text": "verified source"}],
        }
        spec = {
            "source_content_hash": "a" * 64,
            "queries": ["复核精确来源：https://example.com/article/123"], "results": [],
            "external_corroboration_state": "no_accessible_corroboration", "confidence": "low",
            "corroboration_gap": "No accessible corroboration.", "research_summary": "summary",
            "hook_analysis": {"audience_hook": "hook", "why_unfamiliar_audience_clicks": "reason",
                "hook_evidence_ids": ["src-1"], "product_name_is_not_hook": True},
            "claim_evidence": [{"claim_id": "c1", "claim": "claim", "evidence_ids": ["src-1"]}],
        }
        dossier = dossier_builder.build_dossier(spec)
        with self.assertRaisesRegex(research.ContractError, "topical/entity/claim query"):
            research.validate_research_dossier(
                {"exact_url": "https://example.com/article/123"}, source, dossier
            )

    def test_opened_research_must_be_real_artifact_not_snippet_or_prior_state(self) -> None:
        source = {
            "open_status": "opened", "eligible": True, "captured_content_hash": "a" * 64,
            "content_evidence": [{"evidence_id": "src-1", "text": "verified source"}],
        }
        for forbidden_surface in ("search_snippet", "model_memory", "prior_dossier"):
            spec = {
                "source_content_hash": "a" * 64, "queries": [{"query": "real topical query"}],
                "results": [{
                    "evidence_id": "web-1", "open_status": "opened", "url": "https://example.com/a",
                    "final_url": "https://example.com/a", "title": "Article", "publisher": "Publisher",
                    "opened_at": "2026-07-13T00:00:00Z", "captured_at": "2026-07-13T00:00:01Z",
                    "captured_content_hash": "b" * 64, "dom_text_path": "/private/tmp/missing",
                    "source_class": "independent", "supported_claim": "claim",
                    "supporting_excerpt": "literal excerpt from page body", "evidence_locator": "body",
                    "capture_method": "trusted_browser_dom",
                    "evidence_surface": forbidden_surface,
                }],
                "external_corroboration_state": "opened", "confidence": "medium", "corroboration_gap": "",
                "research_summary": "summary",
                "hook_analysis": {"audience_hook": "hook", "why_unfamiliar_audience_clicks": "reason",
                    "hook_evidence_ids": ["web-1"], "product_name_is_not_hook": True},
                "claim_evidence": [{"claim_id": "c1", "claim": "claim", "evidence_ids": ["web-1"]}],
            }
            dossier = dossier_builder.build_dossier(spec)
            with self.assertRaisesRegex(research.ContractError, "not research evidence"):
                research.validate_research_dossier({}, source, dossier)

    def _validated_source(self) -> dict:
        return {
            "open_status": "opened", "eligible": True, "captured_content_hash": "a" * 64,
            "content_evidence": [{"evidence_id": "src-1", "text": "verified source"}],
        }

    def _research_spec(self, dom_path: Path, excerpt: str) -> dict:
        return {
            "source_content_hash": "a" * 64, "queries": [{"query": "entity material claim context"}],
            "results": [{
                "evidence_id": "web-1", "open_status": "opened", "url": "https://example.com/a",
                "final_url": "https://example.com/a", "title": "Article", "publisher": "Publisher",
                "opened_at": "2026-07-13T00:00:00Z", "captured_at": "2026-07-13T00:00:01Z",
                "captured_content_hash": hashlib.sha256(dom_path.read_bytes()).hexdigest(),
                "dom_text_path": str(dom_path), "source_class": "independent",
                "supported_claim": "The opened page supports the material claim.",
                "supporting_excerpt": excerpt, "evidence_locator": "body paragraph 2",
                "capture_method": "current_task_trusted_browser_dom",
                "evidence_surface": "current_task_trusted_web_open",
            }],
            "external_corroboration_state": "opened_support", "confidence": "medium",
            "corroboration_gap": "Only the cited claim is supported.", "research_summary": "Factual summary.",
            "hook_analysis": {"audience_hook": "Public consequence", "why_unfamiliar_audience_clicks": "Clear consequence",
                "hook_evidence_ids": ["web-1"], "product_name_is_not_hook": True},
            "claim_evidence": [{"claim_id": "c1", "claim": "claim", "evidence_ids": ["web-1"]}],
        }

    def test_hash_valid_synthetic_claim_file_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            dom = Path(temp) / "raw.txt"
            dom.write_text("Title\nPublisher\nCanonical URL\nOpened evidence: generated claim", encoding="utf-8")
            dossier = dossier_builder.build_dossier(self._research_spec(dom, "Opened evidence: generated claim"))
            with self.assertRaisesRegex(research.ContractError, "synthetic or lacks page body"):
                research.validate_research_dossier({}, self._validated_source(), dossier)

    def test_raw_capture_missing_body_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            dom = Path(temp) / "raw.txt"; dom.write_text("", encoding="utf-8")
            dossier = dossier_builder.build_dossier(self._research_spec(dom, "missing literal excerpt long enough"))
            with self.assertRaisesRegex(research.ContractError, "no readable DOM"):
                research.validate_research_dossier({}, self._validated_source(), dossier)

    def test_excerpt_not_found_in_raw_capture_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            dom = Path(temp) / "raw.txt"; dom.write_text("Actual opened page body. " * 40, encoding="utf-8")
            dossier = dossier_builder.build_dossier(self._research_spec(dom, "This literal excerpt is absent from capture"))
            with self.assertRaisesRegex(research.ContractError, "not a literal substring"):
                research.validate_research_dossier({}, self._validated_source(), dossier)

    def test_valid_raw_capture_and_literal_excerpt_pass(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            excerpt = "The opened article states a concrete material claim for readers."
            dom = Path(temp) / "raw.txt"
            dom.write_text(("Actual browser-captured page paragraph with context. " * 12) + excerpt, encoding="utf-8")
            dossier = dossier_builder.build_dossier(self._research_spec(dom, excerpt))
            validated = research.validate_research_dossier({}, self._validated_source(), dossier)
            self.assertTrue(validated["eligible"])

    def test_claim_or_hook_without_known_evidence_fails(self) -> None:
        dossier = {"source": {"content_evidence": [{"evidence_id": "src-1"}]}, "results": []}
        with self.assertRaises(research.ContractError):
            research.validate_claim_trace({
                "audience_hook": "外部结果承诺", "natural_austin_angle": "", "selected_visible_title": "",
                "public_decision_summary": "", "research_evidence_ids": "missing", "hook_evidence_ids": "",
            }, dossier)

    def test_no_accessible_corroboration_can_only_observe_or_reject(self) -> None:
        dossier = {"source": {"content_evidence": [{"evidence_id": "src-1"}]}, "results": []}
        research.validate_recommendation_research_eligibility({
            "decision": "observe", "recommendation_status": "补证据",
            "research_evidence_ids": "src-1", "hook_evidence_ids": "src-1",
        }, dossier)
        research.validate_recommendation_research_eligibility({
            "decision": "reject", "recommendation_status": "不做",
        }, dossier)

    def test_opened_matching_corroboration_can_be_recommended(self) -> None:
        dossier = {
            "source": {"content_evidence": [{"evidence_id": "src-1"}]},
            "results": [{
                "evidence_id": "web-1", "open_status": "opened", "url": "https://example.com/a",
                "captured_content_hash": "b" * 64, "dom_text_path": "/private/tmp/web-1.txt",
                "opened_at": "2026-07-13T00:00:00Z",
            }],
        }
        research.validate_recommendation_research_eligibility({
            "decision": "select", "recommendation_status": "生成脚本包",
            "research_evidence_ids": "src-1,web-1", "hook_evidence_ids": "web-1",
        }, dossier)


class PersonaIsolationTests(unittest.TestCase):
    def test_runtime_layers_exclude_experience_archive_and_case_scaffolding(self) -> None:
        authority = Path("/Users/congcong/Desktop/AI/AI项目/AI账号工作流/00_资料库/04_案例库/我的案例库.docx")
        if not authority.exists():
            self.skipTest("private persona authority unavailable")
        with tempfile.TemporaryDirectory() as temp:
            manifest = persona.build_bundle(authority, Path(temp))
            facts = json.loads((Path(temp) / "persona_facts.private.json").read_text())
            examples = json.loads((Path(temp) / "judgment_and_style_examples.private.json").read_text())
        self.assertEqual(manifest["experience_archive_runtime"], "excluded")
        runtime_text = json.dumps({"facts": facts, "examples": examples}, ensure_ascii=False)
        self.assertNotIn("案例名：", runtime_text)
        self.assertNotIn("source_situation", runtime_text)
        self.assertNotIn("persona_context", runtime_text)
        self.assertNotIn("natural_expression", runtime_text)

    def test_tagged_retrieval_is_candidate_specific_and_verbatim(self) -> None:
        examples = [
            {"example_id": f"e{i}", "source_hash": str(i), "verbatim_text": f"原文段落 {i}",
             "judgment_operations": [operation], "role": "judgment_and_style_reference_only"}
            for i, operation in enumerate([
                "public_contradiction", "shallow_take_rejection", "evidence_skepticism",
                "story_or_social_proof", "result_promise", "decision_tradeoff", "natural_voice",
            ])
        ]
        first = persona.retrieve_style_examples(examples, ["story_or_social_proof", "natural_voice"], candidate_id="story", limit=3)
        second = persona.retrieve_style_examples(examples, ["result_promise", "decision_tradeoff"], candidate_id="result", limit=3)
        self.assertNotEqual([item["example_id"] for item in first], [item["example_id"] for item in second])
        self.assertTrue(all(item["verbatim_text"].startswith("原文段落") for item in first + second))

    def test_operation_inference_varies_with_evidence_shape(self) -> None:
        story = machine.infer_judgment_operations({
            "hook_analysis": {"hook_type": ["story", "social_proof"]},
            "confidence": "high", "claim_evidence": [], "conflicts": [],
        })
        uncertain = machine.infer_judgment_operations({
            "hook_analysis": {"hook_type": ["audience_benefit"]},
            "confidence": "low", "claim_evidence": [], "conflicts": ["conflict"],
        })
        self.assertIn("story_or_social_proof", story)
        self.assertIn("evidence_skepticism", uncertain)
        self.assertIn("public_contradiction", uncertain)
        self.assertNotEqual(story, uncertain)

    def test_actionable_title_family_gate_detects_contrast_dominance(self) -> None:
        rows = [
            {"今日建议级别": "推荐制作", "选题命题": title}
            for title in ["不是工具，是结果", "不缺模型，缺现场", "不是会聊，是做完", "一个公开问题"]
        ]
        report = persona_audit.actionable_title_family_report(rows)
        self.assertFalse(report["ok"])
        self.assertGreater(report["max_family_rate"], 0.30)

    def test_douyin_row_and_card_do_not_duplicate_caption_as_title(self) -> None:
        source = {"platform": "抖音", "exact_title": "完整发布文案", "caption_body": "完整发布文案"}
        self.assertFalse(source.get("independent_title_verified"))
        fields = {"选题标题": "建议选题", "原始来源标题": "", "原始发布文案": source["caption_body"]}
        markdown = card.card_markdown_for_candidate(1, fields)
        self.assertIn("原始标题：平台未提供独立标题", markdown)
        self.assertEqual(markdown.count("完整发布文案"), 1)

    def test_counterfactual_computes_fact_stability_and_expression_change(self) -> None:
        base = {
            "candidate_id": "c1", "source": {"hash": "s"}, "research": {"hash": "r"},
            "hook_analysis": {"hook": "h"}, "decision": "observe", "recommendation_status": "补证据",
            "natural_austin_angle": "自然角度 A", "selected_visible_title": "标题 A",
            "title_rationale": "理由 A", "public_decision_summary": "摘要 A",
        }
        control = {**base, "natural_austin_angle": "自然角度 B", "selected_visible_title": "标题 B"}
        result = persona_audit.compare_pair(base, control)
        self.assertTrue(result["facts_stable"])
        self.assertTrue(result["eligibility_stable"])
        self.assertTrue(result["persona_changes_expression_only"])

    def test_leakage_report_detects_universal_retrieval(self) -> None:
        report = persona_audit.leakage_report(
            [{"selected_visible_title": "不同标题一"}, {"selected_visible_title": "不同标题二"}],
            [{"example_ids": ["e1", "e2"]}, {"example_ids": ["e1", "e2"]}],
        )
        self.assertTrue(report["all_candidates_same_retrieval"])


class DynamicRankingTests(unittest.TestCase):
    def test_all_recommended_rows_survive_without_dynamic_ranking_cap(self) -> None:
        decisions = [decision(index) for index in range(7)]
        ranked = runner.apply_global_ranking(decisions, [ranking_row(row, index + 1) for index, row in enumerate(decisions)])
        self.assertEqual(len(ranked), 7)
        self.assertEqual([row["locked_global_rank_position"] for row in ranked], [str(i) for i in range(1, 8)])
        self.assertTrue(all(row["locked_should_produce"] == "是" for row in ranked))

    def test_ranking_cannot_downgrade_eligibility(self) -> None:
        row = decision(0)
        output = ranking_row(row, 1)
        output["global_daily_level"] = "暂存观察"
        with self.assertRaisesRegex(RuntimeError, "change eligibility"):
            runner.apply_global_ranking([row], [output])

    def test_ranking_requires_lossless_positions(self) -> None:
        decisions = [decision(0), decision(1)]
        outputs = [ranking_row(decisions[0], 1), ranking_row(decisions[1], 1)]
        with self.assertRaisesRegex(RuntimeError, "lossless"):
            runner.apply_global_ranking(decisions, outputs)


class CardPaginationTests(unittest.TestCase):
    def test_writer_preserves_evidence_first_visible_fields(self) -> None:
        row = {
            "选题命题": "Austin topic",
            "原始来源标题": "Exact article title",
            "来源内容": "Long post caption",
            "来源链接": "https://example.com/article/1",
            "研究摘要": "What the source establishes",
            "受众钩子": "Why an unfamiliar viewer cares",
            "内容结构": "1. conflict 2. evidence 3. decision",
            "我的切入": "Natural Austin angle",
            "推荐动作": "生成脚本包",
            "今日建议级别": "推荐制作",
        }
        mapped = topic_writer.map_row(row, 1, "2026-07-12", "run")
        self.assertEqual(mapped["原始来源标题"], "Exact article title")
        self.assertEqual(mapped["原始发布文案"], "Long post caption")
        self.assertEqual(mapped["研究摘要"], "What the source establishes")
        self.assertEqual(mapped["受众钩子"], "Why an unfamiliar viewer cares")
        self.assertEqual(mapped["内容结构"], "1. conflict 2. evidence 3. decision")
        self.assertEqual(mapped["我的切入"], "Natural Austin angle")

    def records(self, count: int) -> list[dict]:
        return [{
            "record_id": f"rec-{index}",
            "fields": {
                "选题标题": f"topic-{index}", "原始来源标题": f"source-{index}",
                "来源链接": f"https://example.com/article/{index}", "研究摘要": "summary",
                "受众钩子": "陌生观众为什么会点", "研究置信度": "中",
                "内容结构": "1. opening 2. conflict 3. evidence", "我的切入": "Austin 自然角度",
                "需要补的证据": "无关键缺口",
                "推荐动作": "生成脚本包", "title_permission": "可发布标题",
                "是否建议进入制作": "是", "状态": "待判断",
            },
        } for index in range(count)]

    def test_lossless_pages_for_required_sizes(self) -> None:
        for count in [0, 1, 3, 7, 12]:
            with self.subTest(count=count):
                manifest = card.build_card_pages(self.records(count), "run", page_size=5)
                self.assertEqual(manifest["candidate_count"], count)
                self.assertEqual([value for page in manifest["pages"] for value in page["candidate_ids"]], manifest["candidate_ids"])
                self.assertEqual(len(set(manifest["candidate_ids"])), count)

    def test_pagination_rejects_missing_or_duplicate_ids(self) -> None:
        missing = self.records(1)
        missing[0]["record_id"] = ""
        with self.assertRaisesRegex(ValueError, "requires record_id"):
            card.build_card_pages(missing, "run")
        duplicate = self.records(2)
        duplicate[1]["record_id"] = duplicate[0]["record_id"]
        with self.assertRaisesRegex(ValueError, "must be unique"):
            card.build_card_pages(duplicate, "run")

    def test_each_page_uses_a_distinct_message_key(self) -> None:
        source = Path(inspect.getsourcefile(card) or "").read_text(encoding="utf-8")
        self.assertIn('message_key=f"page-{page[\'page\']:02d}"', source)
        self.assertIn("message_key", inspect.signature(card.send_card).parameters)

    def test_unselected_candidates_remain_pending(self) -> None:
        decisions = card.decisions_from_form({card.ENTER_SCRIPT_PACKAGE_FORM_KEY: ["rec-1"]}, ["rec-1", "rec-2"])
        self.assertEqual(set(decisions), {"rec-1"})
        self.assertEqual(decisions["rec-1"]["status"], card.SCRIPT_PACKAGE_READY_STATUS)
        self.assertEqual(card.decisions_from_form({}, ["rec-1"], force_no_selection=True), {})

    def test_normal_submit_callback_has_no_implicit_unselected_status(self) -> None:
        payload = card.build_card(self.records(2), "run")
        serialized = json.dumps(payload, ensure_ascii=False)
        submit_marker = f'"action": "{card.SUBMIT_SELECTION_ACTION}"'
        before_no_selection = serialized.split('"action": "submit_no_selection"', 1)[0]
        self.assertIn(submit_marker, before_no_selection)
        self.assertNotIn('"unselected_status": "不做"', before_no_selection)

    def test_card_displays_exact_clickable_source_and_research(self) -> None:
        markdown = card.card_markdown_for_candidate(1, self.records(1)[0]["fields"])
        self.assertIn("[查看原始文章](https://example.com/article/0)", markdown)
        self.assertIn("原始标题：source-0", markdown)
        self.assertIn("来源摘要：summary", markdown)
        self.assertIn("受众钩子：陌生观众为什么会点", markdown)
        self.assertIn("内容结构：", markdown)

    def test_card_uses_natural_angle_and_page_scoped_reject(self) -> None:
        record = self.records(1)[0]
        record["fields"].update({"我的切入": "自然公开判断", "对应方向": "内部分类"})
        payload = card.build_card([record], "run_20260712_test")
        serialized = json.dumps(payload, ensure_ascii=False)
        self.assertIn("Austin 角度：自然公开判断", serialized)
        self.assertIn("方向分类：内部分类", serialized)
        self.assertIn("本页都不选", serialized)
        self.assertNotIn("本批都不选", serialized)
        self.assertNotIn("未识别日期", serialized)

    def test_card_keeps_summary_hook_and_title_caption_distinct(self) -> None:
        fields = self.records(1)[0]["fields"]
        fields.update({"原始来源标题": "Article title", "原始发布文案": "Post body", "我的切入": "Public angle"})
        markdown = card.card_markdown_for_candidate(1, fields)
        expected = ["精确来源：", "原始标题：", "原始发布文案：", "来源摘要：", "受众钩子：", "Austin 角度：", "内容结构："]
        self.assertEqual([markdown.index(value) for value in expected], sorted(markdown.index(value) for value in expected))

    def test_topic_writer_lists_every_field_page_before_schema_setup(self) -> None:
        calls = []

        def fake_request(_method, path, **_kwargs):
            calls.append(path)
            if "page_token=next" in path:
                return {"data": {"items": [{"field_name": "研究置信度"}], "has_more": False}}
            return {
                "data": {
                    "items": [{"field_name": "我的选题标题"}],
                    "has_more": True,
                    "page_token": "next",
                }
            }

        with mock.patch.object(topic_writer.feishu, "request_json", side_effect=fake_request):
            fields = topic_writer.list_fields("token", "app", "table")
        self.assertEqual(set(fields), {"我的选题标题", "研究置信度"})
        self.assertIn("page_size=500", calls[0])
        self.assertIn("page_token=next", calls[1])


class LegacyCliTests(unittest.TestCase):
    def test_legacy_runner_fails_before_output_creation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "business.csv"
            result = subprocess.run([
                sys.executable, str(Path(__file__).with_name("editorial_skill_runner.py")),
                "--engine", "deterministic", "--input", str(Path(temp) / "missing.csv"), "--output", str(output),
            ], text=True, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("unrecognized arguments", result.stderr)
            self.assertFalse(output.exists())

    def test_active_sources_have_no_editorial_fallback_routes(self) -> None:
        prohibited = [
            "allow-deterministic-fallback", "fallback_after_error", "explicit_deterministic",
            "run_deterministic", "skill_fallback_rows", "fallback_row_count",
            'choices=["state-machine", "codex", "deterministic"]',
        ]
        active = ["editorial_skill_runner.py", "topic_editorial_state_machine.py", "topic_skill_replay_evaluation.py"]
        text = "\n".join(Path(__file__).with_name(name).read_text(encoding="utf-8") for name in active)
        self.assertEqual([marker for marker in prohibited if marker in text], [])


if __name__ == "__main__":
    unittest.main()
