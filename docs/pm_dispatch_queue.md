# PM 派发队列

这个文件用于记录 PM 线程准备派发、但不能立即发送给执行线程的任务。目标是实现“排队等待现有任务完成”，避免直接给 active 线程发送消息导致上下文被打断。

## 使用规则

- PM 线程是唯一派发者；开发、测试、生产线程之间不得互相下达新任务。
- 如果目标线程是 active / in progress，PM 不调用 `send_message_to_thread`，而是把任务记录为 `Queued / Waiting Dispatch`。
- 如果目标线程 idle，PM 可以派发任务，并把队列状态更新为 `Dispatched`。
- 只有 P0 生产事故、用户明确要求中止、或任务标注 `emergency interrupt` 时，PM 才能打断 active 线程。
- 当前用户拥有的 Codex 线程工具 `send_message_to_thread` 没有原生队列参数；队列语义由本文件、线程状态读回和 PM 事件触发检查共同实现。
- 不做固定频率轮询，避免无效消耗 token。只有收到执行线程回传、用户发来新指令、PM 处理发布/需求事项，或 PM 明确需要推进队列时，才检查并派发下一项。
- 队列任务必须包含目标线程、任务 ID、派发条件、禁止事项和验收口径。
- dev/test 返修任务必须额外包含当前轮次、测试线程验收结论、PM 验收结论、失败证据、复现方式、期望结果、实际结果和派发条件；三轮计数由 PM 维护。
- 派发后仍要在 `docs/thread_handoff_log.md` 记录真实派发和读回结果。

## 状态定义

- `Queued / Waiting Dispatch`：已排队，等待目标线程空闲。
- `Dispatched`：已发送给目标线程，等待回传。
- `Blocked / Need Authorization`：需要用户授权或外部资源。
- `Cancelled`：PM 或用户取消。
- `Completed`：已派发并完成闭环，详情转入 `docs/thread_handoff_log.md`。

## 当前队列

### AR-020C QA Recheck - Content Quality Resume Full Replay

- 状态：Completed / Returned to PM Review
- Created：2026-07-09
- Target lane：Test / QA thread `019f4714-3f76-7bb1-b71f-08a41d9f8860`
- Priority：P1
- Dispatch result：user explicitly authorized creating a new QA thread because the old QA thread was not writable by Codex tools. Created new project-local thread `019f4714-3f76-7bb1-b71f-08a41d9f8860` titled `测试验证执行 v2` and sent this task card as its initial prompt.
- Old thread note：old QA thread `019f269e-e26b-74d2-8ba1-a606edef1171` remains visible in the sidebar/list but is no longer the fixed QA target because `send_message_to_thread` / `read_thread` could not resolve its rollout.
- Completed：new QA thread `019f4714-3f76-7bb1-b71f-08a41d9f8860` returned `QA Passed / Content Review Ready / Waiting PM Original-Requirement Review` after completing 7/7 full replay batches in `/private/tmp/ar020c_content_quality_full_replay_round2_20260709`. PM review remains open; this queue item is no longer waiting for dispatch.

Task card:

```markdown
## PM Dispatch

Task: AR-020C QA Recheck - Content Quality Resume Full Replay

Target commit/build:
- dev worktree: `/Users/congcong/Desktop/AI/AI项目/AI账号工作流/ai_account_radar_dev`
- branch: `feature/next-production-flow`
- target commit: `aa5c531 fix: improve AR-020C title expression`
- prior partial replay out-dir from dev: `/private/tmp/ar020c_content_quality_full_replay_round2_20260709`

User goal to verify:
- 回到用户原始需求：选题主编逻辑是否更像 Austin 本人判断，而不是只满足上一轮技术修复点。
- 本轮重点验证标题表达返修是否解决“模板化 / 黑盒 / 同构标题骨架”问题。
- 不能把 6/7 partial replay 当作完整通过；必须尽量完成 7/7 full real Skill replay 后再评价内容质量。

Required levels:
- L0: diff/scope audit for `aa5c531`，确认变更集中在 AR-020C title expression / Skill runner / field contract / tests，不混入生产发布、SCF/runtime、06 或 AR-026/027。
- L1: run/verify targeted tests if feasible，至少覆盖 title/field contract、replay observability、topic flow、content sampler recovery、Topic Card guards；也检查 `py_compile`、`git diff --check`、`pre_merge_check.py`，但不要让这些替代内容质量判断。
- L2: resume full real Skill replay using existing out-dir. Use `/private/tmp/ar020c_content_quality_full_replay_round2_20260709` and `--resume` to complete the remaining failed batch. If quota/usage limit still blocks, report `Blocked / Usage Limit` with artifacts; do not call content quality passed.
- L3: no need to resend staging/test Topic Card unless the full sample package or code changes show user-visible card formatting changed. If L3 retest is necessary, use only `.env.staging.local` dedicated test 04 / personal test target; no production.

Required flow tests:
- Resume the existing batched full replay. After completion, verify `skill_replay_batches.json` shows 7/7 completed and 0 failed.
- Rebuild/verify aggregate artifacts: `skill_replay_summary.json`, `skill_replay_rows.csv`, `skill_actionable.csv`, `skill_observe.csv`, `near_miss_high_fit_unselected.csv`, `title_body_check.csv`, `skill_contract_failures.csv`, `ar020c_user_sample_summary.md`.
- Check `quality_gate_ok`, `contract_failure_count`, `title_quality_failure_count`, `title_quality_warning_count`, `fallback_row_count`, `writes_feishu`.
- Inspect actual titles and `标题思路` / `主编判断摘要`, not only metrics. Compare against prior failures around `先测/会不会/验收/测试/验证/冒号反思壳`, and check whether observe/supplement rows now read like useful evidence gaps rather than placeholder task names.
- Review actionable rows, observe rows, near-miss/high-fit unselected rows, and title body check. Report 5-8 readable examples that PM/user can judge.

Required regression:
- Ensure no fallback rows are treated as quality evidence.
- Ensure AI Hot does not enter actionable just by heat; if it appears, verify major relevance and Austin-specific angle.
- Ensure complement/observe rows remain separated from `生成脚本包` semantics.
- Ensure production boundary is clean: no production Feishu writes, no production Topic Card, no collection, no 06/Codex, no global Skill sync, no SCF/runtime deploy.

Allowed environments:
- Read-only production CSV/log artifacts.
- `/private/tmp` QA artifact output.
- dev worktree only for code/test inspection.
- Staging/test Feishu only if L3 retest becomes necessary.

Forbidden actions:
- No production Feishu writes.
- No production Topic Card send or click.
- No collection / Douyin crawling.
- No 06/Codex generation.
- No global private Skill sync.
- No SCF/runtime deploy.
- No code edits, commits, or push from QA thread.

Evidence required:
- Target commit and worktree status.
- Exact replay command(s), whether `--resume` was used, and final out-dir.
- Batch completion counts and failed batch details, if any.
- Summary counts and quality gate result.
- Paths to PM/user sample package and key CSV/JSON artifacts.
- Human-readable title/content examples: actionable, observe/supplement, near-miss/high-fit unselected, and any failures/warnings.
- Tests run with pass/fail counts.
- Production boundary proof.

Pass/fail criteria:
- Pass candidate only if full 7/7 replay completes, `fallback_row_count=0`, artifacts are self-consistent, and PM/user-readable samples show title expression no longer relies on the rejected template shells.
- If full replay is blocked by usage limit, conclude `Blocked / Usage Limit`, not pass/fail content quality.
- If metrics pass but samples still feel templated/black-box, conclude `QA Failed / Content Rework Needed` with examples.
- If partial 6/7 remains the only evidence, do not pass.

Required handoff:
- Use `## PM交接摘要`.
- Include conclusion, target commit, replay evidence, sample paths, readable examples, tests, production boundary, risks, and recommended AR state.
- Be explicit whether PM can now do original-requirement content review, or whether QA is blocked/rework-needed.
```

### AR-020C QA Recheck - Original Title Hook Polish

- 状态：Dispatched
- Created：2026-07-10
- Target lane：Test / QA thread `019f4714-3f76-7bb1-b71f-08a41d9f8860`
- Priority：P1
- Dispatch result：首次线程检索未返回既定 QA thread；随后直接按固定 thread ID 投递成功，任务卡已发送给 QA lane。开发 aggregate-only 结果仍不替代独立 QA。

Task card:

```markdown
## PM Dispatch

Task: AR-020C QA Recheck - Original Title Hook Polish

Target commit/build:
- dev worktree: `/Users/congcong/Desktop/AI/AI项目/AI账号工作流/ai_account_radar_dev`
- branch: `feature/next-production-flow`
- target commit: `c0dafe5 fix: borrow original title hooks in AR-020C`

User goal to verify:
- 标题可以借鉴对标/原始标题的市场表达资产，但不能照抄，也不能回到工具教程号或泛热点搬运。
- 样例摘要必须让用户清楚区分内部分类、原始标题、原始来源摘录、原始标题钩子、Austin 改写理由和最终可发布标题。
- 原始 caption 的截断脏文本不能被展示成标题。

Required levels:
- L0：审计 `c0dafe5` 范围，确认只涉及 AR-020C Skill/runner/replay report/tests，不混入生产、SCF/runtime、06、AR-026/027。
- L1：独立跑相关 tests、py_compile、`git diff --check`、`pre_merge_check.py`。
- L2：必须新建 QA out-dir，使用 2026-07-01+ production read-only content CSV 跑 fresh full batched real Skill replay；允许 `--batch-size` + `--resume`，但不得只对开发已生成 rows 做 `--aggregate-only` 就判通过。
- L3：不用重发 staging/test Topic Card，除非 QA 发现这次改动影响 card 字段/展示；这次重点是 fresh title generation 和用户样例摘要。

Required content checks:
- 抽查至少 6 条：Codex+Obsidian、故事板、Codex PPT、Agent/飞书执行台、AI视频、AI Hot 或泛增长观察。
- 验证 `原始标题钩子` 与 `Austin改写理由` 来自 fresh Skill rows，不是 report 后处理伪造。
- 对每条可发布标题判断：是否保留工具组合/结果承诺/场景词/学习入口等市场入口；是否没有大段复制原始标题；是否仍落到 Austin 的真实工作流或导演现场。
- 检查 `ar020c_user_sample_summary.md`：内部分类中文可读；原始标题和摘录分开；长 caption/URL/hashtag/截断文本不被当作标题；最终 publish title 明确可见。
- 复查 AI Hot 不会仅凭热度进入 `生成脚本包`，观察/补证据不混进可生成候选。

Forbidden actions:
- 不写生产 Feishu，不发生产 Topic Card，不触发采集、06/Codex，不同步 global Skill，不部署 SCF/runtime，不提交代码。

Pass/fail:
- 通过：fresh real replay 完成且质量门/回归通过；标题借鉴有可读改善但无照抄/教程化；报告展示清楚；生产边界为 0。
- 失败：只完成 aggregate-only；钩子字段未进入 fresh Skill 输出；标题照抄或丢失 Austin 判断；caption 脏文本仍被当标题；AI Hot 以热度挤占对标内容。

Required handoff:
- 结论、fresh replay 路径、batch 完成情况、可读样例、测试结果、生产边界、风险、建议状态。
```

### AR-020C QA Recheck - Title Hook Content

- 状态：Completed / Failed Returned to PM
- Created：2026-07-10
- Target lane：Test / QA thread `019f4714-3f76-7bb1-b71f-08a41d9f8860`
- Priority：P1
- Dispatch result：已向固定 QA thread 投递，验证 dev `7837bf8 fix: harden AR-020C observe title evidence`。
- Core gate：必须以新 QA out-dir 跑 fresh full batched real Skill replay；开发 runtime/backend 失败产物与 aggregate-only 旧 rows 均不得作为通过依据。
- Required checks：四条旧失败壳的自然改写、quality gate、batch final-state notes、六类用户样例、原始标题钩子不照抄、生产边界为 0。
- Result：fresh replay 7/7 完成，但 `quality_gate_ok=false`（3 fail + 1 warn）；已回传 PM。AR-020C 因超过三轮 QA 边界进入停线复盘，暂不排下一轮开发/测试。

### AR-020C Final Dev Self-Validation - title surface and content convergence

- 状态：Completed / Failed Returned to PM
- Created：2026-07-10
- Target lane：Development thread `019f1de3-f3f2-71d2-ae63-a74cd38f8474`
- Priority：P1
- User authorization：用户在 PM 说明 7 次 QA 回传/复测后，明确要求开发先确定改好再交测试，授权一次最终开发自验收敛；不重置 QA 计数。
- PM gate：开发必须 fresh full replay `quality_gate_ok=true`、四条失败/误伤样例 before-after、batch final state、六类样例、tests 和 scoped commit 全部齐备，PM 审核自验包后才可派最终 QA。
- Result：开发按自验门在 `/private/tmp/ar020c_final_dev_self_acceptance_20260710_r3` 运行新的真实回放；第 1/7 batch 成功后发现 Storyboard 前台表达仍为“真正要过的是我的分镜返修验收 / 最后还是要过分镜返修”，不符合自然人格化表达，主动停止后续 batch。结论 `Dev Self-Acceptance Failed / Stay in Development`；未提交、未 push、不得派 QA。

### AR-020C Structural Root-Cause Review - persona, case library, and template ownership

- 状态：Completed / Returned for User Scheme Decision
- Created：2026-07-10
- Target lanes：Development thread `019f1de3-f3f2-71d2-ae63-a74cd38f8474` + QA thread `019f4714-3f76-7bb1-b71f-08a41d9f8860`
- Priority：P1
- Scope：只读/docs-only 审查；追踪案例库和人设是否真的参与真实生成，global private Skill/repo mirror 的实际选择，runner/schema/hint/field guard 如何把自然判断压成任务卡表达，以及原始标题钩子是否影响生成而非仅报告展示。
- Forbidden：不改功能代码、不跑完整 replay、不派 QA、不写 Feishu、不发卡、不采集、不触发 06/Codex、不动生产。
- PM gate：开发和 QA 的独立根因报告必须先汇总给用户确认新的结构方案；不得把本次审查当作 QA 轮次或自动开启下一次开发循环。
- Result：两份审查结论一致。真实运行时 default 是 global private Skill；原始标题钩子已进入输入但案例库只是弱 grounding；单 prompt 同时要求自由判断和完整 workflow field mapping，pre-Skill hints/母场景又提供高密度任务语言，guard 仅能拦截不能自然重写。产物为 `docs/spikes/ar020c_adversarial_structure_review.md`，docs-only commits `8dfd0e1`、`e629b2e`。等待用户确认结构方案。

### AR-020D Development - persona-referenced editorial judgment architecture

- 状态：Implementation Dispatched / Dev Self-Validation Required / QA Reset 0/3
- Created：2026-07-10
- Target lane：Development thread `019f1de3-f3f2-71d2-ae63-a74cd38f8474`
- Priority：P1
- User confirmation：用户确认新架构。案例库只作人格、判断习惯与表达风格参考，不作选题事实/证据/逐条案例锚点；Skill 更新必须同步 Git 源仓库。
- Required boundary：完整风格参考必须真实进入隔离 test private Skill 的运行时 prompt；第一阶段自由主编判断不得接收 experiment/validation/旧字段/mother-scene conclusion/deterministic title hint；第二阶段只映射锁定 judgment，不能生成新标题或新角度；真实 global Skill 更新留到 QA/用户审阅/发布计划通过后的生产线程。
- PM gate：开发完成结构自验包并由 PM 核验后，才允许派 AR-020D 新方案的第 1 次 QA。禁止把 AR-020C 未提交标题补丁作为实现基线或混入提交。
- PM review result：`53d5fb7` 的风格参考嵌入、Stage 1 forbidden input 与标题/角度锁证据通过；选择状态所有权和全局 top3 未通过。已退回开发补 canonical decision/action invariant、global editorial ranking 和 normalize 后最终不变量；未派 QA。
- Rework result：`0fbc386` fresh replay 证明 19 行 global ranking 完整、global/final top=3、最终选择/动作/标题/角度/理由 0 drift。PM 证据复核通过，已向 QA v2 线程派发 AR-020D Round 1/3 一次性结构+内容+L3 可见流验证。
- QA Round 1 result：L0 对抗探针发现 `apply_global_ranking()` 对缺行/重复行没有 fail，以及 raw Stage 2 越权改写可经 normalization/reapply 被洗回 pass；因此判 `QA Failed / Architecture Control Rework Needed`，未运行 L2/L3。
- Rework dispatch：已退回开发线程。必须实现 ranking rows 与 Stage 1 decisions 的严格 bijection 校验，并在任何 normalization 前捕获、保留和阻断 Stage 2 对 title/angle/rationale/summary/decision/daily level/action/produce state 的漂移；先通过最小反例与新的 7/7 fresh self-validation，PM 复核后才可派 QA Round 2/3。
- Rework progress：未提交代码已完成上述控制和 82 项本地测试；用户授权有效，但平台安全审查仍绝对拒绝外发执行。当前 `Blocked / Needs Trusted Skill Replay Environment`。已派本机既有真实 artifacts 的 offline architecture evidence，不调用模型、不绕过审查；它不替代 fresh replay，通过前不 commit/push、不派 QA。
- Offline evidence result：`Offline Architecture Evidence Passed`。真实旧 ranking 可验证字段和全部对抗反例通过预期；旧 Stage2 owner authoring 19/19 被新门禁保留为 fail + guard blocked。证据目录 `/private/tmp/ar020d_arch_control_offline_validation_20260711`；仍不满足 fresh replay 门。
- New confirmed rework：用户确认移除 nested `codex exec`。开发已派 `In-Thread Editorial State Machine`：prepare/validate/finalize 由 Python 管理，当前 Codex task 直接生成 Stage1、全日 ranking 和 operational-only Stage2 outputs；同一路径供后续 QA/production outer automation。禁止 API/subagent/第二模型会话；开发 current-task 7/7 自验和 PM 复核前不派 QA Round 2。
- Dev result / PM gate：`662596e` 自验指标通过，但 PM 发现 test Skill hash 与 final repo mirror 不一致，active config/docs 仍有旧 nested CLI 正式入口，legacy CLI help/default 与硬禁行为冲突。已派 evidence/runtime-contract closure：重新生成 hash 等价 test Skill、全新 current-task 7/7、更新 active rules/commands、明确 legacy migration。QA Round 2 未启动。
- Evidence closure / QA dispatch：`1497cf8` 已完成并 push；repo/test Skill SHA256 同为 `8bc4cb63...`，current-task evidence 为 Stage1 7/7、ranking 19/19/top3、Stage2 7/7、0 fallback/write/drift/failure。PM 独立核对 provenance、Top 3 来源、案例证据边界、warning 位置及 58 项针对性测试后通过开发证据门，已向固定 QA v2 派发 Round 2/3 的 L0 架构对抗、fresh current-task L2 原始需求内容复核和通过后 staging/test L3 真卡验证。
- QA Round 2 / PM review：QA fresh L0-L3 全通过，112 tests，专用测试 04 + 个人测试卡完成，production marker=0。PM 未只采信 quality gate，已逐条审 19 行和用户样例包，判断选题 ownership、Top 3 全日排序、自然借用原始标题钩子、案例库 reference-only、AI Hot gate 与可见卡片分区满足原始需求。当前不派 Round 3；等待用户查看实际样例后决定是否进入 RC 方案。
- User review override：用户看实际样例后不接受。问题不在状态机漂移，而在 research/hook/card product contract：无 source-open + 全网搜索阶段，Storyboard 被账号偏好强行翻成“返修”，Mx-Shell 丢失《丧尸清道夫》爆款传播入口，Topic Card 未展示具体原始文章/视频链接、原始标题、建议方向和内容结构。PM 已撤回原始需求通过判断；当前保持 hold，不派开发、不派 Round 3，先向用户提交新方案。
- Additional hold requirements：先审原始 Word、人设材料与 Skill 的提示偏置；当前 AI 生成的 persona brief/mother-scene 前缀把案例语义固化为 `流程/交付/验收/返修`，不能继续作为 active selection instruction。用户撤销每日 Top 3 上限：保留全局排序但不截断，所有过质量门候选都应展示，UI 超长只能分卡。方案确认前不派开发。
- Confirmed review dispatch：用户已确认 `Research-grounded Editorial Director + Persona-native Topic Card` 方案。已向固定开发线程派只读架构设计，输出 `/private/tmp/ar020d_research_editorial_arch_review_dev_20260711`；向固定 QA v2 派对抗评审与最终 QA 设计，输出 `/private/tmp/ar020d_research_editorial_arch_review_qa_20260711`。均禁止代码/文档修改、commit/push、Feishu、生产和线程间指挥；评审结果先回 PM 汇总给用户再确认实施。
- Joint review result：两侧结论一致，旧 `1497cf8` 不能作为新目标实现基线。硬性实现合同为 research-before-selection、evidence-backed hook、Persona 三层隔离、0..N dynamic recommendation、decision-first card 与 lossless pagination；Storyboard/Mx-Shell 仅作反例，禁止硬编码答案或禁词替换。
- PM resolved defaults：04 新增 `研究摘要 / 受众钩子 / 内容结构` 三个可见字段；证据明细保留 dossier/audit；缓存默认 24h/72h/7d 且 hash 变更立即失效；未打开 exact source 不可推荐制作；每页 5 条，多页消息 + 合并确认，未操作不等于不做。等待用户最终确认后才派开发。
- Final user confirmation：方案已确认；由于是 research/persona/card 实质性重构，QA 重设为 `0/3`。同时确认 active path 绝对零 fallback：旧 Skill/Persona/Top3/deterministic/legacy/summary substitution 必须删除而非降级；失败必须显性 fail closed，不能继续生成或伪装成功。
- Dev self-validation r1：正确返回 `Dev Self-Acceptance Failed / Stay in Development`，无 commit/push。阻塞为通用 web surface 拒绝 14 个精确 Douyin video URL，且旧 Top3/implicit-reject 回归未迁完；未交 QA。
- PM continuation decision：开发不得停工或请求放宽门禁。精确抖音来源统一使用项目已有专用 Chrome CDP profile/port 9333，新建 exact-video read-only opener；任何登录/验证/内容不足保持候选失败。并行完成旧 Top3 和未选即不做迁移，新的 fresh 自验通过前不提交、不派 QA。
- Dev r2 result：16/19 exact-source+research 完成，10 条推荐已在 staging 04/两页个人测试卡无损展示；三条失败均被正确隔离，整体 `ok=false`。开发仍未提交，QA 0/3。
- PM adapter decision：不接受 16/19 作为提交门，也不放宽零 fallback。按域名预声明唯一 primary adapter：Douyin CDP、X/Claude trusted browser、普通文章 standard web open。要求用 trusted browser exact-page 重新打开 `x.com/kimmonismus/...`、`claude.com/blog/how-people-are-using-claude-cowork`、`x.com/emollick/...`；若主 adapter 失败则继续 typed failure，不可换搜索/镜像/摘要。
- Dev r3 progress：新的同一批次 evidence 已达 19/19 exact-source-open，唯一 adapter/provenance、X status identity、Claude path、DOM/截图均已记录，adapter/research 20 tests + Douyin Node 6 tests 通过。但后半链全部 pending，当前不提交、不派 QA。开发继续在同一 r3 完成 research -> Stage1 -> 0..N ranking -> Stage2 -> finalize -> card/pre-merge。
- Dev r3 final / PM review：`c0356ca` 已 push，full chain 指标通过；PM 未放行。active fallback dead code/options/fields 仍存在；Persona retrieval 实际固定返回同五条模板化中文样例且 counterfactual 无 paired evidence；卡片把 research summary 与 hook 写成同一句、angle 写成栏目、两条 article 原始标题为空、Douyin caption/title 未分离、page-level no-selection 文案/动作仍混用 batch 语义。已退回开发做结构返修，QA 保持 0/3。
- Dev r4 progress：fallback/persona/card 结构返修与 97 tests/pre-merge 已通过，但 5 条 non-Douyin fresh screenshot 失败导致开发停线。PM 判定截图不是 source semantic eligibility 的硬字段；exact URL identity、fresh DOM/title/body/author/hash/provenance 完整即可继续，截图失败必须显性 audit warning。开发可从同一 r4 继续，不得复用 r3。
- Dev r4 resumed：同一 r4 已达 19/19 source-open，research 输入已准备；此前工具额度中断不计 QA/开发失败轮。额度恢复后已重新派固定开发线程从 research 写入点继续，禁止新建 run、复用 r3 或降级证据。状态保持 `Dev Rework / Full Self-Validation Running / QA 0/3`。
- PM r4 evidence review：`3e51bc1` 不进入 QA。退回开发修复 14 条 Douyin title/caption 分离、19 条中文 summary/hook + confidence、Persona operation profile 与 actionable title family 集中、当前 R4 DOM/截图同态可见证据；必须新 fresh out-dir 完整重跑，不能 aggregate 旧 r4。状态 `PM Evidence Review Failed / Dev Rework / QA 0/3`。
- PM r5 evidence review：可见字段返修通过方向性复核，但真实 web research 缺失。19/19 dossier 的 `results=[]`、query 仅复核 exact URL、corroboration 均为空，却有 9 条推荐；已退回开发实现逐候选 topical/entity/claim 搜索、真实打开结果页、claim/evidence ID 和 no-search/no-corroboration fail-closed。状态 `PM Evidence Review Failed / Real Web Research Missing / QA 0/3`。
- r6 continuation：开发已完成研究资格门与 42/42 对抗测试，但 full r6 尚未开始/完成。线程空闲后 PM 已续派，要求一次性完成 19 dossier、无法佐证自动降级、全主编链、staging 可见证据和完整回归后才回传；QA 仍 0/3。
- r6 candidate-failure continuation：部分 Douyin 精确视频当前不可用。PM 已续派 candidate-level isolation：同主通道有界重试一次，失败候选不进下游，其余候选继续 full chain，run 保持 non-ok/completed_with_failures。要求补失败隔离、全失败空卡、无泄漏和同 adapter retry 测试；QA 仍 0/3。
- r6 PM evidence failure：`532bed4` 的失败隔离和推荐 URL 映射有效，但外部 evidence 文件只是标题/发布者/URL/生成 claim 四行文本，不含真实页面 body/DOM。已续派 raw capture、literal supporting excerpt substring validator、真实 captured_at、hash 及合成文件反例测试；同时修 Persona actionable 统计口径和 fresh retry <=2 证据。QA 仍 0/3。
- r7 screenshot closure：fresh r7 已完成 16 条 survivor 的 raw DOM/hash/literal excerpt 研究证据和全主编链，3 条失效来源均在同 adapter 最多 2 次后隔离，9 条推荐分 2 页测试卡并已发送个人目标；当前唯一门禁是两页 current r7 Feishu Web 截图。PM 已续派固定开发线程只补 fresh screenshot/DOM 同态核对、最终回归和生产边界；通过后才可 commit/push，QA 仍 0/3。
- r7 PM visible-content failure：`0326c5e` 已 push，四张截图和 DOM 也已补齐，但 PM 对账发现实际前台仍是旧 `[AR-020D R5 TEST]` 内容。第一页第 2 条为已不在 r7 survivor/card manifest 的 Mx-Shell，Agent 摘要/置信度也与 r7 final row 不同；现有 closure 只检查标签和 ID 数量，没有检查内容值。已退回开发用全新隔离 staging run/records 逐字段写入 r7 9 条 actionable、strict record IDs 发卡，并增加 snapshot hash/字段值同态门。QA 仍 0/3。
- r7 PM source-identity failure：`43ff70e` 的新隔离 run、9 条 create-only read-back、2 页 5/4 manifest、当前 DOM/截图和 snapshot hash 已排除旧 R5 串行；PM 独立比较原始 r7 `skill_replay_rows.csv` 后仍发现 1 个语义漂移：Claude Cowork 原始行 `原始发布文案` 为空，`push_today10_to_feishu.map_row()` 以 `来源内容` 回填为“人们如何使用Claude Cowork”。现有 closure 以 writer `expected_rows` 为起点，因此不能发现上游到 writer 的漂移。已续派开发只修来源字段零 fallback、原始行起点 validator 和文章无独立文案反例；不重跑 research/Persona/Stage1/ranking，不派 QA，仍 0/3。
- r7 source-identity closure：`11527d2` 已删除 writer/card 的原始发布文案 fallback，并新增原始 r7 row 起点的可见闭环 validator。PM 独立复跑 `/private/tmp/ar020d_r7_source_identity_retest_20260713`：9 条原始 actionable -> 新 staging read-back -> 2 页 manifest -> DOM 全通过，page hash 为 `38a056...` / `5aa508...`；Claude 原始/04 文案均为空，卡片仅显示缺失占位；56 项针对性测试通过，production clean。开发证据门通过，已派固定 QA v2 执行新架构 Round 1/3 架构与证据完整性审查。
- QA Round 1/3 failure：目标 `11527d2` 的 154 项回归全绿，但独立 mutations 证明 nonexistent exact-source DOM + arbitrary hash 仍 eligible；12 条 opened Douyin source 无 raw body path；state machine/runner/writer 仍有 caption<-title、research_summary<-summary/public decision、original title<-legacy title 四条跨字段替代；Persona paired counterfactual 为人工 control merge；无 external result dossier 仍 eligible；partial run 与绿色 `quality_gate_ok` 并存。已续派固定开发线程做一次 consolidated architecture rework + fresh full self-validation；Round 2 blocked，不安排微复测。
- R8 PM evidence review：`21e772c` 的 19/19 source raw、19/19 external research raw、7 组独立 Persona 输入/输出、full-run status 和 12 条 5/5/2 staging DOM lineage 均通过独立重算；但 active `topic_skill_replay_evaluation.py` 的 `sample_rows/title_body_check/progress/trace` 仍用 `原始来源标题 or 来源内容/来源标题`，最小 mutation 可把 Douyin caption 伪装成原始标题，R8 用户样例报告已实际出现该漂移。另 `staging_r8_page1_top.png` 显示 2 条 MIRA/支付宝，即第 3 页而非第 1 页。状态保持 Round 2 blocked；退回开发一次性删除全部 PM-facing cross-field fallback，并让 screenshot collector/validator 绑定 page manifest、DOM、候选数和首条 identity。
- R8 closure PM recheck：`07f3293` 的三页 top/bottom 截图已逐张人工核对并重算 SHA256，页 1/2/3 分别从 Codex+Obsidian、Agent、MIRA 开始，5/5/2 与 manifest/DOM 一致；原始标题与发布文案的最小 mutation 也已修复。剩余阻断是审计范围不完整：`sample_rows()` 仍用 `Austin改写理由 or 标题思路`，PM mutation 可复现且 `semantic_zero_fallback_audit.json` 仍报 0；visible closure expected snapshot 仍以 `我的选题标题`/locked angle 补 `选题命题`/`我的切入`；source-open `csv_title` 仍以 `来源内容` 补空标题。状态保持 Round 2 blocked，退回开发做最后一次全语义 owner fail-closed 收口，不安排微 QA。
- Final semantic owner PM gate：`1abc92b` 已将五组用户语义 owner 纳入跨 active-path AST 审计；PM 独立 mutation 证明空 `Austin改写理由` 不再由 `标题思路` 代填，空 `选题命题/我的切入` 在 visible expected snapshot 立即失败，视频空标题的 source-open `csv_title` 保持空，四种故意 BoolOp/IfExp/subscript 替代均被报告。127 项相关回归、py_compile、diff check、pre-merge 与 production clean 均通过。开发证据门改为 Passed，已派固定 QA v2 执行 Round 2 完整架构+内容+fresh staging 真卡验证。
- QA Round 2/3 failure：QA 在 L0 增加跨语句变异后发现 semantic audit 只遍历 BoolOp/IfExp；顺序 assignment、嵌套 assignment 与手工 `if/return` 实现同一 `Austin改写理由 <- 标题思路` fallback 时均返回空。PM 已独立复现。Round 2 按门禁停止，未审 12 条内容、未写 staging、未发卡。当前退回最后一次集中开发：实现跨赋值/分支/return 的数据流审计，并为五组 owner 的所有 active transformation 建唯一 sentinel 行为矩阵；完成自验和 PM 复核后才进入最终 Round 3，不重跑 Round 2。
- Final dev evidence gate：`94e5713` 已通过 PM 独立复核。新增正例覆盖顺序赋值、early return、嵌套 alias、get default、try/except、NamedExpr，均被 dataflow gate 拦截；独立字段输出负控不误报。active static 0 violations、behavioral sentinel 7/7、PM 聚合回归 133 tests、pre-merge 和 production clean 均通过。
- Final QA Round 3 dispatch：已向固定 QA v2 线程 `019f4714-3f76-7bb1-b71f-08a41d9f8860` 派发最终一次 L0-L3 验证。目标提交 `94e5713`；要求独立重跑语义数据流反例、逐条审查 immutable R8 12 推荐+7 观察及 exact-source/research/Persona/ranking，随后在专用 staging 04 + 个人目标创建 fresh 12 条并发送 3 页 5/5/2 测试卡。任何 gate 失败即停止并返回 `QA Round 3/3 Failed / Stop`；通过也只能报 `Candidate for PM Original-Requirement Acceptance`。
- Final QA Round 3 result：`QA Round 3/3 Failed / Stop`。L0/L1 通过，L2 在 claim-to-evidence 语义门失败；TechRadar 导航/会员文案被错误登记为 Claude Cowork 业务运营使用分布证据，另有 AI use-case/FDE 弱摘录支撑强 claim。L3 未运行，无 staging/卡片/点击；生产边界为 0。QA 队列停止，不创建 Round 4。
- Pending user decision：是否另立新的 claim-level evidence verification 需求。若确认，先做产品/架构合同，不直接派开发：原子 claim、证据语义判定、事实/推断分层、强主张引用、unsupported typed failure、反确认偏误 blind verifier 和全推荐逐条人工 QA。未确认前保持停止。

### AR-020E Development - aggressive hook and editorial expression calibration

- Created：2026-07-14
- Target lane：固定开发线程 `019f1de3-f3f2-71d2-ae63-a74cd38f8474`
- Priority：P1
- Status：PM Accepted / QA Complete / Ready for RC Planning
- Confirmed product rule：`Hook First / Aggressive by Default / Allow Hyperbole / No Fabricated Verifiable Facts`。研究负责找到真实来源、公共传播钩子和材料边界，不再要求每个编辑观点与标题修辞都通过论文式 entailment；精确数字、直接引语、官方功能/声明和高风险事实仍需来源。
- Scope：以 `94e5713` 和 immutable R8 dossiers 为基线，更新 Git-managed repo Skill、current-task Stage1/editorial contract、标题质量策略和必要测试；输出 12 推荐 + 7 观察的 before/after 主标题与角度。保留 exact-source/research/Persona/zero-fallback/semantic-owner/0..N/pagination/card，不实现重型 claim verifier，不添加 Topic Card 字段。
- Required counterexamples：Storyboard、Mx-Shell/《丧尸清道夫》、Claude Cowork、Codex+Obsidian、Codex PPT、Agent、AI 视频、MIRA。不得硬编码样例或通过禁词表过门。
- Gate：开发 fresh current-task 自验通过后才 commit/push；Skill 变更必须进入 Git mirror。回传 PM 后由 PM 决定是否新开 AR-020E QA，不延续 AR-020D Round 4。
- Forbidden：staging/production Feishu 写入、发卡/点击、采集、06、global Skill sync、SCF/runtime deploy、legacy/deterministic fallback、snippet/model-memory fallback。
- First dev result：`8f452b2` 已 push，代码门与 18 条内容方向正向，但 PM 拦截 19/19 pass。Mx-Shell 最终标题 `一条地产宣传片...` 将创作者职业背景串成作品身份，并丢失 `《丧尸清道夫》`；hard-fact policy 仍 pass。19 条 `human_review` 还全部自报 true 且 note 完全相同，summary 的 0 failure 不能采信。
- Rework：固定开发线程继续同一任务，不占 QA。使用同一 immutable R8 dossiers 全量重算 19 行；保留已通过标题，修 Mx-Shell 来源身份/公共钩子；将 post-generation review 从生成 payload 分离并绑定 output hash，逐候选写具体判断；summary 由 review artifact 真实派生。新增通用身份串义反例，禁止样例硬编码和重型 entailment verifier。
- Rework result：`d075447` 已 push；19/19 post-generation review pass、unique notes=19，generation decision-set SHA=`21d82a...`，review SHA=`222467...`。Mx-Shell 已保留《丧尸清道夫》/行业关注/提示词公开，人物职业背景不再冒充作品身份；军方、Claude Cowork、MIRA 的硬事实与修辞边界已显式拆分。
- PM review：全量 19 行已独立复核通过；PM targeted regression 47 tests OK，diff check passed。证据：`/private/tmp/ar020e_pm_evidence_review_20260714_r2/PM_EVIDENCE_REVIEW.md`。
- QA dispatch contract：仅一次合并独立 QA。目标 `d075447`；不重跑 exact-source/research，不写 staging/Feishu、不发卡。L0 对抗 review ownership/hash/coverage/identity，L1 回归，L2 独立逐条审 12 推荐+7 观察。失败只回一份完整报告，由 PM 决定后续，不自动微复测。
- Dispatch confirmation：固定 QA v2 线程 `019f4714-3f76-7bb1-b71f-08a41d9f8860` 已成功接收任务；PM 不持续轮询。
- QA result：一次性合并 QA 通过。167 Python + 28 Node tests 全过；control mutation 全部按预期 fail closed；独立逐条审阅 12 推荐+7 观察为 19/19 可接受。证据：`/private/tmp/ar020e_hook_first_qa_20260714/`。
- PM acceptance：原始需求验收通过，状态转 `PM Accepted / Ready for RC Planning`。本队列任务关闭；不得自动派发布或 global Skill sync，后续须按 RC/发布控制单独规划和授权。

### AR-020E Release Candidate Preparation

- Created：2026-07-14
- Target lane：固定开发线程 `019f1de3-f3f2-71d2-ae63-a74cd38f8474`
- Priority：P1
- Status：Completed / Released / Scheduled-Day Smoke Pending
- Production baseline：`75801a86f4ef70cc0e882e801d44178b4701c536`
- Accepted target：`d075447a2026a4c84a2d32489a98869ef7cb6275`
- Scope：从 production baseline 新建隔离 RC worktree/branch，审计并移植 AR-020D/E 最小完整依赖，接通真实 outer automation/current-task state machine，输出 schema/runtime/global Skill sync/rollback 计划并完成开发自验。
- Excluded：不得整条合并 feature history；不得包含 AR-009、AR-026/027、learning flow、PM docs 等无关变更，除非逐项证明是运行时硬依赖。
- Forbidden：production main 更新、生产 Feishu 写入/发卡/点击、采集、06、global Skill sync、LaunchAgent/runtime/SCF deploy、生产 smoke。
- Resume trigger：RC commit/push、自验和 release manifest 完整回传后，PM 再派固定 QA 做 RC 全业务回归。
- Plan：`/private/tmp/ar020e_release_prep_20260714/AR020E_RELEASE_PREP_PLAN.md`。
- Dispatch confirmation：固定开发线程 `019f1de3-f3f2-71d2-ae63-a74cd38f8474` 已接收 RC 任务；目标 worktree `ai_account_radar_rc_ar020e_20260714`、branch `release/ar020e-rc-20260714`。PM 不持续轮询，等待完整 handoff。
- RC result：`release/ar020e-rc-20260714` @ `45e858a` 已 push；基线 `75801a8`，63 files manifest 一致，forbidden paths=0，开发自验 196 Python/20 Node/semantic owner/check-only/pre-merge 全过。证据：`/private/tmp/ar020e_release_candidate_20260714/`。
- Full regression target：固定 QA v2 线程 `019f4714-3f76-7bb1-b71f-08a41d9f8860`；仅 RC 分支，允许专用 staging 04、test app/receiver、个人目标和可识别测试记录/卡片/按钮点击，禁止一切生产写入/卡片/采集/06/global Skill sync/deploy。
- Pass state：`RC Full Regression Passed / Ready for Release Authorization Plan`。任何 L0/L1 或 staging flow 失败都停止，不得用 production smoke 或旧 dev evidence 替代。
- Dispatch confirmation：固定 QA v2 线程已成功接收 RC 全业务回归任务；PM 不持续轮询，等待完整 L0-L3 handoff。
- Regression result：L0/L1、19-row 内容、staging 04 四字段、7 records 和 5/2 分页通过；page1 `本页都不选` callback 返回 updated=0，五条仍待判断。QA 已停止 selection click，未用截图覆盖失败，生产边界为 0。报告：`/private/tmp/ar020e_rc_full_regression_20260714/AR020E_RC_FULL_REGRESSION_REPORT.md`。
- Product decision：无需用户重决策。页级按钮只把当前页 direct-generation `candidate_ids` 标为 `不做`；其他页面和 display-only observe/supplement 保持原状态。普通选择提交仍只更新勾选项。
- Fix target：固定开发线程在 RC branch 修复 force_no_selection 和 zero-update receipt/idempotency，提交新 RC HEAD 后，固定 QA 从头重跑完整 RC full regression；不做 micro-recheck，不进入生产授权。
- Fix dispatch confirmation：固定开发线程已成功接收任务；仅允许修改/push `release/ar020e-rc-20260714`，开发自验不写 staging/production，完成后回传新 RC HEAD 和 r2 release manifest。
- Fix result：RC HEAD=`47793c2` 已 push；two-file focused fix，targeted 100 / full RC Python 200 / Node 20 / semantic owner/pre-merge 全过。页面拒绝只影响 explicit candidate IDs，失败预检 writes=0/receipt=0，成功后 duplicate idempotent。
- R2 target：固定 QA v2 从头重跑完整 RC full regression，不做 callback-only recheck；全新 staging run，补完 previous L2 bounded current-task fixture、page reject/selection callback、sequential transport failure retry convergence、Skill temp sync/rollback 和生产边界。
- R2 dispatch confirmation：固定 QA v2 线程 `019f4714-3f76-7bb1-b71f-08a41d9f8860` 已成功接收目标 `47793c2`；验收结论只允许完整 R2 pass/fail，PM 不持续轮询，等待完整 handoff。
- R2 result：Flow A page rejection 通过；Flow B2 选择 1 条后，真实 test receiver 将同页 5 条全部写成 `不做`，page2 保持 pending，选中项未进入 `生成脚本包`。根因是 `src/receiver.js` 和 SCF entry 仍保留 normal-submit implicit rejection，旧 Node test 还将该行为断言为正确。Release blocked。
- Receiver rework target：固定开发线程在 RC branch 集中统一 Python card/local callback 与 Node src/SCF receiver 合同，更新旧测试、RC manifest 和部署/回滚计划；只部署隔离 test receiver，并用 fresh staging records 证明 selected-only write、unchecked pending、page no-selection、duplicate/receipt/transport retry。production SCF 禁止触碰。
- Receiver rework dispatch confirmation：固定开发线程 `019f1de3-f3f2-71d2-ae63-a74cd38f8474` 已成功接收；只有隔离 test receiver 的真实 selected-only/page-reject read-back、完整回归和新 RC manifest 全部通过后才可 commit/push。PM 不持续轮询。
- Receiver code result：四个 receiver/src/SCF/test 文件已完成但未提交；267 Python、28 Node 与全部静态门通过。新包 SHA256=`72d9cbde6f0574e29239f2bbb22786cc5e984010d2c4b43179210475e05c1a0d`，开发因安全浏览器控制面失败而停止，未部署测试或生产 SCF。
- Test SCF deployment target：固定云端执行线程仅对 `feishu-topic-card-receiver-ar018-test`（Guangzhou/default）执行备份、zip upload、部署、hash/code read-back、challenge 和 fresh staging synthetic selected-only/page-reject。禁止 production function；完成后回开发继续，不直接派 QA。
- Test SCF dispatch confirmation：固定云端执行线程 `019f2bc4-079e-7530-903e-484707590482` 已成功接收；本任务只做旧包/配置留证、新包 `72d9cb...` 部署、代码标记读回和 challenge/test-table 只读健康。fresh staging synthetic 写回由部署成功后恢复的开发线程执行。
- Test SCF result：两个可控浏览器上下文均重定向到腾讯云登录页，且本机无 `tccli`/凭证目录；线程在任何云状态变更前停止。新包 `72d9cb...` 与回滚包 `/private/tmp/ar020e_test_receiver_deploy_20260715/rollback_tencent-scf-feishu-card-receiver_47793c2.zip` (`811cbc...`) 已复核。Resume trigger：用户完成当前腾讯云登录。
- Resume confirmation：用户已完成云端执行任务内置浏览器登录；固定云端线程 `019f2bc4-079e-7530-903e-484707590482` 已收到恢复指令，从 test function identity/config/deploy-history 只读确认继续，不重做包、不再请求授权。
- Upload result：内置浏览器成功确认 exact function、Node 20.19、handler、timeout、deploy history 和 test table，但明确不支持 file-input 文件注入；上传 input 保持空，线程未点击部署。用户拒绝手工选文件，已再次恢复同一线程改用外部 Chrome 登录和自动上传。
- External Chrome result：external user Chrome 无腾讯云 authenticated session；精确目标 URL 保留在 login `s_url`，当前停在 WeChat QR challenge。候选包未上传、test SCF 无变更。Resume trigger：账号所有者扫码成功；扫码后用户无需选文件。
- Auth resume confirmation：用户已完成微信扫码并确认登录；固定云端线程 `019f2bc4-079e-7530-903e-484707590482` 已收到恢复指令，自动完成 exact zip selection/deploy/read-back，不再需要用户操作。
- Deployment result：test function `feishu-topic-card-receiver-ar018-test` 已部署新包 `72d9cb...`；deploy record=`2026-07-15 10:05:42 / console`，云端 markers、legacy-loop absence、challenge 和 explicit test-table readiness 均通过。production function 未访问/部署。
- Dev runtime resume confirmation：固定开发线程 `019f1de3-f3f2-71d2-ae63-a74cd38f8474` 已收到部署证据，正执行 fresh staging selected-only/page-reject/fail-before-write read-back；未通过前不 commit/push、不派 QA。
- Runtime result：selected-only run `ar020e_rc_r3_selected_only_20260715_1018` 返回 `TextFieldConvFail`；唯一选中 ID 的 payload 对 type=1 `选择原因标签` 写数组，五条 read-back 全为 `待判断`，无部分写。按门禁停止其余 probes。
- Rework dispatch：固定开发线程已收到集中返修。以历史 `bed3b42` 为通用参考，不整提交 cherry-pick；在当前 src/SCF selected-only diff 上实现 fields pagination、type=1 string/type=4 array、missing/unsupported fail-before-write，并重建新包。新包先回 PM 部署 test SCF，再恢复 dev fresh runtime，不直接派 QA。
- Local/package result：267 Python、32 Node、semantic owner 0/7 和全部静态门通过；新包 `34f929057f6ecf71ef5ee6454426423093215df6bb5e8b10cb2fbae8fc5e6061` / 12,186 bytes。四文件保持未提交，runtime 尚未通过。
- Redeploy confirmation：固定云端线程 `019f2bc4-079e-7530-903e-484707590482` 已收到新包重部署任务；复用 external Chrome auth，仅更新 test function。成功后恢复 dev，不直接派 QA。
- Schema-compatible deploy result：新包 `34f929057f6ecf71ef5ee6454426423093215df6bb5e8b10cb2fbae8fc5e6061` 已于 `2026-07-15 10:36:05` 仅部署到 `feishu-topic-card-receiver-ar018-test` / `ap-guangzhou/default`。云端读回确认 fields pagination、type=1/type=4、missing/unsupported fail、selected-only/page-reject markers，legacy unchecked loop 无命中；challenge 与显式测试表 `tblR730iHAaz9NQ7` readiness 通过。production function 未访问/部署。
- Fresh runtime resume：固定开发线程 `019f1de3-f3f2-71d2-ae63-a74cd38f8474` 已收到部署证据，使用全新 create-only staging run/records 验证 selected-only（含 0/2 tags）、page rejection、empty/outside/stale/missing fail-before-write、receipt/retry 和生产边界。全部通过后才允许 commit/push RC；失败继续留开发，不派 QA。PM 不持续轮询。
- Runtime closure：fresh 7/7 flows 全部通过。两个 selected-only run 均只更新唯一选中 ID，空标签保持空、双标签回读为 `证据够、判断够强`；page reject 只更新三个 page IDs 为 `不做`；display/page2/unchecked 保持 pending；empty/outside/stale/missing 均 0 business writes。RC commit/remote HEAD=`aa0ce3d869dab604cf74f42b88198dcaee2ed9dc`，production marker=0。
- R3 dispatch：固定 QA v2 线程 `019f4714-3f76-7bb1-b71f-08a41d9f8860` 已成功接收 Complete RC Full Regression R3。必须从 L0-L3 使用全新 staging run、真实卡片点击和当前 test receiver 完整重跑，不得用开发 runtime evidence 代替，不得只复测 callback。通过状态仅为 `Ready for PM Production Authorization Plan`，不执行生产发布。PM 不持续轮询。
- R3 result：目标 `aa0ce3d` 的完整 L0-L3 通过。manifest 67/67；267 Python、32 receiver/SCF Node、6 Douyin Node；fresh exact-source/research/current-task typed-failure fixture；真实测试卡 normal submit 只更新一个 ID，page rejection 只更新 page1 五个 direct IDs，分页/DOM/read-back/duplicate/idempotency 与生产 marker 均通过。R1/R2 Failed 历史保留。
- Production authorization plan：`/private/tmp/ar020e_production_authorization_plan_20260715/AR020E_PRODUCTION_AUTHORIZATION_PLAN.md`。授权范围拟为：production main FF 到 `aa0ce3d`；生产 04 只新增四个 type=1 字段；global Skill 备份/sync/hash read-back；production receiver 备份并部署 `34f929...`；更新 `ai-04` prompt 并按 08:00/09:15/10:00 顺序恢复当前均 paused 的 `ai/ai-04/ai-2`；全程 stop/rollback 门。未获用户明确授权前不执行。
- Production authorization：用户已明确回复“同意”，授权上述计划。固定生产线程 `019f2bc4-079e-7530-903e-484707590482` 已成功接收一次性执行单；必须先备份和只读 preflight，再做 schema/main/Skill/SCF/automation，任一 identity/hash/read-back 门失败立即停止并回滚。禁止旧 run smoke、手动生产卡、06、LaunchAgent/runtime 和任何无关变更。PM 不持续轮询。
- Release result：`Release Failed and Rolled Back / Automations Paused`。生产 schema 已成功 35->39，四字段均 type=1、record writes=0 并按计划保留；main 曾到 `aa0ce3d`，但 production checkout 运行 default `pre_merge_check.py` 时命中 dev-only branch 要求和 `Refusing to run Topic Card guard probe from the production worktree`。未豁免，已用四个 normal revert commits 回到 `8c245de`，其 tree 与 `75801a8` 完全一致；Skill/SCF/automations 未变。
- RC2 rework：固定开发线程已收到新任务，从 current production main `8c245de` 建 isolated `release/ar020e-rc2-20260715`，审计性重放 aa0ce3d 产品树，并新增显式 `--production-release-check --expected-head`。默认 dev 门保持；production mode 只允许 clean production main/local=remote=expected，并以 `run_topic_card_if_fresh.py --check-only --no-notify` 做无发送探针。完成自验和新 manifest 后才派独立 release-gate QA，不直接重试生产。
- RC2 result：branch/worktree clean，commit/remote=`8362091570e130fb0e93ebe44620dfff505d6136`。产品 reapply `ad3da97` 与 `aa0ce3d` tree 无差异；最终只差 `pre_merge_check.py`、focused tests、gate doc。273 Python、32 Node、semantic 0/7、default pre-merge、production-like fixture 与负向 CLI 通过；无外部写入。
- Release-gate QA dispatch：固定 QA v2 已收到 RC2 独立门禁验收。范围只覆盖 lineage、production mode、root/main/local/remote/expected、环境 override、Topic Card 单 JSON/check-only/no-notify/artifact unchanged、fresh temp clone fixture 和完整回归；不重复内容/卡片 R3，不执行真实 production mode 或外部写入。通过后仍需重新申请生产授权。
- Release-gate QA result：目标 `8362091` 独立通过。22/22 对抗探针、fresh production-like main clone、274 Python、32 receiver/SCF Node、semantic owner 0/7 与全部静态门通过；production 只读确认 main=`8c245de`、04 四字段存在、global Skill/SCF 未变、三个 automation paused。
- Reauthorization：队列暂停在 PM/用户决策，不派 production。上一份授权不可复用；新计划 `/private/tmp/ar020e_production_reauthorization_plan_20260715/AR020E_PRODUCTION_REAUTHORIZATION_PLAN.md` 需要用户明确回复 `同意按 RC2 生产再授权计划执行` 后，才向固定生产线程派发。
- Production dispatch：用户已明确同意新计划，固定生产线程 `019f2bc4-079e-7530-903e-484707590482` 已成功接收。执行目标为 production `8c245de` -> `8362091`，schema 只读复核、explicit production gate、global Skill hash sync、production SCF exact package deploy、三 automation 顺序恢复；任何门失败保持 paused 并按组件回滚。PM 不持续轮询。
- Production result：发布在 automation update 阶段停止并完整回滚。code gate、Skill、SCF 前置动作均通过；`ai-04` 官方 update 参数/执行失败，三条正式 automation 从未变化且保持 paused。main/Skill/SCF 已恢复，04 四字段保留，0 业务写。完整证据：`/private/tmp/ar020e_rc2_production_release_20260715_1140/RC2_RELEASE_FAILED_ROLLED_BACK.md`。
- Pending probe：等待用户授权 `/private/tmp/ar020e_automation_control_surface_plan_20260715/AR020E_AUTOMATION_CONTROL_SURFACE_VALIDATION_PLAN.md`。授权后只派固定生产线程创建一个临时 paused automation 做 official-tool create/update/delete；禁止触碰 `ai/ai-04/ai-2` 或任何生产业务面。
- User decision：用户确认三个旧任务都无法打开，明确要求删除重建。固定生产线程将按 `/private/tmp/ar020e_automation_rebuild_plan_20260715/AR020E_AUTOMATION_REBUILD_PLAN.md` 执行：先创建/打开/更新三条 paused replacements，全部通过后才删除 exact old IDs；最终三条新任务仍 paused。该任务不是 production release，不改 code/Skill/SCF/Feishu，也不运行 automation。
- Rebuild retry：第一次 create 因复用旧 TOML UUID project binding 失败，old tasks 未变。PM 当前 `list_projects` 只返回 path-form project ID `/Users/congcong/Desktop/AI/AI项目/AI账号工作流`，已确认旧 UUID stale。固定生产线程将在同一授权下用 live project list + path ID 重试；create/view/update 未全过前仍禁止删除 old IDs。
- Project registration retry：父目录 path ID 已证明能 create，但 automation CWD 被固定到父目录，live update 又拒绝 `cwds`；临时 `ai-rebuild` 已立即 pause 后删除，0 run，old tasks 未变。下一次只允许先把 production folder `/Users/congcong/Desktop/AI/AI项目/AI账号工作流/ai_account_radar` 注册成独立 Codex project，并要求 `list_projects` 精确回读；未出现 exact project ID 就停止。出现后逐条 create -> immediate pause -> no-run/read-back/view/update，三条全过才删除 old IDs。
- Final correction：对比旧任务配置后确认正确结构应为父项目 target + production 子目录 CWD，注册 production 子项目属于错误 workaround。三条 replacement 已恢复父项目 target，用户已移除误建子项目；未重新创建任务。
- Final release：production main=`7c469babb6e69431b5aca0a26c2d1ef058210929`，global Skill `SKILL.md=9d364bb0...`，production receiver approved package=`34f929...`，三条 automation ACTIVE。发布后即时 QA 通过，main 已回灌 feature=`fbef226cb87bdb8b4c2dc56048d3e2d4862f35a7`；本 dispatch 项关闭，仅保留下一 scheduled day smoke。
