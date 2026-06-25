const crypto = require("crypto");

const DEFAULT_API_HOST = "https://open.feishu.cn";
const TOPIC_TABLE_NAMES = ["04 分析与选题", "03 分析与选题"];
const ENTER_BRIEF_FORM_KEY = "enter_brief_records";
const SUBMIT_SELECTION_ACTION = "submit_topic_decisions";
const SUBMIT_NO_SELECTION_ACTION = "submit_no_selection";
const SUPPORTED_SUBMIT_ACTIONS = new Set([SUBMIT_SELECTION_ACTION, SUBMIT_NO_SELECTION_ACTION]);

function jsonResponse(body, statusCode = 200) {
  return {
    statusCode,
    headers: { "content-type": "application/json; charset=utf-8" },
    body: JSON.stringify(body),
  };
}

function toast(type, content) {
  return jsonResponse({ toast: { type, content } });
}

function normalize(value) {
  if (value == null) return "";
  if (Array.isArray(value)) return value.map((item) => normalize(item)).filter(Boolean).join("、");
  if (typeof value === "object") return String(value.text || "").trim();
  return String(value).trim();
}

function compact(value, limit = 240) {
  const text = normalize(value).replace(/\s+/g, " ");
  return text.length > limit ? `${text.slice(0, limit).trimEnd()}...` : text;
}

function coerceList(value) {
  if (value == null || value === "") return [];
  if (Array.isArray(value)) return value.map((item) => normalize(item)).filter(Boolean);
  return [normalize(value)].filter(Boolean);
}

function canonicalValue(value) {
  if (Array.isArray(value)) return value.map(canonicalValue).sort();
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.keys(value)
        .sort()
        .map((key) => [key, canonicalValue(value[key])]),
    );
  }
  return value;
}

function sha256(text) {
  return crypto.createHash("sha256").update(text).digest("hex");
}

function submissionFingerprint(actionName, runId, candidateIds, formValue) {
  const payload = {
    action: actionName,
    run_id: runId,
    candidate_ids: [...candidateIds].sort(),
    form_value: canonicalValue(formValue),
  };
  return sha256(JSON.stringify(payload));
}

function envValue(key) {
  return process.env[key] || "";
}

function apiBaseUrl() {
  const host = String(envValue("FEISHU_API_BASE_URL") || DEFAULT_API_HOST).replace(/\/+$/, "");
  return host.endsWith("/open-apis") ? host : `${host}/open-apis`;
}

async function requestJson(method, path, { token = "", body = undefined } = {}) {
  const headers = { "content-type": "application/json; charset=utf-8" };
  if (token) headers.authorization = `Bearer ${token}`;
  const response = await fetch(`${apiBaseUrl()}${path}`, {
    method,
    headers,
    body: body == null ? undefined : JSON.stringify(body),
  });
  const text = await response.text();
  let payload;
  try {
    payload = text ? JSON.parse(text) : {};
  } catch (error) {
    throw new Error(`${method} ${path} returned non-JSON response: ${text.slice(0, 300)}`);
  }
  if (!response.ok) {
    throw new Error(`${method} ${path} failed: HTTP ${response.status} ${text.slice(0, 500)}`);
  }
  if (payload.code && payload.code !== 0) {
    throw new Error(`${method} ${path} failed: ${JSON.stringify(payload)}`);
  }
  return payload;
}

async function tenantToken() {
  if (!envValue("FEISHU_APP_ID") || !envValue("FEISHU_APP_SECRET")) {
    throw new Error("Missing FEISHU_APP_ID or FEISHU_APP_SECRET");
  }
  const payload = await requestJson("POST", "/auth/v3/tenant_access_token/internal", {
    body: {
      app_id: envValue("FEISHU_APP_ID"),
      app_secret: envValue("FEISHU_APP_SECRET"),
    },
  });
  const token = payload.tenant_access_token;
  if (!token) throw new Error("Feishu did not return tenant_access_token");
  return token;
}

async function listTables(token) {
  const payload = await requestJson("GET", `/bitable/v1/apps/${envValue("FEISHU_BASE_APP_TOKEN")}/tables`, {
    token,
  });
  return payload.data?.items || [];
}

async function topicTableId(token) {
  if (envValue("FEISHU_TOPIC_TABLE_ID")) return envValue("FEISHU_TOPIC_TABLE_ID");
  const tables = await listTables(token);
  const found = tables.find((table) => TOPIC_TABLE_NAMES.includes(table.name));
  if (!found?.table_id) throw new Error(`Missing topic table. Expected one of: ${TOPIC_TABLE_NAMES.join(", ")}`);
  return found.table_id;
}

async function allRecords(token, tableId) {
  const records = [];
  let pageToken = "";
  while (true) {
    const query = new URLSearchParams({ page_size: "500" });
    if (pageToken) query.set("page_token", pageToken);
    const payload = await requestJson(
      "GET",
      `/bitable/v1/apps/${envValue("FEISHU_BASE_APP_TOKEN")}/tables/${tableId}/records?${query}`,
      { token },
    );
    const data = payload.data || {};
    records.push(...(data.items || []));
    if (!data.has_more) return records;
    pageToken = String(data.page_token || "");
  }
}

function decisionsFromForm(formValue, candidateIds, forceNoSelection = false) {
  const positiveTags = coerceList(formValue.positive_reason_tags);
  const manualReason = compact(formValue.manual_reason, 240);
  if (forceNoSelection) {
    return Object.fromEntries(
      candidateIds.filter(Boolean).map((recordId) => [
        recordId,
        { status: "不做", tags: [], manual_reason: manualReason },
      ]),
    );
  }

  const decisions = {};
  const selected = new Set(coerceList(formValue[ENTER_BRIEF_FORM_KEY]));
  for (const recordId of selected) {
    decisions[recordId] = { status: "进入Brief", tags: positiveTags, manual_reason: manualReason };
  }
  for (const recordId of candidateIds || []) {
    if (recordId && !decisions[recordId]) {
      decisions[recordId] = { status: "不做", tags: [], manual_reason: "" };
    }
  }
  return decisions;
}

function fieldsEqual(current, next) {
  return (
    normalize(current["状态"]) === normalize(next["状态"]) &&
    normalize(current["学习状态"]) === normalize(next["学习状态"]) &&
    normalize(current["选择原因标签"]) === normalize(next["选择原因标签"]) &&
    normalize(current["人工一句话判断"]) === normalize(next["人工一句话判断"])
  );
}

async function applyFormValue(token, tableId, formValue, { candidateIds, runId, forceNoSelection }) {
  const decisions = decisionsFromForm(formValue, candidateIds, forceNoSelection);
  const records = Object.fromEntries((await allRecords(token, tableId)).map((record) => [record.record_id, record]));
  const updates = [];
  const skipped = [];
  const dryRun = String(envValue("DRY_RUN")).toLowerCase() === "true";

  for (const [recordId, decision] of Object.entries(decisions)) {
    const record = records[recordId];
    if (!record) {
      skipped.push({ record_id: recordId, reason: "record_not_found" });
      continue;
    }
    const fields = record.fields || {};
    if (runId && normalize(fields["运行批次"]) !== runId) {
      skipped.push({ record_id: recordId, title: normalize(fields["选题标题"]), reason: "run_id_mismatch" });
      continue;
    }
    const updateFields = {
      "状态": decision.status,
      "学习状态": "待学习",
      "选择原因标签": decision.tags,
      "人工一句话判断": decision.manual_reason || "",
    };
    if (fieldsEqual(fields, updateFields)) {
      skipped.push({ record_id: recordId, title: normalize(fields["选题标题"]), reason: "no_change" });
      continue;
    }
    updates.push({
      record_id: recordId,
      title: normalize(fields["选题标题"]),
      fields: updateFields,
    });
  }

  if (!dryRun) {
    for (const update of updates) {
      await requestJson(
        "PUT",
        `/bitable/v1/apps/${envValue("FEISHU_BASE_APP_TOKEN")}/tables/${tableId}/records/${update.record_id}`,
        { token, body: { fields: update.fields } },
      );
    }
  }

  return {
    ok: true,
    mode: dryRun ? "dry-run" : "write",
    run_id: runId,
    updated_count: dryRun ? 0 : updates.length,
    candidate_update_count: updates.length,
    updates,
    skipped,
  };
}

async function processCardSubmission(value, formValue) {
  const actionName = String(value.action || "");
  if (!SUPPORTED_SUBMIT_ACTIONS.has(actionName)) {
    return { ok: false, ignored: true, reason: "unsupported_action", action: actionName };
  }
  if (!envValue("FEISHU_BASE_APP_TOKEN")) throw new Error("Missing FEISHU_BASE_APP_TOKEN");

  const candidateIds = coerceList(value.candidate_ids);
  const runId = String(value.run_id || "");
  const effectiveFormValue = { ...formValue };
  if (actionName === SUBMIT_NO_SELECTION_ACTION) {
    effectiveFormValue[ENTER_BRIEF_FORM_KEY] = [];
    effectiveFormValue.positive_reason_tags = [];
  }

  const token = await tenantToken();
  const tableId = await topicTableId(token);
  const summary = await applyFormValue(token, tableId, effectiveFormValue, {
    candidateIds,
    runId,
    forceNoSelection: actionName === SUBMIT_NO_SELECTION_ACTION,
  });
  summary.action = actionName;
  summary.receipt_key = submissionFingerprint(actionName, runId, candidateIds, effectiveFormValue);
  return summary;
}

async function handlePayload(payload) {
  if (payload.challenge) return jsonResponse({ challenge: payload.challenge });
  if (payload.encrypt) {
    return toast("error", "暂不支持加密回调，请先关闭事件加密或改用带解密的版本");
  }

  const expectedToken = envValue("FEISHU_VERIFICATION_TOKEN");
  const actualToken = payload.header?.token || payload.token || "";
  if (expectedToken && actualToken !== expectedToken) {
    return toast("error", "回调 token 校验失败");
  }

  const action = payload.event?.action || {};
  const value = typeof action.value === "object" && action.value ? action.value : {};
  const formValue = action.form_value || {};
  if (!SUPPORTED_SUBMIT_ACTIONS.has(String(value.action || ""))) {
    return toast("warning", "不是选题速选提交");
  }

  try {
    const summary = await processCardSubmission(value, formValue);
    const count = summary.updated_count;
    const skippedNoChange = (summary.skipped || []).filter((item) => item.reason === "no_change").length;
    if (count === 0 && skippedNoChange > 0) {
      return toast("warning", "这次提交已经处理过");
    }
    return toast("success", `已回写 ${count} 条选择`);
  } catch (error) {
    console.error(error);
    return toast("error", `回写失败：${error.message || String(error)}`);
  }
}

function parsePayload(event) {
  if (!event) return {};
  if (typeof event.body === "string" && event.body.trim()) {
    const body = event.isBase64Encoded ? Buffer.from(event.body, "base64").toString("utf8") : event.body;
    return JSON.parse(body);
  }
  if (typeof event === "object" && (event.challenge || event.event || event.encrypt)) return event;
  return {};
}

exports.main_handler = async (event) => {
  if (event?.httpMethod && event.httpMethod !== "POST") {
    return jsonResponse({ ok: false, error: "POST required" }, 405);
  }

  let payload;
  try {
    payload = parsePayload(event);
  } catch (error) {
    return jsonResponse({ ok: false, error: "Invalid JSON body" }, 400);
  }

  return handlePayload(payload);
};
