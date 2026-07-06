# AR-020 Editorial Architecture Review

日期：2026-07-06

状态：Architecture Review Done / Waiting PM Scheme Decision

本轮只做只读架构审查和方案，不继续 Round 4 补丁；未写生产、未写飞书、未发 Topic Card、未触发采集、未触发 06/Codex。

## 结论

AR-020 的根问题不是 replay 报告不够详细，而是“主编决策层”和“确定性预填/兜底层”的职责边界被打穿了。

当前代码里存在三层判断：

1. `content_sampler.py` / `topic_flow_rework.py` 在 Skill 之前生成大量主字段。
2. `editorial_skill_runner.py` 使用 `ai-account-editorial-director` 做主编判断，并再次规范字段。
3. `push_today10_to_feishu.py`、Topic Card、06 runner 只消费 04 主字段，不消费 PM 报告字段。

Round 3 主要修了 `topic_flow_rework.py` 和 `topic_replay_evaluation.py` 的 report/辅助字段，例如 `Austin转译角度`、`主题簇`、PM quality report。它没有从架构上保证 `我的工作流痛点`、`我要做的实验`、`重点体现`、`对应方向`、`一句话Brief` 等真正进入 04 / Topic Card / 06 的主字段同源一致。

因此 QA 看到的危险现象成立：PM report 能显示“知识库 / 信息雷达 / 内容资产”，但同一候选的主字段仍可能出现 `AI视频交付`、`分镜`、`成片验收`。这不是再加几个禁词能解决的问题，必须把主编判断重新收敛到 Skill 输出和字段契约校验。

## 当前真实链路

### 1. 采集与原始内容

入口主要是：

- `scripts/run_daily_collection_job.py`
- `scripts/daily_pipeline.py`
- `scripts/content_sampler.py`

生产 08:00 通常以 `--defer-editorial` 方式运行：先采集、去重、粗筛，生成 `output/runs/<run_id>/content_items.csv`、`content_breakdowns.csv`、`today_10_topics.csv`，然后暂停，让外层 Codex 处理主编 Skill。

`daily_pipeline.py` 明确写着：

- `--defer-editorial` 会停止在 raw candidate generation。
- 后续必须由 outer Codex automation apply `ai-account-editorial-director`，再运行 `finalize_daily_pipeline_after_editorial.py`。

### 2. 主编层

主编层入口：

- `scripts/editorial_skill_runner.py`
- 全局私有 Skill：`/Users/congcong/.codex/skills/ai-account-editorial-director`
- repo mirror：`skills/ai-account-editorial-director`

`editorial_skill_runner.py` 的设计说明非常明确：

- 默认 `engine=codex`。
- 默认读取全局私有 Skill。
- repo Skill 只是 sanitized mirror / sync / bootstrap / testing，不是隐式 fallback。
- `--engine deterministic` 只是显式 emergency fallback，不是内容质量验收路径。

Skill 的职责是 `Gate -> Workflow Experiment Card -> Title Packaging`：

- Gate：判断是否 Austin-fit、是否资讯搬运、是否有真实/相邻业务现场、证据够不够。
- Workflow Experiment Card：产出 `选题命题`、`我要做的实验`、`我的工作流痛点`、`旧流程痛点`、`AI介入点`、`验证方式`、`可沉淀资产` 等。
- Title Packaging：只有 `title_permission=可发布标题` 才产出 `可发布标题` / `标题备选`。

### 3. 04 写入

写入入口：

- `scripts/finalize_daily_pipeline_after_editorial.py`
- `scripts/push_today10_to_feishu.py`
- `scripts/topic_decision_fields.py`

`topic_decision_fields.py` 定义的 04 核心可见字段包括：

- `选题标题`
- `卡片速读`
- `状态`
- `今日建议级别`
- `AI味风险`
- `对应方向`
- `来源构成`
- `来源权重类型`
- `一句话Brief`
- `推荐理由`
- `对标转译角度`
- `AIHOT重大性说明`
- `不建议做的原因`
- `我要做的实验`
- `热点触发点`
- `我的工作流痛点`
- `旧流程痛点`
- `AI介入点`
- `验证方式`
- `可沉淀资产`
- `我的思考点`
- `可展示证据`
- `需要补的证据`

`push_today10_to_feishu.py::map_row()` 把 CSV 行映射到 04 字段。这里实际使用的是主字段，不是 replay 的 PM report。

### 4. Topic Card

Topic Card 使用：

- `scripts/feishu_topic_decision_card.py`

卡片展示读取 04 fields：

- `选题标题`
- `一句话Brief`
- `我要做的实验`
- `可展示证据`
- `需要补的证据`
- `对应方向`
- `来源构成`
- `来源权重类型`
- `对标转译角度`
- `AIHOT重大性说明`
- `AI味风险`
- `推荐日期`
- `运行批次`

因此 Topic Card 会暴露主字段错配；PM report 正确不能保护 Topic Card。

### 5. 06

06 runner 使用：

- `scripts/codex_script_package_runner.py`

它从 Topic Card / 04 record fields 读取：

- `选题标题`
- `选题命题`
- `一句话Brief`
- 以及制作方向补充、状态等

如果 04 主字段已经错配，06 会继续继承错配。

## 责任边界建议

### Skill 应拥有的决策

这些是主编判断，应该由 `ai-account-editorial-director` 产出，代码只能校验：

| 决策 | 原因 |
| --- | --- |
| Austin-fit gate | 是否适合用户账号，不应由关键词硬编码决定 |
| 来源到账号的转译 | 需要理解原始内容、市场验证和用户业务现场 |
| 推荐动作 / 候选状态 | 需要综合证据、表达、人设和制作价值 |
| `选题命题` | 是用户工作台命题，不是机械标题 |
| `我要做的实验` | 决定能不能进入可执行工作流 |
| `我的工作流痛点` / `旧流程痛点` | 需要贴合用户真实工作现场 |
| `AI介入点` / `验证方式` / `可沉淀资产` | 是工作流实验命题卡核心 |
| `title_permission` / `可发布标题` | 防止观察项伪装成发布选题 |
| `重点体现` / `我的思考点` | 决定用户如何讲，而不是来源如何讲 |

### 确定性代码应拥有的决策

这些是工程、安全、数据治理问题，适合确定性代码：

| 决策 | 原因 |
| --- | --- |
| 来源治理 / 污染账号隔离 | 用户已确认名单，必须稳定可 dry-run |
| 全量账号覆盖报告 | 采集计划、尝试、成功、失败是工程事实 |
| AI Hot 低权重影响力 | 可作为排序权重和入池前置约束 |
| 去重 / 同源合并 / stale guard | 工程规则，必须可复现 |
| broad candidate pool assembly | 给 Skill 足够但不过载的候选 |
| 字段一致性校验 | 防止 Skill 或 fallback 输出自相矛盾 |
| replay / reverse evaluation / QA report | 评估工具，不应替代主编输出 |
| Feishu 写入、read-back、guard | 外部系统可靠性和安全边界 |

### `topic_flow_rework.py` 合理和不合理部分

合理：

- `POLLUTED_SOURCE_NAMES`
- `source_weight_label()`
- `source_influence_weight()`
- `source_composition()`
- `reverse_evaluation_rows()`
- 污染来源 dry-run / coverage report
- replay 中的统计和反向评估

需要降级为“上下文/校验”，不能直接成为主字段：

- `Austin映射方向`
- `Austin转译角度`
- `主题簇`
- `Austin转译质量`

这些可以作为 Skill 输入的 evidence / hint，也可以作为 QA invariant 的 expected cluster，但不能直接覆盖 `选题命题`、`我要做的实验`、`我的工作流痛点` 等用户可见主字段。

应该迁移或删除的平行主编逻辑：

- `content_sampler.py::own_scenario_angle()`
- `content_sampler.py::focus_point()`
- `content_sampler.py::workflow_experiment_for()`
- `editorial_skill_runner.py::enrich()` 中大量 deterministic 创作 fallback
- `topic_flow_rework.py` 中任何试图直接修正用户可见主字段的逻辑

这些函数可以保留为 emergency fallback，但必须标明 `fallback_only / not_editorial_quality`，不能用于 PM 接受或 RC 内容质量验收。

## 字段契约

建议把 04 / Topic Card / 06 字段按 owner 分类：

| 字段 | 当前消费方 | 推荐 owner | 说明 |
| --- | --- | --- | --- |
| `选题标题` | 04 / Topic Card | Skill output | `push_today10_to_feishu` 可做长度/空值兜底，但不能创造编辑判断 |
| `我的选题标题` | CSV / 兼容字段 | Skill output | 应等同或派生自 `选题命题`，不是可发布标题 |
| `选题命题` | Skill / 06 | Skill output | AR-020B 的核心主字段 |
| `一句话Brief` | 04 / Topic Card / 06 | Skill output | 必须和 `选题命题` / 实验一致 |
| `我要做的实验` | 04 / Topic Card | Skill output | 必须可执行，有输入、动作、输出、通过/失败标准 |
| `我的工作流痛点` | 04 / Topic Card | Skill output | 不能由 `content_sampler` 场景 profile 盲填 |
| `旧流程痛点` | 04 | Skill output | fallback 只能写待补，不应编造场景 |
| `AI介入点` | 04 | Skill output | 必须和实验一致 |
| `验证方式` | 04 / Topic Card | Skill output | 确定性代码可校验是否可执行 |
| `可沉淀资产` | 04 / Topic Card | Skill output | 确定性代码可检测泛化资产包 |
| `我的思考点` | 04 | Skill output | 用户口吻，不能是报告式分析 |
| `重点体现` | Skill / QA | Skill output | 必须和方向一致 |
| `对应方向` | 04 / Topic Card | Skill output, with deterministic validation | 确定性代码可提示/校验方向冲突 |
| `推荐动作` | 04 / Topic Card guard | Skill output | 确定性代码只做合法值和安全降级 |
| `今日建议级别` | 04 / Topic Card | Skill output | 最多 3 条今日最值得做，代码可校验 |
| `title_permission` | Skill / runner | Skill output | 控制是否允许可发布标题 |
| `可发布标题` | 04 debug / future publishing | Skill output | 只有 `title_permission=可发布标题` 才可有值 |
| `来源权重类型` | 04 / Topic Card | deterministic prefill | 来源治理事实 |
| `来源构成` | 04 / Topic Card | deterministic prefill | 来源事实 |
| `原始来源标题` | 04 / Topic Card | deterministic prefill | 来源事实 |
| `对标转译角度` | 04 / Topic Card | Skill output or Skill-reviewed evidence | 不能只是 `topic_flow_rework.py` report 字段 |
| `AIHOT重大性说明` | 04 / Topic Card | deterministic evidence + Skill decision | 重大性证据可代码给，是否入选由 Skill 判断 |

## Round 3 错配根因

Round 3 错配不是单点 bug，而是字段写入顺序和 owner 不清造成的。

典型链路：

1. `content_sampler.py::topic_from_breakdown()` 先根据 `choose_scene()`、`business_profile()`、`own_scenario_angle()`、`focus_point()`、`workflow_experiment_for()` 生成主字段。
2. 当标题或文本里同时出现 `Codex`、`Obsidian`、`PPT`、`视频` 等混合词时，`choose_scene()` 或 profile 可能归到不合适的场景。
3. Round 3 在 `topic_flow_rework.py` 里新增了 `Austin转译角度` / `主题簇` / PM report，能把知识库识别成信息雷达/内容资产。
4. 但 `replay_selected_topics.csv`、04、Topic Card、06 实际继续消费已经生成的主字段，例如 `我的工作流痛点`、`我要做的实验`、`重点体现`。
5. 因此 PM report 正确，用户可见主字段仍错。

一句话：Round 3 修的是“旁路解释字段”，不是“主字段生成契约”。

## 必要 invariant checks

AR-020B 应新增字段一致性检查，失败时阻断进入 04 / Topic Card，或者至少降级为 `暂存观察`：

1. 知识库 / Obsidian / RAG / 内容资产来源：
   - 不允许 `我的工作流痛点`、`我要做的实验`、`重点体现` 出现 `分镜`、`成片验收`、`AI视频交付`，除非原始来源明确是 AI 视频知识库。
2. AI 视频 / AIGC / 短剧 / 分镜来源：
   - 不允许主字段落成 `知识库搭建` 或 `表格交付`，除非原始来源明确是视频素材管理知识库。
3. 办公文档 / PPT / Excel 来源：
   - 不允许主字段落成 `短剧/分镜/成片`。
4. `对标转译角度` 与 `我要做的实验` 必须共享同一主题簇：
   - 一个说信息雷达，另一个说成片验收，应失败。
5. `对应方向` 与主字段必须一致：
   - `对应方向=真实工作流改造` 时可以讲知识库/Agent/办公流程；
   - `对应方向=AI导演工作流` 时必须有视频/镜头/分镜/成片证据。
6. `推荐动作=生成脚本包` 必须满足：
   - `是否建议进入制作=是`
   - `title_permission` 不是 `不生成标题`
   - `我要做的实验` 可执行
   - `验证方式` 不为空且不是泛化句
7. `AI Hot` 入选：
   - 必须有重大性说明；
   - 必须有 Austin 角度；
   - 普通热点不能挤掉高适配对标来源。
8. fallback 输出：
   - 必须标记 `engine=deterministic` 或 `fallback_only`；
   - 不能作为 PM Accepted 的内容质量证据。

## Skill 变更方向

需要改 Skill，而且应同时设计全局私有 Skill 同步策略。

### 需要改 `skills/ai-account-editorial-director/SKILL.md` 吗？

需要，但不是把 Round 3 的硬编码主题规则搬进 Skill。

Skill 已经定义了正确的主流程，但需要补 AR-020 的新输入契约：

- 对标账号内容是核心来源，但必须经过 Austin-fit gate。
- AI Hot 是低权重热点源，15% 是影响力，不是数量配额。
- `来源权重类型`、`原始来源标题`、`来源构成`、`AIHOT重大性说明`、`候选来源方式` 是判断证据。
- `对标转译角度` 如果由代码预填，只能作为参考 evidence，Skill 必须自行决定是否采纳。
- Skill 输出必须覆盖所有 04/Topic Card/06 主字段，不能只输出 report 字段。

### 需要更新全局私有 Skill 吗？

需要。生产真实主编层默认读取：

`/Users/congcong/.codex/skills/ai-account-editorial-director`

repo mirror 只改不同步，生产不会自然生效。AR-020B 应设计：

1. repo mirror 更新。
2. 测试 Skill 或 staging Skill 验证。
3. PM/用户验收样例通过后，同步到全局私有 Skill。
4. 发布时明确同步步骤和 smoke。

### `editorial_skill_runner.py` 需要改什么？

建议方向：

1. 扩展 `CANDIDATE_CONTEXT_FIELDS`：
   - 增加 `来源权重类型`
   - `来源构成`
   - `原始来源账号`
   - `原始来源标题`
   - `AIHOT重大性说明`
   - `对标转译角度`
   - `候选来源方式`
   - source governance / AI Hot weight hints
   - AR-020 反向评估关注点
2. 明确 prompt：
   - `topic_flow_rework` 产生的是 evidence/hint，不是最终主编输出。
   - Skill 必须重写主字段，不得沿用冲突字段。
3. 调整 `normalize_batch()`：
   - 从“创造性补字段”降级为“合法值、空值、防伪、字段一致性校验”。
   - 对不一致字段降级，而不是改写成另一个确定性模板。
4. 报告必须显示：
   - `engine=codex/private_skill`
   - `skill_dir`
   - 是否触发 fallback
   - fallback 不可作为内容质量通过。

### 2026-07-01+ replay 应怎么跑？

不能只跑 `topic_replay_evaluation.py` 的 deterministic replay。

建议 AR-020B 增加“Skill replay”：

1. 使用生产只读 2026-07-01+ `content_items.csv`。
2. `content_sampler.py` 只生成 broad candidate pool，不最终主编。
3. 用 `editorial_skill_runner.py --engine codex` 调全局私有或测试 Skill。
4. 输出：
   - Skill enriched CSV
   - 04 field contract validation report
   - reverse evaluation report
   - selected-vs-observe report
5. QA/PM 只接受 `engine=codex` 且未 fallback 的样例作为内容质量证据。

## Proposed AR-020B plan

建议新需求命名：

`AR-020B 选题主编 Skill 与字段契约重构`

### 目标

让 AR-020 的质量提升发生在真实主编决策层，而不是 deterministic replay / PM report 旁路。

用户最终应看到：

- 04 记录主字段一致。
- Topic Card 里来源、方向、转译、实验、痛点一致。
- 06 继承的是 Skill 验收过的选题命题卡。
- replay 能用真实 Skill 重跑 2026-07-01+ 内容库，并给出可人工判断样例。

### 范围

1. Skill contract 更新：
   - repo mirror + 测试 Skill + 全局私有 Skill 同步方案。
2. `editorial_skill_runner.py` 上下文输入增强：
   - 把来源治理、对标来源、AI Hot 权重、原始来源、候选证据传给 Skill。
3. 字段 owner 重构：
   - 主字段由 Skill 输出。
   - deterministic code 只做事实预填、校验、fallback 标记。
4. 字段一致性 validator：
   - 在写 04 前检查主字段与来源主题是否冲突。
   - 冲突则降级/阻断，输出可行动原因。
5. Skill replay 工具：
   - 对 2026-07-01+ 内容库跑真实 Skill。
   - 输出 selected / observe / reverse / invariant report。
6. Topic Card / 04 验证：
   - staging/test 写入或 fixture read-back，证明用户可见字段一致。

### 非范围

- 不改 AR-026 全量采集覆盖。
- 不改 AR-027 schema cleanup。
- 不改 AR-013 补偿池。
- 不改 06 脚本生成质量。
- 不清理历史 03。
- 不写生产。

### 可能触碰文件

- `skills/ai-account-editorial-director/SKILL.md`
- `/Users/congcong/.codex/skills/ai-account-editorial-director/SKILL.md`（只在 PM/用户确认同步策略后）
- `scripts/editorial_skill_runner.py`
- `scripts/content_sampler.py`
- `scripts/topic_flow_rework.py`
- `scripts/topic_replay_evaluation.py` 或新增 `scripts/topic_skill_replay_evaluation.py`
- `scripts/push_today10_to_feishu.py`
- 新增字段契约测试，例如 `scripts/test_topic_editorial_field_contract.py`

### 测试

1. Unit tests：
   - Skill context includes source governance fields.
   - deterministic fallback cannot be marked as editorial quality pass.
   - knowledge-base source cannot coexist with AI video experiment fields.
   - AI video source cannot coexist with knowledge-base experiment fields.
   - AI Hot selected requires major relevance and Austin angle.
   - `推荐动作=生成脚本包` requires executable experiment and matching direction.
2. Replay tests：
   - 2026-07-01+ production read-only CSVs.
   - `engine=codex` / test Skill path.
   - no fallback.
3. Staging/test：
   - write test 04 records only.
   - render Topic Card preview.
   - verify card fields match source theme and Skill output.
4. RC gate：
   - no production writes.
   - no real Topic Card send.
   - no 06 generation.

### PM acceptance samples

至少固定看这些样例：

1. `Codex联动Obsidian...知识库`
   - 应落到信息雷达 / 内容资产 / 03->04->06 流转。
   - 不得出现分镜、成片验收、AI视频交付。
2. `AIGC自修室 多宫格故事板`
   - 可落到 AI导演工作流。
   - 要说明视频交付链路，不只是工具教程。
3. `Mx-Shell Skill / 清道夫`
   - 应判断是否与 AI导演/Agent Skill 工作流有关，若证据不足则观察。
4. `大伟聊前端 CI/CD Shell`
   - 如果入选，应解释非技术 Agent 任务验收；如果不入选，应给可信拒绝理由。
5. broad enterprise AI / account growth items
   - 可以观察，但不能挤掉更适合 Austin 的候选。
6. AI Hot
   - 只有重大模型/行业变化可进入；普通热点必须观察或不入选。

### 迁移与回滚

1. production 默认不启用 AR-020B，先 dev/staging。
2. Skill replay 未通过前，不合并生产。
3. 如果 `engine=codex` 失败：
   - daily pipeline 应失败可见，或显式 `deferred_editorial`，不能悄悄用 deterministic 伪装质量通过。
4. deterministic fallback 只允许：
   - 本地 dry-run
   - 字段完整性检查
   - production 紧急保底时生成 `暂存观察 / 不生成标题`
5. 回滚：
   - 保留旧生产 Skill。
   - 发布前备份全局私有 Skill。
   - 若新 Skill 输出更差，恢复旧 Skill + 禁用 AR-020B 字段合同变更。

## 风险和开放决策

### 风险

1. 真实 Skill replay 成本更高，但这是内容质量验收必须成本。
2. 全局私有 Skill 与 repo mirror 双轨容易漂移，需要同步脚本或发布检查。
3. 如果 Skill 输出不稳定，字段一致性 validator 可能频繁降级，需要 PM 判断“宁可少候选”是否接受。
4. 旧 deterministic fallback 里有大量创作性函数，必须逐步降级，否则会继续污染主字段。

### 需要 PM/用户确认

1. 是否接受 AR-020B 作为新架构任务，而不是 Round 4。
2. 是否确认内容质量验收必须以真实 Skill replay 为准。
3. 是否接受“候选少但一致性强”，不再为了 10 条候选补弱项。
4. 是否允许更新全局私有 `ai-account-editorial-director`，以及同步策略。
5. 是否需要先做 Skill-only spike：只改 Skill 和 runner context，不改 04 写入。

## 本轮边界确认

- 未写生产。
- 未写飞书。
- 未发 Topic Card。
- 未触发采集。
- 未触发 06/Codex 生成。
- 未改脚本、配置、Skill、测试、SCF、runtime。
- 本文档是 docs-only 架构审查 artifact。
