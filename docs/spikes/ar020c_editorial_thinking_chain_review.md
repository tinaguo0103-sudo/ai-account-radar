# AR-020C Editorial Thinking Chain Review

日期：2026-07-07

状态：Docs-only Review Done / Waiting PM Scheme Decision

本轮只做架构和产品机制评审；未改 scripts/config/Skill 实现，未写 Feishu，未发 Topic Card，未触发采集，未触发 06/Codex，未同步全局私有 Skill。

## 结论

AR-020B 已经解决了一个重要问题：04 / Topic Card / 06 主字段不再随便继承 deterministic fallback，字段契约也能拦住明显错配。但它还没有解决用户这次指出的核心问题：选题判断和标题表达仍像黑盒，且标题/命题容易落成相似骨架。

当前模板化不是因为 `ai-account-editorial-director` 本身想做标题模板器。全局私有 Skill 和 repo mirror 都明确写了“不是标题模板器”，也反复提醒不要批量复制句式。真实牵引来自三层叠加：

1. `editorial_skill_runner.py` 把 Skill 的自由判断压进强结构：`Gate -> Workflow Experiment Card -> Title Packaging`，同时要求一次性填完大量字段。
2. `topic_flow_rework.py` 在 Skill 前生成固定主题簇、固定转译角度、固定命题修正句，虽然能防错，但也会预设“应该从什么角度讲”。
3. field contract 和 Topic Card UX 负责安全与呈现，本应只做 guardrail；如果被当成内容质量机制，就会鼓励“测试 / 能不能 / 验收 / 试一遍”这一类可校验但相似的标题骨架。

推荐下一步不要继续修几个标题样例，而是拆 `AR-020C Implementation`：把主链路改成“主编自由判断先行，结构化字段后置映射”。也就是让 Skill 先回答“我为什么选 / 为什么不选 / 我会从什么角度切 / 标题思路是什么”，再把这个判断映射到 04 主字段、Topic Card 和 06 输入。代码只提供事实、候选池和一致性校验，不替 Skill 先定角度。

## 当前真实链路

```text
03 content_items.csv
  -> rough candidate / today_10_topics.csv
  -> topic_flow_rework source governance and hints
  -> editorial_skill_runner.py
  -> global private ai-account-editorial-director
  -> topic_field_contract.py
  -> push_today10_to_feishu.py
  -> Feishu 04
  -> Topic Card
  -> 06 script package input
```

### 1. 03 到 rough candidate

`content_sampler.py` 和相关 daily pipeline 负责采集后粗筛、去重、生成 `content_items.csv` 与 `today_10_topics.csv`。这层应该回答“有哪些素材可进入主编判断”，不应该最终决定“用户会怎么讲”。

### 2. `topic_flow_rework.py`

这层在 AR-020 / AR-026 中承担来源治理：

- 污染来源隔离；
- AI Hot 降权和重大性提示；
- 对标账号来源构成；
- 全库 replay / reverse evaluation；
- 主题簇、转译角度、需要补的案例/工具/工作流。

其中治理事实应保留，但 `THEME_RULES` 中固定 `translation`，`theme_topic_title()` 中固定“真正值得看的是...”命题，以及 `align_topic_visible_fields()` 对 `我的选题标题` / `选题命题` / `一句话Brief` 的修正，都已经越过“事实材料”边界，进入了主编表达层。

证据：

- `scripts/topic_flow_rework.py:88-134` 定义了固定主题簇与固定转译句。
- `scripts/topic_flow_rework.py:342-349` 在缺少可用角度时直接回填 theme translation。
- `scripts/topic_flow_rework.py:352-365` 生成固定命题骨架。
- `scripts/topic_flow_rework.py:368-387` 会直接改用户可见标题、命题、Brief 和推荐理由。

### 3. `editorial_skill_runner.py`

这层是 AR-020B 的主编入口，默认使用全局私有 `ai-account-editorial-director`，deterministic engine 只保留为显式 fallback。

当前它有两类好资产：

- 明确把 `editorial_engine=codex`、`fallback_only`、`not_editorial_quality` 区分开。
- 在 `compact_candidate()` 里把来源治理、AI Hot、市场验证、主题 hint、字段契约 guardrails 打包给 Skill。

但它也对 Skill 施加了很强的结构牵引：

- `SKILL_FIELDS` 要求一次性输出大量字段，模型容易按字段列表逐格填充，而不是先完成自由主编判断。
- `build_codex_prompt()` 明确要求批量候选按 `Gate -> Workflow Experiment Card -> Title Packaging` 走。
- prompt 中反复强化 `选题命题`、`我要做的实验`、`验证方式`、`可沉淀资产`，这有利于可执行性，但会把表达推向“测试 / 验证 / 能不能 / 进入 / 验收”。
- prompt 中校准例虽标注不可复制，但例子本身仍会成为 LLM 的高权重写法锚点。

证据：

- `scripts/editorial_skill_runner.py:54-161` 定义大量输出字段。
- `scripts/editorial_skill_runner.py:163-217` 把候选已有主字段、主题 hint、转译角度一起传入 Skill。
- `scripts/editorial_skill_runner.py:1307-1328` 明确传入 `source_governance_evidence`、`field_contract_guardrails`、母场景候选和场景依据候选。
- `scripts/editorial_skill_runner.py:1376-1450` 强化三段流程、字段契约、标题规则和校准例。
- `scripts/editorial_skill_runner.py:739-891` 对 Skill 输出做归一化和安全降级，但也会补齐缺字段。

### 4. 全局私有 `ai-account-editorial-director`

全局私有 Skill 的方向是对的。它开头就说自己不是标题模板器，而是判断来源能不能成为用户自己的业务现场选题。它也要求先判断，再写工作流实验卡，最后才做标题包装。

但目前 Skill 文档仍然把“输出顺序”写成固定流程，并提供了若干正向样例。即使它提醒“不要复制模板”，LLM 在批量任务里仍会倾向复制最近、最完整、最结构化的例子。

证据：

- `/Users/congcong/.codex/skills/ai-account-editorial-director/SKILL.md:8-9` 明确不是标题模板器。
- `/Users/congcong/.codex/skills/ai-account-editorial-director/SKILL.md:46-60` 定义 `Gate -> Workflow Experiment Card -> Title Packaging`。
- `/Users/congcong/.codex/skills/ai-account-editorial-director/SKILL.md:70-88` 说明命题不是发布标题，并警告不要批量使用同一种骨架。
- `/Users/congcong/.codex/skills/ai-account-editorial-director/SKILL.md:112-123` 给出校准例；这些例子可以帮助理解，也可能在批量生成中成为写法锚点。
- `/Users/congcong/.codex/skills/ai-account-editorial-director/SKILL.md:151-164` 再次强调案例只给语气和判断方式，不能复制骨架。

repo mirror 也同步了相同方向：

- `skills/ai-account-editorial-director/SKILL.md:57-84` 明确 Skill 是用户可见主字段 owner，代码 hint 不能盖过来源证据和账号现场。
- `skills/ai-account-editorial-director/SKILL.md:128-148` 继续要求工作流实验卡。
- `skills/ai-account-editorial-director/SKILL.md:193-214` 给出校准例和标题包装规则。
- `skills/ai-account-editorial-director/SKILL.md:245-255` 强调不能批量复制骨架。

### 5. `topic_field_contract.py`

这层职责正确：只做一致性校验，不发明角度。它应该保留为 guardrail，而不是内容生成器。

证据：

- `scripts/topic_field_contract.py:1-7` 明确 Skill owns visible fields，本模块只校验一致性。
- `scripts/topic_field_contract.py:15-33` 列出 Skill-owned main fields。
- `scripts/topic_field_contract.py:129-194` 校验知识库/视频/办公错配、AI Hot 重大性和生成脚本包就绪度。
- `scripts/topic_field_contract.py:205-221` 失败时降级，而不是重写成另一个角度。

### 6. 04 / Topic Card

`push_today10_to_feishu.py` 和 `feishu_topic_decision_card.py` 不应该做主编判断。它们现在更接近正确边界：

- 04 只接受 real Skill、非 fallback、字段契约通过、有可执行实验动作的行。
- Topic Card 已把可生成候选和补证据/观察候选拆开。
- 它们消费的是 Skill 主字段，因此上游如果仍黑盒或模板化，用户会在 04 / Topic Card 里直接看到。

证据：

- `scripts/push_today10_to_feishu.py:213-234` 过滤 fallback、contract failure 和缺实验动作的行。
- `scripts/push_today10_to_feishu.py:332-374` 将 `选题命题`、`我要做的实验`、`我的工作流痛点` 等映射到 04。
- `scripts/feishu_topic_decision_card.py:224-305` 展示标题、Brief、转译、实验、证据、缺口和 caveat。
- `scripts/feishu_topic_decision_card.py:436-492` 拆分可生成候选和补证据候选，只有可生成候选进入多选框。

## 各层当前职责

| 层 | 当前实际职责 | 是否合理 | 调整建议 |
| --- | --- | --- | --- |
| `content_sampler.py` | 采集后粗筛、生成 rough candidates | 部分合理 | 只保留素材池和基本事实，不产出最终角度/标题 |
| `topic_flow_rework.py` | 来源治理、主题簇、转译角度、replay、reverse evaluation | 治理合理，表达越界 | 主题/转译降级为 non-authoritative evidence；不得改主字段 |
| `editorial_skill_runner.py` | 打包上下文、调用全局 Skill、要求结构化输出、fallback 标记、归一化 | 大方向合理，prompt 过强 | 改成“自由判断 first、字段映射 second”的两段式或两步式 |
| 全局私有 Skill | 判断 Austin-fit、转译账号现场、标题权限 | 应该是 owner | 更新为“主编思考链优先”，减少固定流程作为输出顺序 |
| repo mirror Skill | 公开契约和测试镜像 | 合理 | 跟随全局 Skill 方案同步，但不在本评审直接改 |
| `topic_field_contract.py` | 一致性校验、失败降级 | 合理 | 增加标题同构/预设角度泄漏检查，但仍不生成内容 |
| `push_today10_to_feishu.py` | 过滤并写 04 | 合理 | 继续只消费合格 Skill 主字段 |
| Topic Card | 用户可见选择界面 | 合理 | 增加主编判断摘要/标题思路可见性，降低黑盒感 |

## 模板化来源判断

### 一类来源：Skill 文档的流程和案例锚点

责任：中等。

Skill 已经有反模板意识，但仍把“先 Gate、再实验卡、最后标题包装”写成唯一顺序。对人工主编来说，这是思考框架；对 LLM 批量输出来说，它容易变成生成框架。校准例虽然有“不要复制”说明，但完整例子的句式仍会被学习。

应保留：

- 不是资讯搬运；
- 真实业务现场；
- 旧流程痛点、AI介入点、人保留判断、可展示结果、可带走资产；
- `title_permission`；
- 可发布标题必须有证据。

应调整：

- 不把 `Gate -> Workflow Experiment Card -> Title Packaging` 当作输出顺序；
- 把案例从“正向句式样例”改成“判断依据样例 + 不同表达形态样例”；
- 增加“先写自由主编判断，不急着填字段”的要求。

### 二类来源：runner prompt 的强结构

责任：高。

`editorial_skill_runner.py` 明确要求“核心流程只有三步”，又让 Skill 一次性覆盖所有结构化字段。这解决了字段完整性，但会让模型为了填字段而生成模块化句子。

应保留：

- 使用全局私有 Skill；
- real Skill 与 deterministic fallback 的标记；
- 来源事实、source governance、AI Hot 重大性、市场验证作为输入；
- field contract guardrails。

应调整：

- 把候选 context 分为 `source_facts`、`non_authoritative_hints`、`do_not_copy_existing_fields`；
- 把输出拆为 `editorial_thinking` 与 `field_mapping`；
- 第一步先让 Skill 不看字段列表，回答为什么选、不选、角度和标题思路；
- 第二步再把已选思路映射成 04 字段；
- 不再在同一个 prompt 里要求“重写/覆盖所有字段”作为第一目标。

### 三类来源：`topic_flow_rework.py` 主题/转译 hint

责任：高。

它的 `THEME_RULES` 能防止知识库错写成 AI 视频，也能解释 AI Hot 重大性。但里面的固定 `translation`、固定 `needed`、固定 `theme_topic_title()` 会先替 Skill 定“这条应该怎么讲”。当这些字段进入 `CANDIDATE_CONTEXT_FIELDS` 后，Skill 会把它当高置信参考。

应保留：

- 污染来源隔离；
- source weight；
- AI Hot 重大性事实；
- 主题簇作为 QA 分类或召回审计。

应降级：

- `Austin映射方向`；
- `Austin转译角度`；
- `主题簇`；
- `主题簇说明`；
- `需要补的案例/工具/工作流`。

这些只能作为 “可能相关的事实标签 / QA hint”，不能进入用户可见主字段链路，也不能以强名义传给 Skill。

应移除或旁路：

- `theme_topic_title()` 对 `选题命题` 的生成；
- `align_topic_visible_fields()` 对标题、Brief、推荐理由的直接改写；
- 任何用固定主题翻译替代主编判断的路径。

### 四类来源：field contract

责任：低，但容易被误用。

field contract 本身是正确的 guardrail。问题不是它生成模板，而是如果把“能通过 contract”当成“选题质量成立”，就会鼓励所有候选写成可验证、可执行、可资产化的相似句式。

应保留：

- 知识库/视频/办公错配拦截；
- AI Hot actionable 重大性；
- 生成脚本包就绪度；
- fallback 不可作为内容质量证据。

应新增：

- 标题/命题同构检查；
- 一批候选中重复使用“先测 / 能不能 / 验收 / 试一遍”骨架的风险提示；
- `non_authoritative_hints` 与最终主字段过度相似的泄漏检查。

### 五类来源：LLM 输出习惯

责任：中等。

即使 Skill 文档写了不要模板化，LLM 在批量结构化任务中也会倾向使用最安全、最可校验的句式。若上下文里反复出现“测试、验证、能不能、进入、验收”，输出自然会向这些词收敛。

应对方式不是继续加禁词，而是改变任务形态：先让模型进行自由主编判断和标题思路选择，再做字段映射。字段映射阶段只承接已经形成的判断，不负责发明角度。

## Guardrail / Fact / Visible Chain 分层

### 保留为 guardrail

- `topic_field_contract.py` 的一致性校验和降级机制。
- `fallback_only / not_editorial_quality` 标记。
- 04 写入前只接受 real Skill + contract pass + executable experiment。
- Topic Card 中可生成候选与补证据候选分区。
- AI Hot actionable 必须有重大性和 Austin 角度。
- run-specific / test-isolation，避免 QA 混入旧测试候选。

### 降级为事实材料

- `Austin映射方向`、`Austin转译角度`、`主题簇`、`主题簇说明`。
- 母场景候选、热点钩子候选、场景依据候选。
- `THEME_RULES.translation` 与 `needed`。
- 旧 rough candidate 中已有的 `我的工作流痛点`、`我要做的实验`、`重点体现`。

这些材料可以帮助 Skill 理解候选，也可以给 validator 做比对，但必须明确标为 `non_authoritative_hints`。Skill 可以反驳、忽略或重写。

### 从用户可见主字段链路移除

- deterministic 生成的标题/命题/Brief；
- `theme_topic_title()` 生成的命题；
- `align_topic_visible_fields()` 对 `选题命题`、`我的选题标题`、`一句话Brief` 的直接改写；
- 任何“为了让 contract pass”而补出的泛化主字段；
- PM report 字段对主字段的旁路修正。

## 推荐方案：主编自由判断先行

### 核心机制

把主编输出拆成两层：

1. `editorial_thinking`：自由判断层。
2. `field_mapping`：结构化映射层。

第一层只回答主编问题，不急着填 04 字段：

```json
{
  "source_read": "这条来源实际在说什么，不复述标题。",
  "why_i_would_choose": "如果我会选，真实理由是什么。",
  "why_i_would_not_choose": "如果我不选，卡在哪里。",
  "account_fit": "它和四个方向、用户人设、案例库的关系。",
  "source_to_me_translation": "我会把它从对标账号/热点转成我的哪个现场。",
  "angle_options": [
    {"angle": "...", "why": "...", "risk": "..."}
  ],
  "chosen_angle": "最终切入角度。",
  "title_thinking": {
    "title_intent": "标题要制造什么判断/冲突/结果差距。",
    "avoid_patterns": ["本批里已经重复的标题骨架"],
    "candidate_shapes": ["不同结构的标题思路，不是固定模板"]
  },
  "decision": "生成脚本包 / 补证据 / 暂存观察 / 不建议制作"
}
```

第二层才把第一层映射到 04 / Topic Card / 06 主字段：

- `选题命题`
- `一句话Brief`
- `我的工作流痛点`
- `我要做的实验`
- `旧流程痛点`
- `AI介入点`
- `验证方式`
- `可沉淀资产`
- `我的思考点`
- `重点体现`
- `对应方向`
- `推荐动作`
- `今日建议级别`
- `title_permission`
- `可发布标题`

映射时必须保留 trace：

- 每个主字段来自哪个 `editorial_thinking` 片段；
- 是否使用过 `non_authoritative_hints`；
- 是否与 hint 冲突；
- 是否为了 contract 才补字段。

### 为什么这能减少黑盒感

现在用户看到的是字段结果，无法看出 Skill 为什么这么选。AR-020C 应让 04 / Topic Card 或 replay 报告里出现轻量的 `主编判断摘要` 和 `标题思路`：

- `主编判断摘要`：我为什么会把它作为候选 / 为什么只补证据。
- `标题思路`：这条不是最终标题模板，而是“标题要表达的判断”。

这样用户能审查系统是否像她本人在做选择，而不是只看一堆字段是否齐。

### 为什么这不是让代码替 Skill 思考

代码只做三件事：

1. 准备事实：来源、账号、链接、市场验证、AI Hot 重大性、候选池来源。
2. 标注非权威 hint：主题簇、可能方向、可能风险。
3. 校验结果：字段矛盾、标题同构、fallback 泄漏、缺证据却强推。

真正的选择、角度、标题思路仍由 Skill 输出。

## 备选方案

### 方案 A：prompt-only 收缩

做法：

- 修改 runner prompt，弱化三段式和主题 hint；
- 要求 Skill 多写 `主编自由稿` 和 `标题工作坊`；
- 不改输出结构。

优点：改动小，最快。

风险：字段列表仍然很长，批量输出仍会模板化；用户黑盒感只能部分缓解。

适用：如果 PM/用户只想先看一轮低成本样例。

### 方案 B：两段式 Skill 输出（推荐）

做法：

- 第一段输出 `editorial_thinking`；
- 第二段输出 `field_mapping`；
- validator 同时校验字段一致性和思考链到字段的 trace；
- replay 输出同时给 PM/用户看“为什么选”和“最终怎么呈现”。

优点：真正改变任务形态，能对准用户“像我一样判断”的目标。

风险：实现和测试成本中等；token 成本上升；需要决定 04/Topic Card 是否新增可见字段。

适用：AR-020C 正式开发。

### 方案 C：Skill 自由判断 + 人工确认标题思路

做法：

- Skill 只输出候选选择和标题思路；
- 可发布标题进入人工确认队列，不自动进入前台字段。

优点：标题质量最安全，避免机器伪装成用户。

风险：自动化程度下降；短期生产效率下降。

适用：如果用户认为标题表达比自动化更关键，可以作为发布前保守模式。

## 可保留的 AR-020B 资产

1. `topic_field_contract.py`：保留为 post-Skill validator。
2. `fallback_only / not_editorial_quality`：必须保留，不能让 deterministic 输出冒充质量通过。
3. real Skill replay：保留，但要升级为同时输出 thinking trace。
4. `push_today10_to_feishu.py` 的 real Skill / contract pass 过滤。
5. Topic Card 可生成候选与补证据候选分区。
6. staging/test 专用 04 / test App / run-specific QA 能力。
7. AI Hot 低权重重大性原则。
8. AR-026 source governance 与污染来源隔离计划。

## 必须重构或废弃的部分

1. `topic_flow_rework.py::theme_topic_title()` 不应进入主字段链路。
2. `topic_flow_rework.py::align_topic_visible_fields()` 不应直接改 `选题命题` / `一句话Brief` / `推荐理由`。
3. `THEME_RULES.translation` 不能作为默认转译角度进入用户可见字段，只能作为非权威 hint 或 QA 分类。
4. `editorial_skill_runner.py` 的单 prompt 大字段表应拆分；不要让字段完整性成为第一任务。
5. runner prompt 中的正向示例需要从“可复制句式”改为“判断路径 + 多样表达形态 + 反模板约束”。
6. `主编自由稿` 不应只是一个附属字段，应成为字段映射前的核心判断产物。

## 开发方案

### Phase 1：契约设计与 runner 上下文重构

文件可能涉及：

- `skills/ai-account-editorial-director/SKILL.md`
- `scripts/editorial_skill_runner.py`
- `scripts/topic_field_contract.py`
- replay / QA 脚本
- 对应 tests

动作：

1. 更新 repo mirror Skill：新增 `editorial_thinking` 机制，强调先自由判断再字段映射。
2. runner 输入分层：
   - `source_facts`
   - `account_context`
   - `source_governance`
   - `non_authoritative_hints`
   - `existing_fields_do_not_copy`
   - `contract_guardrails`
3. runner 输出分层：
   - `editorial_thinking_json`
   - `field_mapping_json`
   - 现有 CSV 主字段
4. deterministic fallback 只输出占位和错误可见，不输出质量样例。

### Phase 2：标题和角度 anti-template validator

新增或扩展 validator：

- 一批候选中相似标题骨架超过阈值，标 `title_pattern_risk`；
- `选题命题` / `可发布标题` 与 `non_authoritative_hints` 过度相似，标 `hint_leak_risk`；
- `标题思路` 与最终标题不一致，标 `title_trace_mismatch`；
- `editorial_thinking` 为空或泛化，但字段完整，标 `blackbox_decision_risk`。

### Phase 3：真实 Skill replay 与样例包

replay 必须输出：

- `skill_thinking_rows.csv`
- `skill_field_mapping_rows.csv`
- `skill_actionable.csv`
- `skill_observe.csv`
- `title_pattern_risk.csv`
- `thinking_trace_report.md`

PM/用户样例包必须至少包含：

- Codex + Obsidian / 知识库类来源；
- AIGC / 分镜 / AI导演类来源；
- Mx-Shell / Skill / 工具类来源；
- CI/CD Shell / 技术自动化来源；
- broad enterprise / growth / AI Hot 来源。

每条样例展示：

- 原始来源；
- Skill 自由判断；
- 为什么选 / 为什么不选；
- 角度备选；
- 选择的角度；
- 标题思路；
- 04 主字段；
- invariant 结果；
- 是否进入 Topic Card 可生成区。

### Phase 4：staging/test 04 / Topic Card 可见验证

在专用测试 04 中验证：

- `主编判断摘要` 是否可见；
- `标题思路` 是否可见或在 QA report 中可审查；
- 可生成候选与补证据候选继续分区；
- 用户可见字段不泄漏 non-authoritative hints；
- Topic Card 不发送生产目标，不触发 06。

## 测试方案

### 单元测试

1. context serialization：
   - `Austin转译角度`、`主题簇` 等必须进入 `non_authoritative_hints`，不能进入 authoritative facts。
2. Skill output parser：
   - 缺 `editorial_thinking` 时不得标内容质量通过。
3. fallback marking：
   - deterministic 输出必须 `fallback_only=true` / `not_editorial_quality=true`。
4. field contract：
   - 保留知识库 vs AI 视频错配、AI Hot 重大性、生成脚本包 readiness。
5. anti-template：
   - 同批多条命题共享相同“用 X 测试 Y 能不能 Z”骨架时，产生风险标记。
6. hint leak：
   - 最终主字段与 fixed theme translation 高相似时，必须标 `hint_leak_risk` 或要求 trace 解释。

### replay 测试

- 使用 2026-07-01+ production read-only content CSV；
- 不写 Feishu；
- 不发卡；
- 不触发采集；
- 输出 thinking trace 与主字段；
- 检查 selected/actionable 没有 fallback；
- 检查样例包可人工审阅。

### L3 用户可见测试

- 使用 staging/test 04；
- 写入测试记录并 read-back；
- 构建测试 Topic Card preview 或发送到个人测试目标；
- 验证用户能看到为什么选、为什么补证据，而不是只看到字段结果；
- 不点击生产卡，不触发 06。

## 发布边界

1. 开发阶段只改 dev worktree。
2. 不同步全局私有 Skill，直到 PM/用户确认方案和回滚策略。
3. 全局 Skill 同步需要单独发布步骤：
   - 先备份全局 Skill；
   - 同步 repo mirror 或手工应用私有版差异；
   - 跑 real Skill replay；
   - staging/test 04 / Topic Card 验证；
   - 再进入 release candidate。
4. 生产发布前需要 RC full regression，不能用 dev replay 替代。
5. 回滚路径：
   - 保留 AR-020B field contract 和 fallback 标记；
   - 如果 AR-020C Skill 质量不如 AR-020B，可回退 runner prompt / global Skill 到 AR-020B，同时保留 guardrail。

## 风险

1. 自由判断增加后，字段结构可能变松，需要 validator 把安全边界兜住。
2. 两段式 Skill 成本更高，批量 replay token 和时间会上升。
3. 如果把 `editorial_thinking` 全量写进 04，用户界面可能变重；需要决定只写摘要还是只放 QA report。
4. 如果继续把 `topic_flow_rework.py` 的固定 hint 以强字段传给 Skill，AR-020C 仍可能复发模板化。
5. 如果只改 repo mirror，不同步全局私有 Skill，真实生产主编行为不会改变。

## 需要 PM / 用户确认的问题

1. 是否接受推荐方案 B：两段式 `editorial_thinking -> field_mapping`？
2. `主编判断摘要` 和 `标题思路` 是否要写入 04 / Topic Card 用户可见字段，还是只放在 QA report？
3. 是否允许后续开发更新全局私有 `ai-account-editorial-director`？若允许，是否接受备份、同步、回滚步骤？
4. 标题同构阈值怎么定：严格拦截，还是只作为 PM/QA 风险提示？
5. `topic_flow_rework.py` 的主题簇是否继续保留在 Skill 输入中？如果保留，是否同意降级为 `non_authoritative_hints` 并禁止进入主字段？
6. AR-020C 样例验收是否以 2026-07-01+ real Skill replay + staging/test Topic Card 样例包为准？

## 推荐下一步

建议 PM 向用户确认上述 6 个决策点。若用户确认，派发 `AR-020C Implementation - Editorial Thinking Chain and Title Expression`，范围限定为：

- 更新 repo mirror Skill contract；
- 重构 runner prompt / context / output schema；
- 降级 `topic_flow_rework.py` fixed theme translations；
- 增加 anti-template 和 hint-leak validator；
- 跑 2026-07-01+ real Skill replay；
- 做 staging/test 04 / Topic Card 样例验证。

建议状态：`Architecture Review Done / Waiting PM Scheme Decision`。
