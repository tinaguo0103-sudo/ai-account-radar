import test from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { FULL_PACKAGE_FIELD, renderPackage, runScriptPackageJob } = require("../src/script_package_runner.cjs");

const env = {
  FEISHU_APP_ID: "cli_test_app",
  FEISHU_APP_SECRET: "cli_test_secret",
  FEISHU_BASE_APP_TOKEN: "base_test",
  FEISHU_TOPIC_TABLE_ID: "tbl_topic",
  FEISHU_SCRIPT_PACKAGE_TABLE_ID: "tbl_script",
  AUSTIN_SCRIPT_PACKAGE_LIMIT: "3",
};

const topicRecords = [
  {
    record_id: "rec_ready",
    fields: {
      "状态": "进入Brief",
      "选题标题": "把Claude Code团队原则拆成我的AI项目验收表",
      "一句话Brief": "把 Claude Code 的团队原则变成我自己的 AI 项目验收动作。",
      "我的工作流痛点": "Agent 跑完以后，我经常不知道应该看结果、过程还是异常。",
      "旧流程痛点": "旧流程靠人工感觉验收，失败原因没有沉淀。",
      "AI介入点": "让 AI 先生成验收字段，再用一条真实任务测试是否能追责。",
      "可沉淀资产": "AI项目验收表",
      "我的思考点": "原则不能收藏，要变成验收动作。",
      "可展示证据": "旧验收表截图；新验收字段截图",
      "需要补的证据": "一段从任务输入到验收表的录屏",
    },
  },
  {
    record_id: "rec_done",
    fields: {
      "状态": "进入Brief",
      "选题标题": "已生成的题",
      "是否已生成脚本稿": "是",
    },
  },
  {
    record_id: "rec_drop",
    fields: {
      "状态": "不做",
      "选题标题": "不做的题",
    },
  },
];

function makeMockFetch(records, calls) {
  return async (url, init = {}) => {
    const parsed = new URL(url);
    const path = parsed.pathname + parsed.search;
    const method = init.method || "GET";
    calls.push({ method, path, body: init.body ? JSON.parse(init.body) : undefined });
    if (path.endsWith("/auth/v3/tenant_access_token/internal")) {
      return Response.json({ code: 0, tenant_access_token: "tenant_test" });
    }
    if (path === "/open-apis/bitable/v1/apps/base_test/tables") {
      return Response.json({
        code: 0,
        data: {
          items: [
            { name: "04 分析与选题", table_id: "tbl_topic" },
            { name: "06 完整脚本与制作包", table_id: "tbl_script" },
          ],
        },
      });
    }
    if (path.startsWith("/open-apis/bitable/v1/apps/base_test/tables/tbl_topic/records?")) {
      return Response.json({ code: 0, data: { has_more: false, items: records } });
    }
    if (path.endsWith("/fields") && method === "GET") {
      return Response.json({ code: 0, data: { items: [] } });
    }
    if (path.endsWith("/fields") && method === "POST") {
      return Response.json({ code: 0, data: {} });
    }
    if (path === "/open-apis/bitable/v1/apps/base_test/tables/tbl_script/records" && method === "POST") {
      return Response.json({ code: 0, data: { record: { record_id: "rec_script" } } });
    }
    if (path === "/open-apis/bitable/v1/apps/base_test/tables/tbl_topic/records/rec_ready" && method === "PUT") {
      return Response.json({ code: 0, data: {} });
    }
    return Response.json({ code: 999, msg: `unexpected path ${method} ${path}` }, { status: 500 });
  };
}

test("renders a cloud script package with full markdown field", () => {
  const rendered = renderPackage(topicRecords[0]);
  assert.equal(rendered.topic.topic_title, "把Claude Code团队原则拆成我的AI项目验收表");
  assert.equal(rendered.row["关联选题"], "把Claude Code团队原则拆成我的AI项目验收表");
  assert.match(rendered.row[FULL_PACKAGE_FIELD], /# 把Claude Code团队原则拆成我的AI项目验收表/);
  assert.match(rendered.row[FULL_PACKAGE_FIELD], /## 口播全文/);
  assert.match(rendered.row["本地文档"], /腾讯云SCF生成/);
});

test("dry-run scans ready records without writing", async () => {
  const calls = [];
  const result = await runScriptPackageJob(
    { dry_run: true },
    env,
    { fetchImpl: makeMockFetch(topicRecords, calls) },
  );
  assert.equal(result.mode, "dry-run");
  assert.equal(result.ready_topics, 1);
  assert.equal(result.created_script_packages, 0);
  assert.equal(calls.some((call) => call.method === "POST" && call.path.endsWith("/records")), false);
  assert.equal(calls.some((call) => call.method === "PUT"), false);
});

test("write mode creates 06 record and marks 04 as generated", async () => {
  const calls = [];
  const result = await runScriptPackageJob(
    { limit: 1 },
    env,
    { fetchImpl: makeMockFetch(topicRecords, calls) },
  );
  assert.equal(result.mode, "write");
  assert.equal(result.ready_topics, 1);
  assert.equal(result.created_script_packages, 1);
  assert.equal(result.marked_topics, 1);
  const create = calls.find((call) => call.method === "POST" && call.path.endsWith("/tbl_script/records"));
  assert.ok(create);
  assert.match(create.body.fields[FULL_PACKAGE_FIELD], /## QA 报告/);
  const update = calls.find((call) => call.method === "PUT" && call.path.endsWith("/tbl_topic/records/rec_ready"));
  assert.deepEqual(update.body.fields, { "是否已生成脚本稿": "是" });
});
