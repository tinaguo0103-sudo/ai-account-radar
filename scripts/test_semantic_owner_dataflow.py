from __future__ import annotations

import unittest

import ar020d_semantic_owner_gate as active_gate
import semantic_owner_dataflow as audit
import topic_skill_replay_evaluation as replay


FORMS = {
    "boolop": 'def bad(row):\n    return row.get("{a}") or row.get("{b}")\n',
    "ifexp": 'def bad(row):\n    return row.get("{a}") if row.get("{a}") else row.get("{b}")\n',
    "subscript": 'def bad(row):\n    return row["{a}"] or row["{b}"]\n',
    "sequential": 'def bad(row):\n    value = row.get("{a}")\n    if not value:\n        value = row.get("{b}")\n    return value\n',
    "alias_reassignment": 'def bad(row):\n    first = row.get("{a}")\n    value = first\n    if not value:\n        second = row.get("{b}")\n        value = second\n    return value\n',
    "early_return": 'def bad(row):\n    if row.get("{a}"):\n        return row.get("{a}")\n    return row.get("{b}")\n',
    "nested_branch": 'def bad(row):\n    value = row.get("{a}")\n    if not value:\n        if row.get("{b}"):\n            value = row.get("{b}")\n    return value\n',
    "get_default": 'def bad(row):\n    return row.get("{a}", row.get("{b}"))\n',
    "try_except": 'def bad(row):\n    try:\n        value = row["{a}"]\n    except KeyError:\n        value = row.get("{b}")\n    return value\n',
    "annassign": 'def bad(row):\n    value: str = row.get("{a}")\n    if not value:\n        value = row.get("{b}")\n    return value\n',
    "namedexpr": 'def bad(row):\n    if not (value := row.get("{a}")):\n        value = row.get("{b}")\n    return value\n',
}


class SemanticOwnerDataflowTests(unittest.TestCase):
    def test_every_owner_group_and_control_flow_form_is_detected(self) -> None:
        for group, fields in audit.OWNER_GROUPS.items():
            a, b = sorted(fields)[:2]
            for form, template in FORMS.items():
                with self.subTest(group=group, form=form, a=a, b=b):
                    violations = audit.audit_source(template.format(a=a, b=b))
                    self.assertTrue(violations, f"undetected {group}/{form}")

    def test_round2_exact_cross_statement_probes_are_detected(self) -> None:
        probes = [
            '''def bad(row):
    value = row.get("Austin改写理由")
    if not value:
        value = row.get("标题思路")
    return value
''',
            '''def bad(row):
    if row.get("Austin改写理由"):
        return row.get("Austin改写理由")
    return row.get("标题思路")
''',
            '''def bad(row):
    value = row.get("选题命题")
    if not value:
        if row.get("我的选题标题"):
            value = row.get("我的选题标题")
    return value
''',
        ]
        for source in probes:
            self.assertTrue(replay.semantic_cross_field_fallback_violations(source))

    def test_independent_output_keys_are_allowed(self) -> None:
        source = '''def good(row):
    return {"title": row.get("原始来源标题"), "caption": row.get("原始发布文案")}
'''
        self.assertEqual(audit.audit_source(source), [])

    def test_display_placeholder_is_allowed(self) -> None:
        source = '''def good(row):
    return row.get("原始来源标题") or "平台未提供独立标题"
'''
        self.assertEqual(audit.audit_source(source), [])

    def test_technical_metadata_compatibility_is_allowed(self) -> None:
        source = '''def good(row):
    return row.get("record_id") or row.get("id")
'''
        self.assertEqual(audit.audit_source(source), [])

    def test_active_static_and_behavioral_gate_passes(self) -> None:
        result = active_gate.run_gate()
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["static"]["violation_count"], 0)
        self.assertEqual(result["behavioral"]["failure_count"], 0)


if __name__ == "__main__":
    unittest.main()
