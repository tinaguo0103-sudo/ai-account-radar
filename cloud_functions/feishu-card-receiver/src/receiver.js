const DEFAULT_API_HOST = "https://open.feishu.cn";
const TOPIC_TABLE_NAMES = ["04 分析与选题", "03 分析与选题"];
const ENTER_BRIEF_FORM_KEY = "enter_brief_records";
const PRODUCTION_DIRECTION_FIELD = "我的制作补充";
const PRODUCTION_DIRECTION_FORM_PREFIX = "production_direction__";
const SUBMIT_SELECTION_ACTION = "submit_topic_decisions";
const SUBMIT_NO_SELECTION_ACTION = "submit_no_selection";
const SUBMIT_PRODUCTION_DIRECTIONS_ACTION = "submit_production_directions";
const SUPPORTED_SUBMIT_ACTIONS = new Set([
  SUBMIT_SELECTION_ACTION,
  SUBMIT_NO_SELECTION_ACTION,
  SUBMIT_PRODUCTION_DIRECTIONS_ACTION,
]);

function jsonResponse(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json; charset=utf-8" },
  });
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

async function sha256(text) {
  const bytes = new TextEncoder().encode(text);
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest))
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
}

async function submissionFingerprint(actionName, runId, candidateIds, formValue) {
  const payload = {
    action: actionName,
    run_id: runId,
    candidate_ids: [...candidateIds].sort(),
    form_value: canonicalValue(formValue),
  };
  return sha256(JSON.stringify(payload));
}

async function messageUuid(prefix, parts) {
  const seed = parts.map((part) => normalize(part)).filter(Boolean).join("|");
  const hash = (await sha256(seed || prefix)).slice(0, 16);
  return `${prefix}-${hash}`.slice(0, 50);
}

function apiBaseUrl(env) {
  const host = String(env.FEISHU_API_BASE_URL || DEFAULT_API_HOST).replace(/\/+$/, "");
  return host.endsWith("/open-apis") ? host : `${host}/open-apis`;
}

async function requestJson(env, method, path, { token = "", body = undefined, fetchImpl = fetch } = {}) {
  const headers = { "content-type": "application/json; charset=utf-8" };
  if (token) headers.authorization = `Bearer ${token}`;
  const response = await fetchImpl(`${apiBaseUrl(env)}${path}`, {
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

async function tenantToken(env, options) {
  if (!env.FEISHU_APP_ID || !env.FEISHU_APP_SECRET) {
    throw new Error("Missing FEISHU_APP_ID or FEISHU_APP_SECRET");
  }
  const payload = await requestJson(env, "POST", "/auth/v3/tenant_access_token/internal", {
    ...options,
    body: {
      app_id: env.FEISHU_APP_ID,
      app_secret: env.FEISHU_APP_SECRET,
    },
  });
  const token = payload.tenant_access_token;
  if (!token) throw new Error("Feishu did not return tenant_access_token");
  return token;
}

async function listTables(env, token, options) {
  const payload = await requestJson(env, "GET", `/bitable/v1/apps/${env.FEISHU_BASE_APP_TOKEN}/tables`, {
    ...options,
    token,
  });
  return payload.data?.items || [];
}

async function topicTableId(env, token, options) {
  if (env.FEISHU_TOPIC_TABLE_ID) return env.FEISHU_TOPIC_TABLE_ID;
  const tables = await listTables(env, token, options);
  const found = tables.find((table) => TOPIC_TABLE_NAMES.includes(table.name));
  if (!found?.table_id) throw new Error(`Missing topic table. Expected one of: ${TOPIC_TABLE_NAMES.join(", ")}`);
  return found.table_id;
}

function parseReceiveTargets(env) {
  const raw =
    env.FEISHU_PRODUCTION_DIRECTION_RECEIVE_TARGETS ||
    env.FEISHU_CARD_RECEIVE_TARGETS ||
    (env.FEISHU_CARD_RECEIVE_ID && `${env.FEISHU_CARD_RECEIVE_ID_TYPE || "open_id"}:${env.FEISHU_CARD_RECEIVE_ID}`) ||
    "";
  return String(raw)
    .split(",")
    .map((part) => part.trim())
    .filter(Boolean)
    .map((part) => {
      const [receiveIdType, ...rest] = part.split(":");
      const receiveId = rest.join(":").trim();
      return { receive_id_type: String(receiveIdType || "").trim(), receive_id: receiveId };
    })
    .filter((target) => target.receive_id_type && target.receive_id);
}

function productionDirectionKey(recordId) {
  return `${PRODUCTION_DIRECTION_FORM_PREFIX}${recordId}`;
}

function shortField(fields, name, limit = 80) {
  return compact(fields?.[name], limit);
}

function buildProductionDirectionCard(selectedRecords, runId) {
  const elements = [
    {
      tag: "markdown",
      content:
        "你刚刚选中了这些题。这里只补一句制作方向：用什么案例、从哪个角度讲、哪些不要讲。可以留空，留空就由系统按私有案例库建议。",
    },
  ];
  const formElements = [];

  for (const [index, record] of selectedRecords.entries()) {
    const fields = record.fields || {};
    const title = shortField(fields, "选题标题", 56) || `选题 ${index + 1}`;
    const brief = shortField(fields, "一句话Brief", 90);
    const experiment = shortField(fields, "我要做的实验", 80);
    const lines = [`**${index + 1}. ${title}**`];
    if (brief) lines.push(`Brief：${brief}`);
    if (experiment) lines.push(`实验：${experiment}`);
    formElements.push({ tag: "markdown", content: lines.join("\n") });
    formElements.push({
      tag: "input",
      name: productionDirectionKey(record.record_id),
      required: false,
      width: "fill",
      placeholder: {
        tag: "plain_text",
        content: "例：用 AI账号信息雷达案例讲，重点讲选题判断，不要讲成工具教程",
      },
      default_value: "",
    });
    if (index !== selectedRecords.length - 1) formElements.push({ tag: "hr" });
  }

  formElements.push({
    tag: "column_set",
    columns: [
      {
        tag: "column",
        width: "auto",
        elements: [
          {
            tag: "button",
            type: "primary",
            width: "default",
            text: { tag: "plain_text", content: "保存制作方向" },
            form_action_type: "submit",
            name: "submit_production_directions",
            behaviors: [
              {
                type: "callback",
                value: {
                  action: SUBMIT_PRODUCTION_DIRECTIONS_ACTION,
                  run_id: runId,
                  candidate_ids: selectedRecords.map((record) => record.record_id),
                },
              },
            ],
          },
        ],
      },
      {
        tag: "column",
        width: "auto",
        elements: [
          {
            tag: "button",
            type: "default",
            width: "default",
            text: { tag: "plain_text", content: "重置" },
            form_action_type: "reset",
            name: "reset_production_directions",
          },
        ],
      },
    ],
  });
  elements.push({
    tag: "form",
    name: "production_direction_batch",
    padding: "8px 0px 0px 0px",
    vertical_spacing: "8px",
    elements: formElements,
  });

  return {
    schema: "2.0",
    config: {
      update_multi: true,
      enable_forward: false,
      width_mode: "fill",
    },
    header: {
      template: "purple",
      title: { tag: "plain_text", content: "补充制作方向" },
    },
    body: { elements },
  };
}

async function sendInteractiveCard(env, token, card, uuidBase, options) {
  const targets = parseReceiveTargets(env);
  if (!targets.length) {
    return { sent_count: 0, skipped: "missing_receive_targets" };
  }
  const sends = [];
  for (const target of targets) {
    const payload = await requestJson(
      env,
      "POST",
      `/im/v1/messages?receive_id_type=${encodeURIComponent(target.receive_id_type)}`,
      {
        ...options,
        token,
        body: {
          receive_id: target.receive_id,
          msg_type: "interactive",
          content: JSON.stringify(card),
          uuid: await messageUuid("production-direction-card", [
            uuidBase,
            target.receive_id_type,
            target.receive_id,
          ]),
        },
      },
    );
    sends.push({ target, message_id: payload.data?.message_id || "" });
  }
  return { sent_count: sends.length, sends };
}

async function allRecords(env, token, tableId, options) {
  const records = [];
  let pageToken = "";
  while (true) {
    const query = new URLSearchParams({ page_size: "500" });
    if (pageToken) query.set("page_token", pageToken);
    const payload = await requestJson(
      env,
      "GET",
      `/bitable/v1/apps/${env.FEISHU_BASE_APP_TOKEN}/tables/${tableId}/records?${query}`,
      { ...options, token },
    );
    const data = payload.data || {};
    records.push(...(data.items || []));
    if (!data.has_more) return records;
    pageToken = String(data.page_token || "");
  }
}

async function fieldsByName(env, token, tableId, options) {
  const payload = await requestJson(
    env,
    "GET",
    `/bitable/v1/apps/${env.FEISHU_BASE_APP_TOKEN}/tables/${tableId}/fields`,
    { ...options, token },
  );
  return Object.fromEntries((payload.data?.items || []).map((field) => [field.field_name, field]));
}

async function ensureTextFields(env, token, tableId, fieldNames, options) {
  const existing = await fieldsByName(env, token, tableId, options);
  const created = [];
  for (const fieldName of fieldNames) {
    if (existing[fieldName]) continue;
    await requestJson(env, "POST", `/bitable/v1/apps/${env.FEISHU_BASE_APP_TOKEN}/tables/${tableId}/fields`, {
      ...options,
      token,
      body: { field_name: fieldName, type: 1 },
    });
    created.push(fieldName);
  }
  return created;
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

async function applyFormValue(env, token, tableId, formValue, { candidateIds, runId, forceNoSelection, options }) {
  const decisions = decisionsFromForm(formValue, candidateIds, forceNoSelection);
  const records = Object.fromEntries((await allRecords(env, token, tableId, options)).map((record) => [record.record_id, record]));
  const updates = [];
  const skipped = [];
  const dryRun = String(env.DRY_RUN || "").toLowerCase() === "true";

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
        env,
        "PUT",
        `/bitable/v1/apps/${env.FEISHU_BASE_APP_TOKEN}/tables/${tableId}/records/${update.record_id}`,
        { ...options, token, body: { fields: update.fields } },
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
    selected_records: (candidateIds || [])
      .filter((recordId) => decisions[recordId]?.status === "进入Brief" && records[recordId])
      .map((recordId) => records[recordId]),
  };
}

async function applyProductionDirections(env, token, tableId, formValue, { candidateIds, runId, options }) {
  const dryRun = String(env.DRY_RUN || "").toLowerCase() === "true";
  if (!dryRun) {
    await ensureTextFields(env, token, tableId, [PRODUCTION_DIRECTION_FIELD], options);
  }
  const records = Object.fromEntries((await allRecords(env, token, tableId, options)).map((record) => [record.record_id, record]));
  const updates = [];
  const skipped = [];

  for (const recordId of candidateIds || []) {
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
    const direction = compact(formValue[productionDirectionKey(recordId)], 1000);
    if (!direction) {
      skipped.push({ record_id: recordId, title: normalize(fields["选题标题"]), reason: "empty_direction" });
      continue;
    }
    updates.push({
      record_id: recordId,
      title: normalize(fields["选题标题"]),
      fields: { [PRODUCTION_DIRECTION_FIELD]: direction },
    });
  }

  if (!dryRun) {
    for (const update of updates) {
      await requestJson(
        env,
        "PUT",
        `/bitable/v1/apps/${env.FEISHU_BASE_APP_TOKEN}/tables/${tableId}/records/${update.record_id}`,
        { ...options, token, body: { fields: update.fields } },
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

export async function processCardSubmission(env, value, formValue, options = {}) {
  const actionName = String(value.action || "");
  if (!SUPPORTED_SUBMIT_ACTIONS.has(actionName)) {
    return { ok: false, ignored: true, reason: "unsupported_action", action: actionName };
  }
  if (!env.FEISHU_BASE_APP_TOKEN) throw new Error("Missing FEISHU_BASE_APP_TOKEN");

  const candidateIds = coerceList(value.candidate_ids);
  const runId = String(value.run_id || "");
  const effectiveFormValue = { ...formValue };
  if (actionName === SUBMIT_NO_SELECTION_ACTION) {
    effectiveFormValue[ENTER_BRIEF_FORM_KEY] = [];
    effectiveFormValue.positive_reason_tags = [];
  }

  const token = await tenantToken(env, options);
  const tableId = await topicTableId(env, token, options);
  if (actionName === SUBMIT_PRODUCTION_DIRECTIONS_ACTION) {
    const directionSummary = await applyProductionDirections(env, token, tableId, effectiveFormValue, {
      candidateIds,
      runId,
      options,
    });
    directionSummary.action = actionName;
    directionSummary.receipt_key = await submissionFingerprint(actionName, runId, candidateIds, effectiveFormValue);
    return directionSummary;
  }

  const summary = await applyFormValue(env, token, tableId, effectiveFormValue, {
    candidateIds,
    runId,
    forceNoSelection: actionName === SUBMIT_NO_SELECTION_ACTION,
    options,
  });
  if (
    actionName === SUBMIT_SELECTION_ACTION &&
    summary.updated_count > 0 &&
    summary.selected_records?.length &&
    String(env.SEND_PRODUCTION_DIRECTION_CARD || "true").toLowerCase() !== "false"
  ) {
    const card = buildProductionDirectionCard(summary.selected_records, runId);
    summary.production_direction_card = await sendInteractiveCard(
      env,
      token,
      card,
      `production-direction-card-${runId || "latest"}-${await sha256(summary.selected_records.map((record) => record.record_id).join(","))}`,
      options,
    );
  }
  summary.action = actionName;
  summary.receipt_key = await submissionFingerprint(actionName, runId, candidateIds, effectiveFormValue);
  return summary;
}

export async function handlePayload(payload, env, options = {}) {
  if (payload.challenge) return jsonResponse({ challenge: payload.challenge });
  if (payload.encrypt) {
    return toast("error", "暂不支持加密回调，请先关闭事件加密或改用带解密的版本");
  }

  const expectedToken = env.FEISHU_VERIFICATION_TOKEN || "";
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
    const summary = await processCardSubmission(env, value, formValue, options);
    if (summary.action === SUBMIT_PRODUCTION_DIRECTIONS_ACTION) {
      const count = summary.updated_count;
      if (count === 0) return toast("warning", "没有保存新的制作方向");
      return toast("success", `已保存 ${count} 条制作方向`);
    }
    const count = summary.updated_count;
    const skippedNoChange = (summary.skipped || []).filter((item) => item.reason === "no_change").length;
    if (count === 0 && skippedNoChange > 0) {
      return toast("warning", "这次提交已经处理过");
    }
    const directionCard = summary.production_direction_card;
    if (directionCard?.sent_count) {
      return toast("success", `已回写 ${count} 条选择，并发送制作方向卡`);
    }
    if (directionCard?.skipped === "missing_receive_targets") {
      return toast("warning", `已回写 ${count} 条选择，但未配置制作方向卡接收人`);
    }
    return toast("success", `已回写 ${count} 条选择`);
  } catch (error) {
    console.error(error);
    return toast("error", `回写失败：${error.message || String(error)}`);
  }
}

export async function handleRequest(request, env, options = {}) {
  if (request.method !== "POST") return jsonResponse({ ok: false, error: "POST required" }, 405);
  let payload;
  try {
    payload = await request.json();
  } catch (error) {
    return jsonResponse({ ok: false, error: "Invalid JSON body" }, 400);
  }
  return handlePayload(payload, env, options);
}

export default {
  fetch: handleRequest,
};
