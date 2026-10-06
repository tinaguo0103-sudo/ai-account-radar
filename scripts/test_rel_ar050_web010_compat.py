from __future__ import annotations

import csv
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from types import SimpleNamespace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from daily_workflow import DailyWorkflow
from run_daily_workflow import build_scripts_handoff, editorial_handoff_candidates, enrich
from source_claim_validation import claim_contract, digest, research_evidence_id
from spoken_script_runtime import (
    article_sha256,
    load_writer_contract,
    read_article_artifact,
    topic_packet,
)

ROOT = Path(__file__).resolve().parents[1]


class TerminalProjectionHandler(BaseHTTPRequestHandler):
    posts = 0
    gets = 0
    payloads: dict[str, dict] = {}

    def log_message(self, *_args):
        return

    def reply(self, code: int, value: dict):
        body = json.dumps(value).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def readback(self, payload: dict, status: str):
        return {
            "ok": True, "status": status, "run_id": payload["run_id"],
            "business_date": payload["business_date"],
            "run_status": payload["run"]["status"],
            "revision": payload["revision"],
            "authority_identity": payload["authority_identity"],
            "counts": {
                "content": len(payload["collected_items"]),
                "topics": len(payload["topics"]),
                "scripts": len(payload["scripts"]),
            },
            "article_count": len(payload.get("articles", [])),
            "articles": payload.get("articles", []),
            "scripts": payload["scripts"],
        }

    def do_POST(self):
        self.__class__.posts += 1
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.__class__.payloads[payload["run_id"]] = payload
        self.reply(200, self.readback(payload, "applied"))

    def do_GET(self):
        self.__class__.gets += 1
        run_id = self.path.split("run_id=", 1)[-1]
        payload = self.__class__.payloads.get(run_id)
        if not payload:
            self.reply(404, {"error": "business_projection_missing"})
        else:
            self.reply(200, self.readback(payload, "readback"))


def write(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def last_json(output: str) -> dict:
    return json.loads(output.strip().splitlines()[-1])


def add_claim_review(
    content: dict,
    evidence: dict,
    *,
    research_materials: list[dict] | None = None,
    claims: list[dict] | None = None,
) -> None:
    materials = research_materials or []
    contract = claim_contract(
        evidence,
        research_materials=materials,
        topic_id=content["topic_id"],
    )
    bound = {
        key: str(content[key])
        for key in ("title", "hook", "structure", "body")
        if key in content
    }
    if claims is None:
        claims = [
            {
                "field": field,
                "text": paragraph.strip(),
                "scope": "interpretation",
                "evidence_ids": [],
            }
            for field, value in bound.items()
            for paragraph in value.split("\n\n")
            if paragraph.strip()
        ]
    content["claim_review"] = {
        "evidence_sha256": contract["evidence_sha256"],
        "content_sha256": digest(bound),
        "excluded_warning_ids": [row["warning_id"] for row in contract["warnings"]],
        "research_materials": materials,
        "claims": claims,
    }


class PublicV2FlowTest(unittest.TestCase):
    def setUp(self):
        TerminalProjectionHandler.posts = 0
        TerminalProjectionHandler.gets = 0
        TerminalProjectionHandler.payloads = {}
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), TerminalProjectionHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def config(self, root: Path, port: int | None = None) -> Path:
        path = root / "publisher.json"
        write(path, {
            "website_url": f"http://127.0.0.1:{port or self.server.server_port}",
            "authority_identity": "qa-private:v2",
            "app_bearer": "runtime-only-test",
            "sites_bearer": "runtime-only-test",
        })
        return path

    def command(self, root: Path, run_id: str, fixture: Path) -> list[str]:
        command = [
            sys.executable, str(ROOT / "scripts/run_daily_workflow.py"),
            "--run-id", run_id, "--business-date", "2026-07-28",
            "--workflow-db", str(root / "workflow.sqlite3"),
            "--artifact-root", str(root / "runs"),
            "--collection-fixture", str(fixture), "--video-mode", "disabled",
        ]
        frozen_packages = root / "qa-packages.json"
        if frozen_packages.is_file():
            command.extend(["--qa-frozen-packages", str(frozen_packages)])
        return command

    @staticmethod
    def failure_details(root: Path, run_id: str) -> str:
        path = root / "runs" / run_id / "workflow_handoff.json"
        return path.read_text(encoding="utf-8") if path.is_file() else ""

    def execute(self, command: list[str], config: Path, extra_env: dict[str, str] | None = None):
        env = os.environ.copy()
        env["WEBSITE_PUBLISHER_CONFIG"] = str(config)
        if extra_env:
            env.update(extra_env)
        return subprocess.run(command, text=True, capture_output=True, env=env)

    def test_public_three_stage_single_publish_and_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_id = "run_20260728_080000"
            fixture = root / "collection.json"
            content = [
                {"aweme_id": str(7000 + i), "source": "Douyin",
                 "source_url": f"https://www.douyin.com/video/{7000+i}",
                 "title": f"AI {i}", "summary": "真实冻结理解包"}
                for i in range(6)
            ]
            write(fixture, {
                "run_id": run_id, "business_date": "2026-07-28",
                "content_items": content,
                "candidates": [
                    {"candidate_id": f"douyin:{7000+i}", "title": f"AI {i}"}
                    for i in range(6)
                ],
                "source_runs": [{"source": "Douyin", "status": "completed", "item_count": 6}],
            })
            frame = root / "frozen-frame.jpg"
            frame.write_bytes(b"qa-private-fixed-frame")
            write(root / "qa-packages.json", [{
                "run_id": run_id,
                "source_url": content[0]["source_url"],
                "status": "completed_with_failures",
                "media_resolution_status": "resolved",
                "evidence_quality": {
                    "actual_item_type": "video",
                    "media_access": "accessible",
                    "ocr": "completed",
                    "asr": "completed",
                    "visual_understanding": "not_reviewed",
                    "fact_confirmation": "pending",
                    "unresolved_terms": [{"term": "GPT‑5"}],
                    "failures": [],
                    "fully_verified": False,
                },
                "asr": {"text": "同 run 的来源口播事实"},
                "screen_text": [{
                    "kind": "tool", "value": "Codex", "time_second": 2.0,
                }],
                "keyframes": [{
                    "time_second": 2.0,
                    "path": str(frame),
                    "sha256": hashlib.sha256(frame.read_bytes()).hexdigest(),
                }],
                "unresolved_terms": [{"term": "GPT‑5"}],
            }])
            command = self.command(root, run_id, fixture)
            config = self.config(root)
            normalized = enrich(
                SimpleNamespace(
                    run_id=run_id,
                    business_date="2026-07-28",
                    video_mode="disabled",
                    qa_frozen_packages=str(root / "qa-packages.json"),
                ),
                json.loads(fixture.read_text(encoding="utf-8")),
            )
            identities = [row["candidate_id"] for row in normalized["candidates"]]
            handoff_by_id = {
                row["candidate_id"]: row
                for row in editorial_handoff_candidates(normalized)
            }
            editorial = root / "editorial.json"
            write(editorial, {
                "run_id": run_id, "topics": [{
                    "candidate_id": identities[0], "decision": "select",
                    "title": "选题", "hook": "钩子", "structure": "结构",
                    "selection_reason": "理由",
                    "evidence_source_ids": [
                        handoff_by_id[identities[0]]["sources"][0]["source_id"]
                    ],
                    "editorial_thesis": {
                        "thesis": "这条来源事实支持一个具体判断。",
                        "audience_conflict": "受众在事实和旧做法之间有明确冲突。",
                        "why_now": "同 run 资料让这个判断现在值得讲。",
                        "evidence_boundary": {
                            "source_facts": "同 run 来源事实。",
                            "interpretation": "由事实推到判断，不把推断当观察结果。",
                            "proposed_test": "可以用一个有界动作继续验证。",
                        },
                    },
                }] + [{
                    "candidate_id": identity, "decision": "observe",
                    "selection_reason": f"未达到本轮选择标准：{identity}",
                    "evidence_source_ids": [
                        handoff_by_id[identity]["sources"][0]["source_id"]
                    ],
                } for identity in identities[1:]],
            })
            first = self.execute(command + ["--editorial-result-file", str(editorial)], config)
            self.assertEqual(
                first.returncode, 0,
                first.stderr + first.stdout + self.failure_details(root, run_id),
            )
            self.assertEqual(last_json(first.stdout)["action"], "article_required")
            workflow = DailyWorkflow(root / "workflow.sqlite3")
            collection_stage = workflow.stage(run_id, "collection_enrichment")
            package = collection_stage["payload"]["understanding_results"][0]["package"]
            available_package = package["available_packages"][0]
            self.assertEqual(available_package["status"], "completed_with_failures")
            self.assertEqual(package["run_id"], run_id)
            self.assertEqual(available_package["asr"]["text"], "同 run 的来源口播事实")
            self.assertFalse(package["evidence_quality"]["fully_verified"])
            editorial_stage = workflow.stage(run_id, "editorial")
            scripts_stage = workflow.stage(run_id, "scripts")
            all_handoff = build_scripts_handoff(
                run_id, "2026-07-28", collection_stage["payload"], editorial_stage["payload"],
            )
            article_handoff = topic_packet(
                run_id, "2026-07-28", all_handoff["selected_topics"][0], 0, 1,
                len(scripts_stage["payload"]["completed_items"]), load_writer_contract(),
            )
            self.assertEqual(
                article_handoff["claim_dependency_authority"]["still_frames_do_not_verify_motion"],
                True,
            )
            self.assertTrue(any(
                row.get("kind") == "recognized_audio"
                and row.get("text") == "同 run 的来源口播事实"
                for row in article_handoff["claim_dependency_authority"]["anchors"]
            ))
            self.assertTrue(any(
                row.get("kind") == "recognized_screen_text"
                and row.get("text") == "Codex"
                for row in article_handoff["claim_dependency_authority"]["anchors"]
            ))
            article = root / "article.json"
            article_value = {
                "packet_id": article_handoff["topic_input"]["packet_id"],
                "article": {
                    "topic_id": identities[0], "title": "完整文章标题",
                    "body": (
                        "同 run 的来源口播事实\n\n"
                        "这是一段不依赖来源的新解读。\n\n"
                        "Synthetic excerpt for traceability plumbing; not factual content."
                    ),
                },
            }
            evidence = all_handoff["selected_topics"][0]["source_evidence"]
            source_anchor = next(
                row for row in claim_contract(evidence)["anchors"]
                if row.get("kind") == "recognized_audio"
                and row.get("text") == "同 run 的来源口播事实"
            )
            synthetic_research = {
                "run_id": run_id,
                "business_date": "2026-07-28",
                "topic_id": identities[0],
                "source_url": "https://research.example/qa/synthetic-web010",
                "title": "Synthetic QA-only public research record",
                "excerpt": "Synthetic excerpt for traceability plumbing; not factual content.",
            }
            research_reference = research_evidence_id(synthetic_research)
            add_claim_review(
                article_value["article"],
                evidence,
                research_materials=[synthetic_research],
                claims=[
                    {
                        "field": "title", "text": "完整文章标题",
                        "scope": "interpretation", "evidence_ids": [],
                    },
                    {
                        "field": "body", "text": "同 run 的来源口播事实",
                        "scope": "source_quote", "evidence_ids": [source_anchor["evidence_id"]],
                    },
                    {
                        "field": "body", "text": "这是一段不依赖来源的新解读。",
                        "scope": "interpretation", "evidence_ids": [],
                    },
                    {
                        "field": "body", "text": "Synthetic excerpt for traceability plumbing; not factual content.",
                        "scope": "source_context", "evidence_ids": [research_reference],
                    },
                ],
            )
            write(article, article_value)
            article_call = self.execute(command + [
                "--editorial-result-file", str(editorial),
                "--article-item-file", str(article),
            ], config)
            self.assertEqual(article_call.returncode, 0, article_call.stderr + article_call.stdout)
            handoff = json.loads(
                (root / "runs" / run_id / "workflow_handoff.json").read_text(encoding="utf-8")
            )
            frozen_article = read_article_artifact(
                handoff["topic_input"]["article_artifact"],
                run_id=run_id,
                business_date="2026-07-28",
                topic_id=identities[0],
                artifact_root=root / "runs",
            )
            self.assertEqual(frozen_article["claim_review"]["research_materials"], [synthetic_research])
            self.assertEqual(article_sha256(frozen_article), handoff["topic_input"]["article_artifact"]["sha256"])
            self.assertTrue(any(
                row.get("evidence_id") == research_reference
                and row.get("kind") == "public_research_material"
                for row in handoff["claim_dependency_authority"]["anchors"]
            ))
            scripts = root / "scripts.json"
            script_value = {
                "packet_id": handoff["topic_input"]["packet_id"],
                "article_sha256": handoff["topic_input"]["article_artifact"]["sha256"],
                "script": {
                    "topic_id": identities[0], "title": "稿件", "hook": "钩子",
                    "structure": "结构",
                    "body": "Synthetic excerpt for traceability plumbing; not factual content.\n\n这是另一段独立解读。",
                },
            }
            add_claim_review(
                script_value["script"],
                evidence,
                research_materials=[synthetic_research],
                claims=[
                    {"field": "title", "text": "稿件", "scope": "interpretation", "evidence_ids": []},
                    {"field": "hook", "text": "钩子", "scope": "interpretation", "evidence_ids": []},
                    {"field": "structure", "text": "结构", "scope": "interpretation", "evidence_ids": []},
                    {
                        "field": "body", "text": "Synthetic excerpt for traceability plumbing; not factual content.",
                        "scope": "source_context", "evidence_ids": [research_reference],
                    },
                    {
                        "field": "body", "text": "这是另一段独立解读。",
                        "scope": "interpretation", "evidence_ids": [],
                    },
                ],
            )
            write(scripts, script_value)
            second = self.execute(command + [
                "--editorial-result-file", str(editorial),
                "--script-item-file", str(scripts),
            ], config)
            self.assertEqual(second.returncode, 0, second.stderr + second.stdout)
            first_value = last_json(second.stdout)
            self.assertEqual(first_value["action"], "completed")
            self.assertEqual(first_value["selected_count"], 1)
            self.assertEqual(first_value["script_count"], 1)
            self.assertEqual(TerminalProjectionHandler.posts, 1)
            self.assertEqual(first_value["candidate_count"], 6)
            before = (root / "workflow.sqlite3").read_bytes()
            post_count = TerminalProjectionHandler.posts
            replay = self.execute(command, config)
            self.assertEqual(last_json(replay.stdout)["action"], "noop")
            self.assertEqual((root / "workflow.sqlite3").read_bytes(), before)
            self.assertEqual(TerminalProjectionHandler.posts, post_count)

    def test_offline_is_terminal_pending_and_replay_only_publishes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_id = "run_20260728_080000"
            fixture = root / "collection.json"
            write(fixture, {
                "run_id": run_id, "business_date": "2026-07-28",
                "content_items": [], "candidates": [], "source_runs": [],
            })
            offline = self.config(root, 1)
            command = self.command(root, run_id, fixture)
            first = self.execute(command, offline)
            self.assertEqual(
                first.returncode, 0,
                first.stderr + first.stdout + self.failure_details(root, run_id),
            )
            result = last_json(first.stdout)
            self.assertEqual(result["action"], "completed_publish_pending")
            self.assertEqual(result["status"], "completed_empty")
            self.assertEqual(result["publish_status"], "pending")
            before_stages = DailyWorkflow(root / "workflow.sqlite3").read_run(run_id)["stages"]
            recovered = self.execute(command, self.config(root))
            value = last_json(recovered.stdout)
            self.assertEqual(value["action"], "noop")
            self.assertEqual(value["publish_status"], "applied")
            self.assertEqual(
                DailyWorkflow(root / "workflow.sqlite3").read_run(run_id)["stages"],
                before_stages,
            )
            self.assertEqual(TerminalProjectionHandler.posts, 1)

    def test_historical_adapter_is_not_reconnected_to_normal_branch(self):
        normal_source = (ROOT / "scripts/run_daily_workflow.py").read_text()
        release = json.loads(
            (ROOT / "config/web010_single_daily_workflow_release.json").read_text()
        )
        protocol = "\n".join(release["externalSchedule"]["outerAgentProtocol"])
        self.assertNotIn("recover_web010_historical", normal_source)
        self.assertNotIn("historical/latest", protocol)
        self.assertIn("historical adapter", release["normalRuntimeForbiddenCalls"])


if __name__ == "__main__":
    unittest.main()
