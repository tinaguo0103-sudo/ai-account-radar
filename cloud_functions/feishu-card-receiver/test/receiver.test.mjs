import test from "node:test";
import assert from "node:assert/strict";
import { handlePayload } from "../src/receiver.js";

const env = {
  FEISHU_APP_ID: "cli_test_app",
  FEISHU_APP_SECRET: "cli_test_secret",
  FEISHU_BASE_APP_TOKEN: "base_test",
  FEISHU_VERIFICATION_TOKEN: "verify_test",
  SEND_PRODUCTION_DIRECTION_CARD: "false",
};

function makeMockFetch(records, calls) {
  return async (url, init = {}) => {
    const parsed = new URL(url);
    const path = parsed.pathname + parsed.search;
    calls.push({ method: init.method || "GET", path, body: init.body ? JSON.parse(init.body) : undefined });
    if (path.endsWith("/auth/v3/tenant_access_token/internal")) {
      return Response.json({ code: 0, tenant_access_token: "tenant_test" });
    }
    if (path.includes("/bitable/v1/apps/base_test/tables") && !path.includes("/records") && !path.includes("/fields")) {
      return Response.json({ code: 0, data: { items: [{ name: "04 分析与选题", table_id: "tbl_topic" }] } });
    }
    if (path.includes("/records?")) {
      return Response.json({ code: 0, data: { has_more: false, items: records } });
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
    if (path.includes("/im/v1/messages")) {
      return Response.json({ code: 0, data: { message_id: "om_test" } });
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

test("sends production direction card after selected topics are written", async () => {
  const records = [
    { record_id: "rec_a", fields: { "选题标题": "A", "一句话Brief": "A brief", "运行批次": "run_1", "状态": "待判断" } },
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
            manual_reason: "",
          },
        },
      },
    },
    { ...env, SEND_PRODUCTION_DIRECTION_CARD: "true", FEISHU_CARD_RECEIVE_TARGETS: "open_id:ou_follow" },
    { fetchImpl: makeMockFetch(records, calls) },
  );
  assert.deepEqual(await response.json(), { toast: { type: "success", content: "已回写 2 条选择，并发送制作方向卡" } });
  const sends = calls.filter((call) => call.path.includes("/im/v1/messages"));
  assert.equal(sends.length, 1);
  const card = JSON.parse(sends[0].body.content);
  const cardText = JSON.stringify(card);
  assert.match(cardText, /补充制作方向/);
  assert.match(cardText, /production_direction__rec_a/);
  assert.doesNotMatch(cardText, /production_direction__rec_b/);
});

test("writes per-topic production directions", async () => {
  const records = [
    { record_id: "rec_a", fields: { "选题标题": "A", "运行批次": "run_1", "状态": "进入Brief" } },
    { record_id: "rec_b", fields: { "选题标题": "B", "运行批次": "run_1", "状态": "进入Brief" } },
  ];
  const calls = [];
  const response = await handlePayload(
    {
      header: { token: "verify_test" },
      event: {
        action: {
          value: { action: "submit_production_directions", run_id: "run_1", candidate_ids: ["rec_a", "rec_b"] },
          form_value: {
            production_direction__rec_a: "用 AI账号信息雷达案例讲，重点讲选题判断",
            production_direction__rec_b: "",
          },
        },
      },
    },
    env,
    { fetchImpl: makeMockFetch(records, calls) },
  );
  assert.deepEqual(await response.json(), { toast: { type: "success", content: "已保存 1 条制作方向" } });
  const puts = calls.filter((call) => call.method === "PUT");
  assert.equal(puts.length, 1);
  assert.deepEqual(puts[0].body.fields, {
    "我的制作补充": "用 AI账号信息雷达案例讲，重点讲选题判断",
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
