from __future__ import annotations

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
from run_daily_workflow import build_scripts_handoff, enrich
from run_daily_workflow import WorkflowExecutionLock
from spoken_script_runtime import load_writer_contract, topic_packet


ROOT = Path(__file__).resolve().parents[1]
RUN_ID = "run_20260731_080000"
BUSINESS_DATE = "2026-07-31"


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def last_json(output: str) -> dict:
    return json.loads(output.strip().splitlines()[-1])


class ProjectionHandler(BaseHTTPRequestHandler):
    posts = 0
    gets = 0
    payloads: dict[str, dict] = {}

    def log_message(self, *_args):
        return

    def reply(self, status: int, value: dict) -> None:
        body = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    @staticmethod
    def readback(payload: dict) -> dict:
        return {
            "ok": True,
            "run_id": payload["run_id"],
            "business_date": payload["business_date"],
            "run_status": payload["run"]["status"],
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
        self.reply(200, {"status": "applied", **self.readback(payload)})

    def do_GET(self):
        self.__class__.gets += 1
        run_id = self.path.split("run_id=", 1)[-1]
        payload = self.__class__.payloads.get(run_id)
        if payload is None:
            self.reply(404, {"error": "business_projection_missing"})
        else:
            self.reply(200, {"status": "readback", **self.readback(payload)})


class NaturalRunContinuationTest(unittest.TestCase):
    def setUp(self):
        ProjectionHandler.posts = 0
        ProjectionHandler.gets = 0
        ProjectionHandler.payloads = {}
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), ProjectionHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def base_command(self, root: Path, fixture: Path) -> list[str]:
        return [
            sys.executable,
            str(ROOT / "scripts" / "run_daily_workflow.py"),
            "--run-id",
            RUN_ID,
            "--business-date",
            BUSINESS_DATE,
            "--workflow-db",
            str(root / "workflow.sqlite3"),
            "--artifact-root",
            str(root / "runs"),
            "--collection-fixture",
            str(fixture),
            "--video-mode",
            "disabled",
        ]

    def execute(
        self,
        command: list[str],
        config: Path | None = None,
        extra_env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        if config is not None:
            environment["WEBSITE_PUBLISHER_CONFIG"] = str(config)
        if extra_env:
            environment.update(extra_env)
        return subprocess.run(command, text=True, capture_output=True, env=environment)

    def fixture(self, root: Path) -> Path:
        path = root / "collection.json"
        rows = [
            {
                "aweme_id": str(8000 + index),
                "source": "Douyin",
                "source_url": f"https://www.douyin.com/video/{8000 + index}",
                "title": f"AI workflow {index}",
                "summary": "production-shaped frozen input",
            }
            for index in range(6)
        ]
        write_json(path, {
            "run_id": RUN_ID,
            "business_date": BUSINESS_DATE,
            "content_items": rows,
            "candidates": [
                {"candidate_id": f"douyin:{8000 + index}", **row}
                for index, row in enumerate(rows)
            ],
            "source_runs": [
                {"source": "Douyin", "status": "completed", "item_count": 6},
            ],
        })
        return path

    def publisher_config(self, root: Path) -> Path:
        path = root / "publisher.json"
        write_json(path, {
            "website_url": f"http://127.0.0.1:{self.server.server_port}",
            "authority_identity": "qa-private:natural-continuation",
            "app_bearer": "runtime-only",
            "sites_bearer": "runtime-only",
        })
        return path

    def test_status_only_is_read_only_and_returns_unique_exact_date_run(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "workflow.sqlite3"
            command = [
                sys.executable,
                str(ROOT / "scripts" / "run_daily_workflow.py"),
                "--business-date",
                BUSINESS_DATE,
                "--workflow-db",
                str(database),
                "--artifact-root",
                str(root / "runs"),
                "--status-only",
            ]
            missing = self.execute(command)
            self.assertEqual(last_json(missing.stdout)["action"], "no_run_for_business_date")
            self.assertFalse(database.exists())
            workflow = DailyWorkflow(database)
            workflow.begin(RUN_ID, BUSINESS_DATE)
            workflow.mark_waiting(RUN_ID)
            before = database.read_bytes()
            status = self.execute(command)
            value = last_json(status.stdout)
            self.assertEqual(value["run_id"], RUN_ID)
            self.assertEqual(value["status"], "waiting")
            self.assertEqual(database.read_bytes(), before)

    def test_concurrent_long_process_reports_waiting_without_starting_collection(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "workflow.sqlite3"
            workflow = DailyWorkflow(database)
            workflow.begin(RUN_ID, BUSINESS_DATE)
            workflow.mark_waiting(RUN_ID)
            write_json(root / "runs" / RUN_ID / "workflow_handoff.json", {
                "schema_version": 1,
                "run_id": RUN_ID,
                "business_date": BUSINESS_DATE,
                "ok": True,
                "action": "editorial_required",
                "stage": "editorial",
                "status": "waiting",
                "candidates": [{"candidate_id": "must-not-be-replayed"}],
            })
            before = database.read_bytes()
            missing_fixture = root / "must-not-be-read.json"
            lock = WorkflowExecutionLock(database)
            self.assertTrue(lock.acquire())
            try:
                result = self.execute(self.base_command(root, missing_fixture))
            finally:
                lock.release()
            self.assertEqual(result.returncode, 0)
            result_value = last_json(result.stdout)
            self.assertEqual(result_value["action"], "waiting_stage_process")
            self.assertEqual(result_value["stage"], "editorial")
            self.assertNotIn("candidates", result_value)
            self.assertEqual(database.read_bytes(), before)
            self.assertFalse(missing_fixture.exists())

    def test_public_interruption_continuation_terminal_publish_and_replay(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture = self.fixture(root)
            command = self.base_command(root, fixture)
            normalized = enrich(
                SimpleNamespace(
                    run_id=RUN_ID,
                    business_date=BUSINESS_DATE,
                    video_mode="disabled",
                    qa_frozen_packages=None,
                ),
                json.loads(fixture.read_text(encoding="utf-8")),
            )
            candidate_ids = [row["candidate_id"] for row in normalized["candidates"]]
            editorial = root / "editorial.json"
            write_json(editorial, {
                "run_id": RUN_ID,
                "topics": [{
                    "candidate_id": candidate_ids[0],
                    "decision": "select",
                    "title": "AI workflow",
                    "hook": "具体冲突",
                    "structure": "场景到动作",
                    "selection_reason": "候选包含明确工作流事实",
                }] + [{
                    "candidate_id": candidate_ids[index],
                    "decision": "observe",
                    "selection_reason": f"候选 {index} 暂不进入脚本",
                } for index in range(1, len(candidate_ids))],
            })
            first = self.execute(
                command + ["--editorial-result-file", str(editorial)],
                extra_env={"WEB010_INJECT_WRITER_FAILURE_TOPIC": candidate_ids[0]},
            )
            self.assertEqual(first.returncode, 2, first.stderr + first.stdout)
            first_lines = [json.loads(line) for line in first.stdout.splitlines() if line.strip()]
            self.assertEqual(first_lines[0]["action"], "waiting_stage")
            self.assertEqual(first_lines[-1]["action"], "child_failed_recoverable")
            handoff_path = Path(first_lines[-1]["handoff_path"])
            status_command = [
                sys.executable,
                str(ROOT / "scripts" / "run_daily_workflow.py"),
                "--business-date",
                BUSINESS_DATE,
                "--workflow-db",
                str(root / "workflow.sqlite3"),
                "--artifact-root",
                str(root / "runs"),
                "--status-only",
            ]
            interrupted = self.execute(status_command)
            interrupted_value = last_json(interrupted.stdout)
            self.assertEqual(interrupted_value["status"], "failed_recoverable")
            self.assertEqual(interrupted_value["next_action"], "child_failed_recoverable")
            fixture.unlink()
            (root / "runs" / RUN_ID / "workflow_collection.json").unlink()
            workflow = DailyWorkflow(root / "workflow.sqlite3")
            collection_stage = workflow.stage(RUN_ID, "collection_enrichment")
            editorial_stage = workflow.stage(RUN_ID, "editorial")
            scripts_stage = workflow.stage(RUN_ID, "scripts")
            all_handoff = build_scripts_handoff(
                RUN_ID, BUSINESS_DATE,
                collection_stage["payload"], editorial_stage["payload"],
            )
            scripts_handoff = topic_packet(
                RUN_ID, BUSINESS_DATE, all_handoff["selected_topics"][0], 0, 1,
                len(scripts_stage["payload"]["completed_items"]),
                load_writer_contract(),
            )
            scripts = root / "scripts.json"
            write_json(scripts, {
                "packet_id": scripts_handoff["topic_input"]["packet_id"],
                "script": {
                    "topic_id": candidate_ids[0],
                    "title": "AI workflow",
                    "hook": "具体冲突",
                    "structure": "场景到动作",
                    "body": "这是一篇可以继续进入提词器修改的完整正文。",
                },
            })
            third = self.execute(command + [
                "--editorial-result-file",
                str(editorial),
                "--script-item-file",
                str(scripts),
            ], self.publisher_config(root))
            self.assertEqual(third.returncode, 0, third.stderr + third.stdout)
            terminal = last_json(third.stdout)
            self.assertEqual(terminal["action"], "completed")
            self.assertEqual(
                (terminal["candidate_count"], terminal["selected_count"], terminal["script_count"]),
                (6, 1, 1),
            )
            self.assertEqual(ProjectionHandler.posts, 1)
            self.assertGreaterEqual(ProjectionHandler.gets, 1)
            workflow = DailyWorkflow(root / "workflow.sqlite3").read_run(RUN_ID)
            self.assertEqual(
                [row["stage"] for row in workflow["stages"]],
                ["collection_enrichment", "editorial", "scripts"],
            )
            self.assertEqual(len(workflow["skill_diagnostics"]), 2)
            database_before = (root / "workflow.sqlite3").read_bytes()
            handoff_before = handoff_path.read_bytes()
            post_count = ProjectionHandler.posts
            get_count = ProjectionHandler.gets
            replay = self.execute(command, self.publisher_config(root))
            self.assertEqual(last_json(replay.stdout)["action"], "noop")
            self.assertEqual((root / "workflow.sqlite3").read_bytes(), database_before)
            self.assertEqual(handoff_path.read_bytes(), handoff_before)
            self.assertEqual(ProjectionHandler.posts, post_count)
            self.assertEqual(ProjectionHandler.gets, get_count)

    def test_release_contract_keeps_one_automation_and_forbids_nested_runtime(self):
        release = json.loads(
            (ROOT / "config" / "web010_single_daily_workflow_release.json").read_text()
        )
        schedule = release["externalSchedule"]
        self.assertEqual(schedule["existingId"], "ai-rebuild")
        self.assertTrue(schedule["singleAutomationContinuation"])
        self.assertIn("--status-only", schedule["statusEntrypoint"])
        protocol = "\n".join(schedule["outerAgentProtocol"])
        self.assertIn("same ai-rebuild heartbeat and fixed task", protocol)
        self.assertIn("wait for that live process to exit", protocol)
        self.assertIn("Do not infer a hang from artifact or file-mtime silence", protocol)
        self.assertIn("bounded tail retry may remain silent for 600000ms", protocol)
        self.assertIn("existing total automation execution boundary remains unchanged", protocol)
        source = (ROOT / "scripts" / "run_daily_workflow.py").read_text()
        for forbidden in (
            "codex exec",
            "subagent",
            "skill_requests",
            "projection_receipts",
            "request envelope",
        ):
            self.assertNotIn(forbidden, source)

    def test_live_child_outlasts_old_file_silence_limit_without_real_wait(self):
        release = json.loads(
            (ROOT / "config" / "web010_single_daily_workflow_release.json").read_text()
        )
        protocol = "\n".join(release["externalSchedule"]["outerAgentProtocol"])

        def simulate(*, use_file_silence_stop: bool) -> tuple[int, int]:
            now_ms = 0
            child_exit_ms = 600_001
            old_file_silence_limit_ms = 540_000
            while now_ms < child_exit_ms:
                if use_file_silence_stop and now_ms >= old_file_silence_limit_ms:
                    return 130, now_ms
                now_ms += 60_000
            return 0, child_exit_ms

        self.assertEqual(simulate(use_file_silence_stop=True), (130, 540_000))
        self.assertIn("do not interrupt a live child on that basis", protocol)
        self.assertEqual(simulate(use_file_silence_stop=False), (0, 600_001))


if __name__ == "__main__":
    unittest.main()
