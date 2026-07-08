import test from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { main_handler } = require("../tencent-scf/index.js");

function bodyJson(response) {
  return JSON.parse(response.body || "{}");
}

function toastBody(type, content) {
  return { code: 0, toast: { type, content } };
}

function makeScfMockFetch(records, calls) {
  return async (url, init = {}) => {
    const parsed = new URL(url);
    const path = parsed.pathname + parsed.search;
    calls.push({ method: init.method || "GET", path, body: init.body ? JSON.parse(init.body) : undefined });
    if (path.endsWith("/auth/v3/tenant_access_token/internal")) {
      return Response.json({ code: 0, tenant_access_token: "tenant_test" });
    }
    if (path.includes("/bitable/v1/apps/base_test/tables") && !path.includes("/records") && !path.includes("/fields")) {
      return Response.json({
        code: 0,
        data: {
          items: [
            { name: "04 分析与选题", table_id: "tbl_topic" },
            { name: "06 完整脚本与制作包", table_id: "tbl_script_package" },
            { name: "08 学习记录", table_id: "tbl_learning" },
          ],
        },
      });
    }
    const recordMatch = path.match(/\/records\/([^/?]+)$/);
    if (recordMatch && (init.method || "GET") === "GET") {
      const record = records.find((item) => item.record_id === decodeURIComponent(recordMatch[1]));
      if (!record) return Response.json({ code: 1254045, msg: "record not found" }, { status: 404 });
      return Response.json({ code: 0, data: { record } });
    }
    if (path.endsWith("/fields")) {
      return Response.json({ code: 0, data: { items: [] } });
    }
    if (path.includes("/fields")) {
      return Response.json({ code: 0, data: {} });
    }
    if (path.includes("/records/")) {
      return Response.json({ code: 0, data: {} });
    }
    return Response.json({ code: 999, msg: `unexpected path ${path}` }, { status: 500 });
  };
}

test("Tencent SCF entry returns Feishu challenge", async () => {
  const response = await main_handler({
    httpMethod: "POST",
    body: JSON.stringify({ challenge: "hello" }),
  });
  assert.equal(response.statusCode, 200);
  assert.deepEqual(bodyJson(response), { challenge: "hello" });
});

test("Tencent SCF entry writes learning confirmation feedback", async () => {
  const originalEnv = { ...process.env };
  const originalFetch = global.fetch;
  const calls = [];
  process.env.FEISHU_APP_ID = "cli_test_app";
  process.env.FEISHU_APP_SECRET = "cli_test_secret";
  process.env.FEISHU_BASE_APP_TOKEN = "base_test";
  process.env.FEISHU_VERIFICATION_TOKEN = "verify_test";
  process.env.FEISHU_TOPIC_DECISION_TABLE_ID = "tbl_topic";
  process.env.FEISHU_SCRIPT_PACKAGE_TABLE_ID = "tbl_script_package";
  process.env.FEISHU_LEARNING_TABLE_ID = "tbl_learning";
  global.fetch = makeScfMockFetch([
    { record_id: "learn_a", fields: { "学习批次": "learn_1", "确认状态": "待确认" } },
    { record_id: "topic_a", fields: { "选题标题": "A", "学习状态": "待确认学习" } },
    { record_id: "pkg_a", fields: { "脚本标题": "A 脚本包", "内容学习状态": "待确认学习" } },
  ], calls);
  try {
    const response = await main_handler({
      httpMethod: "POST",
      body: JSON.stringify({
        header: { token: "verify_test" },
        event: {
          action: {
            value: {
              action: "submit_learning_feedback_confirmation",
              decision: "已采纳",
              environment: "staging",
              learning_record_id: "learn_a",
              learning_batch_id: "learn_1",
              topic_record_ids: ["topic_a"],
              script_record_ids: ["pkg_a"],
              learning_summary: "学习确认 SCF 入口测试。",
            },
            form_value: {
              learning_confirmation_note: "入口测试通过。",
            },
          },
        },
      }),
    });
    assert.deepEqual(bodyJson(response), toastBody("success", "已确认学习日结：已采纳"));
    const puts = calls.filter((call) => call.method === "PUT");
    assert.equal(puts.length, 3);
    assert.equal(puts[0].path, "/open-apis/bitable/v1/apps/base_test/tables/tbl_learning/records/learn_a");
    assert.equal(puts[0].body.fields["Skill同步状态"], "待同步");
    assert.equal(puts[1].body.fields["学习状态"], "已学习");
    assert.equal(puts[2].body.fields["内容学习状态"], "已学习");
  } finally {
    process.env = originalEnv;
    global.fetch = originalFetch;
  }
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

test("Tencent SCF entry writes script package quality feedback", async () => {
  const originalEnv = { ...process.env };
  const originalFetch = global.fetch;
  const calls = [];
  process.env.FEISHU_APP_ID = "cli_test_app";
  process.env.FEISHU_APP_SECRET = "cli_test_secret";
  process.env.FEISHU_BASE_APP_TOKEN = "base_test";
  process.env.FEISHU_VERIFICATION_TOKEN = "verify_test";
  global.fetch = makeScfMockFetch([{ record_id: "pkg_a", fields: { "脚本标题": "A 脚本包" } }], calls);
  try {
    const response = await main_handler({
      httpMethod: "POST",
      body: JSON.stringify({
        header: { token: "verify_test" },
        event: {
          action: {
            value: {
              action: "submit_script_package_quality_feedback",
              candidate_ids: ["pkg_a"],
            },
            form_value: {
              script_quality__pkg_a: "需要重写",
              script_issues__pkg_a: ["太泛", "证据不可拍"],
              script_note__pkg_a: "需要补具体真实场景。",
              script_note__pkg_a__2: "不能只写成通用方法论。",
            },
          },
        },
      }),
    });
    assert.deepEqual(bodyJson(response), toastBody("success", "已保存 1 条质量反馈"));
    const put = calls.find((call) => call.method === "PUT");
    assert.ok(put);
    assert.equal(put.path, "/open-apis/bitable/v1/apps/base_test/tables/tbl_script_package/records/pkg_a");
    assert.equal(put.body.fields["人工质量反馈"], "需要重写");
    assert.equal(put.body.fields["质量问题标签"], "太泛、证据不可拍");
    assert.equal(put.body.fields["人工修改意见"], "需要补具体真实场景。 不能只写成通用方法论。");
    assert.equal(put.body.fields["反馈来源"], "06完成卡");
    assert.equal(put.body.fields["内容学习状态"], "待学习");
  } finally {
    process.env = originalEnv;
    global.fetch = originalFetch;
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
