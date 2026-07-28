from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import active_skill_executor as executor


class ActiveSkillExecutorTest(unittest.TestCase):
    def test_large_payload_uses_stdin_instead_of_process_arguments(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skill = root / "skill-a"
            skill.mkdir()
            (skill / "SKILL.md").write_text("skill")

            def complete(command, **kwargs):
                self.assertEqual(command[-1], "-")
                self.assertNotIn("x" * 1000, " ".join(command))
                self.assertGreater(len(kwargs["input"]), 500_000)
                Path(command[-2]).write_text(json.dumps({"ok": True}))
                return mock.Mock(returncode=0)

            with mock.patch.object(executor, "ACTIVE_ROOT", root), \
                    mock.patch.object(executor.subprocess, "run", side_effect=complete):
                result, identities = executor.invoke(
                    ["skill-a"],
                    {"content": "x" * 600_000, "output_contract": {"ok": "boolean"}},
                )
            self.assertEqual(result, {"ok": True})
            self.assertEqual(identities[0]["name"], "skill-a")


if __name__ == "__main__":
    unittest.main()
