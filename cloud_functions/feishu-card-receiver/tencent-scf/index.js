const crypto = require("crypto");

const DEFAULT_API_HOST = "https://open.feishu.cn";
const TOPIC_TABLE_NAMES = ["04 分析与选题"];
const SCRIPT_PACKAGE_TABLE_NAMES = ["06 完整脚本与制作包"];
const ENTER_SCRIPT_PACKAGE_FORM_KEY = "script_package_records";
const PRODUCTION_DIRECTION_FIELD = "我的制作补充";
const PRODUCTION_DIRECTION_FORM_PREFIX = "production_direction__";
const SCRIPT_PACKAGE_QUALITY_FORM_PREFIX = "script_quality__";
const SCRIPT_PACKAGE_ISSUES_FORM_PREFIX = "script_issues__";
const SCRIPT_PACKAGE_NOTE_FORM_PREFIX = "script_note__";
const SCRIPT_PACKAGE_QUALITY_FIELDS = [
  "人工质量反馈",
  "质量问题标签",
  "人工修改意见",
  "反馈时间",
  "反馈来源",
  "内容学习状态",
];
const PRODUCTION_DIRECTION_CARD_STATUS_FIELD = "制作方向卡状态";
const SELECTION_SUBMISSION_ID_FIELD = "选择提交批次";
const SELECTION_SUBMITTED_AT_FIELD = "选择提交时间";
const PRODUCTION_DIRECTION_CARD_SENT_AT_FIELD = "制作方向卡发送时间";
const PRODUCTION_DIRECTION_CARD_ERROR_FIELD = "制作方向卡错误";
const PRODUCTION_DIRECTION_CARD_PENDING = "待发送";
const PRODUCTION_DIRECTION_CARD_SENDING = "发送中";
const PRODUCTION_DIRECTION_CARD_SENT = "已发送";
const PRODUCTION_DIRECTION_CARD_SUBMITTED = "已提交";
const PRODUCTION_DIRECTION_CARD_FAILED = "发送失败";
const PRODUCTION_DIRECTION_CARD_IGNORED = "已忽略";
const SCRIPT_PACKAGE_READY_STATUS = "生成脚本包";
const SUBMIT_SELECTION_ACTION = "submit_topic_decisions";
const SUBMIT_NO_SELECTION_ACTION = "submit_no_selection";
const SUBMIT_PRODUCTION_DIRECTIONS_ACTION = "submit_production_directions";
const SUBMIT_SCRIPT_PACKAGE_QUALITY_ACTION = "submit_script_package_quality_feedback";
const SEND_PENDING_PRODUCTION_DIRECTION_CARDS_ACTION = "send_pending_production_direction_cards";
const SUPPORTED_SUBMIT_ACTIONS = new Set([
  SUBMIT_SELECTION_ACTION,
  SUBMIT_NO_SELECTION_ACTION,
  SUBMIT_PRODUCTION_DIRECTIONS_ACTION,
  SUBMIT_SCRIPT_PACKAGE_QUALITY_ACTION,
]);
const TOKEN_CACHE_SAFETY_SECONDS = 300;
const DEFAULT_CARD_EXPIRE_DAYS = 5;
const DEFAULT_DIRECTION_CARD_STUCK_MINUTES = 15;
const OPEN_SELECTION_STATUSES = new Set(["", "待判断"]);

let cachedTenantToken = { value: "", expiresAt: 0 };
let cachedTopicTableId = "";

function jsonResponse(body, statusCode = 200) {
  return {
    statusCode,
    headers: { "content-type": "application/json; charset=utf-8" },
    body: JSON.stringify(body),
  };
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

function cardExpireDays() {
  const raw = Number(envValue("FEISHU_CARD_EXPIRE_DAYS") || envValue("CARD_EXPIRE_DAYS") || DEFAULT_CARD_EXPIRE_DAYS);
  return Number.isFinite(raw) && raw > 0 ? raw : DEFAULT_CARD_EXPIRE_DAYS;
}

function directionCardStuckMinutes() {
  const raw = Number(envValue("FEISHU_DIRECTION_CARD_STUCK_MINUTES") || DEFAULT_DIRECTION_CARD_STUCK_MINUTES);
  return Number.isFinite(raw) && raw > 0 ? raw : DEFAULT_DIRECTION_CARD_STUCK_MINUTES;
}

function parseTimeMs(value) {
  const text = normalize(value);
  if (!text) return 0;
  const ms = Date.parse(text);
  return Number.isFinite(ms) ? ms : 0;
}

function cardExpiryStatus(value) {
  const issuedAtMs = parseTimeMs(value.card_issued_at || value.issued_at);
  const explicitExpiresAtMs = parseTimeMs(value.card_expires_at || value.expires_at);
  const expiresAtMs = explicitExpiresAtMs || (issuedAtMs ? issuedAtMs + cardExpireDays() * 24 * 60 * 60 * 1000 : 0);
  if (!expiresAtMs) return { expired: false, configured: false };
  return {
    expired: Date.now() > expiresAtMs,
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

function messageUuid(prefix, parts) {
  const seed = parts.map((part) => normalize(part)).filter(Boolean).join("|");
  const hash = sha256(seed || prefix).slice(0, 16);
  return `${prefix}-${hash}`.slice(0, 50);
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
  if (cachedTenantToken.value && cachedTenantToken.expiresAt > Date.now()) {
    return cachedTenantToken.value;
  }
  const payload = await requestJson("POST", "/auth/v3/tenant_access_token/internal", {
    body: {
      app_id: envValue("FEISHU_APP_ID"),
      app_secret: envValue("FEISHU_APP_SECRET"),
    },
  });
  const token = payload.tenant_access_token;
  if (!token) throw new Error("Feishu did not return tenant_access_token");
  const ttlSeconds = Math.max(60, Number(payload.expire || 7200) - TOKEN_CACHE_SAFETY_SECONDS);
  cachedTenantToken = { value: token, expiresAt: Date.now() + ttlSeconds * 1000 };
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
  if (cachedTopicTableId) return cachedTopicTableId;
  const tables = await listTables(token);
  const found = tables.find((table) => TOPIC_TABLE_NAMES.includes(table.name));
  if (!found?.table_id) throw new Error(`Missing topic table. Expected one of: ${TOPIC_TABLE_NAMES.join(", ")}`);
  cachedTopicTableId = found.table_id;
  return found.table_id;
}

async function scriptPackageTableId(token) {
  if (envValue("FEISHU_SCRIPT_PACKAGE_TABLE_ID")) return envValue("FEISHU_SCRIPT_PACKAGE_TABLE_ID");
  const tables = await listTables(token);
  const found = tables.find((table) => SCRIPT_PACKAGE_TABLE_NAMES.includes(table.name));
  if (!found?.table_id) throw new Error(`Missing script package table. Expected one of: ${SCRIPT_PACKAGE_TABLE_NAMES.join(", ")}`);
  return found.table_id;
}

function shouldSendProductionDirectionCard(actionName, summary) {
  return (
    actionName === SUBMIT_SELECTION_ACTION &&
    summary.updated_count > 0 &&
    summary.selected_records?.length &&
    String(envValue("SEND_PRODUCTION_DIRECTION_CARD") || "true").toLowerCase() !== "false"
  );
}

function shouldQueueProductionDirectionCard(actionName, summary) {
  return shouldSendProductionDirectionCard(actionName, summary);
}

async function sendProductionDirectionCard(token, runId, selectedRecords) {
  const card = buildProductionDirectionCard(selectedRecords, runId);
  return sendInteractiveCard(
    token,
    card,
    `production-direction-card-${runId || "latest"}-${sha256(selectedRecords.map((record) => record.record_id).join(","))}`,
  );
}

function selectionSubmittedAt(record) {
  return parseTimeMs(record?.fields?.[SELECTION_SUBMITTED_AT_FIELD]);
}

function queueCutoffIso() {
  return new Date(Date.now() - cardExpireDays() * 24 * 60 * 60 * 1000).toISOString();
}

function recordFilterCondition(fieldName, operator, value = []) {
  const condition = { field_name: fieldName, operator };
  if (!["isEmpty", "isNotEmpty"].includes(operator)) {
    condition.value = Array.isArray(value) ? value : [value];
  }
  return condition;
}

function productionDirectionQueueFilter(cutoffIso = "", cutoffOperator = "") {
  const conditions = [
    recordFilterCondition(PRODUCTION_DIRECTION_CARD_STATUS_FIELD, "is", [PRODUCTION_DIRECTION_CARD_PENDING]),
    recordFilterCondition("状态", "is", [SCRIPT_PACKAGE_READY_STATUS]),
    recordFilterCondition(PRODUCTION_DIRECTION_FIELD, "isEmpty"),
  ];
  if (cutoffIso && cutoffOperator) {
    conditions.push(recordFilterCondition(SELECTION_SUBMITTED_AT_FIELD, cutoffOperator, [cutoffIso]));
  }
  return { conjunction: "and", conditions };
}

function productionDirectionStatusFilter(status, cutoffIso = "", cutoffOperator = "") {
  const conditions = [
    recordFilterCondition(PRODUCTION_DIRECTION_CARD_STATUS_FIELD, "is", [status]),
    recordFilterCondition("状态", "is", [SCRIPT_PACKAGE_READY_STATUS]),
    recordFilterCondition(PRODUCTION_DIRECTION_FIELD, "isEmpty"),
  ];
  if (cutoffIso && cutoffOperator) {
    conditions.push(recordFilterCondition(SELECTION_SUBMITTED_AT_FIELD, cutoffOperator, [cutoffIso]));
  }
  return { conjunction: "and", conditions };
}

function pendingQueueRecords(records) {
  const nowMs = Date.now();
  const expiresAfterMs = cardExpireDays() * 24 * 60 * 60 * 1000;
  const expired = [];
  const pending = [];
  for (const record of records) {
    const fields = record.fields || {};
    if (normalize(fields[PRODUCTION_DIRECTION_CARD_STATUS_FIELD]) !== PRODUCTION_DIRECTION_CARD_PENDING) continue;
    if (normalize(fields["状态"]) !== SCRIPT_PACKAGE_READY_STATUS) continue;
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

function staleSendingRecords(records) {
  const nowMs = Date.now();
  const staleAfterMs = directionCardStuckMinutes() * 60 * 1000;
  return records.filter((record) => {
    const fields = record.fields || {};
    if (normalize(fields[PRODUCTION_DIRECTION_CARD_STATUS_FIELD]) !== PRODUCTION_DIRECTION_CARD_SENDING) return false;
    if (normalize(fields["状态"]) !== SCRIPT_PACKAGE_READY_STATUS) return false;
    if (normalize(fields[PRODUCTION_DIRECTION_FIELD])) return false;
    const submittedAtMs = selectionSubmittedAt(record);
    return Boolean(submittedAtMs && nowMs - submittedAtMs > staleAfterMs);
  });
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

async function markRecords(token, tableId, records, fields) {
  if (!records.length) return;
  await Promise.all(records.map((record) => updateRecordFields(token, tableId, record.record_id, fields)));
}

async function queueRecords(token, tableId) {
  const cutoffIso = queueCutoffIso();
  try {
    return await allRecords(token, tableId, {
      filter: productionDirectionQueueFilter(cutoffIso, "isGreaterEqual"),
    });
  } catch (error) {
    const records = await allRecords(token, tableId);
    records.filter_fallback_error = errorText(error);
    return records;
  }
}

async function expiredQueueRecords(token, tableId) {
  try {
    return await allRecords(token, tableId, {
      filter: productionDirectionQueueFilter(queueCutoffIso(), "isLess"),
    });
  } catch (_error) {
    return [];
  }
}

async function sendingQueueRecords(token, tableId) {
  const cutoffIso = new Date(Date.now() - directionCardStuckMinutes() * 60 * 1000).toISOString();
  try {
    return await allRecords(token, tableId, {
      filter: productionDirectionStatusFilter(PRODUCTION_DIRECTION_CARD_SENDING, cutoffIso, "isLess"),
    });
  } catch (_error) {
    return [];
  }
}

function uniqueRecords(records) {
  const byId = new Map();
  for (const record of records) {
    if (!record?.record_id || byId.has(record.record_id)) continue;
    byId.set(record.record_id, record);
  }
  return [...byId.values()];
}

function parseNotifyTargets() {
  const raw =
    envValue("FEISHU_PRODUCTION_DIRECTION_ALERT_TARGETS") ||
    envValue("FEISHU_AUTOMATION_NOTIFY_TARGETS") ||
    envValue("FEISHU_CARD_RECEIVE_TARGETS") ||
    "";
  return String(raw)
    .split(",")
    .map((part) => part.trim())
    .filter(Boolean)
    .map((part) => {
      const [receive_id_type, ...rest] = part.split(":");
      return { receive_id_type: normalize(receive_id_type), receive_id: normalize(rest.join(":")) };
    })
    .filter((target) => target.receive_id_type && target.receive_id);
}

function queueIssueAlertText(summary) {
  const lines = [
    "【AI账号信息雷达】制作方向卡发送异常",
    `时间：${summary.checked_at}`,
    `待发送：${summary.pending_count}｜已发送批次：${summary.sent.length}｜失败批次：${summary.failed.length}｜卡死：${summary.stuck_count}｜过期忽略：${summary.expired_count}`,
  ];
  if (summary.failed.length) {
    lines.push("失败：");
    for (const item of summary.failed.slice(0, 5)) {
      lines.push(`- ${item.submission_id || item.run_id || "unknown"}｜${item.record_count}条｜${item.error}`);
    }
  }
  if (summary.stuck.length) {
    lines.push("卡在发送中：");
    for (const item of summary.stuck.slice(0, 5)) {
      lines.push(`- ${item.record_id}｜${item.title || "未命名"}｜${item.error}`);
    }
  }
  if (summary.expired_count) {
    lines.push(`过期忽略：${summary.expired_count} 条超过 ${summary.expire_days} 天未发送的制作方向卡队列。`);
  }
  lines.push("处理：查看飞书 04 的「制作方向卡状态 / 制作方向卡错误」，必要时把状态改回「待发送」后等待下一次定时扫描。");
  return lines.join("\n");
}

async function sendTextMessage(token, target, text, uuidBase) {
  return requestJson(
    "POST",
    `/im/v1/messages?receive_id_type=${encodeURIComponent(target.receive_id_type)}`,
    {
      token,
      body: {
        receive_id: target.receive_id,
        msg_type: "text",
        content: JSON.stringify({ text }),
        uuid: `direction-card-alert-${sha256(uuidBase).slice(0, 16)}`,
      },
    },
  );
}

async function notifyProductionDirectionQueueIssue(token, summary) {
  const targets = parseNotifyTargets();
  if (!targets.length) return { sent_count: 0, skipped: "missing_notify_targets" };
  const text = queueIssueAlertText(summary);
  const sent = [];
  for (const target of targets) {
    const payload = await sendTextMessage(
      token,
      target,
      text,
      `${summary.action}|${summary.checked_at}|${target.receive_id_type}|${target.receive_id}`,
    );
    sent.push({ target, message_id: payload.data?.message_id || "" });
  }
  return { sent_count: sent.length, sent };
}

async function sendPendingProductionDirectionCards() {
  if (!envValue("FEISHU_BASE_APP_TOKEN")) throw new Error("Missing FEISHU_BASE_APP_TOKEN");
  const token = await tenantToken();
  const tableId = await topicTableId(token);
  const records = await queueRecords(token, tableId);
  const expiredRecords = await expiredQueueRecords(token, tableId);
  const sendingRecords = await sendingQueueRecords(token, tableId);
  const { pending, expired } = pendingQueueRecords(uniqueRecords([...records, ...expiredRecords]));
  const stuck = staleSendingRecords(sendingRecords);
  const nowIso = new Date().toISOString();
  const limit = Math.max(1, Number(envValue("PRODUCTION_DIRECTION_SEND_GROUP_LIMIT") || 1));
  const groups = groupPendingRecords(pending).slice(0, limit);
  const sent = [];
  const failed = [];

  await markRecords(token, tableId, expired, {
    [PRODUCTION_DIRECTION_CARD_STATUS_FIELD]: PRODUCTION_DIRECTION_CARD_IGNORED,
    [PRODUCTION_DIRECTION_CARD_ERROR_FIELD]: `超过 ${cardExpireDays()} 天未发送，已忽略`,
  });

  const stuckMessage = `停留在发送中超过 ${directionCardStuckMinutes()} 分钟，可能上次定时发送中断`;
  await markRecords(token, tableId, stuck, {
    [PRODUCTION_DIRECTION_CARD_STATUS_FIELD]: PRODUCTION_DIRECTION_CARD_FAILED,
    [PRODUCTION_DIRECTION_CARD_ERROR_FIELD]: stuckMessage,
  });

  for (const group of groups) {
    await markRecords(token, tableId, group.records, {
      [PRODUCTION_DIRECTION_CARD_STATUS_FIELD]: PRODUCTION_DIRECTION_CARD_SENDING,
      [PRODUCTION_DIRECTION_CARD_ERROR_FIELD]: "",
    });
    try {
      const result = await sendProductionDirectionCard(token, group.run_id, group.records);
      if (!result.sent_count) {
        throw new Error(result.skipped === "missing_receive_targets" ? "未配置制作方向卡接收人" : "制作方向卡未发送");
      }
      await markRecords(token, tableId, group.records, {
        [PRODUCTION_DIRECTION_CARD_STATUS_FIELD]: PRODUCTION_DIRECTION_CARD_SENT,
        [PRODUCTION_DIRECTION_CARD_SENT_AT_FIELD]: nowIso,
        [PRODUCTION_DIRECTION_CARD_ERROR_FIELD]: "",
      });
      sent.push({ submission_id: group.submission_id, run_id: group.run_id, record_count: group.records.length, ...result });
    } catch (error) {
      const message = errorText(error);
      await markRecords(token, tableId, group.records, {
        [PRODUCTION_DIRECTION_CARD_STATUS_FIELD]: PRODUCTION_DIRECTION_CARD_FAILED,
        [PRODUCTION_DIRECTION_CARD_ERROR_FIELD]: message,
      });
      failed.push({ submission_id: group.submission_id, run_id: group.run_id, record_count: group.records.length, error: message });
    }
  }

  const summary = {
    ok: failed.length === 0 && stuck.length === 0 && expired.length === 0,
    action: SEND_PENDING_PRODUCTION_DIRECTION_CARDS_ACTION,
    checked_at: nowIso,
    scanned_count: records.length,
    expired_scanned_count: expiredRecords.length,
    sending_scanned_count: sendingRecords.length,
    filter_fallback_error: records.filter_fallback_error || "",
    pending_count: pending.length,
    expired_count: expired.length,
    stuck_count: stuck.length,
    group_count: groups.length,
    sent,
    failed,
    stuck: stuck.map((record) => ({
      record_id: record.record_id,
      title: compact(record.fields?.["选题标题"], 80),
      error: stuckMessage,
    })),
    expire_days: cardExpireDays(),
  };
  const shouldNotify = summary.failed.length > 0 || summary.stuck_count > 0 || summary.expired_count > 0;
  if (shouldNotify && String(envValue("FEISHU_PRODUCTION_DIRECTION_ALERTS") || "true").toLowerCase() !== "false") {
    try {
      summary.notification = await notifyProductionDirectionQueueIssue(token, summary);
    } catch (error) {
      summary.notification = { sent_count: 0, error: errorText(error) };
    }
  }
  return summary;
}

function parseReceiveTargets() {
  const raw =
    envValue("FEISHU_PRODUCTION_DIRECTION_RECEIVE_TARGETS") ||
    envValue("FEISHU_CARD_RECEIVE_TARGETS") ||
    (envValue("FEISHU_CARD_RECEIVE_ID") && `${envValue("FEISHU_CARD_RECEIVE_ID_TYPE") || "open_id"}:${envValue("FEISHU_CARD_RECEIVE_ID")}`) ||
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

function scriptPackageQualityKey(recordId) {
  return `${SCRIPT_PACKAGE_QUALITY_FORM_PREFIX}${recordId}`;
}

function scriptPackageIssuesKey(recordId) {
  return `${SCRIPT_PACKAGE_ISSUES_FORM_PREFIX}${recordId}`;
}

function scriptPackageNoteKey(recordId) {
  return `${SCRIPT_PACKAGE_NOTE_FORM_PREFIX}${recordId}`;
}

function scriptPackageNoteKeys(recordId) {
  const base = scriptPackageNoteKey(recordId);
  return [base, `${base}__2`, `${base}__3`];
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

async function sendInteractiveCard(token, card, uuidBase) {
  const targets = parseReceiveTargets();
  if (!targets.length) {
    return { sent_count: 0, skipped: "missing_receive_targets" };
  }
  const sends = [];
  for (const target of targets) {
    const payload = await requestJson(
      "POST",
      `/im/v1/messages?receive_id_type=${encodeURIComponent(target.receive_id_type)}`,
      {
        token,
        body: {
          receive_id: target.receive_id,
          msg_type: "interactive",
          content: JSON.stringify(card),
          uuid: messageUuid("production-direction-card", [
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

async function allRecords(token, tableId, queryOptions = {}) {
  const records = [];
  let pageToken = "";
  while (true) {
    const query = new URLSearchParams({ page_size: "500" });
    if (pageToken) query.set("page_token", pageToken);
    if (queryOptions.filter) query.set("filter", JSON.stringify(queryOptions.filter));
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

async function updateRecordFields(token, tableId, recordId, fields) {
  return requestJson(
    "PUT",
    `/bitable/v1/apps/${envValue("FEISHU_BASE_APP_TOKEN")}/tables/${tableId}/records/${recordId}`,
    { token, body: { fields } },
  );
}

async function getRecord(token, tableId, recordId) {
  try {
    const payload = await requestJson(
      "GET",
      `/bitable/v1/apps/${envValue("FEISHU_BASE_APP_TOKEN")}/tables/${tableId}/records/${recordId}`,
      { token },
    );
    return payload.data?.record || null;
  } catch (error) {
    const message = String(error?.message || error);
    if (message.includes("HTTP 404") || message.includes("1254045")) return null;
    throw error;
  }
}

async function recordsById(token, tableId, recordIds) {
  const entries = await Promise.all(
    [...new Set(recordIds.filter(Boolean))].map(async (recordId) => [recordId, await getRecord(token, tableId, recordId)]),
  );
  return Object.fromEntries(entries.filter(([, record]) => record));
}

function runIdMismatch(record, runId) {
  const actualRunId = normalize(record?.fields?.["运行批次"]);
  return Boolean(runId && actualRunId && actualRunId !== runId);
}

async function selectionCardGuard(token, tableId, candidateIds, runId) {
  const records = await recordsById(token, tableId, candidateIds);
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

async function productionDirectionCardGuard(token, tableId, candidateIds, runId) {
  const records = await recordsById(token, tableId, candidateIds);
  const missing = candidateIds.filter((recordId) => recordId && !records[recordId]);
  if (missing.length) return { blocked: true, reason: "card_records_missing", missing };

  const mismatched = candidateIds.filter((recordId) => runIdMismatch(records[recordId], runId));
  if (mismatched.length) return { blocked: true, reason: "card_run_mismatch", record_ids: mismatched };

  const alreadyFilled = candidateIds.filter((recordId) => normalize(records[recordId]?.fields?.[PRODUCTION_DIRECTION_FIELD]));
  if (alreadyFilled.length) return { blocked: true, reason: "production_direction_card_already_submitted", record_ids: alreadyFilled };
  return { blocked: false };
}

async function scriptPackageQualityCardGuard(token, tableId, candidateIds) {
  const records = await recordsById(token, tableId, candidateIds);
  const missing = candidateIds.filter((recordId) => recordId && !records[recordId]);
  if (missing.length) return { blocked: true, reason: "card_records_missing", missing };
  return { blocked: false };
}

async function fieldsByName(token, tableId) {
  const payload = await requestJson(
    "GET",
    `/bitable/v1/apps/${envValue("FEISHU_BASE_APP_TOKEN")}/tables/${tableId}/fields`,
    { token },
  );
  return Object.fromEntries((payload.data?.items || []).map((field) => [field.field_name, field]));
}

async function ensureTextFields(token, tableId, fieldNames) {
  const existing = await fieldsByName(token, tableId);
  const created = [];
  for (const fieldName of fieldNames) {
    if (existing[fieldName]) continue;
    await requestJson("POST", `/bitable/v1/apps/${envValue("FEISHU_BASE_APP_TOKEN")}/tables/${tableId}/fields`, {
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
  const selected = new Set(coerceList(formValue[ENTER_SCRIPT_PACKAGE_FORM_KEY]));
  for (const recordId of selected) {
    decisions[recordId] = { status: SCRIPT_PACKAGE_READY_STATUS, tags: positiveTags, manual_reason: manualReason };
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
  if (!queueInfo?.enabled || decision.status !== SCRIPT_PACKAGE_READY_STATUS) return {};
  return {
    [PRODUCTION_DIRECTION_CARD_STATUS_FIELD]: PRODUCTION_DIRECTION_CARD_PENDING,
    [SELECTION_SUBMISSION_ID_FIELD]: queueInfo.submissionId,
    [SELECTION_SUBMITTED_AT_FIELD]: queueInfo.submittedAt,
    [PRODUCTION_DIRECTION_CARD_SENT_AT_FIELD]: "",
    [PRODUCTION_DIRECTION_CARD_ERROR_FIELD]: "",
  };
}

async function applyFormValue(token, tableId, formValue, { candidateIds, runId, forceNoSelection, queueInfo }) {
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
    await Promise.all(updates.map((update) => updateRecordFields(token, tableId, update.record_id, update.fields)));
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
      .filter((recordId) => decisions[recordId]?.status === SCRIPT_PACKAGE_READY_STATUS && records[recordId])
      .map((recordId) => records[recordId]),
  };
}

async function applyFormValueFast(token, tableId, formValue, { candidateIds, runId, forceNoSelection, snapshots, queueInfo }) {
  const decisions = decisionsFromForm(formValue, candidateIds, forceNoSelection);
  const dryRun = String(envValue("DRY_RUN")).toLowerCase() === "true";
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
    await Promise.all(updates.map((update) => updateRecordFields(token, tableId, update.record_id, update.fields)));
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
      .filter((recordId) => decisions[recordId]?.status === SCRIPT_PACKAGE_READY_STATUS)
      .map((recordId) => snapshotRecord(recordId, snapshots[recordId] || {})),
  };
}

async function applyProductionDirections(token, tableId, formValue, { candidateIds, runId }) {
  const dryRun = String(envValue("DRY_RUN")).toLowerCase() === "true";
  if (!dryRun && String(envValue("ENSURE_PRODUCTION_DIRECTION_FIELD")).toLowerCase() === "true") {
    await ensureTextFields(token, tableId, [PRODUCTION_DIRECTION_FIELD]);
  }
  const updates = [];
  const skipped = [];

  for (const recordId of candidateIds || []) {
    const direction = compact(formValue[productionDirectionKey(recordId)], 1000);
    const fields = {
      [PRODUCTION_DIRECTION_CARD_STATUS_FIELD]: PRODUCTION_DIRECTION_CARD_SUBMITTED,
      [PRODUCTION_DIRECTION_CARD_ERROR_FIELD]: "",
    };
    if (!direction) {
      skipped.push({ record_id: recordId, reason: "empty_direction" });
    } else {
      fields[PRODUCTION_DIRECTION_FIELD] = direction;
    }
    updates.push({
      record_id: recordId,
      fields,
    });
  }

  if (!dryRun) {
    await Promise.all(updates.map((update) => updateRecordFields(token, tableId, update.record_id, update.fields)));
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

async function applyScriptPackageQualityFeedback(token, tableId, formValue, { candidateIds }) {
  const dryRun = String(envValue("DRY_RUN")).toLowerCase() === "true";
  const updates = [];
  const skipped = [];
  const feedbackAt = new Date().toISOString();

  if (!dryRun && String(envValue("ENSURE_SCRIPT_PACKAGE_QUALITY_FIELDS") || "true").toLowerCase() !== "false") {
    await ensureTextFields(token, tableId, SCRIPT_PACKAGE_QUALITY_FIELDS);
  }

  for (const recordId of candidateIds || []) {
    const quality = compact(formValue[scriptPackageQualityKey(recordId)], 80);
    const issues = coerceList(formValue[scriptPackageIssuesKey(recordId)]);
    const note = compact(scriptPackageNoteKeys(recordId).map((key) => normalize(formValue[key])).filter(Boolean).join("\n"), 2000);
    if (!quality && issues.length === 0 && !note) {
      skipped.push({ record_id: recordId, reason: "empty_feedback" });
      continue;
    }
    updates.push({
      record_id: recordId,
      fields: {
        "人工质量反馈": quality,
        "质量问题标签": issues.join("、"),
        "人工修改意见": note,
        "反馈时间": feedbackAt,
        "反馈来源": "06完成卡",
        "内容学习状态": "待学习",
      },
    });
  }

  if (!dryRun) {
    await Promise.all(updates.map((update) => updateRecordFields(token, tableId, update.record_id, update.fields)));
  }

  return {
    ok: true,
    mode: dryRun ? "dry-run" : "write",
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
  const expiry = cardExpiryStatus(value);
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
    effectiveFormValue[ENTER_SCRIPT_PACKAGE_FORM_KEY] = [];
    effectiveFormValue.positive_reason_tags = [];
  }

  const token = await tenantToken();
  if (actionName === SUBMIT_SCRIPT_PACKAGE_QUALITY_ACTION) {
    const tableId = await scriptPackageTableId(token);
    const guard = await scriptPackageQualityCardGuard(token, tableId, candidateIds);
    if (guard.blocked) {
      return {
        ok: false,
        ...guard,
        action: actionName,
        updated_count: 0,
        candidate_update_count: 0,
      };
    }
    const feedbackSummary = await applyScriptPackageQualityFeedback(token, tableId, effectiveFormValue, {
      candidateIds,
    });
    feedbackSummary.action = actionName;
    feedbackSummary.receipt_key = submissionFingerprint(actionName, runId, candidateIds, effectiveFormValue);
    return feedbackSummary;
  }

  const tableId = await topicTableId(token);
  if (actionName === SUBMIT_PRODUCTION_DIRECTIONS_ACTION) {
    const guard = await productionDirectionCardGuard(token, tableId, candidateIds, runId);
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
    const directionSummary = await applyProductionDirections(token, tableId, effectiveFormValue, {
      candidateIds,
      runId,
    });
    directionSummary.action = actionName;
    directionSummary.receipt_key = submissionFingerprint(actionName, runId, candidateIds, effectiveFormValue);
    return directionSummary;
  }

  const guard = await selectionCardGuard(token, tableId, candidateIds, runId);
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

  const receiptKey = submissionFingerprint(actionName, runId, candidateIds, effectiveFormValue);
  const queueInfo = {
    enabled: actionName === SUBMIT_SELECTION_ACTION && String(envValue("SEND_PRODUCTION_DIRECTION_CARD") || "true").toLowerCase() !== "false",
    submissionId: `${runId || "selection"}:${receiptKey.slice(0, 12)}`,
    submittedAt: new Date().toISOString(),
  };
  const hasSnapshots = Object.keys(candidateSnapshots).length > 0;
  const summary = hasSnapshots
    ? await applyFormValueFast(token, tableId, effectiveFormValue, {
        candidateIds,
        runId,
        forceNoSelection: actionName === SUBMIT_NO_SELECTION_ACTION,
        snapshots: candidateSnapshots,
        queueInfo,
      })
    : await applyFormValue(token, tableId, effectiveFormValue, {
        candidateIds,
        runId,
        forceNoSelection: actionName === SUBMIT_NO_SELECTION_ACTION,
        queueInfo,
      });
  if (shouldQueueProductionDirectionCard(actionName, summary)) {
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

async function handlePayload(payload) {
  if (payload.challenge) return jsonResponse({ challenge: payload.challenge });
  if (payload.encrypt) {
    return toast("error", "暂不支持加密回调，请先关闭事件加密或改用带解密的版本");
  }

  if (payload.action === SEND_PENDING_PRODUCTION_DIRECTION_CARDS_ACTION) {
    const expectedRunnerToken = envValue("FEISHU_QUEUE_RUNNER_TOKEN");
    const actualRunnerToken = payload.runner_token || payload.token || "";
    if (expectedRunnerToken && actualRunnerToken !== expectedRunnerToken) {
      return jsonResponse({ ok: false, error: "runner token mismatch" }, 403);
    }
    return jsonResponse(await sendPendingProductionDirectionCards());
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
    if (summary.action === SUBMIT_SCRIPT_PACKAGE_QUALITY_ACTION) {
      const count = summary.updated_count;
      if (count === 0) return toast("warning", "没有保存新的质量反馈");
      return toast("success", `已保存 ${count} 条质量反馈`);
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

function parsePayload(event) {
  if (!event) return {};
  if (typeof event.body === "string" && event.body.trim()) {
    const body = event.isBase64Encoded ? Buffer.from(event.body, "base64").toString("utf8") : event.body;
    return JSON.parse(body);
  }
  for (const key of ["Message", "message"]) {
    if (typeof event[key] === "string" && event[key].trim()) {
      try {
        return JSON.parse(event[key]);
      } catch (_error) {
        return {};
      }
    }
  }
  if (typeof event === "object" && (event.challenge || event.event || event.encrypt || event.action)) return event;
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
