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
  buildWorksFactParity,
  buildSourceRuntimeCoverage,
  checkpointPayload,
  classifyWorksResponse,
  collectionStatusWithHealth,
  configuredAccountIdentity,
  deriveAccountHealth,
  FixedPageSession,
  explicitVerificationState,
  isNavigationTimeout,
  decodeRuntimeEvaluation,
  fixedDouyinTarget,
  limitedPlanRejection,
  loadCandidateLifecycle,
  materializeHistoricalBacklog,
  mergeNewAndBacklog,
  persistAccountHealth,
  probeSourcesWithTailRetry,
  notifyManualVerification,
  persistCollectedCandidates,
  probeAccount,
  parseWorksResponseBody,
  waitForNavigationAndWorksGrid,
  selectIncrementalWorks,
  selectedSources,
  isTransientAccountFailure,
  sourceGlobalRisk,
  runDouyinPreflightWithRecheck,
  tailRetryReadinessCheck,
  validateDouyinSourceIdentity,
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
    id: `source_${name}`,
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
const duplicatePlan = validateSourcePlan([...sources, source("account-1")]);
assert.equal(duplicatePlan.ok, true);
assert.equal(duplicatePlan.invalid_account_count, 2);
assert.equal(validateSourcePlan([{ ...source(""), account_name: "" }]).ok, false);
assert.equal(validateDouyinSourceIdentity({ ...source("x"), url: "https://x.com/example" }).failure_code, "douyin_configured_account_wrong_platform");
assert.equal(validateDouyinSourceIdentity(source("valid")).ok, true);
assert.equal(isTransientAccountFailure({
  status: "failed",
  extraction_diagnostics: { failure_code: "douyin_works_response_timeout" },
}), true);
assert.equal(isTransientAccountFailure({
  status: "failed",
  extraction_diagnostics: { failure_code: "douyin_configured_account_identity_missing" },
}), false);
assert.equal(collectionStatusWithHealth(true, null, { projection: { ok: true } }), "completed");
assert.equal(
  collectionStatusWithHealth(true, null, { projection: { ok: false } }),
  "completed_with_failures",
);

const shapedSources = Array.from({ length: 29 }, (_, index) => source(`valid-${index + 1}`));
shapedSources.push({ ...source("铁锤人"), url: "https://x.com/lxfater" });
shapedSources.push({ ...source("歸藏 guizang.ai"), url: "https://x.com/op7418/status/1" });
const shapedPlan = validateSourcePlan(shapedSources);
assert.equal(shapedPlan.planned_accounts, 31);
assert.equal(shapedPlan.executable_accounts, 29);
assert.equal(shapedPlan.invalid_account_count, 2);
const callOrder = [];
const attempts = new Map();
const transientNames = ["valid-3", "valid-7", "valid-9", "valid-11", "valid-13", "valid-15", "valid-17"];
const shapedProbe = async (_client, row) => {
  const name = row.account_name;
  callOrder.push(name);
  const count = (attempts.get(name) || 0) + 1;
  attempts.set(name, count);
  if (transientNames.includes(name) && (count === 1 || name === "valid-17")) {
    return {
      account_name: name,
      status: "failed",
      failure_reason: "douyin_works_response_timeout",
      extraction_diagnostics: { failure_code: "douyin_works_response_timeout" },
      video_links: [],
    };
  }
  return { account_name: name, status: name === "valid-29" ? "updated_no_new_items" : "success", video_links: name === "valid-29" ? [] : [`https://www.douyin.com/video/${name}`] };
};
const sleeps = [];
const shapedResult = await probeSourcesWithTailRetry(
  {},
  shapedPlan.valid_sources,
  { tailRetryDelayMs: 2500, accountPacingMs: 0 },
  shapedProbe,
  async (ms) => { sleeps.push(ms); },
);
assert.deepEqual(sleeps, [2500]);
assert.deepEqual(callOrder.slice(0, 29), shapedPlan.executable_account_names);
assert.deepEqual(callOrder.slice(29), transientNames);
assert.equal(shapedResult.rows.filter((row) => row.attempts === 2).length, 7);
assert.equal(shapedResult.rows.filter((row) => row.status === "success").length, 27);
assert.equal(shapedResult.rows.filter((row) => row.status === "updated_no_new_items").length, 1);
assert.equal(shapedResult.rows.filter((row) => row.status === "failed").length, 1);

const riskSources = Array.from({ length: 8 }, (_, index) => source(`risk-${index + 1}`));
const riskCalls = [];
const riskCheckpoints = [];
const riskResult = await probeSourcesWithTailRetry(
  {},
  riskSources,
  { batchSize: 5, accountPacingMs: 10, batchCooldownMs: 120, tailRetryDelayMs: 600 },
  async (_client, row) => {
    riskCalls.push(row.id);
    if (row.id === "source_risk-3") {
      return {
        account_name: row.account_name,
        status: "needs_login_or_verification",
        source_global_risk: "verification_required",
        video_links: [],
      };
    }
    return { account_name: row.account_name, status: "success", video_links: [`https://www.douyin.com/video/${row.id}`] };
  },
  async () => {},
);
assert.deepEqual(riskCalls, ["source_risk-1", "source_risk-2", "source_risk-3"]);
assert.equal(riskResult.riskSignal, "verification_required");
assert.equal(riskResult.rows.filter((row) => row.status === "not_attempted_waiting_manual_verification").length, 5);
const riskPayload = checkpointPayload(riskSources, riskResult.rows, riskResult.riskSignal);
assert.deepEqual(riskPayload.slice(0, 3).map((row) => row.status), [
  "completed", "completed", "failed_account_local",
]);
assert.equal(riskPayload.slice(3).every((row) => row.status === "not_attempted_waiting_manual_verification"), true);
assert.equal(sourceGlobalRisk({ status: "needs_login_or_verification" }), "verification_required");
assert.equal(notifyManualVerification(() => ({ status: 0 })), "sent");
assert.equal(notifyManualVerification(() => ({ status: 1 })), "failed");
assert.equal(explicitVerificationState({ login_state: "verification_required" }), true);
assert.equal(explicitVerificationState({ login_state: "indeterminate" }), false);
const preflightSequence = [
  { ok: false, status: "browser_readiness_inconclusive", login_state: "indeterminate" },
  { ok: true, status: "session_verified", login_state: "logged_in" },
];
const preflightSleeps = [];
const recoveredPreflight = await runDouyinPreflightWithRecheck(
  () => preflightSequence.shift(),
  async (ms) => preflightSleeps.push(ms),
  25,
);
assert.equal(recoveredPreflight.ok, true);
assert.equal(recoveredPreflight.preflight_attempts, 2);
assert.deepEqual(preflightSleeps, [25]);
const inconclusivePreflight = await runDouyinPreflightWithRecheck(
  () => ({ ok: false, status: "browser_readiness_inconclusive", login_state: "indeterminate" }),
  async () => {},
  0,
);
assert.equal(inconclusivePreflight.status, "browser_readiness_inconclusive");
assert.equal(inconclusivePreflight.preflight_attempts, 2);

const inconclusiveTailReadiness = await tailRetryReadinessCheck(
  () => ({ ok: false, status: "browser_readiness_inconclusive", login_state: "indeterminate" }),
  async () => {},
  0,
);
assert.equal(inconclusiveTailReadiness.riskSignal, "");
assert.equal(inconclusiveTailReadiness.readinessFailure, "browser_readiness_inconclusive");
const challengeTailReadiness = await tailRetryReadinessCheck(
  () => ({ ok: false, status: "verification_required", login_state: "verification_required" }),
  async () => {},
  0,
);
assert.equal(challengeTailReadiness.riskSignal, "verification_required");
assert.equal(challengeTailReadiness.readinessFailure, "");

const transientSource = [source("transient-indeterminate")];
const transientSleeps = [];
const transientCheckpoints = [];
const transientIndeterminate = await probeSourcesWithTailRetry(
  {},
  transientSource,
  {
    batchSize: 5,
    accountPacingMs: 0,
    batchCooldownMs: 0,
    tailRetryDelayMs: 600000,
    riskCheck: async () => inconclusiveTailReadiness,
    onCheckpoint: async (rows, signal) => transientCheckpoints.push({
      signal,
      statuses: rows.map((row) => row.status),
    }),
  },
  async (_client, row) => ({
    account_name: row.account_name,
    status: "failed",
    failure_reason: "douyin_works_response_timeout",
    extraction_diagnostics: { failure_code: "douyin_works_response_timeout" },
    video_links: [],
  }),
  async (ms) => transientSleeps.push(ms),
);
assert.equal(transientIndeterminate.riskSignal, "");
assert.deepEqual(transientSleeps, []);
assert.equal(transientIndeterminate.rows[0].status, "failed");
assert.equal(transientIndeterminate.rows[0].tail_retry_status, "not_attempted_browser_readiness_failure");
assert.equal(transientIndeterminate.rows[0].tail_retry_reason, "browser_readiness_inconclusive");
assert.equal(transientCheckpoints.some((row) => row.signal), false);
assert.equal(transientIndeterminate.rows.some((row) => row.status === "not_attempted_waiting_manual_verification"), false);

const transientChallengeSleeps = [];
const transientChallenge = await probeSourcesWithTailRetry(
  {},
  [source("transient-challenge")],
  {
    batchSize: 5,
    accountPacingMs: 0,
    batchCooldownMs: 0,
    tailRetryDelayMs: 600000,
    riskCheck: async () => challengeTailReadiness,
  },
  async (_client, row) => ({
    account_name: row.account_name,
    status: "failed",
    failure_reason: "douyin_works_response_timeout",
    extraction_diagnostics: { failure_code: "douyin_works_response_timeout" },
    video_links: [],
  }),
  async (ms) => transientChallengeSleeps.push(ms),
);
assert.equal(transientChallenge.riskSignal, "verification_required");
assert.deepEqual(transientChallengeSleeps, []);

const completedIds = new Set(["source_risk-1", "source_risk-2"]);
const resumeOrder = [];
await probeSourcesWithTailRetry(
  {},
  riskSources,
  {
    completedSourceIds: [...completedIds],
    batchSize: 5,
    accountPacingMs: 0,
    batchCooldownMs: 0,
    tailRetryDelayMs: 600,
    onCheckpoint: async (rows) => riskCheckpoints.push(rows.map((row) => row.source_id)),
  },
  async (_client, row) => {
    resumeOrder.push(row.id);
    return { account_name: row.account_name, status: "updated_no_new_items", video_links: [] };
  },
  async () => {},
);
assert.deepEqual(resumeOrder, riskSources.slice(2).map((row) => row.id));
assert.equal(riskCheckpoints.length, 6);

const resumeSources = Array.from({ length: 31 }, (_, index) => source(`resume-${index + 1}`));
const immutablePrior = resumeSources.slice(0, 25).map((row, ordinal) => ({
  source_id: row.id,
  status: ordinal % 2 ? "updated_no_new_items" : "completed",
  artifact_sha256: `sha-${ordinal}`,
  artifact_count: ordinal + 1,
  ordinal,
}));
const failedPrior = resumeSources.slice(25).map((row, offset) => ({
  source_id: row.id,
  status: "failed_account_local",
  artifact_sha256: "",
  artifact_count: 0,
  ordinal: 25 + offset,
}));
const resumeCalls = [];
const resumeCheckpoints = [];
await probeSourcesWithTailRetry(
  {},
  resumeSources,
  {
    completedSourceIds: immutablePrior.map((row) => row.source_id),
    batchSize: 5,
    accountPacingMs: 0,
    batchCooldownMs: 0,
    tailRetryDelayMs: 600000,
    riskCheck: async () => ({ riskSignal: "", readinessFailure: "" }),
    onCheckpoint: async (rows) => resumeCheckpoints.push(
      checkpointPayload(resumeSources, rows, "", [...immutablePrior, ...failedPrior]),
    ),
  },
  async (_client, row) => {
    resumeCalls.push(row.id);
    return { account_name: row.account_name, status: "updated_no_new_items", video_links: [] };
  },
  async () => {},
);
assert.deepEqual(resumeCalls, resumeSources.slice(25).map((row) => row.id));
assert.equal(resumeCheckpoints.length, 6);
for (const checkpoint of resumeCheckpoints) {
  assert.deepEqual(checkpoint.slice(0, 25), immutablePrior);
}
assert.equal(resumeCheckpoints[0][25].status, "updated_no_new_items");
assert.deepEqual(resumeCheckpoints[0].slice(26), failedPrior.slice(1));

const quarantinedSources = Array.from({ length: 8 }, (_, index) => source(`quarantined-${index + 1}`));
const selectedSourceIds = new Set(resumeSources.map((row) => row.id));
assert.equal(quarantinedSources.some((row) => selectedSourceIds.has(row.id)), false);

const timingSources = Array.from({ length: 31 }, (_, index) => source(`timing-${index + 1}`));
const timingOrder = [];
const timingSleeps = [];
const timingAttempts = new Map();
await probeSourcesWithTailRetry(
  {},
  timingSources,
  { batchSize: 5, accountPacingMs: 10000, batchCooldownMs: 120000, tailRetryDelayMs: 600000 },
  async (_client, row) => {
    timingOrder.push(row.id);
    const attempt = (timingAttempts.get(row.id) || 0) + 1;
    timingAttempts.set(row.id, attempt);
    if (row.id === "source_timing-7" && attempt === 1) {
      return {
        account_name: row.account_name,
        status: "failed",
        extraction_diagnostics: { failure_code: "douyin_works_response_timeout" },
        video_links: [],
      };
    }
    return { account_name: row.account_name, status: "updated_no_new_items", video_links: [] };
  },
  async (ms) => timingSleeps.push(ms),
);
assert.deepEqual(timingOrder.slice(0, 31), timingSources.map((row) => row.id));
assert.deepEqual(timingOrder.slice(31), ["source_timing-7"]);
assert.equal(timingSleeps.filter((value) => value === 120000).length, 6);
assert.equal(timingSleeps.filter((value) => value === 10000).length, 24);
assert.equal(timingSleeps.filter((value) => value === 600000).length, 1);

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
const factShape = parseWorksResponseBody(JSON.stringify({ aweme_list: [{
  aweme_id: "50000000009",
  desc: "facts",
  author: { sec_uid: "account-1" },
  create_time: 1785369600,
  statistics: {
    digg_count: 0, comment_count: 12, collect_count: 3, share_count: 1,
  },
}] }), "account-1");
assert.equal(factShape.ok, true);
assert.equal(factShape.cards[0].create_time, 1785369600);
assert.deepEqual(
  ["likes", "comments", "favorites", "shares"].map((key) => factShape.cards[0][key]),
  [0, 12, 3, 1],
);
assert.deepEqual(factShape.cards[0].fact_missing_reasons, {});
const factContent = buildHomepageCardItems([{
  status: "success",
  account_name: "account-1",
  video_links: ["https://www.douyin.com/video/50000000009"],
  video_cards: factShape.cards,
}])[0];
assert.equal(factContent.published_at, "2026-07-30T00:00:00.000Z");
assert.deepEqual(
  ["likes", "comments", "favorites", "shares"].map((key) => factContent[key]),
  [0, 12, 3, 1],
);
const factParity = buildWorksFactParity([{
  status: "success",
  video_cards: factShape.cards,
}], [factContent]);
assert.equal(factParity.status, "passed");
assert.equal(factParity.ordinary_work_count, 1);
assert.equal(factParity.raw_supported_field_count, 5);
assert.equal(factParity.projection_missing_count, 0);
assert.equal(factParity.parity_percent, 100);
assert.equal(factParity.real_zero_field_count, 1);
assert.equal(factParity.complete_fact_candidate_count, 1);
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

assert.equal(isNavigationTimeout(new Error("Page.navigate timed out after 8000ms")), true);
assert.equal(isNavigationTimeout(new Error("verification_required")), false);
const timeoutClients = [
  {
    async open() {}, close() {},
    async send(method) {
      if (["Runtime.enable", "Page.enable", "Network.enable"].includes(method)) return {};
      if (method === "Page.navigate") throw new Error("Page.navigate timed out after 8000ms");
      throw new Error(`unexpected timeout client command:${method}`);
    },
  },
  {
    async open() {}, close() {},
    async send(method) {
      if (["Runtime.enable", "Page.enable", "Network.enable"].includes(method)) return {};
      if (method === "Page.navigate") return { frameId: "fixed-frame" };
      throw new Error(`unexpected recovered client command:${method}`);
    },
  },
];
let timeoutClientIndex = 0;
const timeoutSession = new FixedPageSession("http://127.0.0.1:9333", {
  id: "fixed-target-id", type: "page", url: "https://www.douyin.com/user/account-1", webSocketDebuggerUrl: "ws://first",
}, {
  maxReattachments: 1,
  clientFactory: () => timeoutClients[timeoutClientIndex++],
  listTargets: async () => [{
    id: "fixed-target-id", type: "page", url: "https://www.douyin.com/user/account-1", webSocketDebuggerUrl: "ws://second",
  }],
});
await timeoutSession.open();
assert.deepEqual(await timeoutSession.send("Page.navigate", { url: "https://www.douyin.com/user/account-1" }), { frameId: "fixed-frame" });
assert.equal(timeoutSession.reattachments, 1);
assert.equal(timeoutSession.navigationTimeoutRecoveries, 1);
timeoutSession.close();

const repeatedTimeoutClients = [0, 1].map(() => ({
  async open() {}, close() {},
  async send(method) {
    if (["Runtime.enable", "Page.enable", "Network.enable"].includes(method)) return {};
    if (method === "Page.navigate") throw new Error("Page.navigate timed out after 8000ms");
    throw new Error(`unexpected repeated timeout command:${method}`);
  },
}));
let repeatedTimeoutIndex = 0;
const repeatedTimeoutSession = new FixedPageSession("http://127.0.0.1:9333", {
  id: "fixed-target-id", type: "page", url: "https://www.douyin.com/user/account-1", webSocketDebuggerUrl: "ws://first",
}, {
  maxReattachments: 1,
  clientFactory: () => repeatedTimeoutClients[repeatedTimeoutIndex++],
  listTargets: async () => [{
    id: "fixed-target-id", type: "page", url: "https://www.douyin.com/user/account-1", webSocketDebuggerUrl: "ws://second",
  }],
});
await repeatedTimeoutSession.open();
await assert.rejects(
  repeatedTimeoutSession.send("Page.navigate", { url: "https://www.douyin.com/user/account-1" }),
  /douyin_navigation_timeout_after_reattach/,
);
assert.equal(repeatedTimeoutSession.navigationTimeoutRecoveries, 1);
repeatedTimeoutSession.close();

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

const healthLedger = path.join(tmp, "health.json");
const healthRun = path.join(tmp, "run-health.json");
const healthSource = source("health-account");
persistAccountHealth(healthLedger, healthRun, [healthSource], [{
  account_name: "health-account",
  homepage_url: healthSource.url,
  status: "failed",
  extraction_diagnostics: { failure_code: "douyin_works_response_timeout" },
}], "run_20260725_080000", "2026-07-25T00:00:00Z");
persistAccountHealth(healthLedger, healthRun, [healthSource], [{
  account_name: "health-account",
  homepage_url: healthSource.url,
  status: "failed",
  extraction_diagnostics: { failure_code: "douyin_works_response_timeout" },
}], "run_20260726_080000", "2026-07-26T00:00:00Z");
let health = persistAccountHealth(healthLedger, healthRun, [healthSource], [{
  account_name: "health-account",
  homepage_url: healthSource.url,
  status: "failed",
  extraction_diagnostics: { failure_code: "douyin_works_response_timeout" },
}], "run_20260727_080000", "2026-07-27T00:00:00Z");
assert.equal(health.accounts[0].consecutive_failures, 3);
assert.equal(health.accounts[0].action_required, true);
assert.equal(health.authority.ok, true);
assert.equal(health.projection.ok, true);
health = persistAccountHealth(healthLedger, healthRun, [healthSource], [{
  account_name: "health-account",
  homepage_url: healthSource.url,
  status: "success",
}], "run_20260728_080000", "2026-07-28T00:00:00Z");
assert.equal(health.accounts[0].consecutive_failures, 0);
assert.equal(health.accounts[0].action_required, false);
const eventCount = Object.keys(JSON.parse(fs.readFileSync(healthLedger, "utf8")).events).length;
persistAccountHealth(healthLedger, healthRun, [healthSource], [{
  account_name: "health-account",
  homepage_url: healthSource.url,
  status: "success",
}], "run_20260728_080000", "2026-07-28T00:00:00Z");
assert.equal(Object.keys(JSON.parse(fs.readFileSync(healthLedger, "utf8")).events).length, eventCount);
assert.equal(deriveAccountHealth([], healthSource, "run_20260728_080000").rolling_success, null);

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
assert.equal(sourceText.includes("probeAccountWithRetry"), false);
assert.equal(sourceText.includes("tailRetryDelayMs"), true);

const outerSource = fs.readFileSync(path.resolve("scripts/run_daily_collection_job.py"), "utf8");
assert.equal(outerSource.includes('"--force-fetch-douyin"'), false);

console.log(JSON.stringify({ ok: true, tests: 60, planned_accounts: 33, rejected_limit_mutations: rejectedLimits.length }));
