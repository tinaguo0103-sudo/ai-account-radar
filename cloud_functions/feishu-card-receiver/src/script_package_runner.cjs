const DEFAULT_API_HOST = "https://open.feishu.cn";
const TOPIC_TABLE_NAMES = ["04 分析与选题", "03 分析与选题"];
const SCRIPT_PACKAGE_TABLE_NAMES = ["06 完整脚本与制作包", "06 内容任务主表"];
const SCRIPT_VERSION = "austin-cloud-script-package-runner-v0.1";
const TOPIC_MARK_FIELD = "是否已生成脚本稿";
const FULL_PACKAGE_FIELD = "完整脚本与执行包";
const SCRIPT_PACKAGE_FIELDS = [
  "关联选题",
  "脚本状态",
  "推荐模板",
  "核心观点",
  "开头钩子",
  "本地文档",
  "素材提醒",
  "发布前核验",
  "QA结果",
  "是否可拍",
  "版本",
  FULL_PACKAGE_FIELD,
];
const REQUIRED_FIELDS = [
  "topic_title",
  "core_thesis",
  "pain_point",
  "target_audience",
  "old_workflow",
  "ai_intervention",
  "takeaway_asset",
];
const TOKEN_CACHE_SAFETY_SECONDS = 300;

let cachedTenantToken = { value: "", expiresAt: 0 };

function envValue(env, key) {
  return env[key] || "";
}

function boolEnv(env, key, fallback = false) {
  const raw = String(envValue(env, key) || "").trim().toLowerCase();
  if (!raw) return fallback;
  return ["1", "true", "yes", "y", "on"].includes(raw);
}

function intEnv(env, key, fallback) {
  const value = Number(envValue(env, key));
  return Number.isFinite(value) && value >= 0 ? value : fallback;
}

function apiBaseUrl(env) {
  const host = String(envValue(env, "FEISHU_API_BASE_URL") || DEFAULT_API_HOST).replace(/\/+$/, "");
  return host.endsWith("/open-apis") ? host : `${host}/open-apis`;
}

function normalize(value) {
  if (value == null) return "";
  if (Array.isArray(value)) return value.map((item) => normalize(item)).filter(Boolean).join("、");
  if (typeof value === "object") return String(value.text || "").trim();
  return String(value).trim();
}

function compact(value, limit = 240) {
  const text = normalize(value).replace(/\s+/g, " ");
  return text.length > limit ? `${text.slice(0, Math.max(1, limit - 1)).trimEnd()}…` : text;
}

function isEmptyish(value) {
  const text = normalize(value).replace(/[。.!！?？；;，,、\s]+$/g, "");
  return !text || ["无", "暂无", "none", "null", "nan", "n/a"].includes(text.toLowerCase());
}

function firstNonEmpty(fields, names, fallback = "") {
  for (const name of names) {
    const value = fields[name];
    if (Array.isArray(value)) {
      const joined = value.map(normalize).filter(Boolean).join("、");
      if (!isEmptyish(joined)) return joined;
    } else if (!isEmptyish(value)) {
      return normalize(value);
    }
  }
  return fallback;
}

function splitItems(value) {
  if (value == null) return [];
  if (Array.isArray(value)) return value.map(normalize).filter(Boolean);
  const text = normalize(value);
  if (!text) return [];
  return text
    .split(/[；;\n、]+|(?<=。)/g)
    .map((item) => normalize(item).replace(/[。；;]+$/g, ""))
    .filter(Boolean);
}

function unique(items) {
  const seen = new Set();
  const result = [];
  for (const item of items) {
    const text = normalize(item);
    if (!text || seen.has(text)) continue;
    seen.add(text);
    result.push(text);
  }
  return result;
}

function readyStatus(value) {
  const text = normalize(value);
  return text === "进入Brief" || text === "本周做" || text.includes("进入Brief") || text.includes("本周做") || text.includes("进入制作");
}

function markdownBullets(items, fallback = "待补") {
  const clean = items.map(normalize).filter(Boolean);
  return clean.length ? clean.map((item) => `- ${item}`).join("\n") : `- ${fallback}`;
}

function markdownNumbered(items, fallback = "待补") {
  const clean = items.map(normalize).filter(Boolean);
  return clean.length ? clean.map((item, index) => `${index + 1}. ${item}`).join("\n") : `1. ${fallback}`;
}

function inlineItems(items, fallback = "待补", limit = 3) {
  const clean = items.map((item) => compact(item, 52)).filter(Boolean);
  return clean.length ? clean.slice(0, limit).join(" / ") : fallback;
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

async function tenantToken(env, deps = {}) {
  if (!envValue(env, "FEISHU_APP_ID") || !envValue(env, "FEISHU_APP_SECRET")) {
    throw new Error("Missing FEISHU_APP_ID or FEISHU_APP_SECRET");
  }
  if (cachedTenantToken.value && cachedTenantToken.expiresAt > Date.now()) {
    return cachedTenantToken.value;
  }
  const payload = await requestJson(env, "POST", "/auth/v3/tenant_access_token/internal", {
    body: {
      app_id: envValue(env, "FEISHU_APP_ID"),
      app_secret: envValue(env, "FEISHU_APP_SECRET"),
    },
    fetchImpl: deps.fetchImpl,
  });
  const token = payload.tenant_access_token;
  if (!token) throw new Error("Feishu did not return tenant_access_token");
  const ttlSeconds = Math.max(60, Number(payload.expire || 7200) - TOKEN_CACHE_SAFETY_SECONDS);
  cachedTenantToken = { value: token, expiresAt: Date.now() + ttlSeconds * 1000 };
  return token;
}

async function listTables(env, token, deps = {}) {
  const payload = await requestJson(env, "GET", `/bitable/v1/apps/${envValue(env, "FEISHU_BASE_APP_TOKEN")}/tables`, {
    token,
    fetchImpl: deps.fetchImpl,
  });
  return payload.data?.items || [];
}

function findTableId(tables, names, explicit = "") {
  if (explicit) return explicit;
  const found = tables.find((table) => names.includes(table.name));
  return found?.table_id || "";
}

async function allRecords(env, token, tableId, deps = {}) {
  const records = [];
  let pageToken = "";
  while (true) {
    const suffix = `?page_size=500${pageToken ? `&page_token=${encodeURIComponent(pageToken)}` : ""}`;
    const payload = await requestJson(
      env,
      "GET",
      `/bitable/v1/apps/${envValue(env, "FEISHU_BASE_APP_TOKEN")}/tables/${tableId}/records${suffix}`,
      { token, fetchImpl: deps.fetchImpl },
    );
    const data = payload.data || {};
    records.push(...(data.items || []));
    if (!data.has_more) return records;
    pageToken = data.page_token || "";
  }
}

async function fieldsByName(env, token, tableId, deps = {}) {
  const payload = await requestJson(
    env,
    "GET",
    `/bitable/v1/apps/${envValue(env, "FEISHU_BASE_APP_TOKEN")}/tables/${tableId}/fields`,
    { token, fetchImpl: deps.fetchImpl },
  );
  return Object.fromEntries((payload.data?.items || []).map((field) => [field.field_name, field]));
}

async function ensureTextFields(env, token, tableId, fieldNames, deps = {}) {
  const existing = await fieldsByName(env, token, tableId, deps);
  const created = [];
  for (const fieldName of fieldNames) {
    if (existing[fieldName]) continue;
    await requestJson(
      env,
      "POST",
      `/bitable/v1/apps/${envValue(env, "FEISHU_BASE_APP_TOKEN")}/tables/${tableId}/fields`,
      {
        token,
        body: { field_name: fieldName, type: 1 },
        fetchImpl: deps.fetchImpl,
      },
    );
    created.push(fieldName);
  }
  return created;
}

async function createRecord(env, token, tableId, fields, deps = {}) {
  const payload = await requestJson(
    env,
    "POST",
    `/bitable/v1/apps/${envValue(env, "FEISHU_BASE_APP_TOKEN")}/tables/${tableId}/records`,
    { token, body: { fields }, fetchImpl: deps.fetchImpl },
  );
  return payload.data?.record || payload.data || {};
}

async function updateRecord(env, token, tableId, recordId, fields, deps = {}) {
  return requestJson(
    env,
    "PUT",
    `/bitable/v1/apps/${envValue(env, "FEISHU_BASE_APP_TOKEN")}/tables/${tableId}/records/${recordId}`,
    { token, body: { fields }, fetchImpl: deps.fetchImpl },
  );
}

function normalizeTopic(record) {
  const fields = record.fields || {};
  const topicTitle = firstNonEmpty(fields, ["topic_title", "选题命题", "我的选题标题", "选题标题", "可发布标题"], "未命名选题");
  return {
    topic_id: firstNonEmpty(fields, ["topic_id", "选题ID", "内容指纹"], record.record_id || ""),
    record_id: record.record_id || "",
    status: firstNonEmpty(fields, ["status", "状态", "推荐动作", "脚本状态"], ""),
    topic_title: topicTitle,
    content_pillar: firstNonEmpty(fields, ["content_pillar", "对应方向", "对应栏目", "业务场景"], "真实工作流改造"),
    core_thesis: firstNonEmpty(fields, ["core_thesis", "一句话Brief", "重点体现", "选题命题", "我的切入", "选题标题", "可发布标题"], topicTitle),
    target_audience: firstNonEmpty(fields, ["target_audience", "目标观众", "影响对象", "业务场景"], "内容团队、品牌方、创作者、创业者"),
    pain_point: firstNonEmpty(fields, ["pain_point", "我的工作流痛点", "旧流程痛点", "我的场景拆解", "真实用户问题"], ""),
    old_workflow: firstNonEmpty(fields, ["old_workflow", "旧流程痛点", "我的场景拆解"], ""),
    ai_intervention: firstNonEmpty(fields, ["ai_intervention", "AI介入点", "我要做的实验", "验证方式"], ""),
    demo_materials: splitItems(firstNonEmpty(fields, ["demo_materials", "可展示证据", "可展示结果", "演示素材"], "")),
    missing_evidence: splitItems(firstNonEmpty(fields, ["missing_evidence", "需要补的证据", "证据缺口"], "")),
    production_direction: firstNonEmpty(fields, ["production_direction", "我的制作补充", "制作方向", "使用案例", "人工制作补充"], ""),
    unique_judgment: firstNonEmpty(fields, ["unique_judgment", "人工一句话判断", "我的思考点", "主编判断", "选题判断", "我的切入"], ""),
    takeaway_asset: firstNonEmpty(fields, ["takeaway_asset", "可沉淀资产", "资料包承接方式", "重点体现"], ""),
    fact_check_points: splitItems(firstNonEmpty(fields, ["fact_check_points", "不能声称的部分", "不能照搬/风险提示", "风险点"], "")),
    source_fields: fields,
  };
}

function workflowObject(topic) {
  const text = [
    topic.topic_title,
    topic.content_pillar,
    topic.core_thesis,
    topic.pain_point,
    topic.old_workflow,
    topic.ai_intervention,
    topic.production_direction,
    topic.takeaway_asset,
  ].join(" ").toLowerCase();
  if (/[汽車车]|智能驾驶|辅助驾驶|l3|l4|国标/i.test(text)) return "汽车AI内容";
  if (/候选池|表格|飞书|选题|brief|主编|门控/i.test(text)) return "选题台";
  if (/ppt|pptx|导出|样式|视觉|baoyu|设计/i.test(text)) return "视觉交付";
  if (/封面/i.test(text)) return "封面流程";
  if (/agent|claude|codex|自动执行|任务拆解/i.test(text)) return "Agent任务";
  return "AI流程";
}

function classifyTemplate(topic) {
  const text = [topic.topic_title, topic.content_pillar, topic.core_thesis, topic.pain_point, topic.ai_intervention].join(" ").toLowerCase();
  if (/复盘|揭秘|交付|三天|从idea到成片/i.test(text)) return ["项目复盘型", "标题或场景指向项目过程、交付难点和方法沉淀。"];
  if (/agent|codex|claude|知识库|监控|自动执行/i.test(text)) return ["Agent实战型", "内容涉及Agent任务边界、执行过程或验收。"];
  if (/为什么|2026|一定要|文科生|业务人/i.test(text)) return ["认知定调型", "内容更像能力模型或认知立场，需要结论先行。"];
  if (/更新|上新|发布|模型|插件/i.test(text)) return ["热点业务转译型", "内容从外部热点进入，需要转译成业务场景和边界。"];
  if (/skill|自动化|公开|模板|一键生成|工作流/i.test(text)) return ["Skill公开型", "内容重点是把高频流程讲清，并保留可复用方向。"];
  return ["真实工作流改造型", "默认按真实业务场景、旧流程、新流程和证据判断来拍。"];
}

function validateTopic(topic) {
  const missingRequired = REQUIRED_FIELDS.filter((field) => !normalize(topic[field]));
  const evidenceGaps = [...topic.missing_evidence];
  const notes = [];
  const factCheckPoints = [...topic.fact_check_points];
  if (!topic.demo_materials.length) evidenceGaps.push("缺少可展示证据：需要截图、录屏、结果对比或实际输出。");
  if (!topic.unique_judgment) notes.push("缺少独有判断：需要补奥斯汀的主观判断、取舍或人工修正点。");
  const factText = [
    topic.topic_title,
    topic.core_thesis,
    topic.ai_intervention,
    ...Object.values(topic.source_fields || {}).map(normalize),
  ].join(" ");
  if (/OpenAI|ChatGPT|Codex|Claude|飞书|价格|规则|更新|发布/i.test(factText)) {
    factCheckPoints.push("涉及产品能力、平台规则或更新信息，发布前需事实核验。");
  }
  if (/政策|法规|国标|强制性|公示|实施|监管|国家标准|行业标准/i.test(factText)) {
    factCheckPoints.push("涉及政策、法规、国标、公示或实施时间，发布前需核验权威原文和具体日期。");
  }
  if (/L3|L4|自动驾驶|智能驾驶|辅助驾驶|功能安全/i.test(factText)) {
    factCheckPoints.push("涉及智能驾驶等级、功能安全或汽车功能边界，发布前需核验官方定义，不能扩大声称。");
  }
  const status = missingRequired.length ? "blocked" : notes.length || evidenceGaps.some((item) => item.includes("缺少可展示证据")) ? "revise" : "pass";
  return {
    status,
    missing_required: missingRequired,
    evidence_gaps: unique(evidenceGaps),
    fact_check_points: unique(factCheckPoints),
    notes,
  };
}

function hookFor(topic, validation) {
  const object = workflowObject(topic);
  if (validation.status === "blocked") return "这条先别急着拍，缺的不是标题，是能支撑它的真实任务样本。";
  if (/claude/i.test(topic.topic_title) && /验收|agent|原则/i.test([topic.core_thesis, topic.ai_intervention].join(" "))) {
    return "我不是想学 Claude Code 的原则，我想看 AI 任务交出去以后谁来验。";
  }
  if (object === "汽车AI内容") return "汽车内容省一分钟可以，但一句卖点越界就不值得。";
  if (object === "视觉交付") return "AI设计截图好看不算数，导出以后不变形才算数。";
  if (object === "封面流程") return "封面自动化最怕的不是不出图，是每张都像另一个账号。";
  if (object === "Agent任务") return "AI任务最怕的不是没跑完，是跑完以后没人知道错在哪。";
  if (object === "选题台") return "选题台最怕的不是没灵感，是每条看起来都能做。";
  return "AI流程最怕的不是慢，是快到没人知道哪里该验收。";
}

function keyJudgments(topic) {
  const object = workflowObject(topic);
  const base = topic.unique_judgment ? [topic.unique_judgment] : [];
  const map = {
    汽车AI内容: ["汽车内容里的AI提效，必须先过功能边界和证据线。", "热点只能给入口，能不能上线要看风险复核。"],
    选题台: ["选题台不是灵感池，是把能做和不该做分开的判断系统。", "AI可以帮我初筛，但升级或放弃必须留下理由。"],
    视觉交付: ["视觉AI能不能进交付，不看截图好不好看，看导出后还剩多少人工修正。", "最后一公里不稳定，前面的生成效率都不算数。"],
    封面流程: ["封面自动化不是让AI随机出图，是把账号视觉和标题规则锁住。", "如果每张封面都要重新解释风格，自动化就没有成立。"],
    Agent任务: ["Agent任务能不能进流程，不看跑没跑完，看输入、输出、异常和人工判断能不能对上。", "真正的AI改造，是把人的判断、异常和回滚留在现场。"],
    AI流程: ["AI改造的价值，是把判断留在流程里，而不是把步骤藏进黑箱。", "没有证据链的AI，只是一次演示。"],
  };
  return unique([...base, ...(map[object] || map.AI流程)]);
}

function goldenLines(topic) {
  const object = workflowObject(topic);
  const map = {
    汽车AI内容: ["汽车内容先守边界，再谈效率。", "卖点可以被AI放大，责任不能。", "能过风险线的内容，才配上线。"],
    选题台: ["选题不是灵感池，是判断系统。", "能说清为什么不做，才算真的会选题。", "好的选题台，先挡住泛资讯。"],
    视觉交付: ["导不出来的设计，不算交付。", "好看的截图，不等于能交付的文件。", "导出稳定，才算真正进交付。"],
    封面流程: ["封面不是出图，是账号识别。", "随机好看，不如稳定像我。", "能复用的风格，才是封面系统。"],
    Agent任务: ["没有验收记录的Agent，只是跑得更快的黑箱。", "能追责的AI，才配进流程。", "自动化不是省人，是把人的判断固定下来。"],
    AI流程: ["能复用的才叫流程，不能复用的只是表演。", "AI越快，验收越要慢半拍。", "没有证据链的AI，只是一次演示。"],
  };
  return map[object] || map.AI流程;
}

function evidenceItems(topic, validation) {
  return unique([...topic.demo_materials, ...validation.evidence_gaps]).slice(0, 5);
}

function releaseReminders(validation) {
  return validation.fact_check_points;
}

function shootingReminders(validation) {
  return validation.evidence_gaps.filter((item) => !item.includes("发布前") && !item.includes("事实核验"));
}

function coreViewpoint(topic, validation) {
  const pain = compact(topic.old_workflow || topic.pain_point, 90);
  const aiAction = compact(topic.ai_intervention, 90);
  const judgment = compact(topic.unique_judgment || "AI只能辅助判断，最终取舍仍然要回到人的业务标准", 90);
  const evidence = inlineItems(evidenceItems(topic, validation), "输入、输出和人工验收画面", 2);
  if (validation.status === "blocked") {
    return `这条现在先不拍。原因不是话题弱，而是它还没有贴到真实现场：${inlineItems(validation.missing_required, "必填字段缺失", 3)}。如果要救它，先补真实任务、输入输出和成败证据。`;
  }
  return `我想把这条拍成一个小实验：${compact(topic.core_thesis, 90)}。\n\n它真正要证明的是：${judgment}。旧流程里的卡点是：${pain}。\n\n开头先给${evidence}，中段再录${aiAction}。具体案例和素材选择服从真实材料，不硬指定。`;
}

function outlineSegments(topic, validation) {
  const hook = hookFor(topic, validation);
  const pain = compact(topic.old_workflow || topic.pain_point, 70);
  const judgment = compact(topic.unique_judgment || "我的判断和边界", 70);
  const aiAction = compact(topic.ai_intervention, 78);
  const evidence = inlineItems(evidenceItems(topic, validation), "关键截图、录屏或前后对比", 2);
  return [
    `00:00-00:10｜开场钩子：${hook}`,
    `00:10-00:40｜真实痛点：交代${pain}，画面给旧流程或任务卡住的现场。`,
    `00:40-01:15｜核心判断：切真人，说清${judgment}。`,
    `01:15-02:30｜实操主线：只跑一个小任务，展示${aiAction}。`,
    `02:30-03:20｜失败和修正：放出${evidence}，说明哪里必须人工接手。`,
    "03:20-04:00｜收尾判断：回到是否值得继续拍；不要把提醒说成已经完成。",
  ];
}

function renderTeleprompter(topic, validation) {
  const opening = hookFor(topic, validation);
  const pain = topic.pain_point || topic.old_workflow || "旧流程里有一个真实卡点";
  const oldFlow = topic.old_workflow || pain;
  const aiAction = topic.ai_intervention || "让 AI 介入一个可以验收的小环节";
  const judgment = topic.unique_judgment || "AI真正要进入业务流程，必须留下可验收的证据";
  const asset = topic.takeaway_asset || "一份可复用的流程清单";
  const evidence = inlineItems(evidenceItems(topic, validation), "输入、输出、异常和人工验收画面", 3);
  const todos = inlineItems(shootingReminders(validation), "拍摄前不额外补P0素材", 2);
  const direction = topic.production_direction ? `\n\n这条我会按第二张卡里的制作方向收住：${topic.production_direction}。` : "";
  return [
    `### 00:00-00:10｜开场钩子\n\n${opening}`,
    `### 00:10-00:40｜真实痛点\n\n我现在做 AI 项目，最怕的不是它不会执行，而是它执行完以后，我不知道怎么验收。\n\n${pain}。${oldFlow}。所以最后经常会变成：AI 跑了一堆东西，我还是要靠感觉判断能不能用。${direction}`,
    `### 00:40-01:15｜核心判断\n\n所以这条的重点不是复述「${topic.topic_title}」。我真正想测的是：${topic.core_thesis}。\n\n我的判断很简单：${judgment}。\n\n一个 AI 工作流如果只有结果，没有状态、异常和验收记录，它只是看起来自动化了。真的进入业务，必须能追责、能回滚、能复盘。`,
    `### 01:15-02:30｜实操主线\n\n我会拿一条真实小任务来跑，不做大而全。\n\n第一步，先把任务输入说清楚：我要处理什么资料，最后交付什么结果。\n\n第二步，把 AI 的动作限制住：${aiAction}。\n\n第三步，看验收表，而不是只看最终答案。这里至少要留下三类画面：${evidence}。`,
    `### 02:30-03:20｜失败和人工修正\n\n这一段一定要放失败样例。因为我不想把它讲成“AI 一跑就对”。\n\n如果中间缺了输入、输出、异常原因，或者验收结论写不清楚，我会直接判失败。然后我再补一轮人工修正，看这套流程到底能不能减少我的返工。\n\n拍摄前还要补：${todos}。`,
    `### 03:20-04:00｜收尾判断\n\n最后我不会说这套东西已经完美解决问题。\n\n我只想证明一件事：AI 任务不是交出去就结束，而是从一开始就要设计它怎么被验收。\n\n如果这次能跑通，它后面可以沉淀成${asset}；如果跑不通，也很好，至少我知道问题不是模型不够强，而是我的任务拆解和验收字段还不够清楚。`,
  ].join("\n\n");
}

function qaStatusText(validation) {
  if (validation.status === "blocked") return "完整脚本包-阻塞";
  if (validation.status === "revise") return "完整脚本包-待修订";
  return "已生成完整脚本包";
}

function canShootText(validation) {
  if (validation.status === "blocked") return "否：先补字段";
  if (validation.status === "revise") return "否：先修订关键判断或证据";
  return "是：可拍；按素材提醒和发布前核验处理";
}

function qaIssues(validation) {
  return unique([...validation.missing_required, ...validation.evidence_gaps, ...validation.notes]);
}

function renderFullMarkdown(topic, validation, template, templateReason) {
  const hooks = [hookFor(topic, validation), "先看证据能不能撑住观点，再决定这条值不值得拍。", "拿不出输入输出和人工验收画面，就先别急着讲成案例。"];
  const judgments = keyJudgments(topic);
  const lines = goldenLines(topic);
  const outline = outlineSegments(topic, validation);
  const evidence = evidenceItems(topic, validation);
  const reminders = shootingReminders(validation);
  const release = releaseReminders(validation);
  const issueRows = qaIssues(validation);
  return `# ${topic.topic_title}

> 云端生成说明：本文由腾讯云 SCF 定时任务生成。完整 Markdown 暂存在飞书 06 字段「${FULL_PACKAGE_FIELD}」；后续可迁移到腾讯 COS 或飞书云文档。

## 先看结论

- QA：${validation.status}
- 推荐模板：${template}（${templateReason}）
- 是否可拍：${canShootText(validation)}
- 下一步：${validation.status === "pass" ? "打开本执行包，人工确认口播和素材后进入拍摄。" : "先补关键判断、证据或必填字段，再重新生成。"}

## 核心观点

${coreViewpoint(topic, validation)}

## 开头钩子候选

${markdownNumbered(hooks)}

## 中段关键判断

${markdownBullets(judgments)}

## 可用金句

${markdownBullets(lines)}

## 视频结构

${markdownNumbered(outline)}

## 口播全文

${renderTeleprompter(topic, validation)}

## 录屏与素材清单

- 已有/计划证据：${inlineItems(evidence, "待补一组截图、录屏或结果对比", 5)}
- 拍摄提醒：${inlineItems(reminders, "无P0素材缺口", 4)}
- 发布前核验：${inlineItems(release, "无额外事实核验点", 4)}

## 剪辑交接

- 开头 10 秒先给结果、冲突或失败画面，不先铺背景。
- 实操段只保留输入、AI动作、输出、人工验收这四类画面。
- 失败样例和人工修正必须留下，不要剪成“AI一次就对”。
- 字幕重点放在判断句和金句，不要把所有解释塞进口播。

## 发布包草稿

- 标题方向：${compact(topic.topic_title, 42)}
- 适合平台：视频号 / 抖音 / 小红书视频，先按 3-4 分钟口播结构准备。
- CTA：如果你也在把 AI 接进真实工作流，先别急着自动化，先把验收字段写出来。

## QA 报告

- 状态：${validation.status}
- 问题：${inlineItems(issueRows, "可进入拍摄准备", 8)}
- 边界：事实核验、素材补拍和是否最终发布仍由人工确认。
`;
}

function renderPackage(record) {
  const topic = normalizeTopic(record);
  const [template, templateReason] = classifyTemplate(topic);
  const validation = validateTopic(topic);
  const markdown = renderFullMarkdown(topic, validation, template, templateReason);
  return {
    topic,
    validation,
    template,
    templateReason,
    markdown,
    row: {
      "关联选题": topic.topic_title,
      "脚本状态": qaStatusText(validation),
      "推荐模板": template,
      "核心观点": coreViewpoint(topic, validation),
      "开头钩子": hookFor(topic, validation).slice(0, 500),
      "本地文档": `腾讯云SCF生成：完整内容见 06 字段「${FULL_PACKAGE_FIELD}」`,
      "素材提醒": inlineItems(shootingReminders(validation), "无P0素材缺口", 4),
      "发布前核验": inlineItems(releaseReminders(validation), "无额外事实核验点", 4),
      "QA结果": `${validation.status}｜${inlineItems(qaIssues(validation), "可进入拍摄准备", 8)}`.slice(0, 1000),
      "是否可拍": canShootText(validation),
      "版本": SCRIPT_VERSION,
      [FULL_PACKAGE_FIELD]: markdown,
    },
  };
}

async function runScriptPackageJob(event = {}, env = process.env, deps = {}) {
  if (!envValue(env, "FEISHU_BASE_APP_TOKEN")) throw new Error("Missing FEISHU_BASE_APP_TOKEN");
  const dryRun = boolEnv(env, "AUSTIN_SCRIPT_PACKAGE_DRY_RUN", false) || Boolean(event.dry_run);
  const limit = Number(event.limit ?? intEnv(env, "AUSTIN_SCRIPT_PACKAGE_LIMIT", 3));
  const token = await tenantToken(env, deps);
  const tables = await listTables(env, token, deps);
  const topicTableId = findTableId(tables, TOPIC_TABLE_NAMES, envValue(env, "FEISHU_TOPIC_TABLE_ID"));
  const scriptTableId = findTableId(tables, SCRIPT_PACKAGE_TABLE_NAMES, envValue(env, "FEISHU_SCRIPT_PACKAGE_TABLE_ID"));
  if (!topicTableId) throw new Error(`Missing topic table. Expected one of: ${TOPIC_TABLE_NAMES.join(", ")}`);
  if (!scriptTableId) throw new Error(`Missing script package table. Expected one of: ${SCRIPT_PACKAGE_TABLE_NAMES.join(", ")}`);

  const records = await allRecords(env, token, topicTableId, deps);
  const ready = records
    .filter((record) => {
      const fields = record.fields || {};
      return readyStatus(fields["状态"] || fields["推荐动作"]) && normalize(fields[TOPIC_MARK_FIELD]) !== "是";
    })
    .slice(0, Math.max(0, limit || 0) || records.length);

  const packages = ready.map(renderPackage);
  const result = {
    ok: true,
    mode: dryRun ? "dry-run" : "write",
    topic_table_id: topicTableId,
    script_package_table_id: scriptTableId,
    ready_topics: ready.length,
    created_script_packages: 0,
    marked_topics: 0,
    version: SCRIPT_VERSION,
    packages: packages.map((item) => ({
      record_id: item.topic.record_id,
      topic_title: item.topic.topic_title,
      qa_status: item.validation.status,
      recommended_template: item.template,
    })),
  };

  if (dryRun || ready.length === 0) return result;

  await ensureTextFields(env, token, scriptTableId, SCRIPT_PACKAGE_FIELDS, deps);
  await ensureTextFields(env, token, topicTableId, [TOPIC_MARK_FIELD], deps);
  for (let index = 0; index < ready.length; index += 1) {
    const record = ready[index];
    const item = packages[index];
    await createRecord(env, token, scriptTableId, item.row, deps);
    result.created_script_packages += 1;
    await updateRecord(env, token, topicTableId, record.record_id, { [TOPIC_MARK_FIELD]: "是" }, deps);
    result.marked_topics += 1;
  }
  return result;
}

async function main_handler(event = {}, context = {}) {
  try {
    const parsedEvent =
      typeof event.body === "string" && event.body.trim()
        ? { ...event, ...JSON.parse(event.body) }
        : event || {};
    const result = await runScriptPackageJob(parsedEvent, process.env);
    return {
      statusCode: 200,
      headers: { "content-type": "application/json; charset=utf-8" },
      body: JSON.stringify(result),
    };
  } catch (error) {
    console.error(error);
    return {
      statusCode: 500,
      headers: { "content-type": "application/json; charset=utf-8" },
      body: JSON.stringify({ ok: false, error: String(error.message || error) }),
    };
  }
}

module.exports = {
  FULL_PACKAGE_FIELD,
  SCRIPT_PACKAGE_FIELDS,
  SCRIPT_VERSION,
  normalizeTopic,
  validateTopic,
  renderPackage,
  runScriptPackageJob,
  main_handler,
};
