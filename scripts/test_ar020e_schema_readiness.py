#!/usr/bin/env python3
from __future__ import annotations

import unittest

import ar020e_schema_readiness as readiness


class AR020ESchemaReadinessTests(unittest.TestCase):
    def test_matrix_reports_missing_without_mutating(self) -> None:
        matrix = readiness.field_matrix({"研究摘要": {"field_id": "fld1", "type": 1}})
        by_name = {item["field"]: item for item in matrix}
        self.assertEqual(by_name["研究摘要"]["release_action"], "none")
        self.assertEqual(by_name["受众钩子"]["release_action"], "create_field_before_runtime_enablement")
        self.assertEqual(len(matrix), 4)


if __name__ == "__main__":
    unittest.main()
