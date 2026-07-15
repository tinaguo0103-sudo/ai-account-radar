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
| AR-026 | 飞书 01 全量对标账号采集覆盖 | P1 | QA Passed / Release Blocked by AR-031 + RC | dev/test -> RC -> production release | Round 2 QA 通过；生产 01 仍需隔离 8 个污染源；固定 profile/login 硬门与当前 production baseline RC 未完成前不得上线 |
| AR-027 | 飞书 01/03/04 标签和表格列业务清理 | P1 | Schema Audit QA Passed / Waiting PM Cleanup Decision | schema audit -> dry-run -> QA -> release cleanup | Round 2 QA 通过；cleanup matrix 可用于第一轮字段/选项清理决策，view 仍需人工确认 |
| AR-031 | 固定抖音 Chrome Profile 与登录态硬门 | P1 | PM Accepted for Hotfix RC / RC Building | hotfix RC -> release QA -> profile migration | `aadfd99` 的登录门与 schema fail-closed 已接受；empty-output 具体错误标签为非阻断，code-only RC 正在构建 |

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
| AR-026 | 飞书 01 全量对标账号采集覆盖 | P1 | QA Passed / Release Blocked by AR-031 + RC | feature/next-production-flow | 先通过 AR-031 固定 profile/login 门，再基于当前 production main 建最小 RC；发布时需生产 01 写入授权，将 8 个污染源隔离并 read-back |
| AR-027 | 飞书 01/03/04 标签和表格列业务清理 | P1 | Schema Audit QA Passed / Scheduled After AR-026 | feature/next-production-flow | AR-026 上线与首次全量采集稳定后，再基于 production read-only cleanup matrix 单独授权字段/选项/view 清理 |
| AR-031 | 固定抖音 Chrome Profile 与登录态硬门 | P1 | RC Building / Pre-08:00 Blocker | production-base RC -> release QA -> hotfix main | 当前 9333 仍为旧 RC profile；代码已 PM 接受，等待 production-base 窄 RC 与发布授权 |
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
