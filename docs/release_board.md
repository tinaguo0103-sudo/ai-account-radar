# AI账号雷达发布看板

这个文件按“能不能发布”和“从哪里发布”组织工作。需求细节放在 `docs/backlog.md`。

## Production 当前状态

- 生产目录：`/Users/congcong/Desktop/AI/AI项目/AI账号工作流/ai_account_radar`
- 生产分支：`main`
- 开发目录：`/Users/congcong/Desktop/AI/AI项目/AI账号工作流/ai_account_radar_dev`
- 开发分支：`feature/next-production-flow`
- 当前判断：2026-07-15 `AR-020E` 已完成生产部署、即时发布后静态/运行时 QA和 main->feature 回灌，当前状态为 `Released / Post-release Static QA Passed / Main Synced to Feature / Awaiting First Scheduled-Day Smoke`，尚未标记 `Release Closed / PM Accepted`。生产 `main` clean，local/remote HEAD=`7c469babb6e69431b5aca0a26c2d1ef058210929`；feature 已包含 released main 回灌和需求池清理；global Skill `SKILL.md` SHA256=`9d364bb0...`；production receiver 已部署 approved `34f929...` 包；08:00/09:15/10:00 三条 automation 已 ACTIVE，保持父项目归属和 production 子目录 CWD。即时 QA 274 Python + 32 Node、dynamic production gate、Skill/SCF/schema/automation/no-write 证据全过；回灌 310 Python + 32 Node 通过，真实连续链路等待下一 scheduled day。
- 当前生产事件：`AR-028 2026-07-08 制作方向卡发送失败与腾讯云报警关联` 已定位、hotfix、dev sync 并部署 production SCF；状态为 `Hotfix Deployed / Observe / Needs Logging Follow-up / No Card Re-send`。production `main` hotfix `75801a8` 与 dev `418b32b` 均完成；腾讯云 production `feishu-topic-card-receiver` 已部署包含 `DEFAULT_FEISHU_API_TIMEOUT_MS = 8000` 的新包，health/read-only 04 检查通过。今天两条制作方向卡未补发/requeue/触发队列，仍保持 `发送失败`。后续以一个 `Production Reliability Pack` 推进：`AR-029` 负责云端与请求链路可观测性（吸收 AR-016 剩余观测缺口），`AR-030` 负责安全重试与状态未知恢复；二者共用一份发布计划，但各自保持独立验收和回滚边界。
- 当前主门控：用户已确认 `AR-020D 人格化选题主编架构重构`。它以完整人格/风格参考真实加载、自由主编判断先行、受约束字段映射后置为核心；案例库只作人格和表达参考，不作选题证据或逐条案例锚点。AR-020C 的 7 次 QA 不延续为标题补丁循环；AR-020D 作为确认后的新架构方案从 `0/3` 开始，但开发必须先交结构自验包，PM 审核后才可派 QA。测试和发布前必须校验真实 global/private Skill provenance 与 Git mirror/hash；不可进入 PM Accepted、RC 或发布。`AR-026 飞书 01 全量对标账号采集覆盖` 已通过 QA，等待生产 01 隔离污染源的发布授权计划；`AR-027 飞书 01/03/04 标签和表格列业务清理` 已通过 schema audit QA，等待 PM/用户基于 cleanup matrix 做清理决策。`AR-025` 生产恢复口径与验收规范作为 PM 治理事项单独梳理；`AR-021` SCF CLI 部署通道作为发布工程 backlog。`AR-023` Chrome 启动根因与 `AR-024` 抖音补采覆盖恢复均已通过 QA/PM 验收。

## PM Coordination

PM 统筹、线程派发、队列规则、用户输出标识和 QA 门禁属于运行规则，不作为 AR 需求进入发布候选。详见 `docs/pm_operating_rules.md`、`docs/pm_dispatch_queue.md` 和 `docs/thread_handoff_log.md`。

当前固定线程：

- PM / 发布控制线程：`019f2649-423f-7812-8efc-af6dd02eb511`
- 开发分支线程：`019f1de3-f3f2-71d2-ae63-a74cd38f8474`
- 测试验证线程：`019f4714-3f76-7bb1-b71f-08a41d9f8860`
  - 旧测试线程 `019f269e-e26b-74d2-8ba1-a606edef1171` 因 Codex 后台工具无法投递，已由 v2 测试线程替代。
- 生产分支线程：`019f2bc4-079e-7530-903e-484707590482`

## QA Lane

用于独立验证开发线程交付结果。测试线程默认不改代码，只做测试计划、对抗性审查、回归验证、staging/test 验证和证据整理。用户可见输出类任务必须提供真实样例证据，只有代码测试通过但没有可人工确认样例时，不得标为最终 Ready。Bug 返修最多 3 轮，超过后回到 PM 做取舍。

| ID | 标题 | 优先级 | 状态 | 验证路径 | 当前轮次 |
|---|---|---:|---|---|---:|

## Hotfix Lane

适合优先于 dev 大功能发布的小修或生产稳定优化。

| ID | 标题 | 优先级 | 状态 | 发布路径 | 验证 |
|---|---|---:|---|---|---|
| AR-005 | 生产唤醒/保活机制上线 | P1 | Installed / Synced to Dev | hotfix main -> synced dev | 生产 `cf88643` 已安装 `-ims`，dev `03d6de3` 已同步；明天 07:55-10:50 观察 |
| AR-012 | 08:00 daily pipeline 飞书写入超时导致今日任务失败 | P1 | Recovered / Synced to Dev | hotfix main -> synced dev | 生产已恢复且未发卡；`b46070b` 已同步回 `feature/next-production-flow` |
| AR-014 | 飞书写入链路 RCA 与系统性防复发 | P1 | Hotfix Done / Synced to Dev | hotfix main -> synced dev | 生产 `00036d9` 已完成，dev `a0e62b3` 已同步；非幂等 batch_create 不盲目 retry |
| AR-016 | 2026-07-04 飞书 03 update 读超时深层根因定位 | P1 | RCA Complete / Residual Merged into AR-029 | historical RCA -> AR-029 observability | DarkWake / 网络恢复窗口结论保留；剩余跨端观测缺口不再单独排队 |
| AR-017 | Feishu 请求级 telemetry | P1 | Hotfix Done / Dev Equivalent | hotfix main -> observed tomorrow | 生产 `70e16c8` + `9e2faf3` 已 push；dev `08685fb` + `6eaf223` patch-equivalent；明天查 telemetry JSONL |
| AR-023 | 2026-07-06 抖音对标采集 Chrome CDP 启动失败 | P1 | Recovered / Hotfix Done / Synced to Dev / QA Passed / PM Accepted | hotfix main -> recover today -> synced dev -> QA read-only -> PM accepted | 生产 `6a4efed` 已恢复同日 run `run_20260706_085249`；dev `4f49826` 已同步；测试只读复核通过；Topic Card 未发送，06/Codex 未触发 |
| AR-024 | 2026-07-06 抖音补采只恢复 3 条的根因与完整恢复 | P1 | Recovered / QA Passed / PM Accepted | production diagnosis -> full same-day recovery -> QA read-only -> PM accepted | 根因是 AR-023 人为 3 账号限流；已按生产默认 12 账号补采到 `run_20260706_092517`，抖音 33 条；未发卡、未触发 06 |
| AR-025 | 生产恢复口径与验收规范 | P1 | Backlog / Needs Spec | PM spec -> user confirmation -> rules/checklist | 治理类事项；从 AR-023/024 复盘抽象恢复口径、partial recovery 标识、QA/PM 验收标准 |
| AR-026 | 飞书 01 全量对标账号采集覆盖 | P1 | Released / 01 Migration Passed / Automations Active / Awaiting Scheduled Smoke | scheduled smoke | 三任务已status-only恢复且配置逐字节一致；不手动补跑，明日正常验收 |
| AR-027 | 飞书 01/03/04 标签和表格列业务清理 | P1 | Schema Audit QA Passed / Waiting PM Cleanup Decision | schema audit -> dry-run -> QA -> release cleanup | Round 2 QA 通过；cleanup matrix 可用于第一轮字段/选项清理决策，view 仍需人工确认 |
| AR-031 | 固定抖音 Chrome Profile 与登录态硬门 | P1 | Hotfix Done / Canonical Logged In / Automations Active | released -> 07:45 preflight -> scheduled smoke | canonical 9333健康；三任务ACTIVE且未补跑 |
| AR-032 | Automation 激活补跑防护与执行 Lineage | P1 | Cancelled by PM / No Release | none | 用户接受单次误触，不继续activation/lease重构；保留事件记录 |

## Released / Resolved

| ID | 标题 | 优先级 | 状态 | 发布路径 | 验证 |
|---|---|---:|---|---|---|
| AR-008 | 06 watcher 飞书文档同步读取 `.env.local` 权限失败 | P1 | Released | 已在生产 main/runtime 修复 | 生产只读日志 + 06 记录读回 |
| AR-009 | 06 口播稿从泛化结构转向场景化表达 | P2 | Released / Minimal Smoke Passed | production main + global Skill | 生产全局 Skill 已同步，未覆盖 `-ar009-test`，runtime 关键文件 hash 一致；未跑真实 06/Codex 生成 |
| AR-010 | 06 测试/生成链路每条样例重复生成两次 | P2 | Released / Minimal Smoke Passed | production main + runtime | runner 已进入 production runtime；本轮未触发真实 06，后续下一次 06 观察 attempt history |
| AR-011 | 生产 06 飞书链接改为可点击超链接 | P2 | Released / Backfill Passed | production main + 生产 06 表 schema/backfill | 生产 06 已创建 URL mirror 字段，5 条旧记录 backfill/read-back OK，旧文本字段保留 |
| AR-013 | 未发卡候选补偿池 | P2 | Released / Minimal Smoke Passed | production main + production SCF | production SCF 已部署新包并 health OK；后续真实卡片观察覆盖日期和 snapshot 回写 |
| AR-015 | 非幂等飞书写入 checkpoint / read-back / idempotency 设计 | P1 | Released / Minimal Smoke Passed | production main + ledger | release smoke telemetry 无 status_unknown/error；ledger/unknown guard 已进入生产 |
| AR-019 | 2026-07-05 定时任务网络异常后补跑 | P1 | Recovered | production main | 今日 run `run_20260705_102318` 已恢复，正式卡片已由 fresh guard 发送 |
| AR-022 | `run_topic_card_if_fresh.py --no-notify` 语义修正 | P1 | Released / Post-release Retest Passed | hotfix main -> synced dev | production `3631bf2`、dev `c58dc57`、RC local `b63146b` 已同步；runtime blocker 已复核解除，`--check-only` 为后续 smoke 标准 |
| AR-020E | 传播钩子与大胆主编表达校准 | P1 | Released / Post-release Static QA Passed / Main Synced to Feature / Scheduled-Day Smoke Pending | RC R3 + RC2 gate -> production main/Skill/SCF/automations -> feature sync | production `7c469ba`、feature `fbef226`、global Skill `9d364bb0...`、receiver `34f929...` 已生效；即时 QA 274 Python + 32 Node、回灌 310 Python + 32 Node、dynamic gate、schema/Skill/SCF/automation/no-write 全过；明日验证同一 run_id 的 08:00/09:15/10:00 连续链路 |

## Historical / Superseded

以下事项保留为架构演进和 QA 反例证据，不再占用当前开发或发布队列。

| ID | 标题 | 最终状态 | 后继事项 |
|---|---|---|---|
| AR-020B | 选题主编 Skill 与字段契约重构 | Superseded / Historical | AR-020C -> AR-020D -> AR-020E |
| AR-020C | 选题主编思考链与标题表达机制评审 | Architecture Review Done / Superseded | AR-020D -> AR-020E |
| AR-020D | 人格化选题主编架构重构 | QA Round 3/3 Failed / Stop / Historical Evidence Retained | AR-020E 采用 Hook First 产品决策并独立 QA/发布 |
| AR-003 | 学习确认卡上线前部署腾讯云 SCF receiver | Absorbed into AR-006 / Historical Dependency | AR-006 学习闭环生产启用统一负责发布与 smoke |
| AR-018 | 飞书测试卡 receiver / test app 隔离 | Test Infrastructure Complete / Absorbed into AR-006 | 测试 App、测试 SCF、测试 Base 作为 AR-006 验收基础设施保留 |
| AR-016 | 飞书 03 update 读超时深层根因定位 | RCA Complete / Residual Scope Absorbed | AR-029 统一承接剩余可观测性 |

## Next Feature Release

当前未发布 / 后续候选。已在 2026-07-05 发布收口的事项已移入 `Released / Resolved`，不再作为下一轮发布候选。

| ID | 标题 | 优先级 | 状态 | 发布路径 | 验证 |
|---|---|---:|---|---|---|
| AR-006 | 学习闭环生产启用（含 AR-003/018 发布依赖） | P2 | Staging Tested / Needs Product Reconfirmation | feature/next-production-flow -> main + production receiver smoke | staging 04/06/08 + test receiver + production no-write/read-back |
| AR-026 | 飞书 01 全量对标账号采集覆盖 | P1 | Released / Awaiting Scheduled Smoke | production 5e733cd | Git/01/03/session通过；三任务ACTIVE，按明日正常链路验收 |
| AR-027 | 飞书 01/03/04 标签和表格列业务清理 | P1 | Schema Audit QA Passed / Scheduled After AR-026 | feature/next-production-flow | AR-026 上线与首次全量采集稳定后，再基于 production read-only cleanup matrix 单独授权字段/选项/view 清理 |
| AR-031 | 固定抖音 Chrome Profile 与登录态硬门 | P1 | Released / Awaiting Scheduled Smoke | production 178f047 | canonical identity/logged_in保持；三任务ACTIVE，做07:45与scheduled smoke |
| AR-032 | Automation 激活补跑防护与执行 Lineage | P1 | Cancelled by PM | no release | 不再作为任务恢复前置门 |
| AR-029 | 生产可观测性（含 AR-016 residual） | P1 | Reliability Pack / Needs Plan | AR-029 + AR-030 shared RC | 先完成日志/告警/请求链路证据，再作为 AR-030 恢复判断输入 |
| AR-030 | 制作方向卡安全重试与状态未知恢复 | P1 | Reliability Pack / Needs Architecture Review | AR-029 + AR-030 shared RC | 与 AR-029 共用发布计划；重试、unknown、幂等和 no-duplicate 独立验收 |
| AR-021 | 腾讯云 SCF receiver 标准 CLI 部署通道 | P2 | Backlog / Needs Plan | feature/next-production-flow；不纳入当前发布窗口 | 目标是减少每次控制台登录上传；需支持测试/生产分环境、包 hash 校验、部署记录、health/smoke 和失败回滚说明 |

## Authorization / Watch

| 事项 | 原因 | 下一步 |
|---|---|---|
| 飞书用户 OAuth refresh token 重新授权 | refresh token 已撤销，重建测试环境或刷新个人 open_id 时会卡住；这不是产品需求 | 需要时向用户要授权 |

## 2026-07-05 Release 归档

以下内容保留为本轮发布的历史证据和复盘依据，不再代表当前待执行发布门禁。新的发布候选必须重新建立当轮 RC、回归和发布清单。

### Release Candidate 检查清单

任何从 dev 发布到生产前，必须逐项确认：

- 本轮默认 release scope：`AR-009 / AR-010 / AR-011 / AR-013 / AR-015`。`AR-003 / AR-006 / AR-020` 不纳入本轮，除非用户另行确认。
- 本轮 RC：本地分支 `release/2026-07-05-ai-account-radar-rc`，路径 `/Users/congcong/Desktop/AI/AI项目/AI账号工作流/ai_account_radar_release_20260705_rc`，基于 production `origin/main=9e2faf3`；RC 分支已因发布越界事故被 push，后续不得由 PM 线程继续执行生产动作。
- RC 初始 commit：`8e33bf4 release: prepare 2026-07-05 rc`。
- RC Full Regression 通过 commit：`d43411f fix: load explicit env files in rc tools`。RC 包审计、本地门禁、staging/test Flow、生产只读反查均已通过。
- 发布越界后的 emergency safety patch：production `408a365 fix: block pre-merge card probe in production` 已阻断生产目录 `pre_merge_check.py` 再误触发真实发卡；dev 已同步 `9d928c1 chore: sync production pre-merge safety guard` 并 push；RC 已同步本地 `82188a5 chore: sync production pre-merge safety guard`，未 push。
- RC 当前执行基线：主体回归证据来自 `d43411f`，后置 safety patch `82188a5` 只有针对性安全测试和 `pre_merge_check.py` 门禁验证；误发卡已由生产线程删除；生产 SCF、Skill、AR-011 schema/backfill 和最小 smoke 已完成；AR-022 production hotfix `3631bf2` 已修正 `--check-only` 并清理污染 ledger，dev 已同步 `c58dc57`，RC 本地已同步 `b63146b`。发布后业务回归先发现 runtime 未同步 AR-022，生产线程已做最小 runtime sync，测试线程窄复核通过，PM 验收通过。本轮发布状态：`Release Closed / PM Accepted`。
- `docs/backlog.md` 中本次 release 涉及需求状态已更新。
- `docs/release_board.md` 中本次 release lane 明确。
- 本次 release scope 已冻结；未确认方案或未完成 QA 的需求不得顺手混入。
- dev worktree 干净，分支是 `feature/next-production-flow`。
- production worktree 干净，分支是 `main`。
- `python3 scripts/pre_merge_check.py` 通过。
- 涉及飞书写入/卡片/SCF/定时任务的功能已在 staging/test 表验证。
- 先形成 release candidate：把当前生产代码和本次待发布需求代码合成到同一个候选状态中；不得只在旧 dev 分支或单需求分支上跑全量回归。
- release candidate 上的发布前全量业务回归已通过，至少覆盖：
  - Python 单测：06 runner/retry、AR-011 clickable links、AR-013 compensation pool、AR-015 idempotency、Feishu retry/recovery。
  - Node 单测：Feishu card receiver / Tencent SCF entry。
  - staging/test Flow：Topic Card 测试发送、按钮回写、生产 04/06/output 反查为 0。
  - 06 Flow：测试或隔离路径验证生成包写表/文档链接/可点击 URL 字段；生产 smoke 只能在发布后最小执行。
  - Skill smoke：AR-009 使用发布候选 Skill 生成真实样例，确认不回退到模板化/同构输出。
  - 发布包差异审计：只包含本次 release scope 文件和必要配置说明，不混入 PM 管理文档脏改。
- 不写生产业务表、不发真实选题卡、不写生产文档文件夹。
- 合并后生产只通过 `git pull` 更新。
- 更新后只做最小 production smoke。
- smoke 失败时先诊断，再最小修复或回滚。

### 本轮逐需求发布准备最终确认

| ID | 是否可进入本轮发布 | 发布前必须完成 | 发布后最小 smoke / 观察 | 停止条件 |
|---|---|---|---|---|
| AR-009 | 是 | 合并/拉取 RC 后，同步生产全局私有 Skill：`austin-no-overtime-scripting`、`austin-voice-scriptwriter`；同步 06 watcher runtime；确认生产全局 Skill 不是测试 `-ar009-test` 副本。 | 只做最小 06 watcher/Skill 可用性检查；如用户随后点击生成 06，观察真实输出不回退到模板化/同构、内部边界不进用户可见区。 | 全局 Skill 同步失败、runtime 未同步、或 smoke 发现仍调用旧 Skill 时停止发布，不继续放行 06 生产链路。 |
| AR-010 | 是 | 随 RC 代码发布；确认 `generate_package_with_retry()` 和 attempt history 进入生产 runtime。 | 下一次 06 生成后检查 attempt history：普通 `qa_status=revise` 不应固定进入 attempt 2；硬失败仍应 retry。 | 生产 runtime 未更新或 attempt history 语义异常时停止并回滚/热修 runner。 |
| AR-011 | 是，但依赖发布授权 | 生产 06 表 dry-run：新增 `飞书文档链接`、`飞书文件夹链接` URL 字段；patch 主 grid view；backfill dry-run 审核 9 条旧记录中可解析 URL；授权后 write + read-back；旧文本字段不改不删。 | 读回所有 backfill 写入记录；确认 URL payload 与旧文本 URL 一致；新 06 记录同时保留旧文本字段和新增 URL 字段；必要时用 grid view 做一次可点击验证。 | 字段创建/视图 patch 有非预期副作用、backfill 计划包含旧字段修改、read-back mismatch 或 URL 字段不可见时停止；旧字段作为恢复依据保留。 |
| AR-013 | 是 | 部署生产腾讯云 SCF receiver 新包；确认生产 App callback 仍指向生产 receiver；确认 Topic Card sender/receiver 使用生产 04；不改测试 App/测试 Base。 | 只读/安全 smoke：receiver challenge 或等价健康检查；Topic Card guard 不应误发；下次真实卡片应显示覆盖日期，历史候选回写受 snapshot 保护，制作方向卡能进入队列。 | SCF 部署失败、health 失败、生产/测试 App 或表配置混淆、或回调疑似写错表时停止。 |
| AR-015 | 是 | 随 RC 代码发布；确认 `output/feishu_write_ledger/` 可写；发布前只读检查没有未处理 blocking unknown；明确 unknown 是安全刹车，不自动绕过。 | 下一次 04 `batch_create` / Topic Card send 后检查 ledger：intent/receipt 正常；若出现 `unknown_*`，发卡 guard 应阻断并给出可恢复原因。 | 发布前已有 blocking unknown 未处理、ledger 不可写、或 unknown guard 被绕过时停止发卡链路。 |

### 本轮发布总步骤

1. 用户授权后 push / merge RC 到 `main`，生产 worktree 只通过 `git pull` 更新，不在生产目录开发。
2. 运行生产代码级门禁和最小只读检查：`git status`、版本确认、`pre_merge_check.py` 或发布定义的等价检查。
3. 同步 06 watcher runtime：`python3 scripts/install_script_package_watcher_launch_agent.py --sync-runtime-only`，再 `--status` 确认 LaunchAgent 指向 runtime 且文件已刷新。
4. 同步生产全局私有 Skill，且只在发布授权后同步；同步前备份，确认目标不是 `-ar009-test`。
5. 部署生产 SCF receiver 新包，部署后做 receiver health / challenge / 配置只读确认。
6. 执行 AR-011 生产 06 schema setup dry-run、write、backfill dry-run、backfill write、read-back；每步失败都停止，不继续后续 smoke。
7. 做最小 production smoke：不触发额外采集、不手动发真实旧卡、不跑非必要真实 06；只验证版本、runtime、Skill、SCF、06 字段、ledger/telemetry 可用。
8. 发布完成后更新 `docs/backlog.md`、`docs/release_board.md`、`docs/thread_handoff_log.md` 的 Released / Smoke 证据；如果 smoke 失败，记录阻断和恢复动作，不把失败隐藏成已发布。

## Hotfix 检查清单

当生产修复优先于 dev 大功能时：

- 从 `main` 或生产当前提交开 hotfix，不合并未 Ready 的 dev 大功能。
- 只改解决生产问题所需的最小文件。
- 自动测试并提交。
- 发布到生产后做最小 smoke。
- hotfix 发布后同步回 `feature/next-production-flow`。
- 更新 `docs/backlog.md` 和本文件状态。

## 给开发对话的任务卡模板

```md
任务：AR-XXX 标题

分支策略：hotfix main / feature/next-production-flow
禁止事项：
- 不合并未 Ready 的 dev 大功能
- 不写生产业务表
- 不发真实选题卡

必须先读：
- docs/backlog.md
- docs/release_board.md
- docs/production_development_workflow.md

验收：
- 本地测试：
- staging/test 验证：
- production smoke：
- 提交/push：
- 更新需求状态：
```

## 每日 5 分钟维护

- 看生产定时任务是否成功。
- 如果失败，新增或更新 backlog。
- 判断是否 P0/P1。
- P0/P1 进入 Hotfix Lane。
- 非紧急需求进入 Next Feature Release 或 Parked。
- 明确今天是修生产稳定，还是推进 dev 大功能。

## 2026-07-16 AR-033 Release Board Entry

- 结论：`AR-033 Released / AR-033B Fresh RC QA Passed - Production Execution In Progress`。
- 生产事实：`run_20260716_080311` 为 account-level partial，31 planned/attempted、29 succeeded、2 failed、03 已写入、9 条 today candidates 已生成；不得重跑采集或改历史 03。
- 发布目标：新增 downstream usability machine field 与 persistent editorial Skill release manifest，恢复 09:15/10:00 对 partial-but-usable collection 的正确判定。
- 发布边界：feature 开发与生产基线 RC 分离；PM docs 只留 feature 追踪，不进入 product RC patch。生产 04 写入、个人 Topic Card 发送和任何恢复动作必须在修复发布与独立 QA 通过后由生产线程执行。
- 停止条件：global/profile/CDP failure、计划未完整 attempted、lineage 破坏、失败账号 artifact 泄漏、候选为空、manifest 缺失/漂移、04 finalization 未完成或 card guard 不新鲜时均 fail closed。
- AR-033 发布：production main 已发布 fresh RC `e6f04c547d70745c65b88d08aa2c4a9694b732fa`，release gate、manifest/source identity 和 exact run check-only 均通过。
- AR-033B 阻断：状态机官方 prepare 路径把 exact 9 行 same-day CSV 重算成 8 行，出现 candidate identity 缺失/替换；生产线程已在 04/card 前停止，业务写入和发送均为 0。
- AR-033B 发布门：exact-input 9/9 ordered bijection、URL/fingerprint/file hash 绑定、no-resampling/no-replacement 对抗测试、完整回归、production-base narrow RC、独立 QA。通过后才允许恢复今日 04/read-back/个人 Topic Card。
- Automation 门：三条任务保持 PAUSED。恢复前必须保留 projectless 和用户当前模型设置，并通过 official control 把 cwd 从 `~` 修复为 production repo；不得手改 TOML。最终仅 status-only 恢复 ACTIVE，并确认无即时补跑。
- RC：`release/ar033b-exact-input-20260716@f99db53ca428a6c2f650f9e51176205422d6c1c2`，production base=`e6f04c547d70745c65b88d08aa2c4a9694b732fa`，7-file patch SHA=`68b8db2d725f9b1e14680775949417dcf6b9c7ea8b97e65e8e0a9fad954826b2`，clean-base apply/byte parity 7/7。
- Dev evidence：真实 `run_20260716_080311` CSV SHA=`63450c79afa389d6ee7435681bfb55994f4424fb9302535b8e99587d898e64f5`；check-exact-input 与 official prepare 均 9/9，source outputs=0，writes/sends/collection=false。QA 必须独立重算并覆盖 prepare 后 CSV/state mutation、no-resampling/no-replacement 和完整 adjacent regressions。
- QA 阻断：exact prepare 本身通过，但后续四个公共阶段仍调用 legacy pool reconstruction；QA sentinel 命中 `exact_mode_resampling_or_pool_rebuild_called`。此外 prepare 后修改 CSV，`candidate_rows_from_state` 未复核当前文件 SHA，结果为 `NOT_BLOCKED`。绿色 332 Python/Node/semantic/pre-merge 不覆盖这两个独立反例。
- 返修门：集中 exact-mode state revalidation；每阶段重新读取 canonical CSV 并核对 bytes SHA/run/date/order/URL/fingerprint/manifest；exact pool 全程只来自 locked candidates，legacy builder 调用数必须为 0。原 QA 两个 probe 必须原样通过，再产出 new production-base RC 做完整 QA。
- Fresh RC：`release/ar033b-exact-input-rework-20260716@8af084621d01e639c54b5dc847a6439ce96fd8bd`，parent 为 production `e6f04c547d70745c65b88d08aa2c4a9694b732fa`；7-file patch SHA=`4c196641b0c25bab1888574ab11bfaf05bb19dfa6dd5a81ab260da7ab87f3b01`。开发包缺独立 `apply_byte_parity.json`，PM 已直接重算 base-apply 与 RC 七文件 SHA，结果 7/7 一致；QA 仍须独立生成 machine evidence。
- Rework evidence：CSV append/content/reorder/URL/title/publication/truncate/replace/symlink、candidate/manifest/state/local source drift 均阻断；validate-stage1/prepare-stage2/validate-stage2/finalize 的 legacy pool sentinel 全部 0 calls。真实 9 行 check/prepare/downstream reload read-only 通过，未启动 source fetch 或外部副作用。
- Fresh QA：exact targeted 10、full Python 336、receiver 32、Douyin 39+6、Unicode 4/4、semantic 7/7、compile/node/diff/pre-merge 全通过；独立 patch/apply/parity 7/7，forbidden scope=0，production telemetry 和业务状态未变化。结论 `Ready for PM Production Authorization`。
- PM 验收/授权：用户此前已确认合并方案，授权 Git-only release、exact same-day current-task recovery、一次 04 write/read-back、一次个人 Topic Card，以及三条 projectless automation 的 official cwd repair/status-only resume。PM 已派固定生产线程执行。
- 生产 stop order：release gate -> exact SHA/manifest -> source-open/research/Stage1/ranking/Stage2/finalize -> 04 read-back -> card check-only -> one personal send -> official cwd repair -> status-only ACTIVE。任一阶段失败停在当前组件，不绕过、不重采集、不改 03/06。

## 2026-07-16 AR-034 Release Board Entry

- 结论：`Approved for Consolidated Development / Production Recovery Blocked`。
- 触发事实：AR-033B 已发布，但 exact recovery 在 AIHOT owner contract 处阻断；进一步只读复核证明今天的 candidate universe 本身不完整，不能只修 Stage2 后继续原 9 行。
- 抖音数据：31 attempted、29 succeeded、2 failed、87 valid items；account-partial 被 `optional_failed` 吞掉后，87 条均未进入 combined input、content items、03 或 shortlist。任何成功来源 artifact 丢失都必须使 `downstream_usable=false`。
- 公众号数据：本日仅使用 1 个 active 公众号源，返回 5 篇 2026-06-11..16 的缓存；provider 日志持续 `暂无可用读书账号!`。HTTP/cache 可读不等于登录健康、刷新成功或今日新增。
- 固定认证门：公众号和抖音一样使用 canonical runtime identity。正常定时只访问固定 provider；重新登录只允许独立固定端口/profile/marker 和本机管理页，不允许线程自行寻找随机浏览器。生产 profile/data migration、扫码和刷新需单独授权及 read-back。
- 内容门：先证明 Douyin/WeChat/AIHOT 各自合格产物进入同一 comparison universe，再进行动态 0..N 排序；不设来源配额，但不得把缺源结果称为全源选题。
- Owner 门：Stage1 独占 evidence-bound AIHOT significance rationale，Stage2 只允许 locked mapping；禁止 deterministic fallback、跨字段代填或手改 artifacts。
- 原 run：`run_20260716_080311` 及错误 9 行作为事故证据保留，04=0、card unsent；不得在该 candidate set 上继续。新恢复须是版本化 recovery run，并可复用 87 条已保存的抖音成功 artifacts，排除陈旧公众号缓存。
- Automation：三条任务保持 PAUSED。AR-034 完成、生产恢复通过后，才通过 official control 保留 projectless/model/prompt/schedule 并只修 production cwd，然后 status-only resume；不得手改 TOML。
- 发布路径：fixed dev -> production-base narrow RC -> independent full QA -> PM production authorization -> canonical WeChat migration/login/read-back -> fresh refresh + recovery -> 03/04/card closure -> automation resume。
- 当前禁止：生产采集/Feishu 写入/卡片/06、automation/Chrome/profile/provider data/Skill/SCF/main 变更。
- Dev RC：`release/ar034-rc-20260716@11fab145b0efccce7ff75a458f700606a9f4e183`，parent=`8af084621d01e639c54b5dc847a6439ce96fd8bd`，tree=`29f7cf0c7fddeb32b48f56286939bc88a4c87f15`，patch SHA=`03072f758cb28bee3a6c3e680b5ed581e2dff8aedebf13b66ed98a26ed5534de`。开发自验不等于 QA。
- PM evidence gate：Failed。无 ingestion lineage 时 `downstream_usable=true` 的独立反例通过；unchanged WeChat revision 被接受为 `updated_no_new_items` 的独立反例通过。两项均直接违反本 AR 用户结果，故 QA 未启动。
- Rework gate：把 Douyin source artifact/run/hash/bijection/03 read-back 强制纳入 downstream usable；把 WeChat 状态绑定 current refresh attempt 或独立前进的 revision+timestamp；manifest 使用 exact 40-char RC head。产出 fresh production-base RC 后才派完整 QA。
- Fresh RC：`release/ar034-rc2-20260716@41cb9904b3cf4b36c4b94d85c91e54abb733779c`，parent=`8af084621d01e639c54b5dc847a6439ce96fd8bd`；patch SHA=`8f308719e68d8e2eb9822da54da81b76b73a0cdb5850fd4e6e759a464b98b5f5`；manifest SHA=`76c226611ca00c121522200aa5a47a913d541c06584f617077b58e344f868b13`；tree/apply tree=`eabbf195255e234e2b35109d3b0d5b52be62a114`。
- PM evidence recheck：原 missing-ingestion probe 现 `downstream_usable=false` 且七项 closure reason 可见；unchanged revision/timestamp 现为 `stale_cache`；manifest exact-head command 返回 RC/actual 40-char equality。结论 `Ready for Independent QA`，不是 production ready。
- QA 特别门：独立证明 sampler 前后 lineage/03 read-back、watermark post-closure、scheduled no-browser、AIHOT owner 和完整回归；同时明确当前 provider adapter 永久 typed blocked，迁移/登录是否足以解除阻断，或必须另发 receipt-capable adapter RC。
- QA 结论：A=`RC architecture/control Passed`；B=`Production recoverability Blocked - receipt-capable local adapter/code RC required`。25-file scope/patch/manifest、Douyin closure、WeChat freshness mutation、AIHOT owner 和完整回归通过；无生产副作用。
- PM 决策：不发布 RC2、不申请 recovery authorization。安全的永久 `provider_failed` 不是用户可用修复；继续同一 AR 的 receipt-adapter consolidated development。
- Adapter release gate：固定 provider、exclusive lease、caller-bound attempt、before/after DB state、bounded polling、per-feed completion、durable atomic receipt、crash/timeout/concurrency recovery、watermark post-downstream closure。若现有 provider 无可证明的 completion signal，必须更换或扩展 adapter，不得仅凭更新时间/24h age 猜测成功。
- Receipt RC3：`release/ar034-rc3-20260716@d23ee694a15499f927922eed68a6aadc6578c161`，parent=`8af084621d01e639c54b5dc847a6439ce96fd8bd`，patch SHA=`0f16151ea7cdd898e40c964dbc608bc83e61127d2e31a283d5432e3a77ea4455`，tree=`1d2b6df49a5e9dde2a6c319964f0d48c650dec33`。PM 已复核 lineage/manifest/provider source audit，但未派 QA。
- PM evidence gate：Failed。独立构造一个 canonical receipts 目录之外的 receipt，并故意让 `per_feed.feed_id` 与 live feed 不一致；当前 verifier 仍接受，health classifier 返回 `updated_no_new_items`。现有 hash/live-DB 检查不能证明 receipt 来自本次 adapter/lease，也没有重算 per-feed 与 before/after 的关系。
- 下一门：开发一次性加入 canonical path/filename/realpath 与 exact relational receipt validation，并覆盖 external/manual/symlink、feed set、before/after、completion、aggregate/revision/time mutations。fresh production-base RC 通过 PM evidence gate 后才派 full independent QA；当前不得发布、迁移、reauth、refresh 或 recovery。
- RC4：`release/ar034-rc4-20260716@9868002c97e419a74fd0cb86c253037f40ff42f3`，patch SHA=`46b340b3a49333f981ddff990c17595d6cc49cd22b051992dbacd50d611ef11b`。path/link/exact schema/relational validation 已补齐，但 PM 构造合法 canonical lease+attempt+receipt 三件套仍被 verifier 接受并判为 `updated_no_new_items`。
- RC4 结论：`PM Evidence Review Failed / QA Not Started`。O_EXCL 只能保证文件首次创建，不能证明创建者是 fixed adapter；JSON 间互相 hash 也不是 caller-bound attestation。
- 最终返修方向：优先让 protected provider transaction 持久化 caller attempt nonce并由 verifier 独立读回；若 provider 扩展不可行，使用独立 runtime signing key 完成可审计签名，同时明确同 Unix user arbitrary-code threat boundary。手工 canonical trio 必须 fail，real before snapshot必须与 attestation 绑定。当前继续禁止 production release/recovery。
- RC5：`release/ar034-rc5-20260716@5c0c203c781aeb50d9ce2c6b04ad4b313a059a49`，patch SHA=`94d00404c995c1a747822d3e733757be72043bcf5c7bd20252f62b9d619309fc`。Dedicated runtime HMAC architecture 被 PM 接受；无 key canonical trio、wrong key/signature均在 classifier 前阻断，签名后仍保留 exact relational/live DB validation。
- RC5 PM gate：暂不派 QA。成功 refresh/health 实际读取 HMAC/auth secret却输出 `secrets_read=false`；key loader未核对 current UID，并以分离的 `lstat + read_bytes` 读取。先做一次窄返修：准确区分 secret read/exposure，fd级 `O_NOFOLLOW + fstat + st_uid/mode/nlink`，secrets parent owner/mode read-back。HMAC方案本身不重做。
- RC6：`release/ar034-rc6-20260716@0353e723bc3dc719299fd4962d302a291e6ab714`，patch SHA=`97904e1dca8b0ef3917b2feeb6f5210974d7615f695510d9597980016c5dbe1b`，tree=`d67398ecafb02358411a93084ecfe490003ba3d7`。fd级 current-owner读取与真实 secret evidence 已闭合。
- PM evidence gate：Passed。独立 focused 21/21、patch hash/diff通过；原手工 canonical trio在无 key 时 typed fail且未分类。状态进入 `Ready for Independent QA`，不是 production authorization。QA必须做完整26-file/全链路验证，禁止production key provisioning、真实provider refresh或恢复。
- RC6 scope correction：派发中的25-file为PM口径错误；实际RC diff、patch和manifest一致为26 files。第26项 `scripts/test_ar034_wewe_receipt_adapter.py` 是302行专项测试，需求相关且非禁入范围。PM明确将production candidate scope更正并授权为26 files，无需重出RC。
- RC6 QA：A=`Architecture/Control Passed`；B=`Production Release/Recoverability Pending Explicit Authorization`。26/26 scope、patch/apply/tree、HMAC签名链、fd owner/mode/no-follow、secret evidence、protected provider mutation、Douyin full-source closure、WeChat watermark/fixed runtime、AIHOT owner和全回归通过。当前状态为 `Ready for PM Production Authorization Plan`，不是Released或Production Ready。
- 授权动作必须分阶段：PAUSED保持 -> Git release/gate -> canonical key 0700/0600/UID provisioning -> provider auth/config read-back -> 单次bounded signed refresh及live DB复核 -> 03 exact write/read-back + watermark -> versioned editorial/04/card closure -> official cwd repair -> status-only resume。任一阶段失败保持PAUSED，不把局部成功记为恢复完成。
- 用户授权：已确认完整26-file生产执行计划。固定生产线程负责执行，PM线程不直接改生产；允许本次Git发布、密钥配置、单次真实refresh、03/04写入、一次个人卡片和official automation cwd/status变更。禁止Douyin重采集、旧9行恢复、06/callback/schema/global Skill/SCF/Chrome/profile改动及任何raw TOML workaround。
- Production preflight：`Preflight Blocked / No Release / Automations Paused`。RC6 recovery gate在任何状态变化前发现旧Douyin probe/manual缺少新合同要求的run/artifact identity；production仍为clean `8af0846`，key未创建、provider未refresh、Feishu/card/automation均未变。
- 下一候选：AR-034B legacy lineage attestation。只允许显式old-artifact模式，通过daily run唯一step、canonical command、时间窗、regular/single-link/UID、resolver path、probe/manual hash/size/count、31/29/2与87 fingerprint/account闭环重建source identity；不得修改旧artifact、不得自动fallback、不得降低新产物RC6合同。fresh RC7必须包含完整RC6 scope加该窄修复并重跑full QA，旧生产授权不沿用。
- 用户已授权AR-034B开发；固定开发线程负责feature实现与production-base fresh RC7，完成开发自验后回传PM，不得直接派QA或执行生产。
- RC7：`release/ar034-rc7-20260716@fe09651b2b1cf6457f398b0253ddaa435abcd610`，parent=`8af084621d01e639c54b5dc847a6439ce96fd8bd`，实际28 paths，patch SHA=`acccdfb479335077904a67ec10d10b9f2632b791ac4a6a0aa007ceabe0c94afb`，manifest SHA=`5cf2151ca13918a937b6eb06a9edf630d1b7113a667e200afcebf29d7567b4ec`，tree/apply tree=`e2b215428502d4b8691c4f7752da04cfbb03f9a3`。
- RC7 PM evidence gate：Failed / QA Not Started。公共 legacy CLI 接受 `/private/tmp` 自造同构 root 并返回 `legacy_attestation_verified=true`，证明 validator 只锁定相对目录形状，未锁 configured production root；畸形 step `returncode` 还会抛未捕获 `ValueError`、exit 1/空 stdout，而非 typed JSON fail。
- RC8 gate：只返修 AR-034B public boundary。configured production root 必须由运行时配置/代码位置给出，不能由输入 daily path 推导；初检与 locked prewrite 都要绑定同一 root。所有 legacy evidence schema/type mutation 必须单一 JSON typed nonzero，不能 traceback。RC6 strict native、HMAC receipt、WeChat、AIHOT与其他已通过范围不得放宽或重做。完成 fresh production-base RC8 后再派完整 independent QA，旧 RC7 与旧生产授权均不沿用。
- RC8：`release/ar034-rc8-20260716@af0e4e520cefcacb0efa770992a34a2778b9d36f`，parent=`8af084621d01e639c54b5dc847a6439ce96fd8bd`，28 paths，patch SHA=`abea1284baf80e0c687373dcc65ac149ee67388719f9e2ba47cdb822c7b556dd`，manifest SHA=`cb4bac8b6bc38e189c7f71217bf4ac04bb90eae400746123e4920d6c567daf0a`，tree/apply tree=`fc278ad966acc6e1f24e28082f98570986caef33`。
- RC8 PM gate：Failed / QA Not Started。PM 已独立确认真实 production originals initial+locked 31/31、29/2、87 rows 通过，malformed terminal在有效测试 root 返回单 JSON typed exit 4；但 public CLI 先 `.resolve()` configured root，导致相邻 `ai_account_radar` symlink 指向伪造 tree 时仍被接受并返回 `legacy_attestation_verified=true`。
- RC9 gate：仅修 configured-root raw path identity。禁止在 symlink检查前resolve；优先用 directory fd `O_DIRECTORY|O_NOFOLLOW + fstat`，并验证current UID/canonical path。公共CLI必须独立覆盖 configured symlink、alias、swap，initial与locked两次一致；其余RC8/RC6合同保持。fresh RC9完成全回归后再派QA。
- PM 校准：RC9 gate 取消。此前 symlink probe 属于同一 Unix 用户主动替换相邻 production 目录的理论攻击，不是本次受信 production-thread 一次性恢复合同。RC8 已关闭任意 evidence root 与 malformed schema 两个真实阻断，并在当前非 symlink 的固定 production root 上通过 initial/locked 只读重验，现恢复为 `Ready for Independent QA`。
- RC9 `87e16909271bb10dc4ecd276f8cf9422ae0048e8` 在停止消息到达前已提交/push，仅保留历史，不作为候选、不发布。QA target 固定回到 RC8 `af0e4e520cefcacb0efa770992a34a2778b9d36f`。
- QA 边界：完整验证28-file scope、patch/apply/tree、真实旧原件31/31、29/2、87 items与locked prewrite、strict native、signed WeChat receipt、full-source closure、AIHOT owner及全回归；明确排除same-user malicious root replacement/symlink threat expansion。QA仍不得执行生产refresh、Feishu写入、卡片、automation或production Git。
- RC8 Full QA：Passed / Ready for PM Production Authorization。28/28 scope与byte parity、真实87条initial+locked prewrite、8/8独立legacy mutation、signed WeChat/full-source/AIHOT/adjacent regression、Python 387、receiver 32、Douyin Node 8、semantic 7及supported pre-merge全部通过；生产零副作用。
- 新生产授权必须点名RC8 `af0e4e520cefcacb0efa770992a34a2778b9d36f`、28-file scope与legacy path。执行顺序为PAUSED/备份 -> Git release/gate -> key/provider/signed refresh -> 旧原件再次initial+locked -> full-source/03 exact closure -> current-task/04/read-back -> card check-only/一次个人发送 -> official cwd repair -> status-only resume；任一不一致立即停止并按组件回滚。
- 用户已确认上述RC8生产授权。授权计划=`/private/tmp/ar034b_rc8_pm_acceptance_20260716/PM_ACCEPTANCE_AND_AUTHORIZATION_PLAN.md`，SHA256=`f14efa246ab5488a6e032e4aad0db7d483a653c2986043e537344e8bc5106c17`。固定生产线程负责执行；PM不直接操作生产，不使用已取消RC9，不轮询执行线程。
- RC8 production preflight：Blocked before writes。RC8/legacy通过；唯一provider容器真实mount仍是repo-local `.local_services/wewe-rss/data`，而signed adapter canonical data不存在；容器`AUTH_CODE` masked-present但host adapter auth env absent。production仍clean `8af0846`，key/refresh/03/04/card/automation changes=0。
- 待用户授权provider canonicalization：保持PAUSED，备份后normal-stop exact container；离线复制并校验DB到canonical runtime data；用同image+canonical bind重建；将现有private auth安全接入host-supported env且不输出；read-back container mount/DB/account/feed/auth presence；失败则恢复旧mount/container并保持PAUSED。成功后才重新发起RC8 release/recovery，不能把migration health当refresh receipt。
- Migration plan：`/private/tmp/ar034b_provider_runtime_migration_20260716/PROVIDER_MIGRATION_AUTHORIZATION_PLAN.md`，SHA256=`9bac54f0314635932bf92d3515b5dd4ba63217dd50343912ffab4d996546c0ab`。
- 用户已确认 provider canonicalization + host auth wiring，并授权迁移 read-back 通过后自动继续 RC8 既有生产发布/恢复，无需第二次确认。固定生产线程一次性执行；PM不直接改生产、不轮询。
- Migration gate：保持三任务 PAUSED，fresh backup 后 normal-stop exact `ai-radar-wewe-rss`，确认 4000 与 DB open files 释放；离线复制并校验 current DB 到 canonical runtime data；同容器名、同镜像、同端口、同非秘密配置重建，仅将 bind mount 改为 canonical；现有 private `AUTH_CODE` 仅安全接入 host-supported env，禁止输出 bytes/hash。
- Migration read-back：必须验证 container name/image/port/mount、canonical DB path/inode/SQLite integrity、account/feed 数量、provider health 与 masked auth presence。迁移阶段不调用 refresh。若 `login_required`，保持 PAUSED 并停在线程可控的固定 9334 canonical 登录门；不得随机浏览器或回退旧缓存。
- Automatic continuation：migration green 后从 RC8 Phase 0 fresh 重启并执行已授权 Git/gate -> HMAC key -> one bounded signed refresh -> legacy 87-item initial+locked -> versioned full-source/03/watermark -> editorial/04 -> card check-only/one personal send -> official cwd repair/status-only resume。任一失败停止后续并做组件级回滚。
- Combined authorization supplement：`/private/tmp/ar034b_provider_runtime_migration_20260716/MIGRATION_AND_RC8_CONTINUATION_AUTHORIZATION.md`，SHA256=`248f5725612087c13c4e28a71aec9c2691620afe9b1fd2c29df99a236aa7a772`。
- Provider canonicalization：Passed。新容器唯一mount为canonical data；DB SHA/integrity/counts与迁移前一致；private auth已安全接入host runtime。production main仍clean `8af0846`，RC8未发布，三任务PAUSED，refresh/Feishu/card均为0。
- Stop condition：RC8 provider check-only为 `login_required`，按授权未自动继续。下一门是单独授权固定9334 canonical admin Chrome登录；只打开local `/dash`，不随机寻找浏览器、不导出secret。read-back必须为 `ok=true/status=refresh_required`、account/feed一致、`refresh_requested=false`，随后才从RC8 Phase 0自动继续。
- 9334 login authorization plan：`/private/tmp/ar034b_provider_runtime_migration_20260716/WEWE_9334_LOGIN_AUTHORIZATION_PLAN.md`，SHA256=`64f82417d8ff2cfb15c59ba343e870740ec808c5addb53ad50955cfa08398d13`。用户确认前不启动9334或改生产状态。
- 用户授权：已确认 fixed 9334 canonical login plan。生产线程仅可运行 exact RC8 worktree launcher，固定9334/profile/local `/dash`；登录check-only必须为 `ok=true/status=refresh_required`、无refresh/secret read，随后自动继续已批准RC8发布恢复。QR/SMS/MFA若不可自动完成，只由用户在该固定窗口交互。
- Fixed login result：`Login Interaction Required`。9334 PID 72440 与 canonical profile identity全绿，当前唯一页为local `/dash/login`；需要账号所有者在该固定窗口完成登录。production Git仍clean `8af0846`，RC8/key/refresh/Feishu/card均未发生，三任务PAUSED。用户完成后只需回报“已登录”，生产线程将check-only读回并在green后自动续跑RC8。
- Secret injection blocker：自动使用existing host auth被本机安全审查拒绝，未读取或materialize secret。当前需用户明确批准受控通道：只在本机内存读取，使用CDP直接填入fixed 9334 local页面，禁止disk/log/clipboard/回显。未获该授权前RC8不发布、三任务保持PAUSED。
- Controlled injection authorization：用户已明确同意。production线程只能以本机内存 + CDP操作fixed 9334 local页面；禁止secret落盘、日志、clipboard、截图或回显。login check-only green后自动继续既有RC8 Phase 0；任一身份/账号/feed/DB mismatch保持PAUSED并停止。
- Controlled injection result：admin auth accepted，fixed 9334进入local `/dash`，secret boundary全满足；但provider account仍status=0，check-only=`login_required`。当前转为只读定位正式account reauth/reactivation路径；不点击refresh试错，不发布RC8，不恢复automation。
- Reauth supported path：只读RCA确认旧token因 `WeReadError401` 被provider自动失效；正式续期不是改status，而是fixed 9334 `/dash/accounts` 点击一次“添加读书账号”，由owner扫码，UI polling后按account id upsert。计划SHA=`12c7641c...`；当前等待用户精确授权，RC8未发布、三任务PAUSED。
- Reauth authorization：用户已明确同意。production线程仅执行fixed 9334一次add-account并置前QR，等待owner扫码；不直接API/DB、不改status、不refresh、不切换browser/profile。成功read-back green后自动继续既有RC8授权，失败保持PAUSED。
- RC8 release result：provider reauth passed，account status1=1；production已发布clean `af0e4e5`，28/28 scope、dynamic gate、canonical key provision均通过。因当前日期变为2026-07-17，原2026-07-16 recovery在signed refresh前按date boundary停止，未refresh/写03/04/发卡。三任务PAUSED；建议改走完整7/17同日流程，不混用旧run。
- 2026-07-17 run authorization：用户同意一次fresh全源生产流程，授权signed WeChat refresh、全量Douyin+same-day AIHOT、03/04 write/read-back、一次personal Topic Card、official production cwd repair和status-only resume。禁止复用7/16旧run、卡片点击、callback或06；任一失败保持PAUSED。
- 7/17 execution stop：唯一signed WeChat refresh成功，19 new items、receipt/live DB closure green；canonical first-run watermark absent导致released gate返回stale_cache。未第二次refresh，Douyin/AIHOT/03/04/card均未开始。bounded baseline repair plan SHA=`97e2fc503a...`，等待用户授权；只允许精确pre-refresh payload + existing receipt revalidation。
- Watermark repair authorization：用户已同意计划SHA=`97e2fc503a...`。仅允许atomic install exact baseline SHA=`83fd50f1...`，用existing receipt验证updated_with_new_items=19并继续同run；禁止第二次refresh。后续仍受full-source/03/04/card/cwd gates约束，失败保持PAUSED。
- Watermark/read continuation：baseline repair与receipt验证已通过；首次WeChat fulltext read probe仅因timeout失败、0 items。PM按既有same-day run授权允许一次bounded read-only retry，固定现有revision/watermark/receipt，禁止第二次refresh、无限重试、旧cache或其他来源补位。
- Bounded retry final：唯一retry在约49MB provider JSON处被截断并parse failed，0 truthful items；不再重试。AR-034C narrow hotfix已派dev，目标为signed-receipt-bound bounded current-feed read path，production与三automation保持PAUSED，现有refresh/receipt/DB/watermark保留。
- AR-034C RC：dev self-validation passed，RC=`b7530452...`、base=`af0e4e5`、5 paths、patch SHA=`b4cb2a2a...`。active signed-refresh path改为19条bounded `limit=1` reads，legacy whole-feed JSON不可用作fallback。Independent full QA已派，production recovery仍blocked，三任务PAUSED。
- AR-034C QA：Passed / Ready for PM Production Authorization。L0 5/5、architecture、19/19 mutation、production check-only零请求、full regression全过；无生产副作用。授权计划SHA=`e6567babbd94...`，等待用户确认；production仍 `af0e4e5`、三任务PAUSED、existing refresh/receipt/DB/baseline保留。
- AR-034C authorization：用户已确认计划SHA=`e6567babbd94...`。production线程执行exact RC `b7530452...`、19-page bounded read和同run后续业务闭环；禁止second refresh/whole-feed/old run/06。三任务在业务与cwd read-back前保持PAUSED，任一gate失败停止。
- AR-034C production：release passed；19-page actual read因任一body<800硬门返回current_feed_fulltext_insufficient，0 output/0 downstream。AR-034D已派dev，移除长度=truth硬门：valid短文成功+质量标注，真实page失败candidate-local partial，system identity drift仍hard fail；补安全失败telemetry。production clean `b7530452`，三任务PAUSED。
- PM recalibration：确认返工部分由过严指令造成。AR-034D开发已暂停；不再把内容长度/单篇失败/只读retry升级为system blocker，不再每个单点出micro-RC。后续仅保留真实性、身份、外部写入与system drift硬门；item/account partial保留成功项并显性partial。先完成收敛版单RC定义后再恢复开发。
- AR-034D resumed：用户确认继续后，已以收敛版单RC合同恢复固定dev线程；当前仅开发，不派QA/不生产。项目PM规则与global multi-agent Skill同步升级，派发工具继续默认省略model/thinking。
- AR-034D RC：`d88d0e5...` / parent `b7530452...` / exact 3 files / patch `6797475f...`，dev self-validation与PM evidence review通过。唯一一次 independent full QA 已派；禁止provider page/refresh、Feishu/card、automation和production Git。通过也只进入production authorization。
- AR-034D QA：Passed / Ready for PM Production Authorization。唯一合并QA无blocking finding；19-item partial、all-short、system drift、telemetry与full regression均通过。授权计划SHA=`4e7a9ab7...`；三automation保持PAUSED，本次明确不改其definition/status。
- AR-034D authorization：用户已确认计划SHA=`4e7a9ab7...`。production线程执行exact 3-file release、一次19-page read及same-run 03/04/card闭环；禁止second refresh/read retry/06/automation change。当前状态Running，三任务保持PAUSED。
- AR-034D production：Released / Bounded Read Passed / Recovery Blocked Before 03。production clean `d88d0e5...`；WeChat 19/19 green，Douyin 87与AIHOT 56本地产物保留。阻塞是source fingerprint与sampler canonical fingerprint的representation mismatch；87/87 URL+account+title映射已由PM只读证明，无内容丢失或串账号。Feishu 03/04/card均未写，三任务仍PAUSED。
- AR-034E plan awaiting confirmation：只修source->canonical identity mapping并让comparison/03/read-back使用canonical fingerprint，source fingerprint继续作为provenance；不新增安全架构、不改fingerprint算法、不重采集。计划SHA=`c23fe579...`；确认后走单一窄RC + 单次independent QA。
- AR-034E authorized / Development：用户已确认计划SHA=`c23fe579...`。固定dev线程从production `d88d0e5...`构建单一窄RC；当前不派QA、不生产、不改automation，三任务保持PAUSED。
- AR-034E RC / Independent QA：RC=`ad708bea...`、base=`d88d0e5...`、3 files、patch=`2ba0e3ba...`；dev tests与PM evidence review通过。唯一一次QA已派，必须独立复算87映射、public integration/prewrite、canonical comparison/03/read-back及协同URL/title drift；不调用生产Provider/Feishu/automation。
- AR-034E QA Failed / Consolidated Rework：P0为source manual未治理协同URL/title/source_type漂移；P1为exact-parent与manifest SHA合同。旧RC `ad708bea...`不得发布。固定dev线程一次性返修同一根因，产出fresh exact-parent narrow RC；完成后再走一次完整independent QA，不做micro-recheck。三任务保持PAUSED。
- AR-034E RC2 PM Review Failed / Scope Rework：RC2=`46030c8...`虽已通过旧阻断mutation、writer-call sentinel、exact-parent与逐文件SHA，但其3个路径内包含未授权行为：`content_sampler.py` 带入editorial/score/priority/Skill-pool差异，`source_ingestion_lineage.py` 带入非AR-034E legacy-root差异。QA尚未派发。当前回到Development，只允许从production `d88d0e5...`按hunk构建真正的AR-034E-only fresh RC；三任务继续PAUSED，production与外部系统0动作。
- AR-034E RC3 / Independent Full QA：RC3=`746501b...`、exact parent=`d88d0e5...`、3 files、patch=`0849c408...`。PM从Git对象复核changed-function ownership与forbidden-function production-byte equality，scope收敛通过；manifest/逐文件SHA/patch/real87哈希均独立匹配。唯一一次full QA已派，必须fresh clone重算hunk ownership、协同漂移writer sentinel、真实87 mapping、canonical comparison/03/read-back和完整回归；通过也只进入PM Production Authorization。
- AR-034E RC3 QA Passed / PM Acceptance Rejected：QA通过isolated 87-row identity contract，但真实writer read-back为full ledger 162，RC3却要求与Douyin closure 87整表相等；本地等价探针稳定复现post-write mismatch。状态降级为Development Rework，RC3不得发布。只修full-ledger read-back的87-item canonical projection口径，保留writer对162全量验证和所有manual/prewrite硬门；fresh exact-parent RC后再做一次production-shape independent QA。三任务保持PAUSED，生产0动作。
- AR-034E RC4 / Production-shape Independent QA：RC4=`07940e8...`、exact parent=`d88d0e5...`、3 files、patch=`b2549deb...`。PM等价探针与targeted 3/3确认full writer identity 162通过，87 Douyin canonical projection严格有序通过；read-back mutation保持fail closed，forbidden functions仍与production相同。固定QA线程将独立重放87+19+56 public helper及完整回归；通过也只进入PM Production Authorization。
