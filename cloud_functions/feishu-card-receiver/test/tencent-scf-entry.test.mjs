import test from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { main_handler } = require("../tencent-scf/index.js");

function bodyJson(response) {
  return JSON.parse(response.body || "{}");
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
