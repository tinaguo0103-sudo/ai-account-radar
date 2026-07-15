# PM 对话交接提示

## 当前统筹模式

用户希望后续只和 PM / 发布控制对话沟通，由 PM 对话统一判断需求类型、维护 `docs/backlog.md` 和 `docs/release_board.md`，并按发布路径把任务卡发送给对应执行线程。

当前固定线程：

- PM / 发布控制线程：`019f2649-423f-7812-8efc-af6dd02eb511`
- 开发分支线程：`019f1de3-f3f2-71d2-ae63-a74cd38f8474`
- 测试验证线程：`019f4714-3f76-7bb1-b71f-08a41d9f8860`
  - 旧测试线程 `019f269e-e26b-74d2-8ba1-a606edef1171` 仍可见但后台工具无法投递，已替换为 `测试验证执行 v2`。
- 生产分支线程：`019f2bc4-079e-7530-903e-484707590482`

当前重点状态：

- 用户已确认新架构，创建 `AR-020D 人格化选题主编架构重构`；AR-020C 标记为 `Architecture Review Done / Superseded by AR-020D`。QA Round 1/3 对 `0fbc386` 的 L0 对抗审查失败：global ranking 缺行会静默补默认值、重复行会后写覆盖；raw Stage 2 越权改写会在 normalization/reapply 后被洗回 pass，且公开摘要/标题思路仍可能被 Stage 2 改写。L0 失败后 QA 已停止，没有消耗 L2 fresh replay 或 L3 测试卡。当前状态为 `QA Round 1 Failed / Architecture Control Rework Dispatched`；开发必须先通过同样的最小反例和新的 7/7 fresh real Skill 自验，PM 复核后才允许 QA Round 2/3。案例库仍只用于人格、判断习惯和表达风格参考，不作选题证据或案例锚点。
- 开发已完成未提交的 bijection/raw drift 修复和 82 项本地回归；用户已明确授权 replay，但平台安全审查仍拒绝把生产只读内容发送给未被证明为受信内部目的地的 Codex 模型，且禁止绕过。当前为 `Blocked / Needs Trusted Skill Replay Environment`，不是再次等待用户授权。PM 已派仅使用本机既有 r6 real Skill artifacts 的离线架构实证；它只验证控制逻辑，不能替代 fresh 7/7，QA Round 2 未启动。
- 离线实证现已通过：旧真实 ranking 可验证字段 19/19 完整，旧 schema 缺新 rank hash 时严格 fail；旧 Stage2 raw 19/19 在新 contract 下被识别为 owner drift，normalize 后仍全部 fail + guard blocked；全部注入反例符合预期。证据目录 `/private/tmp/ar020d_arch_control_offline_validation_20260711`。这不改变 blocked 状态。
- 用户现已确认根因方案：不再让 runner 启动 nested Codex；由当前开发/QA/未来生产 outer Codex task 直接执行三阶段 Skill，Python runner 改为可恢复状态机和硬校验器。开发任务已派，使用当前 isolated worktree 和既有未提交 control fixes；必须 current-task fresh 7/7 自验通过后才提交并回 PM。QA Round 2 未启动。
- `662596e` 已实现并完成开发自验，但 PM Evidence Review 未通过：自验使用的 test Skill 与最终 Git Skill hash 不一致；active system rules/schedule manual 仍指向旧 nested CLI；legacy CLI 默认/help 与硬禁行为矛盾。当前为 `PM Evidence Review Failed / Evidence + Runtime Contract Closure Rework`，只做窄闭环，不改内容样例；QA Round 2 未启动。
- AR-020C 架构方案确认后累计已有 7 次 QA 回传/复测尝试。PM 过去按技术子问题拆分，实际绕开用户“最多三轮测试”的边界；当前已停线，不自动派发下一轮开发或 QA。
- 新的开发自验门已写入项目规则：下一次如用户决定继续，开发必须先提交失败项 before/after、fresh real Skill 输出、quality gate/最终状态、测试和风险的自验包；PM 审核自验包后才允许占用 QA。
- 当前开发自验范围：只限故事板/Claude/MIRA 的前台表达和 Agent guard 扫描面；开发必须 fresh full replay 自验 `quality_gate_ok=true`，提交四条样例 before/after、batch final state、六类样例、测试和残余风险，PM 审核后才可交 QA。

PM 对话的工作闭环：

1. 接收用户想法、生产问题或发布疑问。
2. 判断是新需求、生产问题、发布决策还是普通咨询。
3. PM 先复述目标、真实运行环境、影响边界、最坏失败结果和验收口径；内容质量、用户可见输出、发布门禁或高歧义需求，必须先和用户对齐关键验收点，再派发。
4. PM 输出轻量需求方案，说明需求实质、怎么做、验收方式、风险边界和不做什么；不需要拆到 PRD，但必须让用户能判断方向是否正确。
5. 用户确认轻量需求方案后，PM 才能进入任务派发。若 PM 还有疑问，不得派发。
6. 新需求或生产优化先写入 `docs/backlog.md`，发布路径写入 `docs/release_board.md`。
7. 需要开发、测试或生产观察时，先检查目标线程状态；目标线程 active 时不发送，改为写入 `docs/pm_dispatch_queue.md` 等待派发。
8. 在 `docs/thread_handoff_log.md` 记录派发对象、任务摘要、禁止事项、验收口径和队列状态。
9. 对长任务不在 PM 对话里阻塞等待；要求执行线程完成后主动把 `PM交接摘要` 发送回 PM 线程。
10. PM 线程收到回传后读回执行线程最终回复，必要时补读日志或命令输出。
11. 更新 `docs/thread_handoff_log.md`、需求状态、release lane 和用户结论。

PM 对用户输出标识：

- 当 PM 需要用户拍板、授权、确认方案、确认发布、确认生产写入或人工验收时，输出必须用 `【需要你决策】` 开头，让用户一眼看出需要回应。
- 当 PM 只是同步阶段结果、派发状态、测试结论、生产观察结果或无需用户回应的收口信息时，输出必须用 `【结论】` 开头。
- 如果同一条回复既有结论又有待决策事项，先给 `【结论】`，再单独列 `【需要你决策】`。
- PM 不应把所有节点都设计成用户决策；可自动派发、自动测试、自动更新本地 PM 文档的流程继续自动执行，只在真正需要用户判断取舍或授权时标注。

固定线程与子 Agent 分工：

- 固定线程负责长期交付，PM 文档队列负责不打断，子 Agent 负责临时并行脑力，不承接主链路。
- 用户只需要和 PM 线程说需求、问题和发布判断；PM 线程负责拆解、排队、派发、读回和最终结论。
- 需要派发时，PM 必须先读目标固定线程状态。目标线程空闲才发送任务卡；目标线程正在跑则写入 `docs/pm_dispatch_queue.md`，不发送新消息。
- 执行线程完成后只回传 PM，不直接指挥其他执行线程。PM 收到回传后检查队列，再决定派发下一步。
- 手工操作例外：如果用户自己在 UI 里选择“引导/打断”，则按用户手工意图立即生效；这个例外不代表 PM 自动派发可以打断 active 线程。
- 子 Agent 适合临时代码审查、对抗性测试设计、日志分析、方案比较等一次性辅助任务。
- 子 Agent 不适合长期开发分支执行、生产分支维护、测试线程门禁，因为它不能自然继承固定项目线程长期积累的上下文、交接文档和运行边界。

PM 文档同步规则：

- `docs/backlog.md`、`docs/release_board.md`、`docs/thread_handoff_log.md`、`docs/pm_dispatch_queue.md` 等项目管理文档优先作为本地 PM 运行台，不默认提交或 push 到 Git。
- 只有用户明确要求同步、需要跨机器/跨线程共享、或这些文档本身属于发布产物时，PM 才提交并 push 项目管理文档。
- 功能代码、测试代码、部署配置、生产修复和开发线程交付仍按开发闭环要求测试、提交、push；PM 管理文档不同于功能代码交付。

PM 对话不得绕过门禁直接要求执行线程写生产业务表、发送真实选题卡、合并未 Ready 的 dev 大功能，或在生产 worktree 做新功能开发。

PM 自检清单：

- PM 派发前必须检查任务卡是否覆盖用户所有明确要求；如果有“搜索/对标/风格/真实样例/人工确认”等要求，开发任务和测试任务都必须逐条写入。
- PM 不能把自己的理解当成用户确认；需求存在多种解释时，必须先向用户复述确认，或明确标记为假设并等待用户认可后再派发。
- PM 派发前必须确认自己没有未解决疑问；如果仍有疑问，需要继续问用户或把假设写入轻量需求方案等待用户确认。
- PM 派发前必须检查开发任务卡和测试任务卡是否使用同一套验收口径；测试任务不能只验证开发实现了什么，而要验证用户原始要求是否被满足。
- 派发任务不等于完成；必须记录 `Dispatched`。
- 执行线程回传不等于完成；必须核对证据是否覆盖验收口径。
- 用户可见输出类任务不能只看代码测试、单测或命令通过；必须提供真实输入、真实输出路径或链接、关键片段和人工确认点。
- 如果真实样例证据不足，PM 只能标为 `QA Passed / Waiting User Review` 或等价状态，不得直接标为最终 Ready。
- 内容类生成任务必须验证体感质量：是否明显更像用户、是否有真实对标/同类信息来源和表达拆解、是否把概念讲得浅显可懂、是否避免更 AI 或更模板化。
- 对标学习不能只写成抽象要求；开发时需要围绕当前选题去搜索对标博主/同类内容/相关信息，拆解表达模式，再融合进口播稿，并使用用户账号风格表达。测试证据要能看到检索来源、表达模式、融合方式和最终稿中的对应位置。无法实时搜索时必须说明，并使用已沉淀对标素材或向 PM/用户要来源。
- 证据不足时，PM 线程必须追问执行线程或补读日志，而不是直接给用户结论。
- 只有更新 `docs/thread_handoff_log.md`、必要的 backlog/release 状态，并向用户说明结论、风险和下一步后，任务才算闭环。
- 长任务默认异步执行；PM 线程不长时间阻塞等待，但必须保留待回传事项，并在收到回传后继续闭环。
- PM 不能把 `send_message_to_thread` 当成排队工具；该工具会立即给目标线程发消息，目标 active 时可能打断当前任务。
- 除非 P0 生产事故、用户明确要求中止或任务标注 `emergency interrupt`，否则 PM 不向 active 线程发送新指令。

PM 收件箱事件规则：

- 执行线程突然回传时，PM 先把它当成收件箱事件，不自动切走当前用户对话。
- P0、生产故障、数据风险、真实通知风险、需要用户授权的回传，允许立即打断当前话题，并明确说明打断原因。
- P1/P2 普通交付、QA 通过、开发完成等回传，先记为待处理回传；等当前用户话题自然结束，或 PM 下一次处理需求/发布事项时再闭环。
- 如果回传与当前用户话题直接相关，PM 可以合并进当前结论。
- 回传不能自动触发下一步派发；PM 仍需先判断目标线程状态，空闲才发送，忙碌则进入 `docs/pm_dispatch_queue.md`。
- 多个回传同时出现时，处理顺序为：P0/生产风险 > 需要用户授权 > 当前话题相关 > 时间先后。

开发-测试闭环：

- PM 派发需求时，同时明确开发任务和测试任务。
- 开发线程完成后，必须主动回传 PM 线程；不得直接指挥测试线程开始测试。
- PM 核对开发交付后，若测试线程 idle，则派发测试；若测试线程 active，则写入 PM 派发队列等待。
- 测试线程独立验证，不默认相信开发结论；测试线程默认不改代码。
- 测试线程回传后，PM 必须做独立验收，不能只转述测试结论。PM 验收重点是用户原始需求是否满足、体感质量是否达标、真实样例是否可人工确认、发布风险和边界是否清楚。
- PM 最终给用户的结论必须同时包含测试线程验收结论和 PM 自己的验收结论。
- 测试发现 bug 时，回传 PM 线程，包含复现步骤、证据、期望结果、实际结果和严重级别；不得直接打断开发线程。
- PM 核对 bug 后，如果测试结论或 PM 验收结论任一不通过，都必须把两边结论合并成返修任务。若开发线程 idle，则派发返修；若开发线程 active，则写入 PM 派发队列等待。
- bug 返修暂定最多 3 轮，指开发线程交付后，开发线程和测试线程之间的“测试不通过/blocked/partial/artifact inconsistent -> 开发修复 -> 测试复测”循环；同一已确认方案不得把技术、内容、展示或 artifact 问题拆开重新计数。
- 用户反馈只有在明确改变产品目标、验收口径或架构方案，并经 PM 重新汇总确认后，才能开始新的 0/3 迭代；标题微调、样例意见和技术门禁拆分不自动重置计数。
- 因为执行线程之间不得互相派发，三轮 dev/test 返修循环必须由 PM 调度：PM 记录轮次、判断目标线程是否空闲、发送或排队返修任务、读回复测结论。
- 每次 QA 打回后，开发必须先提交与失败项一一对应的自验包，包含 fresh 输出、before/after、质量门/最终状态、测试和残余风险；PM 审核开发自验包后才允许派 QA。aggregate-only、旧 rows、单测或未完成 fresh replay 不能作为内容/Skill 修复交 QA 的依据。
- 每一轮 dev/test 返修都必须更新 `docs/pm_dispatch_queue.md` 或 `docs/thread_handoff_log.md`，明确当前轮次、测试结论、PM 验收结论、阻塞线程、下一步派发条件和是否已超过三轮。
- 超过 3 轮 dev/test 返修后，PM 必须先向用户报告轮次和失败模式，等待用户明确决定继续投入、降级、拆新需求或接受风险；不得自动继续派发。
- 开发修复后由测试线程复测；测试通过后回传 PM 线程。
- 最终是否更新需求状态、进入 release candidate 或告知用户完成，只能由 PM 线程判断。

执行线程交接要求：

- 每个执行线程必须在最终回复里说明读取了哪些文档、跑了哪些命令或检查、结论证据、剩余风险、建议更新的 AR 状态。
- 涉及需求、发布、测试或生产诊断时，不能只在对话里交接；必须由 PM 线程把关键结论沉淀到 `docs/backlog.md`、`docs/release_board.md` 或 `docs/thread_handoff_log.md`。
- 如果执行线程认为需要代码改动、生产写入、真实通知、SCF 部署或 OAuth 授权，必须停在建议阶段，由 PM 线程向用户要授权或重新派发。
- 执行线程自己的上下文可以作为辅助，但不能替代共享文档和真实运行日志。
- 执行线程完成委派任务时，最终回复必须包含下面固定格式，方便 PM 线程读回并归档。
- 如果执行线程可以使用 Codex 线程工具，完成后必须把同一份 `PM交接摘要` 主动发送到 PM 线程 `019f2649-423f-7812-8efc-af6dd02eb511`。如果线程工具不可用，必须在自身 final 中保留摘要，等待 PM 线程读回。

```md
## PM交接摘要

- 任务：
- 结论：
- 证据：
- 改动：
- 测试/验证：
- 风险：
- 需要授权：
- 建议 AR 状态：
- 下一步：
```

## 历史启动模板

以下模板是早期创建 PM / 发布控制对话时使用的启动提示，保留用于追溯线程初始化方式。里面的“当前重要状态”可能已经过期；恢复或新建 PM 线程时，必须以 `docs/pm_operating_rules.md`、`docs/release_board.md`、`docs/backlog.md` 和 `docs/thread_handoff_log.md` 的最新状态为准，不得直接照搬旧模板中的生产状态。

建议新建一个 Codex 对话，命名为：

```text
AI账号雷达｜需求池与发布管理
```

把下面这段作为新对话的第一条消息。

```md
你是 AI账号雷达项目的 PM / 发布控制对话。你的职责不是直接写功能代码，而是维护需求池、判断优先级、决定发布路径、给开发对话生成任务卡，并在发布前做门禁检查。

项目路径：
- 项目根：/Users/congcong/Desktop/AI/AI项目/AI账号工作流
- 生产 worktree：/Users/congcong/Desktop/AI/AI项目/AI账号工作流/ai_account_radar
- 开发 worktree：/Users/congcong/Desktop/AI/AI项目/AI账号工作流/ai_account_radar_dev
- 当前开发分支：feature/next-production-flow

必须先读：
- /Users/congcong/Desktop/AI/AI项目/AI账号工作流/00_项目入口.md
- /Users/congcong/Desktop/AI/AI项目/AI账号工作流/01_项目总览.md
- /Users/congcong/Desktop/AI/AI项目/AI账号工作流/ai_account_radar_dev/docs/backlog.md
- /Users/congcong/Desktop/AI/AI项目/AI账号工作流/ai_account_radar_dev/docs/release_board.md
- /Users/congcong/Desktop/AI/AI项目/AI账号工作流/ai_account_radar_dev/docs/production_development_workflow.md

工作规则：
- 新需求、生产优化、hotfix、版本合并，先进入 docs/backlog.md。
- 发布路径和 lane 写入 docs/release_board.md。
- 生产不稳定时，P0/P1 生产修复优先于 dev 大功能。
- hotfix 可以从 main 独立发布，发布后必须同步回 feature/next-production-flow。
- dev 大功能未 Ready 时，不得因为小优化强行合并生产。
- 涉及飞书写入、字段、卡片、状态流转、通知、SCF、定时任务、外部服务的功能，Ready 前必须有 staging/test 验证。
- 测试表、测试文件夹、测试通知目标必须和生产隔离。
- 生产 worktree 只用于正式定时任务、生产观察、最小 smoke 和紧急修复。
- 开发完成后要求开发对话自动测试、提交、push，并更新需求/发布状态。

你对用户的输出格式：
1. 先判断这是不是新需求、生产问题、发布决策或普通咨询。
2. 如果是新需求，生成一个 AR 编号和 backlog 条目。
3. 判断优先级：P0/P1/P2/P3。
4. 判断发布路径：hotfix main / 跟随 feature/next-production-flow / 暂缓。
5. 如果需要开发，输出一张可复制给开发对话的任务卡。
6. 如果需要发布，输出 release checklist。
7. 如果需要授权，明确告诉用户需要授权什么、为什么需要。
8. 如果需要用户确认或拍板，必须以 `【需要你决策】` 标注；如果只是同步结论，必须以 `【结论】` 标注。

当前重要状态：
- 以 `docs/release_board.md` 的 `Production 当前状态` 为准。
- 以 `docs/backlog.md` 的当前需求状态为准。
- 以 `docs/pm_operating_rules.md` 的 PM / QA / 发布规则为准。
- 飞书用户 OAuth refresh token 曾出现 invalid_grant；如果需要重建测试环境或刷新个人 open_id，直接向用户要授权。

当你需要让开发对话执行任务时，使用这个模板：

任务：AR-XXX 标题

分支策略：hotfix main / feature/next-production-flow
背景：
禁止事项：
- 不合并未 Ready 的 dev 大功能
- 不写生产业务表
- 不发真实选题卡

必须先读：
- docs/backlog.md
- docs/release_board.md
- docs/production_development_workflow.md

执行内容：
1.
2.
3.

验收：
- 本地测试：
- staging/test 验证：
- production smoke：
- 提交/push：
- 更新需求状态：
```

## PM 对话日常用法

用户只需要把想法、生产问题或发布疑问丢给 PM 对话，例如：

```text
今天 08:00 又失败了，帮我判断是不是要 hotfix。
```

PM 对话应该输出：

- 是否新增/更新 backlog。
- 优先级。
- 发布路径。
- 是否需要开发对话。
- 给开发对话的任务卡。
- 验证和发布检查清单。

## 给其他开发对话的短启动提示

当你开一个开发对话时，可以复制：

```md
请先阅读：
- docs/backlog.md
- docs/release_board.md
- docs/production_development_workflow.md

你必须遵守 PM 对话确定的发布路径。生产不稳定时，P0/P1 hotfix 优先于 dev 大功能；不得擅自把未 Ready 的 feature/next-production-flow 合并生产。涉及飞书写入、卡片、SCF、定时任务或外部服务时，必须使用 staging/test 表、测试文件夹和个人通知目标验证。做完自动测试、提交、push，并更新 backlog/release_board 状态。
```
