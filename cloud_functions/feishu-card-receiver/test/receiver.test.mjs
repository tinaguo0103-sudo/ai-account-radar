import test from "node:test";
import assert from "node:assert/strict";
import { handlePayload } from "../src/receiver.js";

const env = {
  FEISHU_APP_ID: "cli_test_app",
  FEISHU_APP_SECRET: "cli_test_secret",
  FEISHU_BASE_APP_TOKEN: "base_test",
  FEISHU_VERIFICATION_TOKEN: "verify_test",
};

function makeMockFetch(records, calls) {
  return async (url, init = {}) => {
    const parsed = new URL(url);
    const path = parsed.pathname + parsed.search;
    calls.push({ method: init.method || "GET", path, body: init.body ? JSON.parse(init.body) : undefined });
    if (path.endsWith("/auth/v3/tenant_access_token/internal")) {
      return Response.json({ code: 0, tenant_access_token: "tenant_test" });
    }
    if (path.includes("/bitable/v1/apps/base_test/tables") && !path.includes("/records")) {
      return Response.json({ code: 0, data: { items: [{ name: "04 分析与选题", table_id: "tbl_topic" }] } });
    }
    if (path.includes("/records?")) {
      return Response.json({ code: 0, data: { has_more: false, items: records } });
    }
    if (path.includes("/records/")) {
      return Response.json({ code: 0, data: {} });
    }
    return Response.json({ code: 999, msg: `unexpected path ${path}` }, { status: 500 });
  };
}

test("returns Feishu challenge", async () => {
  const response = await handlePayload({ challenge: "abc123" }, env);
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), { challenge: "abc123" });
});

test("rejects invalid callback token", async () => {
  const response = await handlePayload({ header: { token: "wrong" }, event: {} }, env);
  assert.deepEqual(await response.json(), { toast: { type: "error", content: "回调 token 校验失败" } });
});

test("updates selected and unselected candidate records", async () => {
  const records = [
    { record_id: "rec_a", fields: { "选题标题": "A", "运行批次": "run_1", "状态": "待判断" } },
    { record_id: "rec_b", fields: { "选题标题": "B", "运行批次": "run_1", "状态": "待判断" } },
  ];
  const calls = [];
  const response = await handlePayload(
    {
      header: { token: "verify_test" },
      event: {
        action: {
          value: { action: "submit_topic_decisions", run_id: "run_1", candidate_ids: ["rec_a", "rec_b"] },
          form_value: {
            enter_brief_records: ["rec_a"],
            positive_reason_tags: ["证据够"],
            manual_reason: "测试原因",
          },
        },
      },
    },
    env,
    { fetchImpl: makeMockFetch(records, calls) },
  );
  assert.deepEqual(await response.json(), { toast: { type: "success", content: "已回写 2 条选择" } });
  const puts = calls.filter((call) => call.method === "PUT");
  assert.equal(puts.length, 2);
  assert.deepEqual(puts[0].body.fields, {
    "状态": "进入Brief",
    "学习状态": "待学习",
    "选择原因标签": ["证据够"],
    "人工一句话判断": "测试原因",
  });
  assert.deepEqual(puts[1].body.fields, {
    "状态": "不做",
    "学习状态": "待学习",
    "选择原因标签": [],
    "人工一句话判断": "",
  });
});

test("no-ops duplicate submission when fields are already updated", async () => {
  const records = [
    {
      record_id: "rec_a",
      fields: {
        "选题标题": "A",
        "运行批次": "run_1",
        "状态": "进入Brief",
        "学习状态": "待学习",
        "选择原因标签": ["证据够"],
        "人工一句话判断": "测试原因",
      },
    },
  ];
  const calls = [];
  const response = await handlePayload(
    {
      header: { token: "verify_test" },
      event: {
        action: {
          value: { action: "submit_topic_decisions", run_id: "run_1", candidate_ids: ["rec_a"] },
          form_value: {
            enter_brief_records: ["rec_a"],
            positive_reason_tags: ["证据够"],
            manual_reason: "测试原因",
          },
        },
      },
    },
    env,
    { fetchImpl: makeMockFetch(records, calls) },
  );
  assert.deepEqual(await response.json(), { toast: { type: "warning", content: "这次提交已经处理过" } });
  assert.equal(calls.filter((call) => call.method === "PUT").length, 0);
});
