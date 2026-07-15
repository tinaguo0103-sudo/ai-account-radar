#!/usr/bin/env python3
"""Build exact-page source-open evidence from the declared primary adapter."""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import trusted_exact_source_adapter as adapter


def build_output(candidate: dict[str, Any], capture: dict[str, Any]) -> dict[str, Any]:
    body = str(capture.get("visible_body") or "").strip()
    title = str(capture.get("exact_title") or "").strip()
    author = str(capture.get("author") or "").strip()
    if not title or not body or not author:
        raise adapter.AdapterContractError("Exact-page capture lacks visible title/body/author")
    primary = str(candidate.get("primary_adapter") or "")
    final_url = str(capture.get("final_url") or "")
    identity = dict(capture.get("page_identity") or adapter.expected_identity(final_url))
    is_post = identity.get("kind") == "x_status"
    dom_text_path = Path(str(capture.get("dom_text_path") or ""))
    if not dom_text_path.is_file():
        raise adapter.AdapterContractError("Exact-page capture lacks persisted raw DOM/body artifact")
    raw_text = dom_text_path.read_text(encoding="utf-8")
    if body not in raw_text and " ".join(body.split()) not in " ".join(raw_text.split()):
        raise adapter.AdapterContractError("Visible body is not present in raw DOM/body artifact")
    content_hash = hashlib.sha256(dom_text_path.read_bytes()).hexdigest()
    requested_screenshot = Path(str(capture.get("screenshot_path") or "")) if capture.get("screenshot_path") else None
    screenshot_path = str(requested_screenshot) if requested_screenshot and requested_screenshot.exists() else ""
    visual_status = "completed" if screenshot_path else "failed"
    visual_error = "" if screenshot_path else str(capture.get("visual_capture_error") or "Page.captureScreenshot timeout")
    output = {
        "protocol": "ar020d_exact_source_evidence_v1",
        "primary_adapter": primary,
        "attempted_adapters": [primary],
        "adapter_version": adapter.VERSION,
        "input_url": candidate["exact_url"],
        "exact_url": candidate["exact_url"],
        "final_url": final_url,
        "page_identity": identity,
        "identity_match": True,
        "open_status": "opened",
        "failure_reason": "",
        "page_state": capture.get("page_state", "exact_page"),
        "exact_title": "" if is_post else title,
        "independent_title_verified": not is_post,
        "source_summary": body[:4000],
        "caption_body": body if is_post else "",
        "author": author,
        "platform": capture.get("platform") or ("X" if identity.get("kind") == "x_status" else "Web"),
        "publish_metadata": str(capture.get("publish_metadata") or ""),
        "source_type": capture.get("source_type", "exact_web_page"),
        "opened_at": capture.get("opened_at") or datetime.now(timezone.utc).isoformat(),
        "captured_content_hash": content_hash,
        "page_content_hash": content_hash,
        "retrieval_surface": primary,
        "browser_surface": str(capture.get("browser_surface") or ""),
        "browser_session_boundary": str(capture.get("browser_session_boundary") or ""),
        "dom_text_path": str(dom_text_path),
        "screenshot_path": screenshot_path,
        "visual_capture_status": visual_status,
        "visual_capture_error": visual_error,
        "audit_warnings": [] if visual_status == "completed" else ["visual_capture_failed_dom_evidence_retained"],
        "content_evidence": [{
            "evidence_id": f"exact-page-{candidate['candidate_id']}",
            "evidence_type": "visible_exact_page",
            "text": body[:4000],
        }],
        "boundary": "one declared primary adapter; no failover or substituted content",
    }
    adapter.validate_primary_adapter(candidate, output)
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-input", required=True)
    parser.add_argument("--capture-json", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    candidate = json.loads(Path(args.candidate_input).read_text(encoding="utf-8"))["candidate"]
    capture = json.loads(Path(args.capture_json).read_text(encoding="utf-8"))
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(build_output(candidate, capture), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "output": str(output)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
