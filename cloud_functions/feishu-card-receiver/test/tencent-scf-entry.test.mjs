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
