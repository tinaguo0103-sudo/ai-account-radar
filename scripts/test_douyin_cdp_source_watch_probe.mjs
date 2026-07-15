#!/usr/bin/env node
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";

import {
  buildCoverage,
  buildHomepageCardItems,
  selectedSources,
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
assert.equal(selectedSources(sources, { accountLimit: 0, onlyAccountNames: "" }).length, 33);
assert.equal(selectedSources(sources, { accountLimit: 12, onlyAccountNames: "" }).length, 12);
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

const sourceText = fs.readFileSync(path.resolve("scripts/douyin_cdp_source_watch_probe.mjs"), "utf8");
assert.equal(sourceText.includes("fallbackItems"), false);
assert.equal(sourceText.includes("buildFallbackContentItem"), false);
assert.equal(sourceText.includes("127.0.0.1:9222"), false);

const outerSource = fs.readFileSync(path.resolve("scripts/run_daily_collection_job.py"), "utf8");
assert.equal(outerSource.includes('"--force-fetch-douyin"'), true);

console.log(JSON.stringify({ ok: true, tests: 25, planned_accounts: 33 }));
