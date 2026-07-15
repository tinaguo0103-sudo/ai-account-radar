#!/usr/bin/env python3
"""Conservative function-local dataflow audit for AR-020D semantic owners."""
from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any


OWNER_GROUPS = {
    "source_identity": {"原始来源标题", "原始发布文案", "来源内容", "来源标题", "原始来源摘录"},
    "research": {"研究摘要", "受众钩子", "research_summary", "audience_hook"},
    "editorial_rationale": {
        "主编判断摘要", "Austin改写理由", "标题思路", "source_read",
        "public_decision_summary", "title_rationale",
    },
    "visible_title": {"选题命题", "选题标题", "我的选题标题", "可发布标题", "selected_visible_title"},
    "natural_angle": {"我的切入", "natural_austin_angle", "locked_natural_austin_angle"},
}
OWNER_FIELDS = set().union(*OWNER_GROUPS.values())
ACTIVE_FILES = (
    "topic_skill_replay_evaluation.py",
    "topic_editorial_state_machine.py",
    "editorial_skill_runner.py",
    "topic_field_contract.py",
    "validate_ar020d_visible_closure.py",
    "push_today10_to_feishu.py",
    "feishu_topic_decision_card.py",
)


def _field_key(node: ast.AST) -> str | None:
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "get"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
    ):
        return node.args[0].value
    if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str):
        return node.slice.value
    return None


class FunctionAudit:
    def __init__(self, source: str, function: str) -> None:
        self.source = source
        self.function = function
        self.violations: list[dict[str, Any]] = []
        self.return_sinks: dict[str, set[str]] = {}
        self.output_sinks: dict[str, set[str]] = {}

    def record(self, node: ast.AST, fields: set[str], kind: str, sink: str = "") -> None:
        semantic = sorted(fields & OWNER_FIELDS)
        if len(semantic) < 2:
            return
        item = {
            "source": self.source,
            "function": self.function,
            "line": getattr(node, "lineno", 0),
            "kind": kind,
            "fields": semantic,
        }
        if sink:
            item["sink"] = sink
        if item not in self.violations:
            self.violations.append(item)

    def expr(self, node: ast.AST | None, env: dict[str, set[str]]) -> set[str]:
        if node is None:
            return set()
        key = _field_key(node)
        if key in OWNER_FIELDS:
            fields = {key}
            if isinstance(node, ast.Call) and len(node.args) > 1:
                fields |= self.expr(node.args[1], env)
                self.record(node, fields, "get_default_fallback")
            return fields
        if isinstance(node, ast.Name):
            return set(env.get(node.id, set()))
        if isinstance(node, ast.NamedExpr):
            fields = self.expr(node.value, env)
            if isinstance(node.target, ast.Name):
                env[node.target.id] = set(fields)
            return fields
        if (isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or)) or isinstance(node, ast.IfExp):
            children = node.values if isinstance(node, ast.BoolOp) else [node.body, node.orelse]
            fields = set().union(*(self.expr(child, env) for child in children))
            self.record(node, fields, "expression_fallback")
            return fields
        if isinstance(node, ast.Call):
            fields = set().union(*(self.expr(arg, env) for arg in node.args))
            fields |= set().union(*(self.expr(value, env) for value in node.keywords))
            return fields
        if isinstance(node, ast.Dict):
            return set().union(*(self.expr(value, env) for value in node.values))
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            return set().union(*(self.expr(value, env) for value in node.elts))
        fields: set[str] = set()
        for child in ast.iter_child_nodes(node):
            fields |= self.expr(child, env)
        return fields

    def assign(self, target: ast.AST, fields: set[str], env: dict[str, set[str]]) -> None:
        if isinstance(target, ast.Name):
            env[target.id] = set(fields)
        elif isinstance(target, (ast.Tuple, ast.List)):
            for item in target.elts:
                self.assign(item, fields, env)

    def assign_dict(self, target: ast.AST, value: ast.Dict, env: dict[str, set[str]]) -> bool:
        if not isinstance(target, ast.Name):
            return False
        for key_node, item in zip(value.keys, value.values):
            key = key_node.value if isinstance(key_node, ast.Constant) else "<dynamic>"
            self.output_sinks.setdefault(f"{target.id}:{key}", set()).update(self.expr(item, env))
        env[target.id] = set()
        return True

    @staticmethod
    def merge(states: list[dict[str, set[str]]]) -> list[dict[str, set[str]]]:
        if not states:
            return []
        merged: dict[str, set[str]] = {}
        for state in states:
            for name, fields in state.items():
                merged.setdefault(name, set()).update(fields)
        return [merged]

    def return_value(self, node: ast.Return, env: dict[str, set[str]]) -> None:
        if isinstance(node.value, ast.Dict):
            for key_node, value in zip(node.value.keys, node.value.values):
                key = key_node.value if isinstance(key_node, ast.Constant) else "<dynamic>"
                fields = self.expr(value, env)
                self.return_sinks.setdefault(f"dict:{key}", set()).update(fields)
            return
        self.return_sinks.setdefault("return", set()).update(self.expr(node.value, env))

    def block(self, statements: list[ast.stmt], states: list[dict[str, set[str]]]) -> list[dict[str, set[str]]]:
        current = states
        for statement in statements:
            if not current:
                break
            if isinstance(statement, (ast.Assign, ast.AnnAssign)):
                value = statement.value
                for env in current:
                    fields = self.expr(value, env)
                    if isinstance(value, (ast.BoolOp, ast.IfExp)):
                        self.record(value, fields, "assignment_fallback")
                    targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
                    for target in targets:
                        if isinstance(value, ast.Dict) and self.assign_dict(target, value, env):
                            continue
                        if isinstance(target, ast.Subscript) and isinstance(target.value, ast.Name):
                            if isinstance(value, (ast.Dict, ast.List, ast.ListComp, ast.Tuple, ast.Set, ast.SetComp)):
                                continue
                            key = target.slice.value if isinstance(target.slice, ast.Constant) else "<dynamic>"
                            self.output_sinks.setdefault(f"{target.value.id}:{key}", set()).update(fields)
                        else:
                            self.assign(target, fields, env)
            elif isinstance(statement, ast.AugAssign):
                for env in current:
                    fields = self.expr(statement.value, env) | self.expr(statement.target, env)
                    self.assign(statement.target, fields, env)
            elif isinstance(statement, ast.Expr):
                for env in current:
                    self.expr(statement.value, env)
            elif isinstance(statement, ast.Return):
                for env in current:
                    self.return_value(statement, env)
                current = []
            elif isinstance(statement, ast.If):
                branches: list[dict[str, set[str]]] = []
                for env in current:
                    test_env = {name: set(fields) for name, fields in env.items()}
                    self.expr(statement.test, test_env)
                    branches.extend(self.block(statement.body, [{name: set(fields) for name, fields in test_env.items()}]))
                    if statement.orelse:
                        branches.extend(self.block(statement.orelse, [{name: set(fields) for name, fields in test_env.items()}]))
                    else:
                        branches.append(test_env)
                current = self.merge(branches)
            elif isinstance(statement, ast.Try):
                branches: list[dict[str, set[str]]] = []
                for env in current:
                    branches.extend(self.block(statement.body + statement.orelse, [{name: set(fields) for name, fields in env.items()}]))
                    for handler in statement.handlers:
                        branches.extend(self.block(handler.body, [{name: set(fields) for name, fields in env.items()}]))
                current = self.merge(branches)
            else:
                for env in current:
                    for child in ast.iter_child_nodes(statement):
                        if isinstance(child, ast.expr):
                            self.expr(child, env)
        return current

    def run(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[dict[str, Any]]:
        self.block(node.body, [{}])
        for sink, fields in self.return_sinks.items():
            self.record(node, fields, "cross_path_return_fallback", sink)
        for sink, fields in self.output_sinks.items():
            self.record(node, fields, "cross_path_output_fallback", sink)
        return self.violations


def audit_source(text: str, source_name: str = "mutation") -> list[dict[str, Any]]:
    tree = ast.parse(text)
    violations: list[dict[str, Any]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            violations.extend(FunctionAudit(source_name, node.name).run(node))
    return violations


def audit_active_paths(scripts_dir: Path | None = None) -> list[dict[str, Any]]:
    root = scripts_dir or Path(__file__).resolve().parent
    violations: list[dict[str, Any]] = []
    for name in ACTIVE_FILES:
        path = root / name
        violations.extend(audit_source(path.read_text(encoding="utf-8"), name))
    return violations


def main() -> int:
    violations = audit_active_paths()
    print(json.dumps({"ok": not violations, "violation_count": len(violations), "violations": violations}, ensure_ascii=False, indent=2))
    return 0 if not violations else 1


if __name__ == "__main__":
    raise SystemExit(main())
