#!/usr/bin/env python3
"""Build an isolated private persona bundle from the authoritative Word file."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any
from xml.etree import ElementTree


VERSION = "persona_reference_v3"
WORD_NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def extract_docx_lines(path: Path) -> list[str]:
    if not path.exists():
        raise RuntimeError(f"Authoritative persona Word file not found: {path}")
    with zipfile.ZipFile(path) as archive:
        root = ElementTree.fromstring(archive.read("word/document.xml"))
    lines: list[str] = []
    for paragraph in root.findall(".//w:p", WORD_NS):
        text = "".join(node.text or "" for node in paragraph.findall(".//w:t", WORD_NS)).strip()
        if text:
            lines.append(text)
    if not lines:
        raise RuntimeError("Authoritative persona Word file contains no readable paragraphs")
    return lines


def slice_between(lines: list[str], start_pattern: str, end_pattern: str | None) -> list[str]:
    start = next((index for index, line in enumerate(lines) if re.search(start_pattern, line)), -1)
    if start < 0:
        raise RuntimeError(f"Missing persona section: {start_pattern}")
    end = len(lines)
    if end_pattern:
        end = next((index for index in range(start + 1, len(lines)) if re.search(end_pattern, lines[index])), len(lines))
    return lines[start:end]


OPERATION_PATTERNS = {
    "public_contradiction": ("但", "反而", "不是", "矛盾", "问题"),
    "shallow_take_rejection": ("不是", "不要", "不能只", "不等于", "别"),
    "evidence_skepticism": ("证据", "判断", "是否", "结果", "真实"),
    "story_or_social_proof": ("故事", "经历", "客户", "团队", "项目"),
    "result_promise": ("结果", "做到", "解决", "留下", "提升"),
    "decision_tradeoff": ("选择", "取舍", "优先", "更重要", "宁可"),
    "natural_voice": ("我", "其实", "所以", "后来", "现在"),
}
EXCLUDED_PREFIXES = ("字段", "飞书", "推荐动作", "今日建议级别", "关联母场景", "可调用案例")


def judgment_examples(lines: list[str]) -> list[dict[str, Any]]:
    examples: list[dict[str, Any]] = []
    for paragraph_index, raw in enumerate(lines):
        text = raw.strip()
        if len(text) < 28 or len(text) > 360 or text.startswith(EXCLUDED_PREFIXES):
            continue
        operations = [name for name, markers in OPERATION_PATTERNS.items() if any(marker in text for marker in markers)]
        if not operations:
            continue
        role = "judgment_and_style_reference_only"
        examples.append({
            "example_id": f"style_p{paragraph_index:04d}",
            "paragraph_index": paragraph_index,
            "source_hash": sha256_bytes(text.encode("utf-8")),
            "verbatim_text": text,
            "role": role,
            "judgment_operations": operations,
            "anti_copy": "Use only voice and judgment habits. Never reuse case facts, names, claims, or sentence shells as candidate evidence.",
        })
    if not examples:
        raise RuntimeError("No judgment/style examples could be derived from the authoritative Word file")
    return examples


def build_bundle(docx_path: Path, out_dir: Path) -> dict[str, Any]:
    lines = extract_docx_lines(docx_path)
    out_dir.mkdir(parents=True, exist_ok=True)
    facts_section = slice_between(lines, r"^1\.\s*\u4f60\u7684\u771f\u5b9e\u4e1a\u52a1\u73b0\u573a", r"^\u7b2c3\u9898")
    fact_prefixes = (
        "目的：", "我现在最头疼的事：", "我真实想解决：", "我这个账号不是",
        "我自己可以接住的经验：", "我对AI视频真实理解：", "怎么区分工具和教程号：", "我适合的点：",
    )
    facts_lines = [line for line in facts_section if line.startswith(fact_prefixes)]
    if len(facts_lines) < 4:
        raise RuntimeError("Authoritative Word did not yield enough compact persona facts")
    archive_lines = slice_between(lines, r"^\u7b2c3\u9898", r"^\u7b2c4\u9898")
    facts = {
        "contract": VERSION,
        "authority": str(docx_path),
        "account_directions": ["AI\u4e1a\u52a1\u5b9a\u8c03", "\u771f\u5b9e\u5de5\u4f5c\u6d41\u6539\u9020", "\u6c7d\u8f66\u4e0e\u5185\u5bb9\u8425\u9500", "AI\u5bfc\u6f14\u5de5\u4f5c\u6d41"],
        "source_lines": facts_lines,
        "rules": {
            "persona_is_not_candidate_evidence": True,
            "no_mother_scene_routing": True,
            "no_case_anchor": True,
            "no_fixed_vocabulary": True,
            "source_facts_and_research_control_eligibility": True,
        },
    }
    examples = judgment_examples(lines)
    archive = {
        "contract": VERSION,
        "runtime_access": "excluded",
        "source_lines": archive_lines,
        "warning": "Private experience archive. Never load into candidate runtime or evidence.",
    }
    outputs = {
        "persona_facts.private.json": facts,
        "judgment_and_style_examples.private.json": examples,
        "experience_archive.private.json": archive,
    }
    hashes: dict[str, str] = {}
    for name, value in outputs.items():
        path = out_dir / name
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        hashes[name] = sha256_file(path)
    manifest = {
        "contract": VERSION,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "authority_path": str(docx_path),
        "authority_sha256": sha256_file(docx_path),
        "outputs": hashes,
        "persona_facts_embedded": True,
        "judgment_examples_retrieved": True,
        "experience_archive_runtime": "excluded",
        "private_material_git_safe": False,
    }
    manifest["manifest_hash"] = sha256_bytes(canonical_json(manifest).encode("utf-8"))
    (out_dir / "persona_provenance_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return manifest


def retrieve_style_examples(
    examples: list[dict[str, Any]], operations: list[str], *, candidate_id: str, limit: int = 6
) -> list[dict[str, Any]]:
    if not 3 <= limit <= 6:
        raise ValueError("Style retrieval limit must be between 3 and 6")
    terms = {term for term in operations if term in OPERATION_PATTERNS}
    if not terms:
        terms = {"natural_voice", "decision_tradeoff"}
    scored = []
    for item in examples:
        tags = set(item.get("judgment_operations") or [])
        score = len(terms & tags)
        tie = sha256_bytes(f"{candidate_id}:{item['example_id']}".encode("utf-8"))
        scored.append((score, tie, item))
    selected = [item for _score, _tie, item in sorted(scored, key=lambda value: (-value[0], value[1]))[:limit]]
    if len(selected) < 3:
        raise RuntimeError("Persona style retrieval returned fewer than 3 examples")
    return selected


def main() -> int:
    parser = argparse.ArgumentParser(description="Build private AR-020D persona references from the authoritative Word file.")
    parser.add_argument("--docx", required=True)
    parser.add_argument("--out-dir", required=True)
    args = parser.parse_args()
    print(json.dumps(build_bundle(Path(args.docx), Path(args.out_dir)), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
