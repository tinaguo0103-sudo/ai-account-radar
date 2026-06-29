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

function toastBody(type, content) {
  return { code: 0, toast: { type, content } };
}

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
  assert.deepEqual(await response.json(), toastBody("error", "回调 token 校验失败"));
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
  assert.deepEqual(await response.json(), toastBody("success", "已回写 2 条选择"));
  const puts = calls.filter((call) => call.method === "PUT");
  assert.equal(puts.length, 2);
  assert.deepEqual(puts[0].body.fields, {
    "状态": "生成脚本包",
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

test("queues production direction card after selected topics are written", async () => {
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
    {
      ...env,
      SEND_PRODUCTION_DIRECTION_CARD: "true",
      FEISHU_CARD_RECEIVE_TARGETS: "open_id:ou_follow",
    },
    { fetchImpl: makeMockFetch(records, calls) },
  );
  assert.deepEqual(await response.json(), toastBody("success", "已回写 2 条选择，制作方向卡稍后发送"));
  const sends = calls.filter((call) => call.path.includes("/im/v1/messages"));
  assert.equal(sends.length, 0);
  const puts = calls.filter((call) => call.method === "PUT");
  assert.equal(puts.length, 2);
  assert.equal(puts[0].body.fields["制作方向卡状态"], "待发送");
  assert.match(puts[0].body.fields["选择提交批次"], /^run_1:/);
  assert.ok(puts[0].body.fields["选择提交时间"]);
  assert.equal(puts[1].body.fields["制作方向卡状态"], undefined);
});

test("uses candidate snapshots to skip full-table reads on selection submit", async () => {
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
          value: {
            action: "submit_topic_decisions",
            run_id: "run_1",
            candidate_ids: ["rec_a", "rec_b"],
            candidate_snapshots: {
              rec_a: { title: "A", brief: "A brief", experiment: "A experiment", run_id: "run_1" },
              rec_b: { title: "B", run_id: "run_1" },
            },
          },
          form_value: {
            enter_brief_records: ["rec_a"],
            positive_reason_tags: ["证据够"],
            manual_reason: "",
          },
        },
      },
    },
    {
      ...env,
      SEND_PRODUCTION_DIRECTION_CARD: "true",
      FEISHU_CARD_RECEIVE_TARGETS: "open_id:ou_follow",
    },
    { fetchImpl: makeMockFetch(records, calls) },
  );
  assert.deepEqual(await response.json(), toastBody("success", "已回写 2 条选择，制作方向卡稍后发送"));
  assert.equal(calls.filter((call) => call.path.includes("/records?")).length, 0);
  assert.equal(calls.filter((call) => call.method === "PUT").length, 2);
  const sends = calls.filter((call) => call.path.includes("/im/v1/messages"));
  assert.equal(sends.length, 0);
});

test("sends queued production direction cards from explicit queue", async () => {
  const records = [
    {
      record_id: "rec_a",
      fields: {
        "选题标题": "A",
        "一句话Brief": "A brief",
        "我要做的实验": "A experiment",
        "运行批次": "run_1",
        "状态": "进入Brief",
        "制作方向卡状态": "待发送",
        "选择提交批次": "run_1:abc",
        "选择提交时间": "2026-06-29T01:00:00.000Z",
      },
    },
    {
      record_id: "rec_b",
      fields: {
        "选题标题": "B",
        "运行批次": "run_1",
        "状态": "不做",
        "制作方向卡状态": "待发送",
        "选择提交批次": "run_1:abc",
        "选择提交时间": "2026-06-29T01:00:00.000Z",
      },
    },
  ];
  const calls = [];
  const response = await handlePayload(
    { action: "send_pending_production_direction_cards" },
    { ...env, SEND_PRODUCTION_DIRECTION_CARD: "true", FEISHU_CARD_RECEIVE_TARGETS: "open_id:ou_follow" },
    { fetchImpl: makeMockFetch(records, calls), nowMs: Date.parse("2026-06-29T02:00:00.000Z") },
  );
  const body = await response.json();
  assert.equal(body.ok, true);
  assert.equal(body.sent.length, 1);
  assert.equal(body.sent[0].record_count, 1);
  const sends = calls.filter((call) => call.path.includes("/im/v1/messages"));
  assert.equal(sends.length, 1);
  const cardText = JSON.stringify(JSON.parse(sends[0].body.content));
  assert.match(cardText, /A experiment/);
  assert.match(cardText, /production_direction__rec_a/);
  assert.doesNotMatch(cardText, /production_direction__rec_b/);
  const puts = calls.filter((call) => call.method === "PUT");
  assert.equal(puts.filter((call) => call.body.fields["制作方向卡状态"] === "发送中").length, 1);
  assert.equal(puts.filter((call) => call.body.fields["制作方向卡状态"] === "已发送").length, 1);
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
  assert.deepEqual(await response.json(), toastBody("success", "已保存 1 条制作方向"));
  const puts = calls.filter((call) => call.method === "PUT");
  assert.equal(puts.length, 1);
  assert.deepEqual(puts[0].body.fields, {
    "我的制作补充": "用 AI账号信息雷达案例讲，重点讲选题判断",
  });
});

test("blocks expired cards before writing records", async () => {
  const records = [
    { record_id: "rec_a", fields: { "选题标题": "A", "运行批次": "run_1", "状态": "待判断" } },
  ];
  const calls = [];
  const response = await handlePayload(
    {
      header: { token: "verify_test" },
      event: {
        action: {
          value: {
            action: "submit_topic_decisions",
            run_id: "run_1",
            candidate_ids: ["rec_a"],
            card_issued_at: "2026-06-01T00:00:00.000Z",
            card_expires_at: "2026-06-06T00:00:00.000Z",
          },
          form_value: {
            enter_brief_records: ["rec_a"],
            positive_reason_tags: ["证据够"],
            manual_reason: "",
          },
        },
      },
    },
    env,
    { fetchImpl: makeMockFetch(records, calls), nowMs: Date.parse("2026-06-07T00:00:00.000Z") },
  );
  assert.deepEqual(await response.json(), toastBody("warning", "这张卡已超过 5 天，不再处理，请使用最新卡片"));
  assert.equal(calls.filter((call) => call.method === "PUT").length, 0);
});

test("blocks a production direction card after direction has already been saved", async () => {
  const records = [
    {
      record_id: "rec_a",
      fields: {
        "选题标题": "A",
        "运行批次": "run_1",
        "状态": "进入Brief",
        "我的制作补充": "已有方向",
      },
    },
  ];
  const calls = [];
  const response = await handlePayload(
    {
      header: { token: "verify_test" },
      event: {
        action: {
          value: { action: "submit_production_directions", run_id: "run_1", candidate_ids: ["rec_a"] },
          form_value: {
            production_direction__rec_a: "新的方向",
          },
        },
      },
    },
    env,
    { fetchImpl: makeMockFetch(records, calls) },
  );
  assert.deepEqual(await response.json(), toastBody("warning", "这张制作方向卡已经保存过，不再重复处理"));
  assert.equal(calls.filter((call) => call.method === "PUT").length, 0);
});

test("blocks a selection card after it has already changed record status", async () => {
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
  assert.deepEqual(await response.json(), toastBody("warning", "这张选题卡已经提交过，不再重复处理"));
  assert.equal(calls.filter((call) => call.method === "PUT").length, 0);
});
