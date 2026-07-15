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

function makeMockFetch(records, calls, { fieldPages } = {}) {
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
    if (path.includes("/fields") && (init.method || "GET") === "GET") {
      const pageToken = parsed.searchParams.get("page_token") || "";
      const pages = fieldPages || { "": { items: [{ field_name: "选择原因标签", type: 1 }], has_more: false } };
      const page = pages[pageToken] || { items: [], has_more: false };
      return Response.json({ code: 0, data: page });
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

function makeStatefulMockFetch(records, calls, { failPutOnceFor = "" } = {}) {
  let failed = false;
  return async (url, init = {}) => {
    const parsed = new URL(url);
    const path = parsed.pathname + parsed.search;
    const body = init.body ? JSON.parse(init.body) : undefined;
    calls.push({ method: init.method || "GET", path, body });
    if (path.endsWith("/auth/v3/tenant_access_token/internal")) return Response.json({ code: 0, tenant_access_token: "tenant_test" });
    if (path.includes("/bitable/v1/apps/base_test/tables") && !path.includes("/records") && !path.includes("/fields")) {
      return Response.json({ code: 0, data: { items: [{ name: "04 分析与选题", table_id: "tbl_topic" }] } });
    }
    const recordMatch = path.match(/\/records\/([^/?]+)$/);
    if (recordMatch && (init.method || "GET") === "GET") {
      const record = records.find((item) => item.record_id === decodeURIComponent(recordMatch[1]));
      if (!record) return Response.json({ code: 1254045, msg: "record not found" }, { status: 404 });
      return Response.json({ code: 0, data: { record } });
    }
    if (recordMatch && (init.method || "GET") === "PUT") {
      const recordId = decodeURIComponent(recordMatch[1]);
      if (!failed && recordId === failPutOnceFor) {
        failed = true;
        return Response.json({ code: 999, msg: "synthetic sequential failure" }, { status: 500 });
      }
      const record = records.find((item) => item.record_id === recordId);
      Object.assign(record.fields, body.fields);
      return Response.json({ code: 0, data: { record } });
    }
    if (path.includes("/fields") && (init.method || "GET") === "GET") return Response.json({ code: 0, data: { items: [{ field_name: "选择原因标签", type: 1 }], has_more: false } });
    return Response.json({ code: 999, msg: `unexpected path ${path}` }, { status: 500 });
  };
}

function makeFilterRejectingFetch(records, calls) {
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
    if (path.includes("/records?") && path.includes("filter=")) {
      return Response.json({ code: 1254018, msg: "InvalidFilter" }, { status: 400 });
    }
    if (path.includes("/records?")) {
      return Response.json({ code: 0, data: { has_more: false, items: records } });
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

function makeHangingDirectionMessageFetch(records, calls) {
  const baseFetch = makeMockFetch(records, calls);
  return async (url, init = {}) => {
    const parsed = new URL(url);
    const path = parsed.pathname + parsed.search;
    if (path.includes("/im/v1/messages")) {
      calls.push({ method: init.method || "GET", path, body: init.body ? JSON.parse(init.body) : undefined });
      return new Promise((resolve, reject) => {
        if (init.signal) {
          init.signal.addEventListener("abort", () => reject(new Error("aborted by timeout")), { once: true });
        }
      });
    }
    return baseFetch(url, init);
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

test("normal submit updates only explicitly selected candidate records", async () => {
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
            script_package_records: ["rec_a"],
            positive_reason_tags: ["证据够"],
            manual_reason: "测试原因",
          },
        },
      },
    },
    env,
    { fetchImpl: makeMockFetch(records, calls) },
  );
  assert.deepEqual(await response.json(), toastBody("success", "已回写 1 条选择"));
  const puts = calls.filter((call) => call.method === "PUT");
  assert.equal(puts.length, 1);
  assert.deepEqual(puts[0].body.fields, {
    "状态": "生成脚本包",
    "学习状态": "待学习",
    "选择原因标签": "证据够",
    "人工一句话判断": "测试原因",
  });
  assert.equal(records[1].fields["状态"], "待判断");
});

test("serializes reason tags according to strict Feishu field metadata", async () => {
  const cases = [
    { name: "text empty", type: 1, tags: [], expected: "" },
    { name: "text joined", type: 1, tags: ["证据够", "判断够强"], expected: "证据够、判断够强" },
    { name: "multi empty", type: 4, tags: [], expected: [] },
    { name: "multi array", type: 4, tags: ["证据够", "判断够强"], expected: ["证据够", "判断够强"] },
  ];
  for (const item of cases) {
    const records = [{ record_id: "rec_a", fields: { "选题标题": "A", "运行批次": "run_1", "状态": "待判断" } }];
    const calls = [];
    const response = await handlePayload(
      { header: { token: "verify_test" }, event: { action: { value: { action: "submit_topic_decisions", run_id: "run_1", candidate_ids: ["rec_a"] }, form_value: { script_package_records: ["rec_a"], positive_reason_tags: item.tags } } } },
      env,
      { fetchImpl: makeMockFetch(records, calls, { fieldPages: { "": { items: [{ field_name: "选择原因标签", type: item.type }], has_more: false } } }) },
    );
    assert.deepEqual(await response.json(), toastBody("success", "已回写 1 条选择"), item.name);
    const put = calls.find((call) => call.method === "PUT");
    assert.deepEqual(put.body.fields["选择原因标签"], item.expected, item.name);
  }
});

test("fails before writes when reason field metadata is missing or unsupported", async () => {
  for (const [name, items, message] of [
    ["missing", [], "Missing required field metadata: 选择原因标签"],
    ["unsupported", [{ field_name: "选择原因标签", type: 3 }], "Unsupported 选择原因标签 field type: 3"],
  ]) {
    const records = [{ record_id: "rec_a", fields: { "选题标题": "A", "运行批次": "run_1", "状态": "待判断" } }];
    const calls = [];
    const response = await handlePayload(
      { header: { token: "verify_test" }, event: { action: { value: { action: "submit_topic_decisions", run_id: "run_1", candidate_ids: ["rec_a"] }, form_value: { script_package_records: ["rec_a"] } } } },
      env,
      { fetchImpl: makeMockFetch(records, calls, { fieldPages: { "": { items, has_more: false } } }) },
    );
    assert.deepEqual(await response.json(), toastBody("error", `回写失败：${message}`), name);
    assert.equal(calls.filter((call) => call.method === "PUT").length, 0, name);
  }
});

test("finds reason metadata on a later field page", async () => {
  const records = [{ record_id: "rec_a", fields: { "选题标题": "A", "运行批次": "run_1", "状态": "待判断" } }];
  const calls = [];
  const firstPage = Array.from({ length: 58 }, (_, index) => ({ field_name: `字段${index + 1}`, type: 1 }));
  const response = await handlePayload(
    { header: { token: "verify_test" }, event: { action: { value: { action: "submit_topic_decisions", run_id: "run_1", candidate_ids: ["rec_a"] }, form_value: { script_package_records: ["rec_a"], positive_reason_tags: ["后页标签"] } } } },
    env,
    { fetchImpl: makeMockFetch(records, calls, { fieldPages: { "": { items: firstPage, has_more: true, page_token: "page2" }, page2: { items: [{ field_name: "选择原因标签", type: 1 }], has_more: false } } }) },
  );
  assert.deepEqual(await response.json(), toastBody("success", "已回写 1 条选择"));
  const fieldReads = calls.filter((call) => call.method === "GET" && call.path.includes("/fields?"));
  assert.equal(fieldReads.length, 2);
  assert.match(fieldReads[1].path, /page_token=page2/);
  assert.equal(calls.find((call) => call.method === "PUT").body.fields["选择原因标签"], "后页标签");
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
            script_package_records: ["rec_a"],
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
  assert.deepEqual(await response.json(), toastBody("success", "已回写 1 条选择，制作方向卡稍后发送"));
  const sends = calls.filter((call) => call.path.includes("/im/v1/messages"));
  assert.equal(sends.length, 0);
  const puts = calls.filter((call) => call.method === "PUT");
  assert.equal(puts.length, 1);
  assert.equal(puts[0].body.fields["制作方向卡状态"], "待发送");
  assert.match(puts[0].body.fields["选择提交批次"], /^run_1:/);
  assert.ok(puts[0].body.fields["选择提交时间"]);
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
            script_package_records: ["rec_a"],
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
  assert.deepEqual(await response.json(), toastBody("success", "已回写 1 条选择，制作方向卡稍后发送"));
  assert.equal(calls.filter((call) => call.path.includes("/records?")).length, 0);
  assert.equal(calls.filter((call) => call.method === "PUT").length, 1);
  const sends = calls.filter((call) => call.path.includes("/im/v1/messages"));
  assert.equal(sends.length, 0);
});

test("allows historical compensation candidates when card snapshots preserve the original run", async () => {
  const records = [
    { record_id: "rec_old", fields: { "选题标题": "历史候选", "运行批次": "run_old", "状态": "待判断" } },
    { record_id: "rec_today", fields: { "选题标题": "今日候选", "运行批次": "run_today", "状态": "待判断" } },
  ];
  const calls = [];
  const response = await handlePayload(
    {
      header: { token: "verify_test" },
      event: {
        action: {
          value: {
            action: "submit_topic_decisions",
            run_id: "run_today",
            candidate_ids: ["rec_old", "rec_today"],
            candidate_snapshots: {
              rec_old: { title: "历史候选", run_id: "run_old", date: "2026-07-02" },
              rec_today: { title: "今日候选", run_id: "run_today", date: "2026-07-04" },
            },
          },
          form_value: {
            script_package_records: ["rec_old"],
            positive_reason_tags: ["证据够"],
            manual_reason: "补发历史候选",
          },
        },
      },
    },
    { ...env, SEND_PRODUCTION_DIRECTION_CARD: "false" },
    { fetchImpl: makeMockFetch(records, calls) },
  );
  assert.deepEqual(await response.json(), toastBody("success", "已回写 1 条选择"));
  const puts = calls.filter((call) => call.method === "PUT");
  assert.equal(puts.length, 1);
  assert.equal(puts[0].path, "/open-apis/bitable/v1/apps/base_test/tables/tbl_topic/records/rec_old");
  assert.equal(puts[0].body.fields["状态"], "生成脚本包");
});

test("rejects historical compensation candidates when card snapshots are missing", async () => {
  const records = [
    { record_id: "rec_old", fields: { "选题标题": "历史候选", "运行批次": "run_old", "状态": "待判断" } },
    { record_id: "rec_today", fields: { "选题标题": "今日候选", "运行批次": "run_today", "状态": "待判断" } },
  ];
  const calls = [];
  const response = await handlePayload(
    {
      header: { token: "verify_test" },
      event: {
        action: {
          value: { action: "submit_topic_decisions", run_id: "run_today", candidate_ids: ["rec_old", "rec_today"] },
          form_value: {
            script_package_records: ["rec_old"],
            positive_reason_tags: ["证据够"],
            manual_reason: "补发历史候选",
          },
        },
      },
    },
    env,
    { fetchImpl: makeMockFetch(records, calls) },
  );
  const body = await response.json();
  assert.deepEqual(body, toastBody("warning", "这张卡对应的记录批次已变化，请使用最新卡片"));
  assert.equal(calls.filter((call) => call.method === "PUT").length, 0);
});

test("accepts production direction feedback for historical candidates with snapshots", async () => {
  const records = [
    { record_id: "rec_old", fields: { "选题标题": "历史候选", "运行批次": "run_old", "状态": "生成脚本包" } },
  ];
  const calls = [];
  const response = await handlePayload(
    {
      header: { token: "verify_test" },
      event: {
        action: {
          value: {
            action: "submit_production_directions",
            run_id: "run_today",
            candidate_ids: ["rec_old"],
            candidate_snapshots: {
              rec_old: { title: "历史候选", run_id: "run_old", date: "2026-07-02" },
            },
          },
          form_value: {
            production_direction__rec_old: "用真实项目复盘讲，不做工具教程。",
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
  assert.equal(puts[0].path, "/open-apis/bitable/v1/apps/base_test/tables/tbl_topic/records/rec_old");
  assert.deepEqual(puts[0].body.fields, {
    "我的制作补充": "用真实项目复盘讲，不做工具教程。",
    "制作方向卡状态": "已提交",
    "制作方向卡错误": "",
  });
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
        "状态": "生成脚本包",
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
  const filteredRead = calls.find((call) => call.path.includes("/records?") && call.path.includes("filter="));
  assert.ok(filteredRead);
  const filter = new URLSearchParams(filteredRead.path.split("?")[1]).get("filter");
  assert.match(filter, /CurrentValue\.\[制作方向卡状态\] = "待发送"/);
  assert.match(filter, /CurrentValue\.\[状态\] = "生成脚本包"/);
});

test("falls back to local queue filtering when Feishu rejects record filters", async () => {
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
        "选择提交时间": "2026-06-29T01:00:00.000Z",
      },
    },
    {
      record_id: "rec_b",
      fields: {
        "选题标题": "B",
        "运行批次": "run_1",
        "状态": "生成脚本包",
        "制作方向卡状态": "已发送",
        "选择提交批次": "run_1:abc",
        "选择提交时间": "2026-06-29T01:00:00.000Z",
      },
    },
  ];
  const calls = [];
  const response = await handlePayload(
    { action: "send_pending_production_direction_cards" },
    { ...env, SEND_PRODUCTION_DIRECTION_CARD: "true", FEISHU_CARD_RECEIVE_TARGETS: "open_id:ou_follow" },
    { fetchImpl: makeFilterRejectingFetch(records, calls), nowMs: Date.parse("2026-06-29T02:00:00.000Z") },
  );
  const body = await response.json();
  assert.equal(body.ok, true);
  assert.equal(body.sent.length, 1);
  assert.equal(body.sent[0].record_count, 1);
  assert.ok(calls.some((call) => call.path.includes("filter=")));
  assert.ok(calls.some((call) => call.path.includes("/records?page_size=500") && !call.path.includes("filter=")));
  const fallbackReads = calls.filter((call) => call.path.includes("/records?page_size=500") && !call.path.includes("filter="));
  assert.equal(fallbackReads.length, 1);
  const sends = calls.filter((call) => call.path.includes("/im/v1/messages"));
  assert.equal(sends.length, 1);
});

test("marks direction card send timeout as failed instead of leaving sending", { timeout: 1000 }, async () => {
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
        "选择提交时间": "2026-06-29T01:00:00.000Z",
      },
    },
  ];
  const calls = [];
  const response = await handlePayload(
    { action: "send_pending_production_direction_cards" },
    {
      ...env,
      SEND_PRODUCTION_DIRECTION_CARD: "true",
      FEISHU_CARD_RECEIVE_TARGETS: "open_id:ou_follow",
      FEISHU_API_TIMEOUT_MS: "5",
      FEISHU_PRODUCTION_DIRECTION_ALERTS: "false",
    },
    { fetchImpl: makeHangingDirectionMessageFetch(records, calls), nowMs: Date.parse("2026-06-29T02:00:00.000Z") },
  );
  const body = await response.json();
  assert.equal(body.ok, false);
  assert.equal(body.failed.length, 1);
  assert.match(body.failed[0].error, /request failed/);
  assert.match(body.failed[0].error, /timed out|aborted/);
  const puts = calls.filter((call) => call.method === "PUT");
  assert.equal(puts.filter((call) => call.body.fields["制作方向卡状态"] === "发送中").length, 1);
  const failedPut = puts.find((call) => call.body.fields["制作方向卡状态"] === "发送失败");
  assert.ok(failedPut);
  assert.match(failedPut.body.fields["制作方向卡错误"], /request failed/);
});

test("marks stale sending direction cards as failed and sends alert", async () => {
  const records = [
    {
      record_id: "rec_stuck",
      fields: {
        "选题标题": "卡住的选题",
        "运行批次": "run_1",
        "状态": "生成脚本包",
        "制作方向卡状态": "发送中",
        "选择提交批次": "run_1:abc",
        "选择提交时间": "2026-06-29T01:00:00.000Z",
      },
    },
  ];
  const calls = [];
  const response = await handlePayload(
    { action: "send_pending_production_direction_cards" },
    {
      ...env,
      SEND_PRODUCTION_DIRECTION_CARD: "true",
      FEISHU_CARD_RECEIVE_TARGETS: "open_id:ou_follow",
      FEISHU_AUTOMATION_NOTIFY_TARGETS: "open_id:ou_alert",
      FEISHU_DIRECTION_CARD_STUCK_MINUTES: "15",
    },
    { fetchImpl: makeMockFetch(records, calls), nowMs: Date.parse("2026-06-29T01:20:00.000Z") },
  );
  const body = await response.json();
  assert.equal(body.ok, false);
  assert.equal(body.stuck_count, 1);
  assert.equal(body.notification.sent_count, 1);
  const puts = calls.filter((call) => call.method === "PUT");
  assert.equal(puts.length, 1);
  assert.deepEqual(puts[0].body.fields, {
    "制作方向卡状态": "发送失败",
    "制作方向卡错误": "停留在发送中超过 15 分钟，可能上次定时发送中断",
  });
  const sends = calls.filter((call) => call.path.includes("/im/v1/messages"));
  assert.equal(sends.length, 1);
  assert.equal(sends[0].body.receive_id, "ou_alert");
  assert.equal(sends[0].body.msg_type, "text");
  const alertText = JSON.parse(sends[0].body.content).text;
  assert.match(alertText, /制作方向卡发送异常/);
  assert.match(alertText, /卡住的选题/);
});

test("submits production direction cards and marks empty directions as reviewed", async () => {
  const records = [
    { record_id: "rec_a", fields: { "选题标题": "A", "运行批次": "run_1", "状态": "生成脚本包" } },
    { record_id: "rec_b", fields: { "选题标题": "B", "运行批次": "run_1", "状态": "生成脚本包" } },
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
  assert.deepEqual(await response.json(), toastBody("success", "已保存 2 条制作方向"));
  const puts = calls.filter((call) => call.method === "PUT");
  assert.equal(puts.length, 2);
  assert.deepEqual(puts[0].body.fields, {
    "我的制作补充": "用 AI账号信息雷达案例讲，重点讲选题判断",
    "制作方向卡状态": "已提交",
    "制作方向卡错误": "",
  });
  assert.deepEqual(puts[1].body.fields, {
    "制作方向卡状态": "已提交",
    "制作方向卡错误": "",
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
            script_package_records: ["rec_a"],
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
        "状态": "生成脚本包",
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
        "状态": "生成脚本包",
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
            script_package_records: ["rec_a"],
            positive_reason_tags: ["证据够"],
            manual_reason: "测试原因",
          },
        },
      },
    },
    env,
    { fetchImpl: makeMockFetch(records, calls) },
  );
  assert.deepEqual(await response.json(), toastBody("warning", "这次提交已经处理过"));
  assert.equal(calls.filter((call) => call.method === "PUT").length, 0);
});

test("page no-selection updates exactly the explicit page candidates", async () => {
  const records = [
    ...Array.from({ length: 5 }, (_, index) => ({ record_id: `page1_${index + 1}`, fields: { "选题标题": `P1-${index + 1}`, "运行批次": "run_page1", "状态": "待判断" } })),
    { record_id: "supplement", fields: { "选题标题": "补证据", "运行批次": "run_page1", "状态": "待判断" } },
    { record_id: "page2_1", fields: { "选题标题": "P2-1", "运行批次": "run_page2", "状态": "待判断" } },
  ];
  const calls = [];
  const candidateIds = records.slice(0, 5).map((record) => record.record_id);
  const response = await handlePayload(
    { header: { token: "verify_test" }, event: { action: { value: { action: "submit_no_selection", run_id: "run_page1", candidate_ids: candidateIds }, form_value: {} } } },
    env,
    { fetchImpl: makeMockFetch(records, calls) },
  );
  assert.deepEqual(await response.json(), toastBody("success", "已回写 5 条选择"));
  const puts = calls.filter((call) => call.method === "PUT");
  assert.equal(puts.length, 5);
  assert.deepEqual(puts.map((call) => Object.keys(call.body.fields)), Array(5).fill(["状态"]));
  assert.ok(puts.every((call) => call.body.fields["状态"] === "不做"));
  assert.equal(records[5].fields["状态"], "待判断");
  assert.equal(records[6].fields["状态"], "待判断");
  assert.equal(calls.filter((call) => call.path.includes("/fields")).length, 0);
});

test("normal submit with no checked candidates is a visible no-op", async () => {
  const records = [{ record_id: "rec_a", fields: { "选题标题": "A", "运行批次": "run_1", "状态": "待判断" } }];
  const calls = [];
  const response = await handlePayload(
    { header: { token: "verify_test" }, event: { action: { value: { action: "submit_topic_decisions", run_id: "run_1", candidate_ids: ["rec_a"] }, form_value: { script_package_records: [] } } } },
    env,
    { fetchImpl: makeMockFetch(records, calls) },
  );
  assert.deepEqual(await response.json(), toastBody("warning", "请至少勾选一条；如需拒绝本页，请使用“本页都不选”"));
  assert.equal(calls.filter((call) => call.method === "PUT").length, 0);
});

test("normal submit rejects selected IDs outside the page before writes", async () => {
  const records = [{ record_id: "rec_a", fields: { "选题标题": "A", "运行批次": "run_1", "状态": "待判断" } }];
  const calls = [];
  const response = await handlePayload(
    { header: { token: "verify_test" }, event: { action: { value: { action: "submit_topic_decisions", run_id: "run_1", candidate_ids: ["rec_a"] }, form_value: { script_package_records: ["rec_outside"] } } } },
    env,
    { fetchImpl: makeMockFetch(records, calls) },
  );
  assert.deepEqual(await response.json(), toastBody("warning", "勾选项不属于当前页面，请刷新后重试"));
  assert.equal(calls.filter((call) => call.method === "PUT").length, 0);
});

test("selection preflight failure writes nothing and remains retryable", async () => {
  const records = [{ record_id: "rec_a", fields: { "选题标题": "A", "运行批次": "run_old", "状态": "待判断" } }];
  const calls = [];
  const payload = { header: { token: "verify_test" }, event: { action: { value: { action: "submit_topic_decisions", run_id: "run_new", candidate_ids: ["rec_a"] }, form_value: { script_package_records: ["rec_a"] } } } };
  const first = await handlePayload(payload, env, { fetchImpl: makeMockFetch(records, calls) });
  assert.deepEqual(await first.json(), toastBody("warning", "这张卡对应的记录批次已变化，请使用最新卡片"));
  assert.equal(calls.filter((call) => call.method === "PUT").length, 0);
  records[0].fields["运行批次"] = "run_new";
  const second = await handlePayload(payload, env, { fetchImpl: makeMockFetch(records, calls) });
  assert.deepEqual(await second.json(), toastBody("success", "已回写 1 条选择"));
  assert.equal(calls.filter((call) => call.method === "PUT").length, 1);
});

test("sequential write failure is receipt-free and retry converges without duplicate writes", async () => {
  const records = [
    { record_id: "rec_a", fields: { "选题标题": "A", "运行批次": "run_1", "状态": "待判断" } },
    { record_id: "rec_b", fields: { "选题标题": "B", "运行批次": "run_1", "状态": "待判断" } },
  ];
  const payload = { header: { token: "verify_test" }, event: { action: { value: { action: "submit_topic_decisions", run_id: "run_1", candidate_ids: ["rec_a", "rec_b"] }, form_value: { script_package_records: ["rec_a", "rec_b"] } } } };
  const calls = [];
  const statefulFetch = makeStatefulMockFetch(records, calls, { failPutOnceFor: "rec_b" });
  const first = await handlePayload(payload, { ...env, SEND_PRODUCTION_DIRECTION_CARD: "true" }, { fetchImpl: statefulFetch, nowMs: Date.parse("2026-07-14T01:00:00Z") });
  assert.equal((await first.json()).toast.type, "error");
  assert.equal(records[0].fields["状态"], "生成脚本包");
  assert.equal(records[1].fields["状态"], "待判断");

  const second = await handlePayload(payload, { ...env, SEND_PRODUCTION_DIRECTION_CARD: "true" }, { fetchImpl: statefulFetch, nowMs: Date.parse("2026-07-14T01:01:00Z") });
  assert.deepEqual(await second.json(), toastBody("success", "已回写 1 条选择，制作方向卡稍后发送"));
  assert.equal(records[1].fields["状态"], "生成脚本包");
  const successfulPutsAfterRetry = calls.filter((call) => call.method === "PUT" && call.path.endsWith("/rec_a"));
  assert.equal(successfulPutsAfterRetry.length, 1);

  const putCountBeforeDuplicate = calls.filter((call) => call.method === "PUT").length;
  const duplicate = await handlePayload(payload, { ...env, SEND_PRODUCTION_DIRECTION_CARD: "true" }, { fetchImpl: statefulFetch, nowMs: Date.parse("2026-07-14T01:02:00Z") });
  assert.deepEqual(await duplicate.json(), toastBody("warning", "这次提交已经处理过"));
  assert.equal(calls.filter((call) => call.method === "PUT").length, putCountBeforeDuplicate);
});
