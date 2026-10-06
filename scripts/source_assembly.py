"""Validate and import an exact-run, source-only package before manual-row merging."""
from __future__ import annotations

import hashlib
import json
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


class SourcePackageError(ValueError):
    pass


SOURCE_REGISTRY: dict[str, dict[str, Any]] = {
    "feishu_recvl4uBak96UM": {
        "source": "openai_news", "source_type": "公开网页",
        "platform": "OpenAI", "account": "OpenAI官网动态", "host": "openai.com", "max_items": 5,
    },
    "feishu_recvl4uBakM7LI": {
        "source": "anthropic_newsroom", "source_type": "公开网页",
        "platform": "Anthropic", "account": "Anthropic Newsroom", "host": "anthropic.com", "max_items": 5,
    },
    "feishu_recvl4uBakDz73": {
        "source": "product_hunt_ai", "source_type": "公开网页",
        "platform": "Product Hunt", "account": "Product Hunt AI工具", "host": "producthunt.com", "max_items": 5,
    },
    "feishu_recvl4uBakYAzP": {
        "source": "hn_ai_discussion", "source_type": "公开网页",
        "platform": "Hacker News", "account": "HN AI相关讨论", "host": "news.ycombinator.com", "max_items": 5,
    },
    "feishu_recvl4uBakTUDT": {
        "source": "x", "source_type": "social_post",
        "platform": "X", "account": "数字生命卡兹克", "host": "x.com", "max_items": 3,
    },
}

SOURCE_IDS = frozenset(SOURCE_REGISTRY)
RUN_ID_RE = re.compile(r"^run_(\d{8})_\d{6}(?:_[A-Za-z0-9_-]+)?$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
RECEIPT_STATUSES = {"completed", "completed_empty", "partial", "failed", "blocked"}


def _fail(reason: str) -> None:
    raise SourcePackageError(reason)


def _read_json(path: Path, reason: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        _fail(reason)
    if not isinstance(value, dict):
        _fail(reason)
    return value


def _read_jsonl(path: Path, reason: str) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        _fail(reason)
    values: list[dict[str, Any]] = []
    for line in lines:
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            _fail(reason)
        if not isinstance(value, dict):
            _fail(reason)
        values.append(value)
    return values


def _int(value: Any, reason: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        _fail(reason)
    return value


def _source_plan(runtime_config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    sources = runtime_config.get("sources")
    if not isinstance(sources, list):
        _fail("source_plan_invalid")
    by_id = {
        str(row.get("id") or row.get("source_id") or ""): row
        for row in sources if isinstance(row, dict)
    }
    if any(source_id not in by_id for source_id in SOURCE_IDS):
        _fail("source_plan_missing_allowed_identity")
    return by_id


def _canonical_source_url(value: Any, source_id: str) -> str:
    raw = str(value or "").strip()
    parsed = urlsplit(raw)
    host = (parsed.hostname or "").lower()
    if (
        parsed.scheme not in {"http", "https"}
        or not host
        or parsed.username
        or parsed.password
    ):
        _fail("source_item_url_identity_mismatch")
    query = urlencode(sorted(parse_qsl(parsed.query, keep_blank_values=True)))
    return urlunsplit((parsed.scheme.lower(), host, parsed.path or "/", query, ""))


def _safe_package_file(root: Path, relative: Any, *, require_raw: bool = False) -> Path:
    value = str(relative or "")
    path = Path(value)
    if not value or path.is_absolute() or ".." in path.parts or "\\" in value:
        _fail("source_package_path_invalid")
    if require_raw and (not path.parts or path.parts[0] != "raw"):
        _fail("source_package_raw_path_invalid")
    candidate = root / path
    try:
        current = root
        for part in path.parts:
            current = current / part
            if current.is_symlink():
                _fail("source_package_symlink_forbidden")
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root)
    except SourcePackageError:
        raise
    except (OSError, RuntimeError, ValueError):
        _fail("source_package_path_escape")
    if not resolved.is_file():
        _fail("source_package_file_missing")
    return resolved


def _timestamp(value: Any, reason: str) -> str:
    raw = str(value or "").strip()
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        _fail(reason)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        _fail(reason)
    return raw


def _parse_receipts(
    rows: list[dict[str, Any]],
    *, run_id: str,
    source_plan: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    receipts: dict[str, dict[str, Any]] = {}
    for raw in rows:
        source_id = str(raw.get("source_id") or "")
        if source_id not in SOURCE_IDS:
            _fail("source_package_unknown_source")
        if source_id in receipts:
            _fail("source_package_duplicate_receipt")
        if raw.get("run_id") != run_id:
            _fail("source_package_wrong_run")
        if not isinstance(raw.get("attempted"), bool):
            _fail("source_package_attempt_identity_missing")
        attempted = bool(raw["attempted"])
        status = str(raw.get("status") or "")
        if status not in RECEIPT_STATUSES:
            _fail("source_package_terminal_status_invalid")
        attempt_count = _int(raw.get("attempt_count"), "source_package_attempt_count_invalid")
        item_count = _int(raw.get("item_count"), "source_package_item_count_invalid")
        failed_attempt_count = _int(
            raw.get("failed_attempt_count", 0), "source_package_failed_attempt_count_invalid",
        )
        if attempted:
            if attempt_count < 1:
                _fail("source_package_attempt_timestamp_missing")
            _timestamp(raw.get("attempted_at"), "source_package_attempt_timestamp_missing")
            if status in {"blocked"}:
                _fail("source_package_attempt_status_conflict")
        elif (
            attempt_count != 0 or item_count != 0 or failed_attempt_count != 0
            or str(raw.get("attempted_at") or "")
            or status != "blocked"
        ):
            _fail("source_package_unattempted_outcome_invalid")
        if status == "completed_empty" and (
            not attempted or item_count != 0 or raw.get("visible_list_complete") is not True
        ):
            _fail("source_package_empty_not_observed")
        if status == "completed" and (not attempted or item_count == 0):
            _fail("source_package_empty_not_observed")
        if status in {"partial", "failed"} and (
            not attempted or not str(raw.get("error_summary") or raw.get("reason") or "").strip()
        ):
            _fail("source_package_failure_reason_missing")
        if status == "blocked" and not str(raw.get("error_summary") or raw.get("reason") or "").strip():
            _fail("source_package_failure_reason_missing")
        error_summary = str(raw.get("error_summary") or raw.get("reason") or "")
        if error_summary and (
            len(error_summary) > 160
            or not re.fullmatch(r"[A-Za-z0-9_.:-]+", error_summary)
        ):
            _fail("source_package_error_summary_invalid")
        failure_class = str(raw.get("failure_class") or "")
        if failure_class and not re.fullmatch(r"[a-z0-9_]{1,80}", failure_class):
            _fail("source_package_failure_class_invalid")
        if status == "failed" and item_count != 0:
            _fail("source_package_failed_items_present")
        if status == "partial" and item_count == 0 and failed_attempt_count == 0:
            _fail("source_package_partial_without_evidence")
        if failed_attempt_count > attempt_count:
            _fail("source_package_attempt_count_invalid")
        registry_identity = str(source_plan[source_id].get("verified_identity") or "")
        package_identity = raw.get("verified_identity")
        if package_identity is not None and str(package_identity) != registry_identity:
            _fail("source_package_verified_identity_mismatch")
        row = dict(raw)
        row["source_id"] = source_id
        row["attempt_count"] = attempt_count
        row["failed_attempt_count"] = failed_attempt_count
        row["item_count"] = item_count
        row["verified_identity"] = registry_identity
        receipts[source_id] = row
    if set(receipts) != SOURCE_IDS:
        _fail("source_package_receipt_set_incomplete")
    return receipts


def _source_runs(
    run_id: str,
    receipts: dict[str, dict[str, Any]],
    unique_item_counts: dict[str, int],
) -> list[dict[str, Any]]:
    output = []
    for source_id in sorted(SOURCE_IDS, key=lambda value: str(SOURCE_REGISTRY[value]["source"])):
        receipt = receipts[source_id]
        source = str(SOURCE_REGISTRY[source_id]["source"])
        status = str(receipt["status"])
        attempts = int(receipt["attempt_count"])
        failed = int(receipt["failed_attempt_count"])
        succeeded = max(0, attempts - failed)
        if status == "failed":
            failed = max(1, failed)
            succeeded = 0
        if status == "partial" and failed == 0:
            failed = 1
        output.append({
            "id": f"{run_id}:{source}", "run_id": run_id, "source": source,
            "status": status if receipt["attempted"] else "not_attempted",
            "planned_count": attempts, "succeeded_count": succeeded,
            "failed_count": failed, "item_count": int(unique_item_counts.get(source_id, 0)),
            "error_summary": str(receipt.get("error_summary") or receipt.get("reason") or ""),
            "completed_at": str(receipt.get("completed_at") or receipt.get("attempted_at") or ""),
            "source_id": source_id,
            "attempt_count": attempts,
            "raw_item_count": int(receipt["item_count"]),
            "attempted": bool(receipt["attempted"]),
            "attempted_at": str(receipt.get("attempted_at") or ""),
            "verified_identity": str(receipt.get("verified_identity") or ""),
            "outcome": {
                "completed": "success", "completed_empty": "updated_no_new_items",
                "partial": "partial", "failed": "failed",
            }.get(status, "not_attempted"),
            "failure_class": str(receipt.get("failure_class") or (
                "source_package_partial" if status == "partial"
                else "source_package_failed" if status == "failed" else ""
            )),
            "artifact_count": int(receipt["item_count"]),
            "substitute_count": 0,
        })
    return output


def _read_checkpoint(
    run_root: Path,
    *, run_id: str, business_date: str, config_revision: int,
) -> dict[str, Any]:
    checkpoint_path = run_root / "sources" / "source_package" / "import.json"
    manual_path = checkpoint_path.with_name("content_items_manual.jsonl")
    checkpoint = _read_json(checkpoint_path, "source_package_checkpoint_invalid")
    if (
        checkpoint.get("run_id") != run_id
        or checkpoint.get("business_date") != business_date
        or checkpoint.get("config_revision") != config_revision
    ):
        _fail("source_package_checkpoint_identity_mismatch")
    raw = manual_path.read_bytes() if manual_path.is_file() else b""
    if hashlib.sha256(raw).hexdigest() != checkpoint.get("manual_sha256"):
        _fail("source_package_checkpoint_hash_mismatch")
    items = checkpoint.get("items")
    if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
        _fail("source_package_checkpoint_invalid")
    for item in items:
        path = Path(str(item.get("raw_artifact_path") or ""))
        expected = str(item.get("raw_sha256") or "")
        if not path.is_file() or not SHA256_RE.fullmatch(expected):
            _fail("source_package_checkpoint_raw_missing")
        try:
            resolved = path.resolve(strict=True)
            resolved.relative_to(run_root.resolve())
            if path.is_symlink():
                _fail("source_package_checkpoint_raw_escape")
        except (OSError, RuntimeError, ValueError):
            _fail("source_package_checkpoint_raw_escape")
        if hashlib.sha256(resolved.read_bytes()).hexdigest() != expected:
            _fail("source_package_checkpoint_raw_hash_mismatch")
    return {
        "manual_path": manual_path,
        "source_runs": checkpoint.get("source_runs", []),
        "items": checkpoint.get("items", []),
        "raw_item_count": int(checkpoint.get("raw_item_count") or 0),
        "deduplicated_item_count": int(checkpoint.get("deduplicated_item_count") or 0),
        "duplicate_url_count": int(checkpoint.get("duplicate_url_count") or 0),
        "input_sha256": str(checkpoint.get("input_sha256") or ""),
        "reused": True,
    }


def import_source_package(
    package_root: str | Path | None,
    *,
    run_id: str,
    business_date: str,
    run_root: str | Path,
    runtime_config: dict[str, Any],
) -> dict[str, Any] | None:
    run_match = RUN_ID_RE.fullmatch(run_id)
    if not run_match or business_date != f"{run_match.group(1)[:4]}-{run_match.group(1)[4:6]}-{run_match.group(1)[6:8]}":
        _fail("source_package_run_date_invalid")
    revision = runtime_config.get("config_revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
        _fail("source_plan_revision_missing")
    run_path = Path(run_root).resolve()
    checkpoint_path = run_path / "sources" / "source_package" / "import.json"
    if package_root is None or str(package_root).strip() == "":
        if checkpoint_path.exists():
            return _read_checkpoint(
                run_path, run_id=run_id, business_date=business_date, config_revision=revision,
            )
        return None

    try:
        root = Path(package_root).resolve(strict=True)
    except (OSError, RuntimeError):
        _fail("source_package_root_missing")
    if not root.is_dir():
        _fail("source_package_root_missing")
    source_plan = _source_plan(runtime_config)
    manifest_path = _safe_package_file(root, "manifest.json")
    receipts_path = _safe_package_file(root, "receipts.jsonl")
    items_path = _safe_package_file(root, "items.jsonl")
    manifest = _read_json(manifest_path, "source_package_manifest_invalid")
    if manifest.get("schema_version") != 1:
        _fail("source_package_schema_unsupported")
    if manifest.get("run_id") != run_id:
        _fail("source_package_wrong_run")
    if manifest.get("business_date") != business_date:
        _fail("source_package_wrong_business_date")
    if manifest.get("config_revision") != revision:
        _fail("source_package_revision_mismatch")
    manifest_source_ids = manifest.get("source_ids")
    if (
        not isinstance(manifest_source_ids, list)
        or any(not isinstance(value, str) for value in manifest_source_ids)
        or set(manifest_source_ids) != SOURCE_IDS
        or len(manifest_source_ids) != len(SOURCE_IDS)
    ):
        _fail("source_package_source_registry_mismatch")

    receipts = _parse_receipts(
        _read_jsonl(receipts_path, "source_package_receipts_invalid"),
        run_id=run_id, source_plan=source_plan,
    )
    raw_items = _read_jsonl(items_path, "source_package_items_invalid")
    item_counts = {source_id: 0 for source_id in SOURCE_IDS}
    unique_urls: dict[str, set[str]] = {source_id: set() for source_id in SOURCE_IDS}
    normalized: list[dict[str, Any]] = []
    input_hash = hashlib.sha256()
    for file_path in (manifest_path, receipts_path, items_path):
        input_hash.update(file_path.name.encode("utf-8") + b"\0" + file_path.read_bytes())
    copied_sources: list[tuple[Path, Path, str]] = []
    for raw in sorted(raw_items, key=lambda row: (str(row.get("source_id") or ""), str(row.get("url") or ""), str(row.get("raw_path") or ""))):
        source_id = str(raw.get("source_id") or "")
        receipt = receipts.get(source_id)
        if source_id not in SOURCE_IDS or receipt is None:
            _fail("source_package_unknown_source")
        if raw.get("run_id") != run_id:
            _fail("source_package_item_wrong_run")
        if not receipt["attempted"] or receipt["status"] not in {"completed", "partial"}:
            _fail("source_package_item_without_successful_attempt")
        registry = SOURCE_REGISTRY[source_id]
        if item_counts[source_id] >= int(registry["max_items"]):
            _fail("source_package_source_budget_exceeded")
        source_url = _canonical_source_url(raw.get("url"), source_id)
        relative = raw.get("raw_path")
        raw_file = _safe_package_file(root, relative, require_raw=True)
        try:
            raw_bytes = raw_file.read_bytes()
            raw_text = raw_bytes.decode("utf-8")
        except (OSError, UnicodeDecodeError):
            _fail("source_package_raw_text_invalid")
        expected_hash = str(raw.get("raw_sha256") or "")
        digest = hashlib.sha256(raw_bytes).hexdigest()
        if not SHA256_RE.fullmatch(expected_hash) or expected_hash != digest:
            _fail("source_package_raw_hash_mismatch")
        if not raw_text.strip():
            _fail("source_package_raw_text_empty")
        title = str(raw.get("title") or "").strip()
        if not title:
            _fail("source_package_item_title_missing")
        item_id = str(raw.get("item_id") or "").strip()
        if not item_id:
            _fail("source_package_item_identity_missing")
        author = str(raw.get("author") or registry["account"])
        published_at = str(raw.get("published_at") or "")
        imported_relative = Path("sources") / "source_package" / "raw" / f"{digest}.txt"
        imported_raw = run_path / imported_relative
        copied_sources.append((raw_file, imported_raw, digest))
        provenance = {
            "source_id": source_id, "source": str(registry["source"]),
            "source_type": str(registry["source_type"]), "platform": str(registry["platform"]),
            "account": author, "title": title, "url": source_url,
            "published_at": published_at, "raw_sha256": digest,
            "package_path": str(relative), "raw_artifact_path": str(imported_raw),
            "item_id": item_id,
        }
        normalized.append({
            "来源类型": str(registry["source_type"]), "平台": str(registry["platform"]),
            "账号名/公众号名": author, "内容标题": title, "内容链接": source_url,
            "正文/字幕/简介片段": raw_text, "发布时间": published_at,
            "抓取方式": "source_package_raw", "抓取状态": "success", "失败原因": "",
            "内容指纹": f"url:{source_url}", "运行批次": run_id,
            "原始payload路径": str(imported_raw), "source_id": source_id,
            "source_key": str(registry["source"]), "source_provenance": [provenance],
            "raw_artifact_path": str(imported_raw), "raw_sha256": digest,
            "item_id": f"url:{source_url}",
        })
        item_counts[source_id] += 1
        unique_urls[source_id].add(source_url)
        input_hash.update(str(relative).encode("utf-8") + b"\0" + raw_bytes)

    for source_id, receipt in receipts.items():
        if int(receipt["item_count"]) != item_counts[source_id]:
            _fail("source_package_receipt_item_count_mismatch")

    by_url: dict[str, dict[str, Any]] = {}
    for row in normalized:
        url = str(row["内容链接"])
        current = by_url.get(url)
        if current is None:
            by_url[url] = row
            continue
        provenance = {
            json.dumps(item, ensure_ascii=False, sort_keys=True): item
            for item in [*current.get("source_provenance", []), *row.get("source_provenance", [])]
        }
        current["source_provenance"] = [provenance[key] for key in sorted(provenance)]
    unique_rows = [by_url[url] for url in sorted(by_url)]
    unique_counts = {
        source_id: len(urls) for source_id, urls in unique_urls.items()
    }
    source_runs = _source_runs(
        run_id, receipts, {source_id: unique_counts[source_id] for source_id in SOURCE_IDS},
    )
    output_dir = run_path / "sources" / "source_package"
    manual_path = output_dir / "content_items_manual.jsonl"
    existing_checkpoint = _read_json(checkpoint_path, "source_package_checkpoint_invalid") if checkpoint_path.exists() else None
    digest = input_hash.hexdigest()
    if existing_checkpoint:
        if existing_checkpoint.get("input_sha256") != digest:
            _fail("source_package_same_run_conflict")
        return _read_checkpoint(
            run_path, run_id=run_id, business_date=business_date, config_revision=revision,
        )

    if manual_path.exists():
        _fail("source_package_checkpoint_conflict")

    for _source, destination, expected in copied_sources:
        if destination.exists() and hashlib.sha256(destination.read_bytes()).hexdigest() != expected:
            _fail("source_package_checkpoint_raw_conflict")
    for source, destination, _expected in copied_sources:
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            shutil.copyfile(source, destination)
    output_dir.mkdir(parents=True, exist_ok=True)
    manual_bytes = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in unique_rows).encode("utf-8")
    manual_path.write_bytes(manual_bytes)
    checkpoint = {
        "schema_version": 1, "run_id": run_id, "business_date": business_date,
        "config_revision": revision, "input_sha256": digest,
        "raw_item_count": len(raw_items), "deduplicated_item_count": len(unique_rows),
        "duplicate_url_count": len(raw_items) - len(unique_rows),
        "source_runs": source_runs,
        "items": [
            {"source_id": str(p["source_id"]), "url": str(p["url"]),
             "raw_artifact_path": str(row["raw_artifact_path"]), "raw_sha256": str(row["raw_sha256"])}
            for row in unique_rows for p in row.get("source_provenance", [])
        ],
        "manual_sha256": hashlib.sha256(manual_bytes).hexdigest(),
    }
    checkpoint_path.write_text(json.dumps(checkpoint, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "manual_path": manual_path, "source_runs": source_runs,
        "items": checkpoint["items"], "raw_item_count": len(raw_items),
        "deduplicated_item_count": len(unique_rows),
        "duplicate_url_count": len(raw_items) - len(unique_rows),
        "input_sha256": digest, "reused": False,
    }
