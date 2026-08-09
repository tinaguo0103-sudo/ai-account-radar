from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from unittest import mock

import web010_codex_child as child


class CodexChildAdapterTest(unittest.TestCase):
    def fake_run(self, expected: dict):
        def run(command, **_kwargs):
            output_path = command[command.index("--output-last-message") + 1]
            wrapper = {
                "run_id": expected.get("run_id", ""),
                "packet_id": expected.get("packet_id", ""),
                "result_json": json.dumps(expected["result"], ensure_ascii=False),
            }
            with open(output_path, "w", encoding="utf-8") as handle:
                json.dump(wrapper, handle, ensure_ascii=False)
            return mock.Mock(returncode=0)

        return run

    def test_transport_schema_is_strict_envelope(self):
        for role, required in (("editorial", {"run_id", "result_json"}), ("writer", {"packet_id", "result_json"})):
            schema = child._schema(role)
            self.assertEqual(schema["additionalProperties"], False)
            self.assertEqual(set(schema["required"]), required)
            self.assertEqual(set(schema["properties"]), required)

    def test_writer_prompt_keeps_item_failure_shape_typed(self):
        prompt = child._prompt("writer", Path("/tmp/topic.json"))
        self.assertIn('"reason":"material_or_angle_insufficiency"', prompt)
        self.assertIn("with no other failure keys", prompt)
        self.assertIn(str(child.VOICE_SKILL), prompt)
        self.assertNotIn("austin-no-overtime-scripting", prompt)
        self.assertNotIn("Semantic Plan", prompt)
        self.assertNotIn("scene/conflict/old workflow/experiment/judgment/consequence/close", prompt)

    def test_child_prompts_have_one_role_specific_skill(self):
        editorial = child._prompt("editorial", Path("/tmp/candidates.json"))
        writer = child._prompt("writer", Path("/tmp/topic.json"))
        self.assertIn(str(child.EDITORIAL_SKILL), editorial)
        self.assertNotIn(str(child.VOICE_SKILL), editorial)
        self.assertIn(str(child.VOICE_SKILL), writer)
        self.assertNotIn(str(child.EDITORIAL_SKILL), writer)
        self.assertNotIn("austin-no-overtime-scripting", writer)
        self.assertNotIn("Semantic Plan", writer)
        self.assertNotIn("conflict/old workflow/action/consequence/QA", writer)

    def test_editorial_child_decodes_existing_result_and_records_safe_metadata(self):
        result = {"run_id": "run-test", "topics": []}
        with mock.patch.object(child, "resolve_codex_cli", return_value="codex"), mock.patch(
            "web010_codex_child.subprocess.run",
            side_effect=self.fake_run({"run_id": "run-test", "result": result}),
        ) as run:
            decoded, metadata = child.run_editorial_child("run-test", "2026-08-08", [])
        self.assertEqual(decoded, result)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(metadata["context_mode"], "ephemeral_isolated_child")
        self.assertTrue(run.call_args.kwargs["env"]["CODEX_HOME"].endswith("codex-home"))
        self.assertEqual(metadata["recursive_codex"], 0)
        self.assertEqual(metadata["business_write"], 0)

    def test_writer_child_decodes_existing_submission_envelope(self):
        result = {
            "packet_id": "packet-test",
            "script": {
                "topic_id": "topic-test",
                "title": "title",
                "hook": "hook",
                "structure": "structure",
                "body": "body",
            },
        }
        packet = {"topic_input": {"packet_id": "packet-test", "topic_id": "topic-test"}}
        with mock.patch.object(child, "resolve_codex_cli", return_value="codex"), mock.patch(
            "web010_codex_child.subprocess.run",
            side_effect=self.fake_run({"packet_id": "packet-test", "result": result}),
        ):
            decoded, metadata = child.run_writer_child("run-test", "2026-08-08", packet)
        self.assertEqual(decoded, result)
        self.assertEqual(metadata["writer_packet_topic_count"], 1)
        self.assertEqual(metadata["other_topic_count"], 0)
        self.assertTrue(metadata["private_authority_transient"])

    def test_injected_failure_is_before_child_and_does_not_start_fallback(self):
        packet = {
            "topic_input": {"packet_id": "packet-test", "topic_id": "topic-test"},
            "selected_topics": [{"topic_id": "topic-test"}],
        }
        with mock.patch.dict(os.environ, {"WEB010_INJECT_WRITER_FAILURE_TOPIC": "topic-test"}), mock.patch(
            "web010_codex_child.subprocess.run"
        ) as run:
            with self.assertRaises(child.ChildExecutionError) as raised:
                child.run_writer_child("run-test", "2026-08-08", packet)
        self.assertEqual(raised.exception.code, "writer_child_injected_failure")
        self.assertEqual(raised.exception.details["invoked"], False)
        run.assert_not_called()

    def test_injected_failure_after_completed_topic_is_before_child(self):
        packet = {
            "completed_count": 1,
            "topic_input": {"packet_id": "packet-test", "topic_id": "topic-test"},
        }
        with mock.patch.dict(os.environ, {"WEB010_INJECT_WRITER_FAILURE_AFTER_COMPLETED": "1"}), mock.patch(
            "web010_codex_child.subprocess.run"
        ) as run:
            with self.assertRaises(child.ChildExecutionError) as raised:
                child.run_writer_child("run-test", "2026-08-08", packet)
        self.assertEqual(raised.exception.code, "writer_child_injected_failure_after_completed")
        self.assertEqual(raised.exception.details["invoked"], False)
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
