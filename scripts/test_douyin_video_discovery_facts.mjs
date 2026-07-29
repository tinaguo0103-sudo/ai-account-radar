#!/usr/bin/env node
import assert from "node:assert/strict";
import {
  buildSourceLedger,
  normalizePageOwnedCandidate,
  normalizeVisibleCard,
} from "./douyin_video_discovery.mjs";

const owned = normalizePageOwnedCandidate({
  aweme_id: "7660000000000000001",
  create_time: 1753766400,
  desc: "AI Agent workflow",
  author: { nickname: "Owner" },
  statistics: {
    digg_count: 0,
    comment_count: 0,
    collect_count: 0,
    share_count: 0,
  },
  video: { duration: 31000, play_addr: { url_list: ["https://media.example/video"] } },
}, "recommendation", {
  endpoint: "https://www.douyin.com/aweme/v1/web/tab/feed/?device=ignored",
});
assert.equal(owned.published_at, new Date(1753766400 * 1000).toISOString());
assert.deepEqual(
  [owned.likes, owned.comments, owned.favorites, owned.shares],
  [0, 0, 0, 0],
);
assert.deepEqual(owned.fact_missing_reasons, {});
assert.equal(owned.fact_provenance.capture, "page_owned_response");

const visible = normalizeVisibleCard({
  href: "https://www.douyin.com/video/7660000000000000002",
  text: "00:31\n102\nAI demo\n@Owner\n1周前",
}, "dynamic_search");
assert.equal(visible.published_at, "");
assert.equal(visible.published_at_display, "1周前");
assert.equal(visible.likes, 102);
assert.equal(visible.comments, null);
assert.equal(visible.favorites, null);
assert.equal(visible.shares, null);
assert.equal(visible.fact_missing_reasons.comments, "field_not_visible");

const ledger = buildSourceLedger([owned, visible], "AI Agent", "2026-07-29T00:00:00Z");
assert.deepEqual(ledger.map((row) => row.source), [
  "configured_account", "recommendation", "dynamic_search",
]);
assert.equal(ledger[1].fact_complete_count, 1);
assert.equal(ledger[2].fact_incomplete_count, 1);
assert.equal(ledger[2].query, "AI Agent");

process.stdout.write("douyin video discovery facts: ok\n");
