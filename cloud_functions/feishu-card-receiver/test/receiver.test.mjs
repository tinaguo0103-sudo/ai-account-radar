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
  assert.deepEqual(await response.json(), toastBody("success", "已回写 2 条选择"));
  const puts = calls.filter((call) => call.method === "PUT");
  assert.equal(puts.length, 2);
  assert.deepEqual(puts[0].body.fields, {
    "状态": "生成脚本包",
    "学习状态": "待学习",
    "选择原因标签": "证据够",
    "人工一句话判断": "测试原因",
  });
  assert.deepEqual(puts[1].body.fields, {
    "状态": "不做",
    "学习状态": "待学习",
    "选择原因标签": "",
    "人工一句话判断": "",
  });
});

test("preserves reason tags as array when field is multi-select", async () => {
  const records = [
    { record_id: "rec_a", fields: { "选题标题": "A", "运行批次": "run_1", "状态": "待判断" } },
  ];
  const calls = [];
  const fetchImpl = async (url, init = {}) => {
    const parsed = new URL(url);
    const path = parsed.pathname + parsed.search;
    calls.push({ method: init.method || "GET", path, body: init.body ? JSON.parse(init.body) : undefined });
    if (path.endsWith("/auth/v3/tenant_access_token/internal")) {
      return Response.json({ code: 0, tenant_access_token: "tenant_test" });
    }
    if (path.includes("/bitable/v1/apps/base_test/tables") && !path.includes("/records") && !path.includes("/fields")) {
      return Response.json({ code: 0, data: { items: [{ name: "04 分析与选题", table_id: "tbl_topic" }] } });
    }
    if (path.endsWith("/fields")) {
      return Response.json({ code: 0, data: { items: [{ field_name: "选择原因标签", type: 4 }] } });
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
    if (path.includes("/records/")) {
      return Response.json({ code: 0, data: {} });
    }
    return Response.json({ code: 999, msg: `unexpected path ${path}` }, { status: 500 });
  };
  const response = await handlePayload(
    {
      header: { token: "verify_test" },
      event: {
        action: {
          value: { action: "submit_topic_decisions", run_id: "run_1", candidate_ids: ["rec_a"] },
          form_value: {
            script_package_records: ["rec_a"],
            positive_reason_tags: ["证据够"],
          },
        },
      },
    },
    env,
    { fetchImpl },
  );
  assert.deepEqual(await response.json(), toastBody("success", "已回写 1 条选择"));
  const put = calls.find((call) => call.method === "PUT");
  assert.deepEqual(put.body.fields["选择原因标签"], ["证据够"]);
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
  assert.deepEqual(await response.json(), toastBody("success", "已回写 2 条选择，制作方向卡稍后发送"));
  assert.equal(calls.filter((call) => call.path.includes("/records?")).length, 0);
  assert.equal(calls.filter((call) => call.method === "PUT").length, 2);
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
  assert.deepEqual(await response.json(), toastBody("success", "已回写 2 条选择"));
  const puts = calls.filter((call) => call.method === "PUT");
  assert.equal(puts.length, 2);
  assert.equal(puts[0].path, "/open-apis/bitable/v1/apps/base_test/tables/tbl_topic/records/rec_old");
  assert.equal(puts[0].body.fields["状态"], "生成脚本包");
  assert.equal(puts[1].path, "/open-apis/bitable/v1/apps/base_test/tables/tbl_topic/records/rec_today");
  assert.equal(puts[1].body.fields["状态"], "不做");
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

test("submits script package quality feedback to 06 table", async () => {
  const records = [
    { record_id: "pkg_a", fields: { "脚本标题": "A 脚本包" } },
    { record_id: "pkg_b", fields: { "脚本标题": "B 脚本包" } },
  ];
  const calls = [];
  const response = await handlePayload(
    {
      header: { token: "verify_test" },
      event: {
        action: {
          value: {
            action: "submit_script_package_quality_feedback",
            candidate_ids: ["pkg_a", "pkg_b"],
          },
          form_value: {
            script_quality__pkg_a: "小修可拍",
            script_issues__pkg_a: ["不像我", "旧流程痛点不准"],
            script_note__pkg_a: "第二段还不够像我的现场判断。",
            script_note__pkg_a__2: "要补具体项目里的旧流程卡点。",
            script_note__pkg_a__3: "标题也可以更直接一点。",
            script_quality__pkg_b: "",
            script_issues__pkg_b: [],
            script_note__pkg_b: "",
          },
        },
      },
    },
    env,
    { fetchImpl: makeMockFetch(records, calls), nowMs: Date.parse("2026-07-02T12:00:00.000Z") },
  );
  assert.deepEqual(await response.json(), toastBody("success", "已保存 1 条质量反馈"));
  const puts = calls.filter((call) => call.method === "PUT");
  assert.equal(puts.length, 1);
  assert.equal(puts[0].path, "/open-apis/bitable/v1/apps/base_test/tables/tbl_script_package/records/pkg_a");
  assert.deepEqual(puts[0].body.fields, {
    "人工质量反馈": "小修可拍",
    "质量问题标签": "不像我、旧流程痛点不准",
    "人工修改意见": "第二段还不够像我的现场判断。 要补具体项目里的旧流程卡点。 标题也可以更直接一点。",
    "反馈时间": "2026-07-02T12:00:00.000Z",
    "反馈来源": "06完成卡",
    "内容学习状态": "待学习",
  });
});

test("confirms learning feedback and marks source records learned", async () => {
  const records = [
    { record_id: "learn_a", fields: { "学习批次": "learn_1", "确认状态": "待确认" } },
    { record_id: "topic_a", fields: { "选题标题": "A", "学习状态": "待确认学习" } },
    { record_id: "pkg_a", fields: { "脚本标题": "A 脚本包", "内容学习状态": "待确认学习" } },
  ];
  const calls = [];
  const response = await handlePayload(
    {
      header: { token: "verify_test" },
      event: {
        action: {
          value: {
            action: "submit_learning_feedback_confirmation",
            decision: "部分采纳",
            learning_record_id: "learn_a",
            learning_batch_id: "learn_1",
            topic_record_ids: ["topic_a"],
            script_record_ids: ["pkg_a"],
            learning_summary: "选题偏好需要保留，06 问题先人工复核。",
          },
          form_value: {
            learning_confirmation_note: "采纳选题规则。",
            learning_confirmation_note__2: "06 规则先别自动同步。",
          },
        },
      },
    },
    {
      ...env,
      FEISHU_TOPIC_DECISION_TABLE_ID: "tbl_topic",
      FEISHU_SCRIPT_PACKAGE_TABLE_ID: "tbl_script_package",
      FEISHU_LEARNING_TABLE_ID: "tbl_learning",
    },
    { fetchImpl: makeMockFetch(records, calls), nowMs: Date.parse("2026-07-02T13:00:00.000Z") },
  );
  assert.deepEqual(await response.json(), toastBody("success", "已确认学习日结：部分采纳"));
  const puts = calls.filter((call) => call.method === "PUT");
  assert.equal(puts.length, 3);
  assert.equal(puts[0].path, "/open-apis/bitable/v1/apps/base_test/tables/tbl_learning/records/learn_a");
  assert.deepEqual(puts[0].body.fields, {
    "确认状态": "部分采纳",
    "确认时间": "2026-07-02T13:00:00.000Z",
    "确认备注": "采纳选题规则。 06 规则先别自动同步。",
    "Skill同步状态": "待同步",
  });
  assert.deepEqual(puts[1].body.fields, {
    "学习状态": "已学习",
    "选择学习批次": "learn_1",
    "选择学习摘要": "选题偏好需要保留，06 问题先人工复核。",
  });
  assert.deepEqual(puts[2].body.fields, {
    "内容学习状态": "已学习",
    "内容学习批次": "learn_1",
    "内容学习摘要": "选题偏好需要保留，06 问题先人工复核。",
  });
});

test("rejected learning feedback is ignored without marking sources learned", async () => {
  const records = [
    { record_id: "learn_a", fields: { "学习批次": "learn_1", "确认状态": "待确认" } },
    { record_id: "topic_a", fields: { "选题标题": "A", "学习状态": "待确认学习" } },
    { record_id: "pkg_a", fields: { "脚本标题": "A 脚本包", "内容学习状态": "待确认学习" } },
  ];
  const calls = [];
  const response = await handlePayload(
    {
      header: { token: "verify_test" },
      event: {
        action: {
          value: {
            action: "submit_learning_feedback_confirmation",
            decision: "暂不采纳",
            learning_record_id: "learn_a",
            learning_batch_id: "learn_1",
            topic_record_ids: ["topic_a"],
            script_record_ids: ["pkg_a"],
            learning_summary: "样本不足。",
          },
          form_value: {},
        },
      },
    },
    {
      ...env,
      FEISHU_TOPIC_DECISION_TABLE_ID: "tbl_topic",
      FEISHU_SCRIPT_PACKAGE_TABLE_ID: "tbl_script_package",
      FEISHU_LEARNING_TABLE_ID: "tbl_learning",
    },
    { fetchImpl: makeMockFetch(records, calls), nowMs: Date.parse("2026-07-02T13:00:00.000Z") },
  );
  assert.deepEqual(await response.json(), toastBody("success", "已确认学习日结：暂不采纳"));
  const puts = calls.filter((call) => call.method === "PUT");
  assert.equal(puts.length, 3);
  assert.equal(puts[0].body.fields["Skill同步状态"], "不同步");
  assert.equal(puts[1].body.fields["学习状态"], "忽略");
  assert.equal(puts[2].body.fields["内容学习状态"], "忽略");
});

test("blocks learning feedback after it has already been confirmed", async () => {
  const records = [
    { record_id: "learn_a", fields: { "学习批次": "learn_1", "确认状态": "已采纳" } },
    { record_id: "topic_a", fields: { "选题标题": "A", "学习状态": "待确认学习" } },
  ];
  const calls = [];
  const response = await handlePayload(
    {
      header: { token: "verify_test" },
      event: {
        action: {
          value: {
            action: "submit_learning_feedback_confirmation",
            decision: "已采纳",
            learning_record_id: "learn_a",
            learning_batch_id: "learn_1",
            topic_record_ids: ["topic_a"],
          },
          form_value: {},
        },
      },
    },
    {
      ...env,
      FEISHU_TOPIC_DECISION_TABLE_ID: "tbl_topic",
      FEISHU_LEARNING_TABLE_ID: "tbl_learning",
    },
    { fetchImpl: makeMockFetch(records, calls) },
  );
  assert.deepEqual(await response.json(), toastBody("warning", "这条学习日结已经确认过，不再重复处理"));
  assert.equal(calls.filter((call) => call.method === "PUT").length, 0);
});

test("blocks staging learning feedback without explicit test table ids", async () => {
  const records = [
    { record_id: "learn_a", fields: { "学习批次": "learn_1", "确认状态": "待确认" } },
    { record_id: "topic_a", fields: { "选题标题": "A", "学习状态": "待确认学习" } },
  ];
  const calls = [];
  const response = await handlePayload(
    {
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
          },
          form_value: {},
        },
      },
    },
    env,
    { fetchImpl: makeMockFetch(records, calls) },
  );
  assert.deepEqual(await response.json(), toastBody("warning", "测试学习卡缺少显式测试表配置，已拒绝回写"));
  assert.equal(calls.filter((call) => call.method === "PUT").length, 0);
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
  assert.deepEqual(await response.json(), toastBody("warning", "这张选题卡已经提交过，不再重复处理"));
  assert.equal(calls.filter((call) => call.method === "PUT").length, 0);
});
