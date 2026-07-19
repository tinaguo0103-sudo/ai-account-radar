#!/usr/bin/env node
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";

import {
  buildCoverage,
  buildHomepageCardItems,
  limitedPlanRejection,
  selectIncrementalWorks,
  selectedSources,
  validateFullAccountLimitArgs,
  validateContentItemLineage,
  validateSourcePlan,
} from "./douyin_cdp_source_watch_probe.mjs";

function source(name) {
  return {
    account_name: name,
    platform: "抖音",
    source_role: "current_aux_competitor",
    url: `https://www.douyin.com/user/${name}`,
  };
}

const sources = Array.from({ length: 33 }, (_, index) => source(`account-${index + 1}`));
assert.equal(selectedSources(sources).length, 33);
assert.equal(validateFullAccountLimitArgs([]).ok, true);
assert.equal(validateFullAccountLimitArgs(["--account-limit", "0"]).ok, true);
assert.equal(limitedPlanRejection(validateFullAccountLimitArgs(["--account-limit", "12"])).status, "limited_plan_rejected");
assert.equal(validateSourcePlan([...sources, source("account-1")]).ok, false);
assert.equal(validateSourcePlan([{ ...source(""), account_name: "" }]).ok, false);

const complete = buildCoverage(sources.slice(0, 2), [
  { account_name: "account-1", status: "success", video_links: ["one"] },
  { account_name: "account-2", status: "success", video_links: ["two"] },
]);
assert.equal(complete.ok, true);
assert.equal(complete.invariants.attempted_equals_planned, true);

const partial = buildCoverage(sources.slice(0, 3), [
  { account_name: "account-1", status: "success", video_links: ["one"] },
  { account_name: "account-2", status: "success", video_links: [] },
  { account_name: "account-3", status: "timeout", failure_reason: "timeout", video_links: [] },
]);
assert.equal(partial.ok, false);
assert.equal(partial.failed_account_count, 2);
assert.equal(partial.failed_accounts[0].status, "zero_artifact");
assert.equal(partial.invariants.success_plus_failed_equals_attempted, true);

const missing = buildCoverage(sources.slice(0, 2), [
  { account_name: "account-1", status: "success", video_links: ["one"] },
]);
assert.equal(missing.ok, false);
assert.deepEqual(missing.missing_account_rows, ["account-2"]);

const duplicate = buildCoverage(sources.slice(0, 2), [
  { account_name: "account-1", status: "success", video_links: ["one"] },
  { account_name: "account-1", status: "success", video_links: ["two"] },
]);
assert.equal(duplicate.ok, false);
assert.deepEqual(duplicate.duplicate_account_rows, ["account-1"]);
assert.deepEqual(duplicate.missing_account_rows, ["account-2"]);

const rowsWithFailedLink = [
  { account_name: "account-1", status: "success", video_links: ["https://video/1"], video_cards: [] },
  { account_name: "account-2", status: "timeout", video_links: ["https://video/2"], video_cards: [] },
];
const homepageItems = buildHomepageCardItems(rowsWithFailedLink);
assert.equal(homepageItems.length, 1);
assert.equal(homepageItems[0]["账号名/公众号名"], "account-1");
assert.equal(validateContentItemLineage(rowsWithFailedLink, homepageItems).ok, true);
assert.equal(validateContentItemLineage(rowsWithFailedLink, [{
  ...homepageItems[0],
  "账号名/公众号名": "account-2",
}]).ok, false);

const workCard = (id, extra = {}) => ({
  href: `https://www.douyin.com/video/${id}`,
  id,
  text: `account work ${id}`,
  in_works_grid: true,
  account_identity_match: true,
  pinned: false,
  ...extra,
});
const incremental = selectIncrementalWorks([
  workCard("10000000001", { pinned: true }),
  workCard("10000000002"),
  workCard("10000000003"),
  workCard("10000000004"),
  workCard("10000000005"),
  workCard("10000000006"),
  workCard("10000000007"),
  workCard("10000000008"),
  workCard("10000000009"),
  workCard("10000000010"),
], new Set(["10000000002", "10000000003"]), { scanLimit: 10, videoLimit: 2 });
assert.deepEqual(incremental.selected.map((item) => item.id), ["10000000004", "10000000005"]);
assert.equal(incremental.counters.pinned, 1);
assert.equal(incremental.counters.seen, 2);

const noNew = selectIncrementalWorks([
  workCard("20000000001"), workCard("20000000002"), workCard("20000000003"),
], new Set(["20000000001", "20000000002", "20000000003"]));
assert.equal(noNew.status, "updated_no_new_items");
assert.equal(noNew.selected.length, 0);

const contaminated = selectIncrementalWorks([
  workCard("30000000001", { href: "https://www.douyin.com/search?modal_id=30000000001", text: "教材热搜聚合", in_works_grid: false }),
  workCard("30000000002", { href: "https://www.baidu.com/baiduspider/video/30000000002", text: "食品商品", in_works_grid: false }),
  workCard("30000000003", { text: "广告商品", account_identity_match: false }),
]);
assert.equal(contaminated.selected.length, 0);
assert.equal(contaminated.counters.contaminated, 3);

const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "ar026-check-only-"));
const config = path.join(tmp, "sources.json");
const out = path.join(tmp, "out");
fs.writeFileSync(config, JSON.stringify({ sources }), "utf8");
const proc = spawnSync(process.execPath, [
  path.resolve("scripts/douyin_cdp_source_watch_probe.mjs"),
  "--config", config,
  "--out-dir", out,
  "--check-only",
], { cwd: path.resolve("."), encoding: "utf8" });
assert.equal(proc.status, 0, proc.stderr);
const preview = JSON.parse(proc.stdout);
assert.equal(preview.coverage.planned_accounts, 33);
assert.equal(preview.collection_started, false);
assert.equal(preview.cdp_contacted, false);
assert.equal(preview.writes_feishu, false);

const rejectedLimits = [
  ["--account-limit", "1"],
  ["--account-limit", "3"],
  ["--account-limit", "12"],
  ["--account-limit", "31"],
  ["--account-limit", "-1"],
  ["--account-limit", "invalid"],
  ["--account-limit", ""],
  ["--account-limit"],
  ["--account-limit=12"],
  ["--account-limit", "0", "--account-limit", "0"],
];
for (const [index, mutation] of rejectedLimits.entries()) {
  const rejectedOut = path.join(tmp, `rejected-${index}`);
  const rejected = spawnSync(process.execPath, [
    path.resolve("scripts/douyin_cdp_source_watch_probe.mjs"),
    "--config", path.join(tmp, "must-not-be-read.json"),
    "--out-dir", rejectedOut,
    "--cdp", "http://127.0.0.1:1",
    ...mutation,
  ], { cwd: path.resolve("."), encoding: "utf8" });
  assert.equal(rejected.status, 2, `${mutation.join(" ")}\n${rejected.stderr}`);
  const payload = JSON.parse(rejected.stdout);
  assert.equal(payload.status, "limited_plan_rejected");
  assert.equal(payload.side_effects_started, false);
  assert.equal(payload.cache_accessed, false);
  assert.equal(payload.chrome_contacted, false);
  assert.equal(payload.collection_started, false);
  assert.equal(fs.existsSync(rejectedOut), false, `output was created for ${mutation.join(" ")}`);
}

const sourceText = fs.readFileSync(path.resolve("scripts/douyin_cdp_source_watch_probe.mjs"), "utf8");
assert.equal(sourceText.includes("fallbackItems"), false);
assert.equal(sourceText.includes("buildFallbackContentItem"), false);
assert.equal(sourceText.includes("127.0.0.1:9222"), false);
assert.equal(sourceText.includes("--only-account-names"), false);
assert.equal(sourceText.includes("onlyAccountNames"), false);
assert.equal(sourceText.includes("rows.slice(0"), false);
assert.equal(sourceText.includes("payload.anchors"), false);
assert.equal(sourceText.includes("payload.htmlSnippet"), false);
assert.equal(sourceText.includes("scanLimit: 10"), true);

const outerSource = fs.readFileSync(path.resolve("scripts/run_daily_collection_job.py"), "utf8");
assert.equal(outerSource.includes('"--force-fetch-douyin"'), true);

console.log(JSON.stringify({ ok: true, tests: 39, planned_accounts: 33, rejected_limit_mutations: rejectedLimits.length }));
