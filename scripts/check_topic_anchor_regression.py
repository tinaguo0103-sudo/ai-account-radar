#!/usr/bin/env python3
"""Local regression checks for 今日Top10 event anchor extraction."""
from __future__ import annotations

from content_sampler import ContentItem, extract_event_anchor


def item(title: str, body: str = "") -> ContentItem:
    return ContentItem(
        source_type="AIHOT热点",
        platform="AIHOT",
        account_name="regression",
        title=title,
        url="",
        content_shape="test",
        cover_text="",
        body_snippet=body,
        published_at="",
        comment_questions="",
        ocr_text="",
        fetch_method="regression",
        fetch_status="ok",
        failure_reason="",
        fingerprint=title,
    )


def main() -> int:
    cases = [
        (
            item("Sensor Tower：OpenAI 旗下 ChatGPT 月活已破 10 亿", "Claude 数据分析不应污染本条锚点"),
            ["Sensor Tower", "ChatGPT"],
            ["Claude 自助数据分析"],
        ),
        (
            item("Anthropic 分析 832 个 AI 恶意账户", "Claude 也出现在公司产品线里，但本条是恶意账户分析"),
            ["Anthropic", "恶意账户"],
            ["Claude 自助数据分析"],
        ),
        (
            item("OpenClaw 2026.6.1发布", "正文可能提到 MiniMax M3，但标题锚点应该保留 OpenClaw"),
            ["OpenClaw"],
            ["MiniMax M3"],
        ),
    ]
    failures: list[str] = []
    for sample, must_contain, must_not_contain in cases:
        anchor = extract_event_anchor(sample)
        if not any(token in anchor for token in must_contain):
            failures.append(f"{sample.title}: anchor={anchor!r} missing one of {must_contain}")
        if any(token in anchor for token in must_not_contain):
            failures.append(f"{sample.title}: anchor={anchor!r} contains forbidden {must_not_contain}")
    if failures:
        print("\n".join(failures))
        return 1
    print("topic anchor regression checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
