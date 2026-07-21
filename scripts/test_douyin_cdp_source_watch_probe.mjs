#!/usr/bin/env node
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";

import {
  accountWorksSnapshotExpression,
  buildCoverage,
  buildHomepageCardItems,
  buildSourceRuntimeCoverage,
  classifyWorksResponse,
  configuredAccountIdentity,
  FixedPageSession,
  decodeRuntimeEvaluation,
  fixedDouyinTarget,
  limitedPlanRejection,
  loadCandidateLifecycle,
  materializeHistoricalBacklog,
  mergeNewAndBacklog,
  persistCollectedCandidates,
  probeAccount,
  parseWorksResponseBody,
  waitForNavigationAndWorksGrid,
  selectIncrementalWorks,
  selectedSources,
  validateFullAccountLimitArgs,
  validateContentItemLineage,
  validateSourcePlan,
} from "./douyin_cdp_source_watch_probe.mjs";

function attachCapture(client, result) {
  client.beginWorksCapture = () => {};
  client.takeWorksCaptureResults = () => [result];
  return client;
}

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

const fixedTarget = await fixedDouyinTarget("http://127.0.0.1:9333", async () => [
  { type: "page", url: "about:blank", webSocketDebuggerUrl: "ws://blank" },
  { type: "page", url: "https://www.douyin.com/user/account-1", webSocketDebuggerUrl: "ws://fixed" },
]);
assert.equal(fixedTarget.webSocketDebuggerUrl, "ws://fixed");

let readinessPolls = 0;
const delayedClient = {
  async send(method) {
    if (method === "Page.getNavigationHistory") {
      const ready = readinessPolls >= 2;
      return { currentIndex: 0, entries: [{ id: 0, url: ready ? "https://www.douyin.com/user/account-1" : "about:blank" }] };
    }
    readinessPolls += 1;
    const ready = readinessPolls >= 3;
    return { result: { value: JSON.stringify({
      title: ready ? "account-1" : "",
      url: ready ? "https://www.douyin.com/user/account-1" : "about:blank",
      body_text_length: ready ? 400 : 0,
      works_ready: ready,
      works_root_count: ready ? 1 : 0,
      videoAnchors: [],
      text: ready ? "works" : "",
    }) } };
  },
};
const delayedReady = await waitForNavigationAndWorksGrid(
  delayedClient,
  "https://www.douyin.com/user/account-1",
  { timeoutMs: 5000, pollMs: 1, sleep: async () => {}, now: (() => { let value = 0; return () => value += 100; })() },
);
assert.equal(delayedReady.works_ready, true);
assert.equal(readinessPolls, 3);
assert.equal(accountWorksSnapshotExpression().includes("/(^|\n)"), false);
assert.doesNotThrow(() => new Function(`return ${accountWorksSnapshotExpression()};`));
assert.equal(accountWorksSnapshotExpression().includes('[data-e2e*="recommend"]'), true);
assert.equal(accountWorksSnapshotExpression().includes('[data-e2e*="hot"]'), true);
assert.throws(
  () => decodeRuntimeEvaluation({ exceptionDetails: { text: "SyntaxError" } }),
  /works_snapshot_javascript_exception/,
);
assert.throws(() => decodeRuntimeEvaluation({ result: {} }), /works_snapshot_value_missing/);
assert.throws(() => decodeRuntimeEvaluation({ result: { value: "{" } }), /works_snapshot_json_malformed/);
assert.equal(configuredAccountIdentity("https://www.douyin.com/user/account-1"), "account-1");
assert.equal(classifyWorksResponse(
  "https://www.douyin.com/aweme/v1/web/aweme/post/?sec_user_id=account-1&a_bogus=redacted",
  "XHR", "account-1", "GET",
).accepted, true);
assert.equal(classifyWorksResponse(
  "https://www.douyin.com/aweme/v1/web/aweme/post/?sec_user_id=other",
  "XHR", "account-1", "GET",
).accepted, false);
assert.equal(classifyWorksResponse(
  "https://www.douyin.com/aweme/v1/web/general/search/?sec_user_id=account-1",
  "XHR", "account-1", "GET",
).accepted, false);
const realShapeFixture = JSON.parse(fs.readFileSync(
  path.resolve("scripts/fixtures/ar040_page_owned_works_sanitized.json"), "utf8",
));
const parsedRealShape = parseWorksResponseBody(JSON.stringify(realShapeFixture.response_shape), realShapeFixture.synthetic_account_identity);
assert.equal(parsedRealShape.ok, true);
assert.equal(parsedRealShape.cards.length >= 10, true);
assert.equal(parsedRealShape.cards[0].pinned, true);
assert.equal(parseWorksResponseBody("", "account-1").failure_code, "douyin_works_response_body_missing");
assert.equal(parseWorksResponseBody("{", "account-1").failure_code, "douyin_works_response_json_malformed");
assert.equal(parseWorksResponseBody("{}", "account-1").failure_code, "douyin_works_response_schema_mismatch");
assert.equal(parseWorksResponseBody(JSON.stringify({ aweme_list: [{ aweme_id: "50000000001", author: { sec_uid: "other" } }] }), "account-1").failure_code, "douyin_works_response_account_or_item_mismatch");
const mixedItems = parseWorksResponseBody(JSON.stringify({ aweme_list: [
  { aweme_id: "50000000001", desc: "trusted", author: { sec_uid: "account-1" } },
  { aweme_id: "50000000002", desc: "foreign", author: { sec_uid: "other" } },
] }), "account-1");
assert.equal(mixedItems.ok, true);
assert.equal(mixedItems.cards.length, 1);
assert.equal(mixedItems.rejected_item_count, 1);

const blankClient = { async send(method) {
  if (method === "Page.getNavigationHistory") return { currentIndex: 0, entries: [{ id: 0, url: "about:blank" }] };
  return { result: { value: JSON.stringify({ title: "", url: "about:blank", text: "", works_ready: false }) } };
} };
await assert.rejects(
  waitForNavigationAndWorksGrid(blankClient, "https://www.douyin.com/user/account-1", {
    timeoutMs: 250, pollMs: 1, sleep: async () => {}, now: (() => { let value = 0; return () => value += 100; })(),
  }),
  /shared_fixed_target_blank/,
);
const sourceRuntimeFailure = buildSourceRuntimeCoverage(sources.slice(0, 3), [{
  account_name: "account-1", status: "source_runtime_failed", video_links: [],
}], "shared_fixed_target_blank");
assert.equal(sourceRuntimeFailure.source_runtime_failure_count, 1);
assert.equal(sourceRuntimeFailure.failed_account_count, 0);
assert.equal(sourceRuntimeFailure.artifact_count, 0);

const transitionClients = [
  {
    async open() {}, close() {},
    async send(method) {
      if (["Runtime.enable", "Page.enable", "Network.enable"].includes(method)) return {};
      if (method === "Page.navigate") throw new Error("Not attached to an active page");
      throw new Error(`unexpected first client command:${method}`);
    },
  },
  {
    evaluateCount: 0,
    async open() {}, close() {},
    async send(method) {
      if (["Runtime.enable", "Page.enable", "Network.enable"].includes(method)) return {};
      if (method === "Page.navigate") return { frameId: "frame-fixed" };
      if (method === "Page.getNavigationHistory") {
        return { currentIndex: 0, entries: [{ id: 9, url: "https://www.douyin.com/user/account-1" }] };
      }
      if (method === "Runtime.evaluate") {
        this.evaluateCount += 1;
        return { result: { value: JSON.stringify({
          title: "account-1", url: "https://www.douyin.com/user/account-1",
          works_ready: true, works_root_count: 1,
          videoAnchors: [{
            href: "https://www.douyin.com/video/50000000001", id: "50000000001", text: "account work",
            in_works_grid: true, account_identity_match: true, pinned: false,
          }], text: "account works ready", loginHint: false,
        }) } };
      }
      throw new Error(`unexpected second client command:${method}`);
    },
  },
];
let transitionClientIndex = 0;
const transitionSession = new FixedPageSession("http://127.0.0.1:9333", {
  id: "fixed-target-id", type: "page", url: "https://www.douyin.com/user/account-1", webSocketDebuggerUrl: "ws://first",
}, {
  clientFactory: () => transitionClients[transitionClientIndex++],
  listTargets: async () => [{
    id: "fixed-target-id", type: "page", url: "https://www.douyin.com/user/account-1", webSocketDebuggerUrl: "ws://second",
  }],
});
await transitionSession.open();
attachCapture(transitionSession, { ok: true, response_item_count: 1, cards: [{
  href: "https://www.douyin.com/video/50000000001", id: "50000000001", text: "account work",
  in_works_grid: true, account_identity_match: true, pinned: false,
}] });
const transitioned = await probeAccount(transitionSession, source("account-1"), {
  waitMs: 1000, scanLimit: 10, videoLimit: 3, seenVideoIds: new Set(),
});
assert.equal(transitioned.status, "success");
assert.deepEqual(transitioned.video_ids, ["50000000001"]);
assert.equal(transitionSession.reattachments, 1);
transitionSession.close();

let contextEvaluateCount = 0;
let contextRecoveries = 0;
const contextTransitionClient = attachCapture({
  async send(method) {
    if (method === "Page.navigate") return { frameId: "fixed-frame" };
    if (method === "Page.getNavigationHistory") {
      return { currentIndex: 0, entries: [{ id: 1, url: "https://www.douyin.com/user/account-1" }] };
    }
    if (method === "Runtime.evaluate") {
      contextEvaluateCount += 1;
      if (contextEvaluateCount === 1) {
        return { exceptionDetails: { text: "Execution context was destroyed." } };
      }
      return { result: { value: JSON.stringify({
        title: "account-1", url: "https://www.douyin.com/user/account-1",
        works_ready: true, works_root_count: 1, text: "works", loginHint: false,
        videoAnchors: [{
          href: "https://www.douyin.com/video/50000000002", id: "50000000002", text: "trusted work",
          in_works_grid: true, account_identity_match: true, pinned: false,
        }],
      }) } };
    }
    throw new Error(`unexpected context transition command:${method}`);
  },
  async recoverExecutionContext() { contextRecoveries += 1; },
}, { ok: true, response_item_count: 1, cards: [{
  href: "https://www.douyin.com/video/50000000002", id: "50000000002", text: "trusted work",
  in_works_grid: true, account_identity_match: true, pinned: false,
}] });
const contextRecovered = await probeAccount(contextTransitionClient, source("account-1"), {
  waitMs: 1000, scanLimit: 10, videoLimit: 3, seenVideoIds: new Set(),
});
assert.equal(contextRecovered.status, "success");
assert.deepEqual(contextRecovered.video_ids, ["50000000002"]);
assert.equal(contextRecoveries, 1);
assert.equal(contextRecovered.extraction_diagnostics.context_recoveries, 1);

const missingValueClient = attachCapture({
  async send(method) {
    if (method === "Page.navigate") return {};
    if (method === "Page.getNavigationHistory") {
      return { currentIndex: 0, entries: [{ id: 1, url: "https://www.douyin.com/user/account-1" }] };
    }
    if (method === "Runtime.evaluate") return { result: {} };
    throw new Error(`unexpected missing-value command:${method}`);
  },
}, { ok: true, cards: [] });
const missingValue = await probeAccount(missingValueClient, source("account-1"), {
  waitMs: 1000, scanLimit: 10, videoLimit: 3, seenVideoIds: new Set(),
});
assert.equal(missingValue.status, "failed");
assert.equal(missingValue.failure_reason, "works_snapshot_value_missing");
assert.equal(missingValue.shared_runtime_failure, true);
assert.deepEqual(missingValue.video_ids, []);

const malformedClient = attachCapture({
  async send(method) {
    if (method === "Page.navigate") return {};
    if (method === "Page.getNavigationHistory") {
      return { currentIndex: 0, entries: [{ id: 1, url: "https://www.douyin.com/user/account-1" }] };
    }
    if (method === "Runtime.evaluate") return { result: { value: "{" } };
    throw new Error(`unexpected malformed command:${method}`);
  },
}, { ok: true, cards: [] });
const malformed = await probeAccount(malformedClient, source("account-1"), {
  waitMs: 1000, scanLimit: 10, videoLimit: 3, seenVideoIds: new Set(),
});
assert.equal(malformed.status, "failed");
assert.equal(malformed.failure_reason, "works_snapshot_json_malformed");
assert.notEqual(malformed.status, "partial_untrusted");
assert.equal(malformed.shared_runtime_failure, true);

let exhaustedRecoveries = 0;
const exhaustedClient = attachCapture({
  async send(method) {
    if (method === "Page.navigate") return {};
    if (method === "Page.getNavigationHistory") {
      return { currentIndex: 0, entries: [{ id: 1, url: "https://www.douyin.com/user/account-1" }] };
    }
    if (method === "Runtime.evaluate") {
      return { exceptionDetails: { text: "Execution context was destroyed." } };
    }
    throw new Error(`unexpected exhausted command:${method}`);
  },
  async recoverExecutionContext() { exhaustedRecoveries += 1; },
}, { ok: true, cards: [] });
const exhausted = await probeAccount(exhaustedClient, source("account-1"), {
  waitMs: 1000, scanLimit: 10, videoLimit: 3, seenVideoIds: new Set(),
});
assert.equal(exhausted.status, "failed");
assert.equal(exhausted.shared_runtime_failure, true);
assert.equal(exhausted.failure_reason, "works_snapshot_execution_context_transition");
assert.equal(exhaustedRecoveries, 2);
assert.deepEqual(exhausted.video_ids, []);

const seenOnlyClient = attachCapture({
  async send(method) {
    if (method === "Page.navigate") return {};
    if (method === "Page.getNavigationHistory") {
      return { currentIndex: 0, entries: [{ id: 1, url: "https://www.douyin.com/user/account-1" }] };
    }
    if (method === "Runtime.evaluate") return { result: { value: JSON.stringify({
      title: "account-1", url: "https://www.douyin.com/user/account-1", works_ready: true,
      works_root_count: 1, text: "works", loginHint: false,
      videoAnchors: [{
        href: "https://www.douyin.com/video/50000000003", id: "50000000003", text: "seen work",
        in_works_grid: true, account_identity_match: true, pinned: false,
      }],
    }) } };
    throw new Error(`unexpected seen-only command:${method}`);
  },
}, { ok: true, response_item_count: 1, cards: [{
  href: "https://www.douyin.com/video/50000000003", id: "50000000003", text: "seen work",
  in_works_grid: true, account_identity_match: true, pinned: false,
}] });
const seenOnly = await probeAccount(seenOnlyClient, source("account-1"), {
  waitMs: 1000, scanLimit: 10, videoLimit: 3, seenVideoIds: new Set(["50000000003"]),
});
assert.equal(seenOnly.status, "updated_no_new_items");
assert.deepEqual(seenOnly.video_ids, []);

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

const lifecycleDir = fs.mkdtempSync(path.join(os.tmpdir(), "ar038-lifecycle-"));
const lifecyclePath = path.join(lifecycleDir, "lifecycle.json");
const collected = buildHomepageCardItems([{
  account_name: "account-1", status: "success",
  video_links: ["https://www.douyin.com/video/40000000001"],
  video_cards: [{ video_id: "40000000001", href: "https://www.douyin.com/video/40000000001", text: "new workflow" }],
}]);
const lifecycle = loadCandidateLifecycle(lifecyclePath);
persistCollectedCandidates(lifecyclePath, lifecycle, collected, "run_20260719_080000");
const runB = materializeHistoricalBacklog(loadCandidateLifecycle(lifecyclePath));
assert.equal(runB.items.length, 1);
assert.equal(runB.items[0]["候选时态"], "historical_unreviewed");
assert.equal(runB.items[0]["是否今日新增"], "否");
assert.equal(runB.items[0]["首次发现日期"], "2026-07-19");
assert.equal(validateContentItemLineage([{
  account_name: "account-1", status: "updated_no_new_items", video_links: [runB.items[0]["内容链接"]],
}], runB.items).ok, true);
const orderedPool = mergeNewAndBacklog([{ ...collected[0], "内容指纹": "today-new" }], runB.items, "run_20260720_080000");
assert.deepEqual(orderedPool.map((item) => item["候选时态"]), ["today_new", "historical_unreviewed"]);
const reviewedLifecycle = loadCandidateLifecycle(lifecyclePath);
reviewedLifecycle.items[collected[0]["内容指纹"]].state = "reviewed";
fs.writeFileSync(lifecyclePath, JSON.stringify(reviewedLifecycle), "utf8");
assert.equal(materializeHistoricalBacklog(loadCandidateLifecycle(lifecyclePath)).items.length, 0);
reviewedLifecycle.items[collected[0]["内容指纹"]].state = "collected_unreviewed";
fs.writeFileSync(lifecyclePath, JSON.stringify(reviewedLifecycle), "utf8");
fs.writeFileSync(loadCandidateLifecycle(lifecyclePath).items[collected[0]["内容指纹"]].artifact_path, "corrupt", "utf8");
const corrupt = materializeHistoricalBacklog(loadCandidateLifecycle(lifecyclePath));
assert.equal(corrupt.items.length, 0);
assert.equal(corrupt.failures.length, 1);

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
assert.equal(sourceText.includes("Target.createTarget"), false);
assert.equal(sourceText.includes("Browser.setWindowBounds"), false);
assert.equal(sourceText.includes('windowState: "minimized"'), false);
assert.equal(sourceText.includes("Network.getResponseBody"), true);
assert.equal(sourceText.includes("fetch(rawUrl"), false);
assert.equal(sourceText.includes("Network.getAllCookies"), false);
assert.equal(sourceText.includes("Network.getCookies"), false);

const outerSource = fs.readFileSync(path.resolve("scripts/run_daily_collection_job.py"), "utf8");
assert.equal(outerSource.includes('"--force-fetch-douyin"'), true);

console.log(JSON.stringify({ ok: true, tests: 60, planned_accounts: 33, rejected_limit_mutations: rejectedLimits.length }));
