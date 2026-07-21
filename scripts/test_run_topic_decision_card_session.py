#!/usr/bin/env python3
from __future__ import annotations

import argparse
import subprocess
import unittest

import run_topic_decision_card_session as session


class TopicDecisionCardSessionTests(unittest.TestCase):
    @staticmethod
    def args(*, dry_run: bool = False) -> argparse.Namespace:
        return argparse.Namespace(run_id="run-test", send_dry_run=dry_run)

    def test_builder_failure_is_truthful_sender_failure(self) -> None:
        result = subprocess.CompletedProcess(["card"], 1, stdout="", stderr="validation failed")
        payload = session.session_result(self.args(), result, {})
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["sent_count"], 0)
        self.assertEqual(payload["reason"], "sender_failed")

    def test_dry_run_and_actual_send_are_distinct(self) -> None:
        dry = session.session_result(
            self.args(dry_run=True),
            subprocess.CompletedProcess(["card"], 0, stdout="", stderr=""),
            {"record_count": 3, "send": "dry-run"},
        )
        sent = session.session_result(
            self.args(),
            subprocess.CompletedProcess(["card"], 0, stdout="", stderr=""),
            {"record_count": 3, "send": [{"message_id": "redacted"}]},
        )
        self.assertEqual((dry["reason"], dry["sent_count"]), ("previewed", 0))
        self.assertEqual((sent["reason"], sent["sent_count"]), ("sent", 1))

    def test_second_same_run_is_owned_zero_send(self) -> None:
        payload = session.session_result(
            self.args(),
            subprocess.CompletedProcess(["card"], 0, stdout="", stderr=""),
            {"record_count": 0, "send": []},
        )
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["sent_count"], 0)
        self.assertEqual(payload["reason"], "already_sent_for_run")


if __name__ == "__main__":
    unittest.main()
