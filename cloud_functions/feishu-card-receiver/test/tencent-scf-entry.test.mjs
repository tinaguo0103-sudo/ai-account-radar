import test from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { handlePayload as handleSrcPayload } from "../src/receiver.js";

const require = createRequire(import.meta.url);
const { main_handler } = require("../tencent-scf/index.js");

function bodyJson(response) {
  return JSON.parse(response.body || "{}");
}

function selectionPayload(action, candidateIds, selectedIds = []) {
  return {
    header: { token: "verify_test" },
    event: {
      action: {
        value: { action, run_id: "run_contract", candidate_ids: candidateIds },
        form_value: { script_package_records: selectedIds, positive_reason_tags: ["证据够"], manual_reason: "" },
      },
    },
  };
}

function contractFetch(records, calls, { reasonFieldType = 1 } = {}) {
  return async (url, init = {}) => {
    const path = new URL(url).pathname + new URL(url).search;
    const body = init.body ? JSON.parse(init.body) : undefined;
    calls.push({ method: init.method || "GET", path, body });
    if (path.endsWith("/auth/v3/tenant_access_token/internal")) return Response.json({ code: 0, tenant_access_token: "tenant_test" });
    if (path.includes("/fields") && (init.method || "GET") === "GET") return Response.json({ code: 0, data: { items: [{ field_name: "选择原因标签", type: reasonFieldType }], has_more: false } });
    const match = path.match(/\/records\/([^/?]+)$/);
    if (match && (init.method || "GET") === "GET") {
      const record = records.find((item) => item.record_id === decodeURIComponent(match[1]));
      if (!record) return Response.json({ code: 1254045, msg: "record not found" }, { status: 404 });
      return Response.json({ code: 0, data: { record } });
    }
    if (match && (init.method || "GET") === "PUT") return Response.json({ code: 0, data: {} });
    return Response.json({ code: 999, msg: `unexpected path ${path}` }, { status: 500 });
  };
}

async function runScfContract(payload, records, fetchOptions = {}) {
  const keys = ["FEISHU_APP_ID", "FEISHU_APP_SECRET", "FEISHU_BASE_APP_TOKEN", "FEISHU_TOPIC_TABLE_ID", "FEISHU_VERIFICATION_TOKEN", "SEND_PRODUCTION_DIRECTION_CARD"];
  const oldEnv = Object.fromEntries(keys.map((key) => [key, process.env[key]]));
  const oldFetch = globalThis.fetch;
  const calls = [];
  try {
    Object.assign(process.env, {
      FEISHU_APP_ID: "cli_test_app",
      FEISHU_APP_SECRET: "cli_test_secret",
      FEISHU_BASE_APP_TOKEN: "base_test",
      FEISHU_TOPIC_TABLE_ID: "tbl_topic",
      FEISHU_VERIFICATION_TOKEN: "verify_test",
      SEND_PRODUCTION_DIRECTION_CARD: "false",
    });
    globalThis.fetch = contractFetch(records, calls, fetchOptions);
    const response = await main_handler({ httpMethod: "POST", body: JSON.stringify(payload) });
    return { body: bodyJson(response), calls };
  } finally {
    globalThis.fetch = oldFetch;
    for (const [key, value] of Object.entries(oldEnv)) {
      if (value === undefined) delete process.env[key];
      else process.env[key] = value;
    }
  }
}

async function runSrcContract(payload, records, fetchOptions = {}) {
  const calls = [];
  const response = await handleSrcPayload(
    payload,
    { FEISHU_APP_ID: "cli_test_app", FEISHU_APP_SECRET: "cli_test_secret", FEISHU_BASE_APP_TOKEN: "base_test", FEISHU_TOPIC_TABLE_ID: "tbl_topic", FEISHU_VERIFICATION_TOKEN: "verify_test", SEND_PRODUCTION_DIRECTION_CARD: "false" },
    { fetchImpl: contractFetch(records, calls, fetchOptions) },
  );
  return { body: await response.json(), calls };
}

test("Tencent SCF entry returns Feishu challenge", async () => {
  const response = await main_handler({
    httpMethod: "POST",
    body: JSON.stringify({ challenge: "hello" }),
  });
  assert.equal(response.statusCode, 200);
  assert.deepEqual(bodyJson(response), { challenge: "hello" });
});

test("Tencent SCF entry rejects non-POST requests", async () => {
  const response = await main_handler({ httpMethod: "GET", body: "" });
  assert.equal(response.statusCode, 405);
  assert.deepEqual(bodyJson(response), { ok: false, error: "POST required" });
});

test("Tencent SCF entry handles invalid JSON body", async () => {
  const response = await main_handler({ httpMethod: "POST", body: "{" });
  assert.equal(response.statusCode, 400);
  assert.deepEqual(bodyJson(response), { ok: false, error: "Invalid JSON body" });
});

test("src and Tencent SCF normal submit update only the selected page row", async () => {
  const records = Array.from({ length: 5 }, (_, index) => ({ record_id: `rec_${index + 1}`, fields: { "选题标题": `候选 ${index + 1}`, "运行批次": "run_contract", "状态": "待判断" } }));
  const payload = selectionPayload("submit_topic_decisions", records.map((record) => record.record_id), ["rec_2"]);
  const src = await runSrcContract(payload, structuredClone(records));
  const scf = await runScfContract(payload, structuredClone(records));
  assert.deepEqual(scf.body, src.body);
  assert.equal(scf.body.toast.content, "已回写 1 条选择");
  for (const result of [src, scf]) {
    const puts = result.calls.filter((call) => call.method === "PUT");
    assert.equal(puts.length, 1);
    assert.match(puts[0].path, /\/rec_2$/);
    assert.equal(puts[0].body.fields["状态"], "生成脚本包");
    assert.equal(puts[0].body.fields["制作方向卡状态"], undefined);
  }
});

test("src and Tencent SCF preserve equivalent text and multi-select reason shapes", async () => {
  const records = [{ record_id: "rec_1", fields: { "选题标题": "候选 1", "运行批次": "run_contract", "状态": "待判断" } }];
  const payload = selectionPayload("submit_topic_decisions", ["rec_1"], ["rec_1"]);
  payload.event.action.form_value.positive_reason_tags = ["证据够", "判断够强"];
  for (const [reasonFieldType, expected] of [[1, "证据够、判断够强"], [4, ["证据够", "判断够强"]]]) {
    const src = await runSrcContract(payload, structuredClone(records), { reasonFieldType });
    const scf = await runScfContract(payload, structuredClone(records), { reasonFieldType });
    assert.deepEqual(scf.body, src.body);
    assert.deepEqual(src.calls.find((call) => call.method === "PUT").body.fields["选择原因标签"], expected);
    assert.deepEqual(scf.calls.find((call) => call.method === "PUT").body.fields["选择原因标签"], expected);
  }
});

test("src and Tencent SCF page no-selection reject exactly page IDs", async () => {
  const records = Array.from({ length: 6 }, (_, index) => ({ record_id: `rec_${index + 1}`, fields: { "选题标题": `候选 ${index + 1}`, "运行批次": "run_contract", "状态": "待判断" } }));
  const pageIds = records.slice(0, 5).map((record) => record.record_id);
  const payload = selectionPayload("submit_no_selection", pageIds);
  const src = await runSrcContract(payload, structuredClone(records));
  const scf = await runScfContract(payload, structuredClone(records));
  assert.deepEqual(scf.body, src.body);
  for (const result of [src, scf]) {
    const puts = result.calls.filter((call) => call.method === "PUT");
    assert.equal(puts.length, 5);
    assert.ok(puts.every((call) => call.body.fields["状态"] === "不做"));
    assert.ok(puts.every((call) => Object.keys(call.body.fields).length === 1));
    assert.ok(puts.every((call) => !call.path.endsWith("/rec_6")));
  }
});

test("src and Tencent SCF reject empty or outside-page normal selections without writes", async () => {
  const records = [{ record_id: "rec_1", fields: { "选题标题": "候选 1", "运行批次": "run_contract", "状态": "待判断" } }];
  for (const payload of [
    selectionPayload("submit_topic_decisions", ["rec_1"], []),
    selectionPayload("submit_topic_decisions", ["rec_1"], ["rec_outside"]),
  ]) {
    const src = await runSrcContract(payload, structuredClone(records));
    const scf = await runScfContract(payload, structuredClone(records));
    assert.deepEqual(scf.body, src.body);
    assert.equal(src.calls.filter((call) => call.method === "PUT").length, 0);
    assert.equal(scf.calls.filter((call) => call.method === "PUT").length, 0);
    assert.equal(src.body.toast.type, "warning");
  }
});

test("Tencent SCF entry fails hanging direction sends before records stay sending", { timeout: 1000 }, async () => {
  const oldFetch = globalThis.fetch;
  const oldEnv = {
    FEISHU_APP_ID: process.env.FEISHU_APP_ID,
    FEISHU_APP_SECRET: process.env.FEISHU_APP_SECRET,
    FEISHU_BASE_APP_TOKEN: process.env.FEISHU_BASE_APP_TOKEN,
    FEISHU_TOPIC_TABLE_ID: process.env.FEISHU_TOPIC_TABLE_ID,
    FEISHU_CARD_RECEIVE_TARGETS: process.env.FEISHU_CARD_RECEIVE_TARGETS,
    SEND_PRODUCTION_DIRECTION_CARD: process.env.SEND_PRODUCTION_DIRECTION_CARD,
    FEISHU_API_TIMEOUT_MS: process.env.FEISHU_API_TIMEOUT_MS,
    FEISHU_PRODUCTION_DIRECTION_ALERTS: process.env.FEISHU_PRODUCTION_DIRECTION_ALERTS,
    FEISHU_QUEUE_RUNNER_TOKEN: process.env.FEISHU_QUEUE_RUNNER_TOKEN,
  };
  const records = [
    {
      record_id: "rec_a",
      fields: {
        "选题标题": "A",
        "一句话Brief": "A brief",
        "我要做的实验": "A experiment",
        "运行批次": "run_1",
        "状态": "生成脚本包",
        "制作方向卡状态": "待发送",
        "选择提交批次": "run_1:abc",
        "选择提交时间": new Date().toISOString(),
      },
    },
  ];
  const calls = [];
  globalThis.fetch = async (url, init = {}) => {
    const parsed = new URL(url);
    const path = parsed.pathname + parsed.search;
    calls.push({ method: init.method || "GET", path, body: init.body ? JSON.parse(init.body) : undefined });
    if (path.endsWith("/auth/v3/tenant_access_token/internal")) {
      return Response.json({ code: 0, tenant_access_token: "tenant_test" });
    }
    if (path.includes("/records?")) {
      return Response.json({ code: 0, data: { has_more: false, items: records } });
    }
    if (path.includes("/records/")) {
      return Response.json({ code: 0, data: {} });
    }
    if (path.includes("/im/v1/messages")) {
      return new Promise((resolve, reject) => {
        if (init.signal) {
          init.signal.addEventListener("abort", () => reject(new Error("aborted by timeout")), { once: true });
        }
      });
    }
    return Response.json({ code: 999, msg: `unexpected path ${path}` }, { status: 500 });
  };
  try {
    Object.assign(process.env, {
      FEISHU_APP_ID: "cli_test_app",
      FEISHU_APP_SECRET: "cli_test_secret",
      FEISHU_BASE_APP_TOKEN: "base_test",
      FEISHU_TOPIC_TABLE_ID: "tbl_topic",
      FEISHU_CARD_RECEIVE_TARGETS: "open_id:ou_follow",
      SEND_PRODUCTION_DIRECTION_CARD: "true",
      FEISHU_API_TIMEOUT_MS: "5",
      FEISHU_PRODUCTION_DIRECTION_ALERTS: "false",
      FEISHU_QUEUE_RUNNER_TOKEN: "",
    });
    const response = await main_handler({
      httpMethod: "POST",
      body: JSON.stringify({ action: "send_pending_production_direction_cards" }),
    });
    assert.equal(response.statusCode, 200);
    const payload = bodyJson(response);
    assert.equal(payload.ok, false);
    assert.equal(payload.failed.length, 1);
    assert.match(payload.failed[0].error, /request failed/);
    const failedPut = calls.find((call) => call.method === "PUT" && call.body.fields["制作方向卡状态"] === "发送失败");
    assert.ok(failedPut);
  } finally {
    globalThis.fetch = oldFetch;
    for (const [key, value] of Object.entries(oldEnv)) {
      if (value === undefined) delete process.env[key];
      else process.env[key] = value;
    }
  }
});
