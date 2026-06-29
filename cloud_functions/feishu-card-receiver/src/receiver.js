const DEFAULT_API_HOST = "https://open.feishu.cn";
const TOPIC_TABLE_NAMES = ["04 分析与选题", "03 分析与选题"];
const ENTER_BRIEF_FORM_KEY = "enter_brief_records";
const PRODUCTION_DIRECTION_FIELD = "我的制作补充";
const PRODUCTION_DIRECTION_FORM_PREFIX = "production_direction__";
const PRODUCTION_DIRECTION_CARD_STATUS_FIELD = "制作方向卡状态";
const SELECTION_SUBMISSION_ID_FIELD = "选择提交批次";
const SELECTION_SUBMITTED_AT_FIELD = "选择提交时间";
const PRODUCTION_DIRECTION_CARD_SENT_AT_FIELD = "制作方向卡发送时间";
const PRODUCTION_DIRECTION_CARD_ERROR_FIELD = "制作方向卡错误";
const PRODUCTION_DIRECTION_CARD_PENDING = "待发送";
const PRODUCTION_DIRECTION_CARD_SENDING = "发送中";
const PRODUCTION_DIRECTION_CARD_SENT = "已发送";
const PRODUCTION_DIRECTION_CARD_FAILED = "发送失败";
const PRODUCTION_DIRECTION_CARD_IGNORED = "已忽略";
const SUBMIT_SELECTION_ACTION = "submit_topic_decisions";
const SUBMIT_NO_SELECTION_ACTION = "submit_no_selection";
const SUBMIT_PRODUCTION_DIRECTIONS_ACTION = "submit_production_directions";
const SEND_PENDING_PRODUCTION_DIRECTION_CARDS_ACTION = "send_pending_production_direction_cards";
const SUPPORTED_SUBMIT_ACTIONS = new Set([
  SUBMIT_SELECTION_ACTION,
  SUBMIT_NO_SELECTION_ACTION,
  SUBMIT_PRODUCTION_DIRECTIONS_ACTION,
]);
const TOKEN_CACHE_SAFETY_SECONDS = 300;
const DEFAULT_CARD_EXPIRE_DAYS = 5;
const OPEN_SELECTION_STATUSES = new Set(["", "待判断"]);

let cachedTenantToken = { value: "", expiresAt: 0 };
let cachedTopicTableId = "";

function jsonResponse(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json; charset=utf-8" },
  });
}

function toast(type, content) {
  return jsonResponse({ code: 0, toast: { type, content } });
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

function cardExpireDays(env) {
  const raw = Number(env.FEISHU_CARD_EXPIRE_DAYS || env.CARD_EXPIRE_DAYS || DEFAULT_CARD_EXPIRE_DAYS);
  return Number.isFinite(raw) && raw > 0 ? raw : DEFAULT_CARD_EXPIRE_DAYS;
}

function parseTimeMs(value) {
  const text = normalize(value);
  if (!text) return 0;
  const ms = Date.parse(text);
  return Number.isFinite(ms) ? ms : 0;
}

function currentTimeMs(options) {
  return Number.isFinite(options?.nowMs) ? Number(options.nowMs) : Date.now();
}

function cardExpiryStatus(value, env, options) {
  const issuedAtMs = parseTimeMs(value.card_issued_at || value.issued_at);
  const explicitExpiresAtMs = parseTimeMs(value.card_expires_at || value.expires_at);
  const expiresAtMs = explicitExpiresAtMs || (issuedAtMs ? issuedAtMs + cardExpireDays(env) * 24 * 60 * 60 * 1000 : 0);
  if (!expiresAtMs) return { expired: false, configured: false };
  return {
    expired: currentTimeMs(options) > expiresAtMs,
    configured: true,
    issued_at: normalize(value.card_issued_at || value.issued_at),
    expires_at: new Date(expiresAtMs).toISOString(),
  };
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
  if (!options?.fetchImpl && cachedTenantToken.value && cachedTenantToken.expiresAt > Date.now()) {
    return cachedTenantToken.value;
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
  if (!options?.fetchImpl) {
    const ttlSeconds = Math.max(60, Number(payload.expire || 7200) - TOKEN_CACHE_SAFETY_SECONDS);
    cachedTenantToken = { value: token, expiresAt: Date.now() + ttlSeconds * 1000 };
  }
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
  if (!options?.fetchImpl && cachedTopicTableId) return cachedTopicTableId;
  const tables = await listTables(env, token, options);
  const found = tables.find((table) => TOPIC_TABLE_NAMES.includes(table.name));
  if (!found?.table_id) throw new Error(`Missing topic table. Expected one of: ${TOPIC_TABLE_NAMES.join(", ")}`);
  if (!options?.fetchImpl) cachedTopicTableId = found.table_id;
  return found.table_id;
}

function shouldSendProductionDirectionCard(env, actionName, summary) {
  return (
    actionName === SUBMIT_SELECTION_ACTION &&
    summary.updated_count > 0 &&
    summary.selected_records?.length &&
    String(env.SEND_PRODUCTION_DIRECTION_CARD || "true").toLowerCase() !== "false"
  );
}

function shouldQueueProductionDirectionCard(env, actionName, summary) {
  return shouldSendProductionDirectionCard(env, actionName, summary);
}

async function sendProductionDirectionCard(env, token, runId, selectedRecords, options) {
  const card = buildProductionDirectionCard(selectedRecords, runId);
  return sendInteractiveCard(
    env,
    token,
    card,
    `production-direction-card-${runId || "latest"}-${await sha256(selectedRecords.map((record) => record.record_id).join(","))}`,
    options,
  );
}

function selectionSubmittedAt(record) {
  return parseTimeMs(record?.fields?.[SELECTION_SUBMITTED_AT_FIELD]);
}

function pendingQueueRecords(records, env, options) {
  const nowMs = currentTimeMs(options);
  const expiresAfterMs = cardExpireDays(env) * 24 * 60 * 60 * 1000;
  const expired = [];
  const pending = [];
  for (const record of records) {
    const fields = record.fields || {};
    if (normalize(fields[PRODUCTION_DIRECTION_CARD_STATUS_FIELD]) !== PRODUCTION_DIRECTION_CARD_PENDING) continue;
    if (normalize(fields["状态"]) !== "进入Brief") continue;
    if (normalize(fields[PRODUCTION_DIRECTION_FIELD])) continue;
    const submittedAtMs = selectionSubmittedAt(record);
    if (submittedAtMs && nowMs - submittedAtMs > expiresAfterMs) {
      expired.push(record);
      continue;
    }
    pending.push(record);
  }
  return { pending, expired };
}

function groupPendingRecords(records) {
  const groups = new Map();
  for (const record of records) {
    const fields = record.fields || {};
    const submissionId = normalize(fields[SELECTION_SUBMISSION_ID_FIELD]) || `record:${record.record_id}`;
    const runId = normalize(fields["运行批次"]);
    if (!groups.has(submissionId)) {
      groups.set(submissionId, {
        submission_id: submissionId,
        run_id: runId,
        submitted_at_ms: selectionSubmittedAt(record) || 0,
        records: [],
      });
    }
    groups.get(submissionId).records.push(record);
  }
  return [...groups.values()].sort((a, b) => a.submitted_at_ms - b.submitted_at_ms);
}

function errorText(error) {
  return compact(error?.message || String(error), 500);
}

async function markRecords(env, token, tableId, records, fields, options) {
  if (!records.length) return;
  await Promise.all(records.map((record) => updateRecordFields(env, token, tableId, record.record_id, fields, options)));
}

export async function sendPendingProductionDirectionCards(env, options = {}) {
  if (!env.FEISHU_BASE_APP_TOKEN) throw new Error("Missing FEISHU_BASE_APP_TOKEN");
  const token = await tenantToken(env, options);
  const tableId = await topicTableId(env, token, options);
  const records = await allRecords(env, token, tableId, options);
  const { pending, expired } = pendingQueueRecords(records, env, options);
  const nowIso = new Date(currentTimeMs(options)).toISOString();
  const limit = Math.max(1, Number(options.limit || env.PRODUCTION_DIRECTION_SEND_GROUP_LIMIT || 1));
  const groups = groupPendingRecords(pending).slice(0, limit);
  const sent = [];
  const failed = [];

  await markRecords(env, token, tableId, expired, {
    [PRODUCTION_DIRECTION_CARD_STATUS_FIELD]: PRODUCTION_DIRECTION_CARD_IGNORED,
    [PRODUCTION_DIRECTION_CARD_ERROR_FIELD]: `超过 ${cardExpireDays(env)} 天未发送，已忽略`,
  }, options);

  for (const group of groups) {
    await markRecords(env, token, tableId, group.records, {
      [PRODUCTION_DIRECTION_CARD_STATUS_FIELD]: PRODUCTION_DIRECTION_CARD_SENDING,
      [PRODUCTION_DIRECTION_CARD_ERROR_FIELD]: "",
    }, options);
    try {
      const result = await sendProductionDirectionCard(env, token, group.run_id, group.records, options);
      if (!result.sent_count) {
        throw new Error(result.skipped === "missing_receive_targets" ? "未配置制作方向卡接收人" : "制作方向卡未发送");
      }
      await markRecords(env, token, tableId, group.records, {
        [PRODUCTION_DIRECTION_CARD_STATUS_FIELD]: PRODUCTION_DIRECTION_CARD_SENT,
        [PRODUCTION_DIRECTION_CARD_SENT_AT_FIELD]: nowIso,
        [PRODUCTION_DIRECTION_CARD_ERROR_FIELD]: "",
      }, options);
      sent.push({ submission_id: group.submission_id, run_id: group.run_id, record_count: group.records.length, ...result });
    } catch (error) {
      const message = errorText(error);
      await markRecords(env, token, tableId, group.records, {
        [PRODUCTION_DIRECTION_CARD_STATUS_FIELD]: PRODUCTION_DIRECTION_CARD_FAILED,
        [PRODUCTION_DIRECTION_CARD_ERROR_FIELD]: message,
      }, options);
      failed.push({ submission_id: group.submission_id, run_id: group.run_id, record_count: group.records.length, error: message });
    }
  }

  return {
    ok: failed.length === 0,
    action: SEND_PENDING_PRODUCTION_DIRECTION_CARDS_ACTION,
    scanned_count: records.length,
    pending_count: pending.length,
    expired_count: expired.length,
    group_count: groups.length,
    sent,
    failed,
  };
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

function candidateSnapshotsFromValue(value) {
  const raw = value?.candidate_snapshots;
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return {};
  return raw;
}

function snapshotRecord(recordId, snapshot = {}) {
  return {
    record_id: recordId,
    fields: {
      "选题标题": normalize(snapshot.title),
      "一句话Brief": normalize(snapshot.brief),
      "我要做的实验": normalize(snapshot.experiment),
      "运行批次": normalize(snapshot.run_id),
    },
  };
}

function shortField(fields, name, limit = 80) {
  return compact(fields?.[name], limit);
}

function buildProductionDirectionCard(selectedRecords, runId) {
  const issuedAt = new Date();
  const expiresAt = new Date(issuedAt.getTime() + DEFAULT_CARD_EXPIRE_DAYS * 24 * 60 * 60 * 1000);
  const candidateIds = selectedRecords.map((record) => record.record_id);
  const submitValue = {
    action: SUBMIT_PRODUCTION_DIRECTIONS_ACTION,
    run_id: runId,
    candidate_ids: candidateIds,
    card_issued_at: issuedAt.toISOString(),
    card_expires_at: expiresAt.toISOString(),
    card_ttl_days: DEFAULT_CARD_EXPIRE_DAYS,
  };
  const elements = [
    {
      tag: "markdown",
      content:
        `你刚刚选中了这些题。下面每条都有一个可选建议字段：准备用哪个真实案例讲、从哪个角度讲、哪些不要讲。可以留空，留空就由系统按私有案例库建议。\n\n这张卡只能提交一次，${DEFAULT_CARD_EXPIRE_DAYS} 天后提交无效。`,
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
    lines.push("建议补充：真实案例 / 讲法方向 / 不要讲什么（可选）");
    formElements.push({ tag: "markdown", content: lines.join("\n") });
    formElements.push({
      tag: "input",
      name: productionDirectionKey(record.record_id),
      required: false,
      width: "fill",
      placeholder: {
        tag: "plain_text",
        content: "例：用 AI账号信息雷达案例讲，重点讲选题判断；不要讲成工具教程",
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
                value: submitValue,
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

async function updateRecordFields(env, token, tableId, recordId, fields, options) {
  return requestJson(
    env,
    "PUT",
    `/bitable/v1/apps/${env.FEISHU_BASE_APP_TOKEN}/tables/${tableId}/records/${recordId}`,
    { ...options, token, body: { fields } },
  );
}

async function getRecord(env, token, tableId, recordId, options) {
  try {
    const payload = await requestJson(
      env,
      "GET",
      `/bitable/v1/apps/${env.FEISHU_BASE_APP_TOKEN}/tables/${tableId}/records/${recordId}`,
      { ...options, token },
    );
    return payload.data?.record || null;
  } catch (error) {
    const message = String(error?.message || error);
    if (message.includes("HTTP 404") || message.includes("1254045")) return null;
    throw error;
  }
}

async function recordsById(env, token, tableId, recordIds, options) {
  const entries = await Promise.all(
    [...new Set(recordIds.filter(Boolean))].map(async (recordId) => [recordId, await getRecord(env, token, tableId, recordId, options)]),
  );
  return Object.fromEntries(entries.filter(([, record]) => record));
}

function runIdMismatch(record, runId) {
  const actualRunId = normalize(record?.fields?.["运行批次"]);
  return Boolean(runId && actualRunId && actualRunId !== runId);
}

async function selectionCardGuard(env, token, tableId, candidateIds, runId, options) {
  const records = await recordsById(env, token, tableId, candidateIds, options);
  const missing = candidateIds.filter((recordId) => recordId && !records[recordId]);
  if (missing.length) return { blocked: true, reason: "card_records_missing", missing };

  const mismatched = candidateIds.filter((recordId) => runIdMismatch(records[recordId], runId));
  if (mismatched.length) return { blocked: true, reason: "card_run_mismatch", record_ids: mismatched };

  const processed = candidateIds.filter((recordId) => {
    const status = normalize(records[recordId]?.fields?.["状态"]);
    return !OPEN_SELECTION_STATUSES.has(status);
  });
  if (processed.length) return { blocked: true, reason: "selection_card_already_submitted", record_ids: processed };
  return { blocked: false };
}

async function productionDirectionCardGuard(env, token, tableId, candidateIds, runId, options) {
  const records = await recordsById(env, token, tableId, candidateIds, options);
  const missing = candidateIds.filter((recordId) => recordId && !records[recordId]);
  if (missing.length) return { blocked: true, reason: "card_records_missing", missing };

  const mismatched = candidateIds.filter((recordId) => runIdMismatch(records[recordId], runId));
  if (mismatched.length) return { blocked: true, reason: "card_run_mismatch", record_ids: mismatched };

  const alreadyFilled = candidateIds.filter((recordId) => normalize(records[recordId]?.fields?.[PRODUCTION_DIRECTION_FIELD]));
  if (alreadyFilled.length) return { blocked: true, reason: "production_direction_card_already_submitted", record_ids: alreadyFilled };
  return { blocked: false };
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
  return Object.entries(next).every(([fieldName, value]) => normalize(current[fieldName]) === normalize(value));
}

function selectionQueueFields(decision, queueInfo) {
  if (!queueInfo?.enabled || decision.status !== "进入Brief") return {};
  return {
    [PRODUCTION_DIRECTION_CARD_STATUS_FIELD]: PRODUCTION_DIRECTION_CARD_PENDING,
    [SELECTION_SUBMISSION_ID_FIELD]: queueInfo.submissionId,
    [SELECTION_SUBMITTED_AT_FIELD]: queueInfo.submittedAt,
    [PRODUCTION_DIRECTION_CARD_SENT_AT_FIELD]: "",
    [PRODUCTION_DIRECTION_CARD_ERROR_FIELD]: "",
  };
}

async function applyFormValue(env, token, tableId, formValue, { candidateIds, runId, forceNoSelection, options, queueInfo }) {
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
      ...selectionQueueFields(decision, queueInfo),
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
    await Promise.all(updates.map((update) => updateRecordFields(env, token, tableId, update.record_id, update.fields, options)));
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

async function applyFormValueFast(env, token, tableId, formValue, { candidateIds, runId, forceNoSelection, options, snapshots, queueInfo }) {
  const decisions = decisionsFromForm(formValue, candidateIds, forceNoSelection);
  const dryRun = String(env.DRY_RUN || "").toLowerCase() === "true";
  const updates = [];
  const skipped = [];

  for (const [recordId, decision] of Object.entries(decisions)) {
    const snapshot = snapshots[recordId] || {};
    if (runId && snapshot.run_id && normalize(snapshot.run_id) !== runId) {
      skipped.push({ record_id: recordId, title: normalize(snapshot.title), reason: "run_id_mismatch" });
      continue;
    }
    updates.push({
      record_id: recordId,
      title: normalize(snapshot.title),
      fields: {
        "状态": decision.status,
        "学习状态": "待学习",
        "选择原因标签": decision.tags,
        "人工一句话判断": decision.manual_reason || "",
        ...selectionQueueFields(decision, queueInfo),
      },
    });
  }

  if (!dryRun) {
    await Promise.all(updates.map((update) => updateRecordFields(env, token, tableId, update.record_id, update.fields, options)));
  }

  return {
    ok: true,
    mode: dryRun ? "dry-run" : "write",
    fast_path: true,
    run_id: runId,
    updated_count: dryRun ? 0 : updates.length,
    candidate_update_count: updates.length,
    updates,
    skipped,
    selected_records: (candidateIds || [])
      .filter((recordId) => decisions[recordId]?.status === "进入Brief")
      .map((recordId) => snapshotRecord(recordId, snapshots[recordId] || {})),
  };
}

async function applyProductionDirections(env, token, tableId, formValue, { candidateIds, runId, options }) {
  const dryRun = String(env.DRY_RUN || "").toLowerCase() === "true";
  if (!dryRun && String(env.ENSURE_PRODUCTION_DIRECTION_FIELD || "").toLowerCase() === "true") {
    await ensureTextFields(env, token, tableId, [PRODUCTION_DIRECTION_FIELD], options);
  }
  const updates = [];
  const skipped = [];

  for (const recordId of candidateIds || []) {
    const direction = compact(formValue[productionDirectionKey(recordId)], 1000);
    if (!direction) {
      skipped.push({ record_id: recordId, reason: "empty_direction" });
      continue;
    }
    updates.push({
      record_id: recordId,
      fields: { [PRODUCTION_DIRECTION_FIELD]: direction },
    });
  }

  if (!dryRun) {
    await Promise.all(updates.map((update) => updateRecordFields(env, token, tableId, update.record_id, update.fields, options)));
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
  const expiry = cardExpiryStatus(value, env, options);
  if (expiry.expired) {
    return {
      ok: false,
      blocked: true,
      reason: "card_expired",
      action: actionName,
      run_id: runId,
      updated_count: 0,
      candidate_update_count: 0,
      expiry,
    };
  }
  const candidateSnapshots = candidateSnapshotsFromValue(value);
  const effectiveFormValue = { ...formValue };
  if (actionName === SUBMIT_NO_SELECTION_ACTION) {
    effectiveFormValue[ENTER_BRIEF_FORM_KEY] = [];
    effectiveFormValue.positive_reason_tags = [];
  }

  const token = await tenantToken(env, options);
  const tableId = await topicTableId(env, token, options);
  if (actionName === SUBMIT_PRODUCTION_DIRECTIONS_ACTION) {
    const guard = await productionDirectionCardGuard(env, token, tableId, candidateIds, runId, options);
    if (guard.blocked) {
      return {
        ok: false,
        ...guard,
        action: actionName,
        run_id: runId,
        updated_count: 0,
        candidate_update_count: 0,
      };
    }
    const directionSummary = await applyProductionDirections(env, token, tableId, effectiveFormValue, {
      candidateIds,
      runId,
      options,
    });
    directionSummary.action = actionName;
    directionSummary.receipt_key = await submissionFingerprint(actionName, runId, candidateIds, effectiveFormValue);
    return directionSummary;
  }

  const guard = await selectionCardGuard(env, token, tableId, candidateIds, runId, options);
  if (guard.blocked) {
    return {
      ok: false,
      ...guard,
      action: actionName,
      run_id: runId,
      updated_count: 0,
      candidate_update_count: 0,
    };
  }

  const receiptKey = await submissionFingerprint(actionName, runId, candidateIds, effectiveFormValue);
  const submittedAt = new Date(currentTimeMs(options)).toISOString();
  const queueInfo = {
    enabled: actionName === SUBMIT_SELECTION_ACTION && String(env.SEND_PRODUCTION_DIRECTION_CARD || "true").toLowerCase() !== "false",
    submissionId: `${runId || "selection"}:${receiptKey.slice(0, 12)}`,
    submittedAt,
  };
  const hasSnapshots = Object.keys(candidateSnapshots).length > 0;
  const summary = hasSnapshots
    ? await applyFormValueFast(env, token, tableId, effectiveFormValue, {
        candidateIds,
        runId,
        forceNoSelection: actionName === SUBMIT_NO_SELECTION_ACTION,
        options,
        snapshots: candidateSnapshots,
        queueInfo,
      })
    : await applyFormValue(env, token, tableId, effectiveFormValue, {
        candidateIds,
        runId,
        forceNoSelection: actionName === SUBMIT_NO_SELECTION_ACTION,
        options,
        queueInfo,
      });
  if (shouldQueueProductionDirectionCard(env, actionName, summary)) {
    summary.production_direction_card = {
      queued: true,
      status: PRODUCTION_DIRECTION_CARD_PENDING,
      submission_id: queueInfo.submissionId,
      selected_count: summary.selected_records.length,
    };
  }
  summary.action = actionName;
  summary.receipt_key = receiptKey;
  return summary;
}

export async function handlePayload(payload, env, options = {}) {
  if (payload.challenge) return jsonResponse({ challenge: payload.challenge });
  if (payload.encrypt) {
    return toast("error", "暂不支持加密回调，请先关闭事件加密或改用带解密的版本");
  }

  if (payload.action === SEND_PENDING_PRODUCTION_DIRECTION_CARDS_ACTION) {
    const expectedRunnerToken = env.FEISHU_QUEUE_RUNNER_TOKEN || "";
    const actualRunnerToken = payload.runner_token || payload.token || "";
    if (expectedRunnerToken && actualRunnerToken !== expectedRunnerToken) {
      return jsonResponse({ ok: false, error: "runner token mismatch" }, 403);
    }
    return jsonResponse(await sendPendingProductionDirectionCards(env, options));
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
    if (summary.blocked) {
      if (summary.reason === "card_expired") return toast("warning", "这张卡已超过 5 天，不再处理，请使用最新卡片");
      if (summary.reason === "selection_card_already_submitted") return toast("warning", "这张选题卡已经提交过，不再重复处理");
      if (summary.reason === "production_direction_card_already_submitted") return toast("warning", "这张制作方向卡已经保存过，不再重复处理");
      if (summary.reason === "card_run_mismatch") return toast("warning", "这张卡对应的记录批次已变化，请使用最新卡片");
      if (summary.reason === "card_records_missing") return toast("warning", "这张卡对应的记录不存在，请使用最新卡片");
      return toast("warning", "这张卡当前不能提交，请使用最新卡片");
    }
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
    if (directionCard?.queued) {
      return toast("success", `已回写 ${count} 条选择，制作方向卡稍后发送`);
    }
    if (directionCard?.deferred) {
      return toast("success", `已回写 ${count} 条选择，制作方向卡稍后发送`);
    }
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
