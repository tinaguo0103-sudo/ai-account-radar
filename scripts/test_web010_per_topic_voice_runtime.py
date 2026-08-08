from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
import zipfile
from unittest import mock
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from daily_workflow import DailyWorkflow
from spoken_script_runtime import load_austin_authority, load_author_edit_contract, sanitize_handoff


ROOT = Path(__file__).resolve().parents[1]
RUN_ID = "run_20260808_120000"
BUSINESS_DATE = "2026-08-08"


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def last_json(output: str) -> dict:
    return json.loads(output.strip().splitlines()[-1])


class Publisher(BaseHTTPRequestHandler):
    posts = 0
    gets = 0
    payloads: dict[str, dict] = {}

    def log_message(self, *_args):
        return

    def respond(self, value: dict, status: int = 200) -> None:
        body = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    @staticmethod
    def readback(payload: dict, status: str) -> dict:
        return {
            "ok": True,
            "status": status,
            "run_id": payload["run_id"],
            "business_date": payload["business_date"],
            "run_status": payload["run"]["status"],
            "revision": payload["revision"],
            "authority_identity": payload["authority_identity"],
            "counts": {
                "content": len(payload["collected_items"]),
                "topics": len(payload["topics"]),
                "scripts": len(payload["scripts"]),
            },
        }

    def do_POST(self):
        self.__class__.posts += 1
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.__class__.payloads[payload["run_id"]] = payload
        self.respond(self.readback(payload, "applied"))

    def do_GET(self):
        self.__class__.gets += 1
        run_id = self.path.split("run_id=", 1)[-1]
        payload = self.__class__.payloads.get(run_id)
        if payload is None:
            self.respond({"error": "business_projection_missing"}, 404)
            return
        self.respond(self.readback(payload, "readback"))


class PerTopicVoiceRuntimeTest(unittest.TestCase):
    def setUp(self):
        Publisher.posts = 0
        Publisher.gets = 0
        Publisher.payloads = {}
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Publisher)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def fixture(self, root: Path, run_id: str = RUN_ID, count: int = 3) -> Path:
        content = []
        candidates = []
        for index in range(count):
            identity = f"douyin:{9000 + index}"
            title = [
                "一段采访如何暴露产品承诺",
                "一个工具功能为什么改变不了交付",
                "一条产品公告里被忽略的限制",
            ][index % 3]
            content.append({
                "item_id": identity,
                "source_url": f"https://www.douyin.com/video/{9000 + index}",
                "source_title": title,
                "source_summary": "QA-private exact same-run source summary",
                "title": title,
                "summary": "QA-private exact same-run content",
            })
            candidates.append({
                "candidate_id": identity,
                "item_id": identity,
                "source_url": f"https://www.douyin.com/video/{9000 + index}",
                "title": title,
                "summary": "QA-private exact same-run candidate",
            })
        path = root / f"collection-{run_id}.json"
        write_json(path, {
            "run_id": run_id,
            "business_date": BUSINESS_DATE,
            "status": "completed",
            "content_items": content,
            "candidates": candidates,
            "source_runs": [{"source": "QA-private", "status": "completed", "item_count": count}],
        })
        return path

    def command(self, root: Path, fixture: Path, run_id: str = RUN_ID) -> list[str]:
        return [
            sys.executable,
            str(ROOT / "scripts" / "run_daily_workflow.py"),
            "--run-id", run_id,
            "--business-date", BUSINESS_DATE,
            "--workflow-db", str(root / f"{run_id}.sqlite3"),
            "--artifact-root", str(root / "runs"),
            "--collection-fixture", str(fixture),
            "--video-mode", "disabled",
        ]

    def execute(self, command: list[str], config: Path | None = None) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        if config is not None:
            environment["WEBSITE_PUBLISHER_CONFIG"] = str(config)
        else:
            environment.pop("WEBSITE_PUBLISHER_CONFIG", None)
        return subprocess.run(command, text=True, capture_output=True, env=environment)

    def publisher_config(self, root: Path) -> Path:
        path = root / "publisher.json"
        write_json(path, {
            "website_url": f"http://127.0.0.1:{self.server.server_port}",
            "authority_identity": "qa-private:content-driven-reset",
            "app_bearer": "runtime-only-test",
            "sites_bearer": "runtime-only-test",
        })
        return path

    def editorial_file(self, root: Path, topic_ids: list[str], run_id: str = RUN_ID) -> Path:
        path = root / f"editorial-{run_id}.json"
        write_json(path, {
            "run_id": run_id,
            "topics": [{
                "candidate_id": topic_id,
                "decision": "select",
                "title": f"题目 {index} 的真实判断",
                "hook": f"先看题目 {index} 自己的冲突。",
                "structure": "按这条材料自然推进",
                "selection_reason": "该题拥有独立事实和用户价值。",
                "unique_judgment": f"题目 {index} 不能被另一题替代。",
                "standalone_eligibility": {
                    "decision": "select",
                    "reason": "隐藏其他候选后仍值得完整表达。",
                },
            } for index, topic_id in enumerate(topic_ids)] + [{
                "candidate_id": topic_id,
                "decision": "observe",
                "selection_reason": "QA fixture non-selected row",
            } for topic_id in topic_ids[len(topic_ids):]],
        })
        return path

    def submission_file(self, root: Path, handoff: dict, index: int, failure: bool = False) -> Path:
        topic = handoff["selected_topics"][0]
        path = root / f"submission-{index}.json"
        if failure:
            payload = {
                "packet_id": handoff["topic_input"]["packet_id"],
                "failure": {
                    "topic_id": topic["topic_id"],
                    "reason": "material_insufficiency",
                    "detail": "该题的同一 run 材料不足以支撑独特的公开判断。",
                },
            }
        else:
            payload = {
                "packet_id": handoff["topic_input"]["packet_id"],
                "script": {
                    "topic_id": topic["topic_id"],
                    "title": f"题目 {index} 的判断",
                    "hook": f"先看题目 {index} 里真正发生了什么。",
                    "structure": "从来源事实推进到题目自己的判断。",
                    "body": (
                        f"这是题目 {index} 自己的连续口播正文。先把这条材料里的变化讲清楚，"
                        "再给出只属于它的判断和下一步，不借用另一题的现场。"
                    ),
                },
            }
        write_json(path, payload)
        return path

    def test_facts_first_contract_has_no_selector_or_style_payload(self):
        contract = load_author_edit_contract()
        self.assertEqual(contract["schema_version"], 1)
        self.assertEqual(contract["phase_order"], ["facts_first_draft", "austin_author_edit"])
        self.assertTrue(contract["facts_first_draft"]["topic_facts_only"])
        self.assertEqual(
            contract["facts_first_draft"]["private_context_read"],
            "forbidden_until_draft_complete",
        )
        self.assertEqual(contract["austin_author_edit"]["starts_after"], "draft_complete")
        self.assertEqual(
            contract["austin_author_edit"]["approved_modules"],
            "optional_local_edit_comparison_only",
        )
        self.assertIn("central_thesis", contract["austin_author_edit"]["preserves"])
        self.assertIn("argument_movement", contract["austin_author_edit"]["preserves"])
        self.assertIn("new_reversal", contract["austin_author_edit"]["must_not"])
        self.assertEqual(contract["raw_text_persistence"], "forbidden")
        packet = {
            "action": "scripts_required",
            "topic_input": {
                "writing_contract": contract,
                "writing_phases": {
                    "draft": {"private_context_read": "forbidden_until_draft_complete"},
                    "author_edit": {"starts_after": "draft_complete"},
                },
            },
        }
        sanitized = sanitize_handoff({
            **packet,
            "reference_selection": {"private_case_id": "should-be-removed"},
            "private_case_catalog": ["should-be-removed"],
        })
        encoded = json.dumps(sanitized, ensure_ascii=False)
        self.assertNotIn("reference_selection", encoded)
        self.assertNotIn("private_case_catalog", encoded)
        self.assertNotIn("voice_pack", encoded)
        self.assertNotIn("editing_reference", encoded)
        self.assertNotIn("semantic_reread", encoded)

        source = (ROOT / "scripts" / "run_daily_workflow.py").read_text(encoding="utf-8")
        self.assertNotIn("--script-reference-selection-file", source)
        self.assertNotIn("set_reference_selection", source)

    def test_runtime_reads_private_authority_and_returns_safe_ledger_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            private = root / "private"
            private.mkdir()
            sentinel = "PRIVATE_PERSONA_SENTINEL_MUST_NOT_ESCAPE"
            (private / "production_context.md").write_text(sentinel, encoding="utf-8")
            (private / "private_runtime.json").write_text(
                json.dumps({"style_rules": {}, "case_anchors": []}), encoding="utf-8",
            )
            cases = root / "cases.json"
            cases.write_text(json.dumps({"cards": []}), encoding="utf-8")
            project = root / "project"
            samples = project / "00_资料库" / "03_口播风格样稿"
            samples.mkdir(parents=True)
            (samples / "封面skill 口播稿final.md").write_text("owned sample cover", encoding="utf-8")
            (samples / "热点监控及脚本落地_1500字口播脚本.md").write_text("owned sample radar", encoding="utf-8")
            case_dir = project / "00_资料库" / "04_案例库"
            case_dir.mkdir(parents=True)
            docx = case_dir / "我的案例库.docx"
            with zipfile.ZipFile(docx, "w") as archive:
                archive.writestr("word/document.xml", "<document>owned edits</document>")
            mvp = root / "approved-mvp.md"
            director = root / "approved-director.md"
            mvp.write_text("approved module mvp", encoding="utf-8")
            director.write_text("approved module director", encoding="utf-8")
            with mock.patch.dict(os.environ, {
                "AUSTIN_PRIVATE_REFERENCE_ROOT": str(private),
                "AUSTIN_CASE_REFERENCE_FILE": str(cases),
                "AUSTIN_PROJECT_ROOT": str(project),
                "AUSTIN_APPROVED_SCRIPT_MVP": str(mvp),
                "AUSTIN_APPROVED_SCRIPT_DIRECTOR": str(director),
            }, clear=False):
                authority = load_austin_authority()
            encoded = json.dumps(authority, ensure_ascii=False)
            self.assertEqual(authority["read_status"], "complete")
            self.assertEqual(len(authority["sources"]), 8)
            self.assertNotIn(sentinel, encoded)
            self.assertTrue(all(set(row) == {"source_id", "role", "sha256", "read_status", "excerpt_ids"} for row in authority["sources"]))
            self.assertNotIn("evidence-playbook", encoded)
            self.assertNotIn("project_prd", encoded)

    def test_public_per_topic_checkpoint_resume_and_noop(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = self.fixture(root)
            config = self.publisher_config(root)
            command = self.command(root, fixture)

            first = self.execute(command, config)
            self.assertEqual(first.returncode, 0, first.stderr + first.stdout)
            self.assertEqual(last_json(first.stdout)["action"], "editorial_required")
            editorial_handoff = json.loads(
                (root / "runs" / RUN_ID / "workflow_handoff.json").read_text(encoding="utf-8")
            )
            topic_ids = [row["candidate_id"] for row in editorial_handoff["candidates"]]
            editorial = self.editorial_file(root, topic_ids)

            second = self.execute(command + ["--editorial-result-file", str(editorial)], config)
            self.assertEqual(second.returncode, 0, second.stderr + second.stdout)
            handoff_path = root / "runs" / RUN_ID / "workflow_handoff.json"
            handoff = json.loads(handoff_path.read_text(encoding="utf-8"))
            self.assertEqual(handoff["action"], "scripts_required")
            self.assertEqual(len(handoff["selected_topics"]), 1)
            self.assertEqual(handoff["topic_index"], 0)
            self.assertNotIn(topic_ids[1], json.dumps(handoff, ensure_ascii=False))
            self.assertIn("writing_contract", handoff)
            self.assertEqual(
                handoff["topic_input"]["writing_phases"]["draft"]["private_context_read"],
                "forbidden_until_draft_complete",
            )
            self.assertEqual(
                handoff["topic_input"]["writing_phases"]["author_edit"]["starts_after"],
                "draft_complete",
            )
            self.assertEqual(
                handoff["topic_input"]["writer_owns_final_fields"],
                ["title", "hook", "structure", "body"],
            )
            self.assertNotIn("PRIVATE_PERSONA", json.dumps(handoff, ensure_ascii=False))
            self.assertNotIn("reference_selection", json.dumps(handoff, ensure_ascii=False))
            self.assertNotIn("full_body_injection", json.dumps(handoff, ensure_ascii=False))
            self.assertNotIn("private_case_routing", json.dumps(handoff, ensure_ascii=False))
            self.assertNotIn("editing_reference", json.dumps(handoff, ensure_ascii=False))
            self.assertNotIn('"austin_authority_read":', json.dumps(handoff, ensure_ascii=False))
            self.assertNotIn('"austin_private_context":', json.dumps(handoff, ensure_ascii=False))

            resumed = self.execute(command + ["--editorial-result-file", str(editorial)], config)
            self.assertEqual(resumed.returncode, 0, resumed.stderr + resumed.stdout)
            resumed_handoff = json.loads(handoff_path.read_text(encoding="utf-8"))
            self.assertEqual(resumed_handoff["topic_input"]["packet_id"], handoff["topic_input"]["packet_id"])
            self.assertEqual(resumed_handoff["selected_topics"][0]["topic_id"], topic_ids[0])

            for index, topic_id in enumerate(topic_ids):
                handoff = json.loads(handoff_path.read_text(encoding="utf-8"))
                self.assertEqual(handoff["action"], "scripts_required")
                self.assertEqual(handoff["selected_topics"][0]["topic_id"], topic_id)
                submission = self.submission_file(root, handoff, index)
                result = self.execute(
                    command + ["--editorial-result-file", str(editorial), "--script-item-file", str(submission)],
                    config,
                )
                self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
                if index < len(topic_ids) - 1:
                    next_handoff = json.loads(handoff_path.read_text(encoding="utf-8"))
                    self.assertEqual(next_handoff["action"], "scripts_required")
                    self.assertEqual(next_handoff["topic_index"], index + 1)
                    self.assertNotIn(topic_id, json.dumps(next_handoff, ensure_ascii=False))

            terminal = last_json(result.stdout)
            self.assertEqual(terminal["action"], "completed")
            self.assertEqual(terminal["script_count"], 3)
            self.assertEqual(Publisher.posts, 1)
            stage = DailyWorkflow(root / f"{RUN_ID}.sqlite3").stage(RUN_ID, "scripts")
            self.assertEqual(stage["status"], "completed")
            self.assertEqual(
                [row["topic_id"] for row in stage["payload"]["scripts"]], topic_ids,
            )
            before = (root / f"{RUN_ID}.sqlite3").read_bytes()
            posts = Publisher.posts
            replay = self.execute(command, config)
            self.assertEqual(replay.returncode, 0, replay.stderr + replay.stdout)
            self.assertEqual(last_json(replay.stdout)["action"], "noop")
            self.assertEqual(Publisher.posts, posts)
            self.assertEqual((root / f"{RUN_ID}.sqlite3").read_bytes(), before)

    def test_sanitize_removes_transient_private_text_if_a_caller_supplies_it(self):
        sanitized = sanitize_handoff({
            "action": "scripts_required",
            "topic_input": {
                "austin_private_context_raw": "PRIVATE_CONTEXT_SENTINEL",
            },
        })
        self.assertNotIn("PRIVATE_CONTEXT_SENTINEL", json.dumps(sanitized, ensure_ascii=False))
        self.assertNotIn("austin_private_context_raw", sanitized["topic_input"])

    def test_material_insufficiency_is_item_local_and_terminal(self):
        run_id = "run_20260808_120001"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = self.fixture(root, run_id=run_id, count=1)
            command = self.command(root, fixture, run_id=run_id)
            first = self.execute(command)
            self.assertEqual(first.returncode, 0, first.stderr + first.stdout)
            handoff_path = root / "runs" / run_id / "workflow_handoff.json"
            ids = [row["candidate_id"] for row in json.loads(handoff_path.read_text())["candidates"]]
            editorial = self.editorial_file(root, ids, run_id=run_id)
            second = self.execute(command + ["--editorial-result-file", str(editorial)])
            self.assertEqual(second.returncode, 0, second.stderr + second.stdout)
            handoff = json.loads(handoff_path.read_text())
            submission = self.submission_file(root, handoff, 0, failure=True)
            terminal = self.execute(
                command + ["--editorial-result-file", str(editorial), "--script-item-file", str(submission)],
            )
            self.assertEqual(terminal.returncode, 0, terminal.stderr + terminal.stdout)
            self.assertEqual(last_json(terminal.stdout)["action"], "completed_publish_pending")
            stage = DailyWorkflow(root / f"{run_id}.sqlite3").stage(run_id, "scripts")
            self.assertEqual(stage["status"], "completed_with_failures")
            self.assertEqual(
                stage["payload"]["failures"][0]["reason"],
                "material_or_angle_insufficiency",
            )

    def test_selector_cli_is_removed_from_normal_entrypoint(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            command = [
                sys.executable,
                str(ROOT / "scripts" / "run_daily_workflow.py"),
                "--business-date", BUSINESS_DATE,
                "--workflow-db", str(root / "workflow.sqlite3"),
                "--artifact-root", str(root / "runs"),
                "--script-reference-selection-file", str(root / "selection.json"),
            ]
            result = self.execute(command)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("unrecognized arguments", result.stderr)


if __name__ == "__main__":
    unittest.main()
