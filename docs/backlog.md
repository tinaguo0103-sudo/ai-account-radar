# AI账号雷达需求池

这个文件是需求和生产优化的轻量入口。任何新想法、生产问题、hotfix、版本合并前缺口，先进入这里；再由 `docs/release_board.md` 决定发布路径。

用户对 PM、开发、测试协作方式的长期要求，不进入需求池，统一记录在 `docs/pm_operating_rules.md`。

## 使用规则

- 生产不稳定时，`P0/P1` 生产修复优先于 dev 大功能。
- 能独立发布的生产修复走 hotfix，不强行合并未 Ready 的 dev 大功能。
- hotfix 从 `main` 或生产当前版本切出；发布后必须同步回 `feature/next-production-flow`。
- 涉及飞书写入、卡片、SCF、定时任务、外部服务的需求，Ready 前必须有 staging/test 验证记录。
- 每个需求只保留必要信息，不写长篇 PRD；细节放到相关文档或实现提交里。

## 状态定义

- `Inbox`：刚记录，还没判断。
- `Next`：近期要做，已明确优先级和发布路径。
- `In Dev`：正在开发。
- `Staging Tested`：已通过测试环境验证。
- `Ready`：可进入发布候选。
- `Released`：已发布到生产并完成 smoke。
- `Parked`：暂缓，不删除。

## 优先级定义

- `P0`：生产链路断、数据污染、定时任务连续失败。
- `P1`：生产不稳定、需要人工盯、失败后不易定位。
- `P2`：明显提升效率或闭环完整度，但不影响当天跑通。
- `P3`：体验、文档、长期优化。

## 需求模板

```md
### AR-XXX 标题

- 类型：生产稳定 / 新功能 / 体验优化 / 技术债 / 文档
- 优先级：P0 / P1 / P2 / P3
- 状态：Inbox / Next / In Dev / Staging Tested / Ready / Released / Parked
- 来源：
- 影响：
- 发布策略：hotfix main / 跟随 feature/next-production-flow / 暂缓
- 验证方式：
- 关联分支/提交：
- 备注：
```

## 当前需求

> 说明：`AR-001` 曾用于记录生产不稳定现状，`AR-002` 曾用于记录发布前检查门禁，`AR-004` 曾用于记录 OAuth 授权提醒。它们不是实际需求，已从需求池移出；编号不重排，避免历史交接引用断裂。生产现状、发布 checklist 和授权提醒分别维护在 `docs/release_board.md`。

### AR-003 学习确认卡上线前部署腾讯云 SCF receiver

- 类型：生产发布
- 优先级：P1
- 状态：Historical Dependency / Absorbed into AR-006
- 来源：学习闭环功能
- 影响：代码合并但 SCF 未部署时，飞书学习卡按钮仍可能走旧 receiver，导致确认失败。
- 发布策略：跟随 `feature/next-production-flow`；合并后部署 SCF，再启用生产学习卡。
- 验证方式：`node --test cloud_functions/feishu-card-receiver/test/receiver.test.mjs cloud_functions/feishu-card-receiver/test/tencent-scf-entry.test.mjs`，部署后 `check_feishu_card_cloud_receiver.py`，再做最小 production smoke。
- 关联分支/提交：`a656159`, `eb7f9a5`
- 备注：健康检查只证明 URL 和读权限；不能替代卡片 action 写回测试。该发布依赖不再作为独立需求排队，由 AR-006 的生产启用计划统一负责 receiver 部署、action smoke 和回滚。

### AR-005 生产唤醒/保活机制上线

- 类型：生产稳定
- 优先级：P1
- 状态：Installed / Synced to Dev
- 来源：生产定时任务不稳定风险
- 影响：Mac 睡眠、断网或空闲可能导致 08:00 任务延迟或失败。2026-07-04 AR-016 RCA 显示，09:18 飞书 03 update 读超时与 macOS Maintenance Sleep / DarkWake / 网络恢复窗口高度重合，AR-005 已从一般稳定性优化升级为生产链路防复发关键项。
- 发布策略：可作为独立生产稳定优化发布；不依赖学习闭环。
- 验证方式：先复核现有 `--status` / `launchctl print` / `pmset -g sched` / `pmset -g log`；配置修正后重新安装并验证 `caffeinate` 包含 `-s`，生产窗口内 `pmset -g assertions` 能看到 `PreventSystemSleep`，明天 08:00-10:00 后复查没有 `Entering Sleep` / DarkWake 循环。
- 关联分支/提交：`cff709a`, `eb7f9a5`, 生产 `9a42f08`, `cf88643`, dev `03d6de3`
- 备注：现在必须显式 `--install` 才会安装，避免误运行。2026-07-04 生产线程复核显示 keepawake 已安装且 07:50 触发，但当前命令 `/usr/bin/caffeinate -im -t 10800` 只产生 `PreventUserIdleSystemSleep` / `PreventDiskIdle`，不足以防 `Clamshell Sleep` / DarkWake。2026-07-04 生产 hotfix 已安装并同步 dev：LaunchAgent 实际命令为 `/usr/bin/caffeinate -ims -t 10800`，07:50 wake schedule 保留；当前时间不在 active window，需明天 07:55-10:50 复查 `pmset -g assertions` 和 `pmset -g log`。

### AR-006 学习闭环生产启用

- 类型：新功能
- 优先级：P2
- 状态：Staging Tested / Needs Product Reconfirmation
- 来源：学习闭环计划
- 影响：把 04/06 反馈沉淀为学习日结、确认卡和 Skill 草稿。
- 发布策略：跟随 `feature/next-production-flow`，不抢在生产稳定修复前发布。
- 验证方式：staging/test 04/06/08 全流程；空样本跳过；Skill 草稿 `草稿已生成 -> 已同步`。
- 关联分支/提交：`57fd01e`, `a656159`, `2645827`, `eb7f9a5`
- 备注：默认不自动修改全局私有 Skill。AR-003 的 production receiver 发布和 AR-018 的测试 App / 测试 SCF / 测试 Base 已并入本需求：AR-018 作为已完成测试基础设施保留，AR-003 作为上线依赖由本需求统一验收，不再单独排队。

### AR-008 06 watcher 飞书文档同步读取 `.env.local` 权限失败

- 类型：生产稳定
- 优先级：P1
- 状态：Released
- 来源：生产目录 `output/logs/codex_script_package_runner_2026-07-03.log`
- 影响：第二张卡提交后的 06 完整脚本与制作包可能无法稳定同步飞书文档或写入 06 记录；如果只写日志不通知，用户可能看不到 06 生成失败。
- 发布策略：优先诊断；如确认影响生产 06 链路，走 hotfix main，并同步回 `feature/next-production-flow`。
- 验证方式：生产只读检查 runner 日志和 LaunchAgent 状态；必要时在 staging/test 06 表、测试文件夹和个人通知目标做 06 runner 写入验证；production smoke 只做最小观察，不写测试数据到生产业务表。
- 关联分支/提交：生产 `main` 当前提交 `db61b84 Clarify script package doc sync failure alerts`
- 备注：生产只读诊断确认 10:59 错误为旧日志残留；11:18 后 runtime `.env.local` 更新，LaunchAgent 指向 `~/.codex/ai-account-radar-runtime`，后续 watcher 未复现。06 记录 `recvoh7TvgV7zl` 已有飞书文档 URL、文件夹 URL，`文档同步状态=已同步到用户可见飞书文件夹`，错误字段为空。未做生产写入 smoke；如需验证下一次创建，另开 P2 staging/test 06 文档创建 smoke。

### AR-009 06 口播稿从泛化结构转向场景化表达

- 类型：生产优化
- 优先级：P2
- 状态：Released / Minimal Smoke Passed
- 来源：用户对 2026-07-02 生成的两个选题/口播稿质量反馈截图
- 影响：当前 06 口播稿结构稳定但表达颗粒度不足，容易显得泛泛而谈；缺少真实使用体验、账号内具体场景、对标视频表达拆解和“先抛场景再引出知识库/方法”的叙事方式，会影响可拍摄性和账号差异化。
- 发布策略：跟随 `feature/next-production-flow`；不走 hotfix main；不抢在生产稳定观察和 release candidate 门禁前发布。
- 验证方式：用昨天两个实际选题做本地回归样例；生成改前/改后对比；开发时需要围绕当前选题去搜索对标博主/同类内容/相关信息，拆解表达模式，并把信息融合进口播稿，最终表达必须使用用户账号风格；知识库类名词必须先用浅显科普、案例或打比方解释，再进入工作流；必须提供用户可人工确认的真实样例路径、关键片段或可读报告；不写生产业务表、不创建生产文档。
- 关联分支/提交：`86f5f07 fix: route AR-009 quality QA to test skills`
- 备注：优化方向包括：先搜索/拆解别人怎么讲同类题或对标账号怎么表达；把抽象主题拆回用户账号里的真实工作流问题；知识库类选题必须先抛具体场景，再引出知识库作为解决方案；口播稿要体现个人使用体验、体验场景和细节，不只复用稳定框架。2026-07-03 已完成 dev/test 多轮验证和用户人工确认，Skill 修改锁定，不再继续在本需求内返修。2026-07-05 已随本轮发布进入生产：正式 global Skill 已同步，未覆盖 `-ar009-test`；runtime 关键文件 hash 已复核。本轮未跑真实 06/Codex 生成，后续下一次用户触发真实 06 时观察内容质量和内部边界。测试中发现“每条真实 `codex exec` 往往 attempt 2 / 生成两次”的独立问题，已拆为 AR-010，不混入 AR-009。

### AR-010 06 测试/生成链路每条样例重复生成两次

- 类型：技术债 / 测试效率
- 优先级：P2
- 状态：Released / Minimal Smoke Passed
- 来源：AR-009 测试过程中用户发现每轮测试通常每条样例都会生成两次，token 和时间成本偏高。
- 影响：真实 `codex exec` 测试成本翻倍，长任务更容易耗时、被打断或造成 PM/测试线程等待；如果生产路径也存在同样逻辑，可能增加 06 生成延迟和 token 成本。
- 发布策略：跟随 `feature/next-production-flow`；不纳入 AR-009；不阻塞 AR-009 锁定和择期发布。
- 验证方式：复现 runner `attempt 2` 触发条件；区分“模型内容真的需要重试”和“测试/返修阶段 `qa_status=revise` 不自评 pass 导致误触发重试”；增加单测或 dry-run，证明 `revise` 不会在非必要场景固定触发第二次生成；真实 `-ar009-test` 隔离跑一条样例验证 attempt 次数和输出质量。
- 关联分支/提交：`83b7b10 fix: avoid unnecessary 06 codex retries`、`95abad9 fix: clarify retry history at max attempts`
- 备注：已确认根因是 `qa_status=revise` 同时承担“需要模型继续重写”和“测试阶段不自评通过”的双重语义，导致 runner 把预期的 `revise` 状态当作重试信号。2026-07-03 PM 自验和测试线程独立复测均通过；控制流由 mock/stub 与函数探针覆盖。2026-07-05 已随本轮发布进入 production runtime；本轮未触发真实 06，后续下一次真实生成时观察 attempt history。剩余风险是重试关键词和 Markdown 区段扫描需后续按日志补模式。

### AR-011 生产 06 飞书链接改为可点击超链接

- 类型：体验优化 / 生产优化
- 优先级：P2
- 状态：Released / Backfill Passed
- 来源：用户反馈生产 06 飞书链接当前是纯文本 URL，不方便直接点击打开。
- 影响：用户查看 06 完整脚本与制作包时，需要复制 URL 或手动打开，降低生产链路使用效率；如果链接字段格式不规范，也可能影响后续生产观察和文档回查。
- 发布策略：跟随 `feature/next-production-flow`；不走 hotfix main；可和后续 06/Skill 发布窗口一起处理。
- 验证方式：先确认当前 06 表/卡片/字段里哪些位置写入纯文本 URL；在 staging/test 06 表或隔离测试记录中验证链接字段/富文本字段能直接点击打开；发布前还需在测试表验证旧记录 backfill；不得写生产业务表做试验；production smoke 只在发布后最小读回确认。
- 关联分支/提交：`877e50e fix: render 06 feishu links clickable`、`97685a3 fix: verify 06 links in grid view`、`48742b0 feat: add 06 clickable link flow checks`
- 备注：2026-07-04 开发线程完成代码侧最小实现并在 staging/test 06 表 `tbl5PQjZhajZtxsP` 验证 URL 字段 payload 可读回；正式生产 06 表 `tblFjYFFH9nfekeK` 的旧 `飞书文档` 字段为文本 `type=1`，不能直接写富文本 URL。第一次 L3 测试在记录详情面板失败：字段可见且 DOM 暴露 `a href`，但多种点击方式未产生目标 URL 导航或新标签。开发线程第 1/3 轮返修定位为验证路径问题：详情面板自动化点击不可靠；新建 staging/test 专用 grid view `AR-011 L3 链接验证`（view id `vewN1u2jdL`）和测试记录 `recvop4Ypg2yjh`，主表格列中链接点击可打开目标 docx/文件夹；提交 `97685a3 fix: verify 06 links in grid view`。PM 自验通过本轮提交范围、报告、截图 `/private/tmp/ar011_l3_grid_links_visible.png` 和 20 个相关 tests。测试线程独立 L3 复测通过，截图 `/private/tmp/ar011_l3_grid_qa_visible_pass.png`；点击 `打开飞书文档` 后 Chrome 用户标签出现 `https://my.feishu.cn/docx/FZuPdGDlmobf6lxk2wmcquksn2c`，点击 `打开飞书文件夹` 后出现 `https://my.feishu.cn/drive/folder/X79kfZ274lcpy4dtjypcEBUmn2b`。用户指出这只覆盖了链接点击表面功能，还没有完整复测 06 写完后把飞书文档链接贴进表格的链路，也没有测试旧数据 backfill 脚本。因此状态修正为 `Partial QA Passed / Needs Flow QA`。第 2/3 轮开发已补齐窄 schema/view setup、旧文本 URL -> 新 URL 字段 backfill、06 `package_row -> create_script_package_record -> read-back` flow QA，提交 `48742b0 feat: add 06 clickable link flow checks`；PM 自验 24 个相关 tests、py_compile、`git diff --check`、`pre_merge_check.py` 通过。测试线程独立 Flow QA：L1 本地门禁通过；L2 staging/test 真实写入与 read-back 通过，flow QA 创建 `recvope7Uy1hzL`，backfill 创建 `recvopebesx0N5`、`recvopec3K3Q8k`、`recvopecSBw1vU`，dry-run `to_update=2 invalid_source=1`，write/read-back OK，幂等复跑 `already_ok=2 invalid_source=1`；L3 使用独立测试 Chrome profile `ai_account_radar_dev/.runtime/browser-profiles/feishu-l3-test` 通过 CDP `127.0.0.1:9227` 补测通过，未使用用户日常 Chrome。2026-07-05 已随本轮发布进入生产：生产 06 已创建 `飞书文档链接`、`飞书文件夹链接` URL mirror 字段，主视图 patch 成功；9 条旧记录中 5 条可回填 URL 已 backfill/read-back OK，旧文本字段保留；idempotency 复跑 `already_ok=5`、`to_update_record_ids=[]`。本轮未做生产 UI 点击跳转验收，后续如用户需要可补一次只读/低影响 L3。

#### AR-011 Released Checklist

1. 已完成生产 06 表新增 URL 字段 `飞书文档链接`、`飞书文件夹链接`，旧 `飞书文档` / `飞书文件夹` 文本字段保留。
2. 已将两个 URL 字段加入 `脚本包主视图`。
3. 已完成 backfill dry-run：生产 06 共 9 条记录，5 条可回填 URL，4 条 skip。
4. 已完成 backfill write：旧文本 URL 转成 `{text, link}` 写入新增 URL 字段，旧字段不动。
5. 已完成 read-back 校验：5 条写入记录 URL 字段 payload 与旧文本 URL 一致。
6. 发布前 staging/test UI 验证已通过；详情面板点击不作为验收路径。
7. 发布后 production smoke 已完成 API read-back；本轮未做生产 UI 点击跳转验收。
8. 回滚/恢复边界仍保留：新增字段可隐藏或清空重刷；旧文本字段不动；backfill 日志保留 record_id、旧值 hash、新字段 payload 摘要。

#### AR-011 Release Authorization Status

- 2026-07-05 已获得发布授权并执行完毕。
- 生产 schema、主视图 patch、旧数据 backfill write/read-back、idempotency rerun 均已完成。
- 后续如需验证生产 UI 点击跳转，可另开一次低影响只读 L3 验证；不是当前发布 blocker。

#### AR-011 Flow QA Status

- 已实现窄 schema/view setup：`setup_script_package_clickable_links.py`，默认 dry-run，只处理 `飞书文档链接` / `飞书文件夹链接` URL 字段和显式指定 grid view；不做标题字段、旧字段清理等 broad workspace setup。
- 已实现 backfill 脚本：`backfill_script_package_clickable_links.py`，支持 dry-run、write、read-back、幂等重跑；write 只更新新增 URL 字段，不改旧文本字段。
- 已实现 06 flow QA fixture：`script_package_clickable_link_flow_qa.py`，在 staging/test 表走 `package_row -> create_script_package_record -> read-back`，验证 `doc_sync.url` / `folder_url` 同时进入旧文本字段和新增 URL 字段。
- L1/L2 已由测试线程复测通过：本地门禁、staging/test setup、06 flow write + read-back、backfill dry-run/write/read-back/idempotency 均通过。
- L3 已补测通过：独立测试 Chrome profile 连接 `127.0.0.1:9227`，未使用用户日常 Chrome；flow/backfill 新记录在 staging/test grid view 中 URL 字段可见，点击可打开目标 docx/folder URL。

#### AR-011 Implementation Slices Completed

1. 已新增窄 schema/view setup：`setup_script_package_clickable_links.py`。
2. 已新增 backfill 脚本：`backfill_script_package_clickable_links.py`。
3. 已新增 06 flow QA fixture/命令：`script_package_clickable_link_flow_qa.py`。
4. 已完成 L1/L2/L3/发布后 smoke 所需证据；剩余仅为可选生产 UI 点击体验复核。

### AR-012 08:00 daily pipeline 飞书写入超时导致今日任务失败

- 类型：生产稳定
- 优先级：P1
- 状态：Recovered / Synced to Dev
- 来源：2026-07-04 用户反馈今天定时任务未成功，PM 生产只读诊断确认。
- 影响：2026-07-04 08:07 生产采集实际启动并生成本地候选，但 `content_sampler.py --write-feishu` 在写飞书 03 内容收件箱更新记录时 `socket.timeout`，导致 `daily_pipeline_2026-07-04.json` `ok=false`；`output/latest` / `output/latest_write` 未更新到 7 月 4 日；10:00 选题卡守卫因 `today_daily_pipeline_log_not_ok` 跳过发卡，避免误发旧候选。
- 发布策略：生产稳定修复优先；用户已授权 hotfix main，发布后同步回 `feature/next-production-flow`。2026-07-04 用户已授权恢复数据到生产飞书，但明确不发选题卡；生产线程只读恢复检查确认现有正式脚本缺少“只用已有 run 产物安全补写 03”的 CLI，因此恢复前需先做最小 hotfix。后续考虑 AR-013 补偿池机制。
- 验证方式：生产只读核对 `output/logs/scheduled_daily_collection_2026-07-04.json`、`output/logs/daily_pipeline_2026-07-04.json`、`output/runs/run_20260704_080730/`、`output/latest_write/` 和 `run_topic_card_if_fresh.py --no-notify`；hotfix 需覆盖从已有 run 安全恢复 03、飞书 API 超时 retry/backoff、`content_sampler_log.json` pending/partial 早落盘、失败通知包含 run_id/run_dir/已同步条数/恢复命令；production smoke 只做最小读回，仍禁止发卡。
- 关联分支/提交：`7ed71b8`, `8526066`, `1a8cd91`, `ea296d7`, `b46070b`
- 备注：本地 run 目录已有 4 条候选，其中 2 条推荐“生成脚本包”。失败点不是采集没启动，也不是候选为空，而是飞书 API 更新已有记录时 30 秒读超时；`output/runs/run_20260704_080730/content_sampler_log.json` 未写出，说明失败发生在 mirror/latest_write 更新之前。用户授权“今天的先刷到飞书上，卡片不发”；恢复应只补 03/04/latest_write/主控台并保留 `run_20260704_080730`，不得发送选题卡。生产线程只读校验显示本地唯一内容 113 条、飞书 03 本 run 记录 34 条、required_minimum 90，正确拒绝写 04；`run_topic_card_if_fresh.py --no-notify` 返回 `today_daily_pipeline_log_not_ok`，未发卡。2026-07-04 hotfix main 已完成并 push：03 恢复日志 `recovery_status=success`，113 条已更新，剩余 0；04 校验通过；`latest_write`、daily/scheduled 日志和 00 主控台已恢复；未发送选题卡。开发线程已通过 `b46070b chore: sync AR-012 recovery hotfix` 同步回 `feature/next-production-flow`。未发卡候选补偿池已拆为 AR-013。

### AR-013 未发卡候选补偿池

- 类型：生产优化 / 体验优化
- 优先级：P2
- 状态：Released / Minimal Smoke Passed
- 来源：用户提出如果某天没发卡，不必当天强行跑完；下一次发卡时可从“没发过卡”的候选里一起挑选。
- 影响：减少失败当天的人工补跑压力，同时避免优秀候选因为一次飞书/API 超时丢失；但如果直接混入明天卡片，可能导致卡片过载、时效不清、run_id 混乱或旧热点误导。
- 发布策略：跟随 `feature/next-production-flow`；不走 hotfix main，除非生产连续失败导致候选恢复成为 P1。
- 验证方式：设计补偿池规则并在 staging/test 验证：失败批次候选必须保留原始日期和 `run_id`，下一次发卡从近 3 天未发卡候选与当天候选中统一选择，卡片上明确标注“本次候选覆盖日期范围/原始日期/原始 run_id”，不得覆盖当天 `latest_write`，不得把旧批次改成新批次。
- 关联分支/提交：`5e87613 feat: add unsent topic compensation pool`；`4855f01 fix: route 06 runner to explicit test topic table`
- 备注：2026-07-04 用户修正：补偿池不是固定补偿区名额，不应设“最多 2 条/1-3 条”的补偿上限；应让近 3 天未发卡候选和当天候选一视同仁进入同一个待发卡候选池，再按现有候选排序/选择逻辑生成卡片。卡片需要告诉用户“本次是哪几天的选题一起选”，每条仍保留原始日期和原始 `run_id`。三天内热点一视同仁，不因热点属性单独缩短有效期；当前选题质量判断不宜在补偿池里过度复杂化，后续可通过选题 Skill 优化。代码现状：Topic Card sender 有 `--limit` 默认 7，这是卡片层全局限制，不是补偿区上限；AR-013 V1 不新增补偿上限，不调整当前卡片全局数量限制。用户确认：若目前选题没有其他数量限制，则卡片 limit 先不动，目前还没有遇到过发 7 个选题的场景。开发提交 `5e87613` 已实现 V1：统一近 3 天补偿候选池、已发卡候选 ledger、候选去重、卡片覆盖日期/原始日期/run_id 展示、历史候选 snapshot 安全回写，并更新 Feishu card receiver / Tencent SCF entry 允许带 snapshot 的历史候选安全通过 selection guard / production direction guard。PM 自验：Python 30 tests、Node 25 tests、`py_compile`、`node --check`、`git diff --check`、`pre_merge_check.py` 通过，dev worktree 真实发卡 guard 仍返回 `running_from_development_worktree`。测试线程独立 QA 通过的是新增代码/控制流：候选池规则、卡片 payload/snapshot、历史候选回调安全回写、制作方向卡延续、AR-015 unknown guard；未写生产、未发真实生产卡、未触发采集。2026-07-04 用户指出当前测试仍偏简单，主要集中在新增代码，缺少流程测试和回归测试；PM 状态降级为 `Control QA Passed / Needs Flow + Regression QA`。补充 QA 必须覆盖：staging/test 个人测试卡真实发送或等价端到端流程、卡片渲染、按钮提交、04 状态写回、制作方向卡触发，以及原有当天发卡/无历史候选/unknown guard/dev guard/重复提交等回归。2026-07-04 测试线程补做 Flow + Regression QA：本地控制流/回归门禁通过，但真实 Feishu 测试卡链路阻塞。2026-07-05 AR-018 Round 5 已打通独立测试 App + 测试 receiver + 专用测试 Base 的真实按钮回写：真实测试卡发送成功，Feishu Web 点击 `本批都不选` 后专用测试 04 记录 read-back 为 `状态=不做`，生产 04/06/output 反查为 0。因此 AR-013 可恢复 Flow + Regression QA，但应注意当前专用测试 Base 主要覆盖 04/卡片 receiver；若要覆盖制作方向卡或 06 后续链路，需要测试线程先确认测试 06 资源边界。
- Flow QA 结论：2026-07-05 测试线程完成真实流程与回归测试，建议 `Flow QA Passed / Waiting PM Review`。本轮 run_id `ar013_flowqa_20260705_122703`，专用测试 04 创建 9 条 `[AR-013 TEST]` 候选，真实候选池选中 4 条应入池候选：当天候选、1 天内历史、2 天内历史、当天重复优先项；旧日期、已处理、已生成、已发卡 ledger、历史重复均被排除或去重。真实卡片显示 `本次候选覆盖：2026-07-03、2026-07-04、2026-07-05`，每条候选保留原始日期和原始 run_id；真实测试卡发送成功并在独立测试 Chrome 可见，点击 `本批都不选` 后 4 条卡内候选均 read-back 为 `状态=不做`，历史候选仍保留各自原始 run_id，证明 snapshot-safe 回写到原始记录。生产 04、生产 06、production output/watcher 对本轮 run_id / 标题 / record_id 匹配 0。本轮未点击 `提交选择` / `生成脚本包`，因此没有真实覆盖制作方向卡和 06 后续链路；该项作为发布前/后续测试资源风险记录，不得宣称 AR-013 已验证完整 06 链路。
- 发布前补测准备：2026-07-05 开发提交 `4855f01` 已补齐安全测试路径。`script_package_shared.py` 让 06 runner ready-topic 读取优先使用 `FEISHU_TOPIC_TABLE_ID` / `FEISHU_TOPIC_DECISION_TABLE_ID`，生产默认 fallback 不变；专用测试 04 已补齐缺失标准字段，专用测试 Base 已新建/复用测试 06 `06 完整脚本与制作包__测试`，本地 `.env.staging.local` 指向测试 06。开发侧用 synthetic/test-only event 验证：测试候选选中后可进入 `生成脚本包`、`制作方向卡状态=待发送`；触发测试 receiver 队列后制作方向卡发送到测试目标并标记 `已发送`；模拟制作方向提交后状态为 `已提交` 且 `我的制作补充` 有值；receiver 路径未创建 06。06 ready dry 探针通过：`codex_script_package_runner.py --skip-codex --include-test-records --record-id <test_record_id> --limit 1` 输出 `ready_topics count=1, write_feishu=false, skip_codex=true`。发布前测试线程需补真实 Feishu Web 按钮链路：选择测试候选 -> 提交 -> 触发测试制作方向卡队列 -> 填写并提交制作方向 -> read-back 测试 04 -> 运行 `--skip-codex` 06 ready 探针。未经单独授权不得跑真实 06 生成。
- 发布前补测结论：2026-07-05 测试线程验证通过，建议 `Direction Card Flow QA Passed / Waiting PM Review`。本轮 run_id `ar013_directionqa_20260705_125654`，测试 04 record `recvotivvF9OWS`，Topic Card message `om_x100b6baa0f6c38a4c1f55900768d4f0`，制作方向卡 message `om_x100b6baa0468d0a0c21cd1f836ab1b2`。真实 Feishu Web 选择测试候选后 read-back：`状态=生成脚本包`、`制作方向卡状态=待发送`、`是否已生成脚本稿=否`；触发测试 receiver 队列后 read-back：`制作方向卡状态=已发送` 且错误为空；提交制作方向后 read-back：`制作方向卡状态=已提交`、`我的制作补充` 有测试文本、`是否已生成脚本稿=否`。06 ready 探针使用 `AI_ACCOUNT_RADAR_ENV_FILE=.env.staging.local ... codex_script_package_runner.py --skip-codex --include-test-records --record-id recvotivvF9OWS --limit 1`，输出 `ready_topics count=1`、`write_feishu=false`、`skip_codex=true`。测试 06、生产 04、生产 06、production output/watcher 对本轮 run_id/title/record_id 匹配 0。Python 37 tests、Node 26 tests、`git diff --check`、`pre_merge_check.py` 通过。本轮未跑真实 Codex/06 生成，不能声称真实 06 生成通过；如发布前要求 L4 真实 06 生成，需要单独授权。
- PM 验收结论：2026-07-05 PM 独立验收通过，状态推进为 `PM Accepted / Ready for RC Regression`。验收判断：AR-013 用户目标是未发卡候选补偿与卡片流转安全，不是证明真实 06 生成质量；本轮已覆盖近 3 天统一候选池、真实测试卡、历史 snapshot-safe 回写、制作方向卡真实提交、06 ready dry-run、生产 04/06/output 0 匹配和关键回归，足以进入 release candidate 全量回归。边界保留：`--skip-codex` 不能等同真实 06 生成；若 release scope 要求发布前 L4 真实 06 生成，需要单独授权。

### AR-018 飞书测试卡 receiver / test app 隔离

- 类型：测试基础设施 / 发布门禁
- 优先级：P1
- 状态：Test Infrastructure Complete / Absorbed into AR-006
- 来源：AR-013 Flow + Regression QA 被阻塞：测试卡虽已默认授权，但当前 staging env 缺可证明隔离的 Tencent SCF receiver URL、测试接收目标和制作方向测试目标；健康检查还会把 topic table 解析到生产同名表。
- 影响：所有飞书卡片类需求的 Flow QA 都可能被迫停在 mock/fixture，或者在真实点击时冒误写生产 04 / 触发真实后续链路风险。若不补这个测试基础设施，AR-013、AR-015 Topic Card、后续卡片/SCF 回写类需求都无法稳定做 L2/L3/Flow QA。
- 发布策略：先在 `feature/next-production-flow` 做方案和最小测试路径配置；如需创建/部署测试 SCF receiver 或修改测试飞书应用配置，需要用户确认。不得复用生产 receiver 作为测试点击目标，除非能证明云端环境显式写入 staging/test 表且不触发生产后续动作。
- 验证方式：配置或建立独立 staging/test receiver URL；云端显式设置 `FEISHU_TOPIC_TABLE_ID=tblWAH8Ba3wh5jdo` 或等价测试 04 表；配置个人/测试接收目标和制作方向测试目标；健康检查必须显示 receiver challenge 成功且 table_id 为 staging/test 表；发送明确标记测试卡后，点击按钮只写 staging/test 04，并且不会触发真实 06 watcher；再回到 AR-013 继续 Flow + Regression QA。
- 关联分支/提交：`9f7b1b0 fix: isolate feishu test card health checks`
- 备注：本需求不是产品功能，不改变 AR-013 的补偿池规则；它是卡片类需求的测试门禁基础设施。2026-07-04 PM 已向用户给出方向 + 详细方案，用户确认同意按 AR-018 先补“测试 receiver / test app 隔离”。开发提交 `9f7b1b0` 已完成最小实现：`check_feishu_card_cloud_receiver.py` 优先使用 explicit staging table id，输出 `table_id_source`，并新增 `--require-test-card-config` 隔离门禁；新增 `test_feishu_card_receiver_healthcheck.py` 和 `docs/spikes/ar018_test_card_receiver_isolation.md`。验证：staging 只读 health 显示 `table_id=tblWAH8Ba3wh5jdo`、`table_id_source=FEISHU_TOPIC_TABLE_ID`，不再解析到生产 04；缺 `FEISHU_TENCENT_SCF_URL`、`FEISHU_CARD_RECEIVE_TARGETS`、`FEISHU_PRODUCTION_DIRECTION_RECEIVE_TARGETS` 时会明确失败并列缺项。当前仍不能证明云端 test receiver 环境变量已配置，因为尚缺测试 SCF URL / 测试接收目标。2026-07-04 用户已授权配置测试 receiver / 测试目标，但开发线程确认本机缺独立测试 SCF URL、缺两个测试接收目标，`serverless` / `tencentcloud` CLI 不可用，仓库也没有可直接执行的测试 SCF 自动部署脚本；README 只有手工创建/上传 zip/配置函数 URL 路径。用户进一步澄清：可以继续使用此前登录过的腾讯云后台去配置；测试卡接收目标可以使用用户个人 ID。开发线程已在腾讯云广州/default 创建独立测试函数 `feishu-topic-card-receiver-ar018-test`，函数 URL 已写入本地 `.env.staging.local`，测试函数 env 已配置 `FEISHU_TOPIC_TABLE_ID=tblWAH8Ba3wh5jdo`，并配置用户个人测试目标用于 `FEISHU_CARD_RECEIVE_TARGETS` / `FEISHU_PRODUCTION_DIRECTION_RECEIVE_TARGETS`。`AI_ACCOUNT_RADAR_ENV_FILE=.env.staging.local python3 scripts/check_feishu_card_cloud_receiver.py --require-test-card-config --table-key topic_decision` 输出 `ok=true`，receiver challenge、test_card_config、staging 04 read check 均通过。2026-07-04 测试线程执行 Test Card Smoke 前置探针，发现真实发卡入口 `scripts/feishu_topic_decision_card.py` 的 `get_topic_table()` 仍按表名解析到生产同名 04 表 `tblz2CFc9eIa8bMG`，而不是 `.env.staging.local` 显式配置的 staging/test 04 表 `tblWAH8Ba3wh5jdo`；`build --run-id ar018-test --limit 1` 未发卡但生成 preview，证明发送路径会沿错误表解析继续。测试线程正确停止，未发送/点击测试卡。开发提交 `f0c0027` 已完成窄返修：真实 Topic Card sender 的 build/send/apply/候选读取路径现在优先使用 `FEISHU_TOPIC_TABLE_ID` / `FEISHU_TOPIC_DECISION_TABLE_ID`，与 receiver health check 语义一致；返修后 staging 探针输出 `health_table_id=tblWAH8Ba3wh5jdo`、`sender_table_id=tblWAH8Ba3wh5jdo`、`same=true`，`build --run-id ar018-test --limit 1` 仅生成本地 preview、未发卡。2026-07-04 测试线程 Round 2 真实测试卡已成功发送到个人测试目标并在 Feishu Web 可见，测试候选 `recvopUtMtOaLO` / run_id `ar018_test_smoke_20260704_230238` 位于 staging/test 04，安全按钮 `本批都不选` 可点击；但点击后 read-back 仍为 `状态=待判断`、`学习状态=待学习`，未回写为 `不做`。生产 04、生产 06、测试 06 和 production output 只读反查均未发现 AR-018 写入或 watcher 触发。开发提交 `bed3b42` 已定位真实代码失败点并修复：receiver 事件能打到测试函数并尝试写 staging/test 04，但 `选择原因标签` 在测试表中是 `type=1 / Text`，旧代码按数组写入导致 Feishu `TextFieldConvFail`；现已改为按字段类型/形态写入，多选保留数组、文本写 `、` 分隔文本、空标签写空字符串。开发线程已将 `bed3b42` 新 receiver 包部署到测试函数 `feishu-topic-card-receiver-ar018-test`，未部署生产函数；部署后 health check 通过，函数配置仍显示 `FEISHU_TOPIC_TABLE_ID=tblWAH8Ba3wh5jdo`。2026-07-05 测试线程 Round 3 复测失败：新测试卡发送、Feishu Web 可见、真实按钮点击均完成，但 read-back `recvoq9wbE4FO0` 仍为 `状态=待判断`；随后用同一张卡的 `submit_no_selection` value 构造 synthetic event 直接 POST 测试 receiver，返回 success，并将同一记录写为 `状态=不做`。这证明新测试 SCF 代码能写 staging/test 04。开发线程进一步诊断：当前飞书后台 app 已订阅 `card.action.trigger`，但回调 URL hash 与生产 `.env.local` receiver 一致，和 `.env.staging.local` 测试 receiver 不一致；staging 发卡使用的 app hash 与该后台 app 一致，因此直接把该 app 回调改成测试 receiver 会影响生产回调。当前必须创建或指定独立飞书测试应用/机器人，把 `.env.staging.local` 切到测试 app id/secret，并让测试 app 的 `card.action.trigger` 指向 `feishu-topic-card-receiver-ar018-test`；生产 app 保持生产 receiver。2026-07-05 用户确认授权继续 AR-018：允许创建或配置独立飞书测试应用/机器人，并将 staging/test 发卡与回调链路切到该测试 App；生产 App、生产 receiver、生产 SCF 和生产表不得改动。
- 最新进展：2026-07-05 开发线程已完成独立测试 App/机器人配置。飞书测试 App `AI账号信息雷达 AR-018 TEST` 已启用，版本 `1.0.0` 已发布通过；回调方式为开发者服务器，订阅新版 `card.action.trigger`；测试 SCF env 已切到测试 App 凭证并保持 `FEISHU_TOPIC_TABLE_ID=tblWAH8Ba3wh5jdo`；本地 `.env.staging.local` 已切到测试 App 凭证但不提交。health check 和 sender table-id probe 均指向 staging/test 04 `tblWAH8Ba3wh5jdo`。测试线程 Round 4 真实 smoke 失败：使用 `.env.staging.local` 发测试卡返回 Feishu `99992361 open_id cross app`，说明当前 `FEISHU_CARD_RECEIVE_TARGETS` 的 `open_id` 不是独立测试 App 体系下的接收 ID；新建 staging/test 04 候选时 `POST /bitable/.../records` 返回 `91403 Forbidden`，提示独立测试 App 对 staging/test Base 的写权限/协作者配置也需复核。开发线程配置返修后，独立测试 App 接收目标已修复：`.env.staging.local` 和测试 SCF 的测试目标已切到测试 App 体系个人 open_id，测试消息发送成功，不再 `open_id cross app`。剩余阻塞是 staging/test 04 写权限：测试 App 对 `tblWAH8Ba3wh5jdo` 的 create/update 仍返回 `91403 Forbidden`；Base UI 中现有生产 App `AI账号信息雷达` 是该 Base 的应用协作者且可管理，但独立测试 App `AI账号信息雷达 AR-018 TEST` 在添加协作者中按全名、短名、App ID 均搜不到。2026-07-05 用户确认改走 A 方案：新建一套专用测试 Base / 测试 04 表用于卡片回调 Flow QA，并要求该测试 Base 放到现有 AI账号信息雷达相关“大文件夹”下，方便用户查找；不继续死磕当前 `tblWAH8Ba3wh5jdo` 的应用协作者入口。
- PM 最新验收：2026-07-05 开发线程已完成专用测试 Base 方案。独立测试 App 创建专用测试 Base `AI账号信息雷达_AR018_TEST` 和测试 04 表 `04 分析与选题_AR018_TEST`；因飞书拒绝直接在共享文件夹下创建 Base，采用“测试 App 创建 Base 保持可写权限 + 添加快捷方式到 AI账号信息雷达共享文件夹”。`.env.staging.local` 与测试 SCF `feishu-topic-card-receiver-ar018-test` 均已切到新测试 04；新表 34 个字段校验通过，关键字段齐备；测试 App create/update/read 成功；synthetic receiver 写回成功；真实 sender 发卡到个人测试目标成功。当前下一步是测试线程 Round 5：真实测试卡发送、后台/隔离 Chrome 点击 `本批都不选`，read-back 新测试 04 为 `状态=不做`，并确认生产 04/06/watcher 无匹配。
- 测试收口：2026-07-05 Round 5 通过。测试线程使用 `.env.staging.local`、独立测试 App、测试 receiver 和专用测试 04 `tblR730iHAaz9NQ7` 完成真实测试卡发送与 Feishu Web 点击；点击安全按钮 `本批都不选` 后，测试记录 `recvot8EjWXDNk` read-back 为 `状态=不做`、`学习状态=待学习`，且未设置制作方向卡状态。生产 04、生产 06、production output/watcher 相关本地 output 对本轮 run_id / 标题 / record_id 匹配 0。该基础设施继续用于 AR-006 和其他卡片类需求的 staging 验收，但不再作为独立产品需求占用发布队列。

### AR-019 2026-07-05 定时任务网络异常后补跑

- 类型：生产恢复 / 当日补跑
- 优先级：P1
- 状态：Recovered
- 来源：用户反馈 2026-07-05 早上没网，希望把几个定时任务重新触发，看今天效果。
- 影响：如果直接分别重跑自动线程，可能重复采集、重复写 04 或误发旧卡；必须按生产正式顺序补跑并让 Topic Card 只通过 fresh guard 发送。
- 发布策略：生产 worktree `main` 运行，不做代码开发；如发现代码缺陷再另开 hotfix。
- 验证方式：先检查今天日志和 run 产物；按需执行 `run_daily_collection_job.py --defer-editorial --no-notify`、主编写回 04、`run_topic_card_if_fresh.py`。成功标准是当天 daily/scheduled/latest_write/04 一致，若发送卡片必须由 fresh guard 触发；失败需报告阶段、日志、恢复建议。
- 备注：2026-07-05 PM 观察到今日自动线程存在 `systemError`：每日全源采集、每日主编写回 04、每日选题卡发送均有今日新线程异常记录。用户已明确要求重新触发，因此允许生产线程补跑当天正式链路；仍不得绕过 fresh guard 手动发送旧卡，不得处理非今天 run，不得触发 06 watcher。生产线程已完成补跑：今日 run 为 `run_20260705_102318`，03 raw 候选 8 条，外层主编补齐后 1 条 `今日最值得做`、2 条 `可选候选`、5 条 `暂存观察`；finalizer 写 04 时近 5 天去重跳过 2 条，最终新建 1 条 04 候选；`daily_pipeline_2026-07-05.json`、`scheduled_daily_collection_2026-07-05.json`、latest_write 与 00 主控台均恢复到今天；`run_topic_card_if_fresh.py` 正式守卫发送今日选题卡，message_id `om_x100b6ba873fd10a8c23d24266e35cf2`。未触发 06 watcher。若用户觉得卡片太少，应另开选题/去重/补偿池策略，不手动绕过本次 guard。

### AR-020 选题流程重构

- 类型：核心流程重构 / 内容质量优化
- 优先级：P1
- 状态：Superseded by AR-020B / Architecture Review Done
- 来源：用户反馈当前整体选题结果不理想，需要对整个选题流程做优化和重构。2026-07-05 用户补充具体问题：AI Hot 相关性弱但权重偏高；对标账号内容已采集却几乎没有进入选题卡；03 内容收件箱存在非用户清单来源被标成 `对标视频` 的污染数据。
- 影响：涉及从 03 原始内容进入、主编判断、04 候选生成、去重/暂存、Topic Card 展示与后续 06 触发的核心生产链路。若直接改 prompt 或单点打分，可能继续出现“候选少、题眼弱、与用户账号不贴合、重复去重误伤、卡片无法解释为什么选它”等问题。AR-020 应同时处理来源治理、来源权重、对标内容转译、候选解释和流程回归。
- 用户要求：
  - AI Hot 保留为每日热点观察源，但不能作为主来源；15% 是来源权重/重要性影响力，不是最终数量硬卡。只有重大模型发布、模型更新或行业级新闻才进入高优先级候选。
  - 用户给出的 AI 对标账号应成为主要来源，目标是来源权重、候选召回、主编判断和最终解释都明显偏向有效对标账号；对标账号内容需要被转译成 Austin 账号的真实工作流改造、AI 方法论、AI 导演工作流、AI 项目复盘等方向。
  - 2026-07-02 类 Codex 选题和 AIGC / AI 视频导演 workflow 不是固定必选题材，而是诊断样例：这些内容明明适合账号人设，却没有进入选题，说明现有逻辑没有正确识别“对标内容是否适合 Austin 账号”。重构目标是选出更适合账号的选题，而不是把 Codex / AIGC 写成白名单。
  - 截图中出现的非确认对标来源全部清掉，不需要复核是否保留，但必须 dry-run 确认不误删，例如 `琼玩车`、`UDG终极梦想车库`、`潜云说-姚捷`、`异世界的光某`、`鲍俞成AI获客`、`羽森说AI赋能IP`、`润宇创业笔记`、`AI短视频工坊` 等。
  - 账号内容方向不变；对标账号白名单从飞书 01 获取；其他未截图来源先不动；03 历史数据不动。
  - 测试内容库使用 2026-07-01 之后收集到的全部内容；测试必须做反向评估：系统筛出的选题是否适合 Austin，内容库里是否有更适合的候选没有被选中，以及为什么。
  - 对标账号采集需要覆盖飞书 01 中清理污染账号后的全量有效账号，不能只跑 12 个默认账号；该采集覆盖能力作为 AR-020 的上游依赖，单独登记为 AR-026。
- 当前只读审计发现：`config/system_rules.yaml` 仍有 `AIHOT 可以是主来源`；`scripts/content_sampler.py` 对 AI Hot review pool 仍有最多 8 条的逻辑；`config/content_sources.yaml` 中截图来源被配置为 `current_aux_competitor` 且可参与主采样。详见 `docs/spikes/ar020_topic_flow_rework_requirements.md`。
- 发布策略：跟随 `feature/next-production-flow` 做方案、staging/test 和真实历史样本回放；不得直接 hotfix 生产。涉及 04 写入、Topic Card、状态流转、SCF 回调或发卡的真实流程测试，必须使用 staging/test 资源；生产启用前需要最小 smoke。
- 验证方式：先给用户确认最终实现方案，再派开发。验收不能只看新增代码单测，必须包含来源白名单/污染来源回归、2026-07-01 之后全内容库回放、反向测试、staging/test 04 写入、Topic Card preview/测试卡、人工样例评审和相邻链路回归。2026-07-02 Codex 类对标内容、AIGC/导演工作流类内容等只能作为诊断样本，用来验证“适配度判断和不选理由”是否合理，不能变成固定题材配额或强制入选清单。
- 备注：本需求不同于 AR-013 补偿池。AR-013 解决未发卡候选是否进入下一次卡片；AR-020 解决“什么内容值得进入候选、如何判断、如何解释、如何呈现给用户选择”。PM 不得在未与用户确认最终实现方案前派发开发。2026-07-06 开发线程已提交并 push `8adce16 feat: rework topic source governance`：AI Hot 改为低权重热点源，重大 AI Hot 可按重要性保留；对标账号候选增加来源构成、市场验证、Austin 映射方向、转译角度和案例/工具/工作流补充；04 写入和 Topic Card preview 展示来源构成/转译/AI Hot 重大性；新增 2026-07-01 后内容库回放与反向评估报告。测试线程独立 QA 判定失败：官方 fuller-data replay 在读取 2026-07-01 后生产只读 `content_items.csv` 时崩溃，且手动 226 条内容探针仍发现 4 条高适配/可转译候选漏选或缺少成立的不选理由。Round 2 开发提交 `07be5a5 fix: harden topic replay audits` 已修复：`content_sampler.write_csv()` 输出所有行字段并集，`topic_replay_evaluation.py` 官方 fuller-data replay 使用 2026-07-02 至 2026-07-06 生产只读 7 个 `content_items.csv` 成功跑完，输出 `/private/tmp/ar020_round2_full_replay_final`，`content_items=212`、`candidate_count=29`、`selected_count=15`、`source_composition={有效对标账号核心源:12, AI Hot低权重热点源:3}`、`reverse_flags=0`、`writes_feishu=false`。此前 QA 点名的 AIGC自修室多宫格故事板、AIGC自修室 Mx-Shell Skill、大伟聊前端 CI/CD Shell、子木AI智能体线下小班课已进入 selected；招生混杂内容未选，理由为原始来源主题明显偏离 Austin 账号方向。PM 已派测试线程 Round 2 复核，重点验证官方 replay 稳定性、reverse_flags=0 的合理性和此前漏选样本处理是否真实改善。Round 2 独立 QA 已通过技术门禁：`/private/tmp/ar020_round2_full_replay_qa/topic_replay_summary.json` 显示 `content_items=226`、`candidate_count=30`、`selected_count=15`、`source_composition={有效对标账号核心源:12, AI Hot 低权重热点源:3}`、`reverse_flags=0`、`writes_feishu=false`。PM 内容验收未接受：抽查 `replay_selected_topics.csv` 发现部分 Austin 映射错配、转译角度模板化、重复主题较多，且 15 条 selected 中 13 条为 `暂存观察`、仅 2 条为 `生成脚本包`。AR-020 进入 Round 3 编辑质量窄返修，不进入 RC / PM Accepted。

- Round 3 开发回传：dev `497a737 fix: improve AR-020 editorial replay quality` 已 push。改动集中在 `topic_flow_rework.py`、`content_sampler.py`、`topic_replay_evaluation.py` 和 `test_topic_flow_rework.py`：新增来源主题识别、主题簇、转译质量、非模板转译、AI Hot Austin 角度；移除对标视频默认“吸收选题承诺和结构”的模板 fallback；新增 PM editorial quality report，并拆分 actionable / observe / AI Hot / quality flags。开发 fuller-data replay 输出 `/private/tmp/ar020_round3_full_replay_dev_v2`：`content_items=226`、`candidate_count=30`、`selected_count=15`、`actionable_count=2`、`observe_count=13`、`aihot_selected_count=3`、`reverse_flags=0`、`writes_feishu=false`。PM 点名的 `Codex联动Obsidian...知识库` 已改为 `真实工作流改造` / 信息雷达复盘角度，不再错配 Excel/运营表格/AI导演。仍有 `selected_quality_flag_count=14` 和主题簇集中风险，故状态为 `Ready for QA Round 3 / Editorial Quality Recheck`，不是 PM Accepted。

- Round 3 QA 与 PM 决策：独立 QA 判定 `QA Failed / Editorial Rework Still Needed`。关键阻断不是 replay 是否能跑，而是报告层与后续主字段层不一致：`Codex联动Obsidian...知识库` 在 PM 报告层已变成信息雷达/内容资产，但同一条 `replay_selected_topics.csv` 的 `我的工作流痛点`、`我要做的实验`、`重点体现` 仍残留 AI 视频交付、分镜、成片验收等错配字段，且质量风险报告未标出冲突。用户进一步指出：自己没有要求 PM report，真实需求一直是优化选题逻辑；项目已有 `ai-account-editorial-director` 主编 Skill，当前开发连续三轮主要改 deterministic/replay 脚本，方向可能偏离“用 Skill 判断选题适配度”的核心。三轮 QA 已触顶，AR-020 暂停继续返修，下一步先做架构评审：梳理 03 raw 内容、候选池、主编 Skill、deterministic fallback、04/Topic Card/06 主字段契约和真实 Skill 回放验收路径，再决定是否拆为 `AR-020B`。

- 架构评审回传：dev `1ef5685 docs: review AR-020 editorial architecture` 已 push，新增 docs-only `docs/spikes/ar020_editorial_architecture_review.md`。评审结论：AR-020 根问题不是 replay 报告不够详细，而是“主编决策层”和“确定性预填/兜底层”的职责边界被打穿；Round 3 修了 `Austin转译角度`、主题簇、PM report 等旁路/辅助字段，但 04 / Topic Card / 06 消费的 `选题命题`、`我要做的实验`、`我的工作流痛点`、`重点体现`、`对应方向` 等主字段仍由早期 deterministic scene/profile 函数生成，未被同一契约约束。评审建议拆新阶段 `AR-020B 选题主编 Skill 与字段契约重构`：更新 `ai-account-editorial-director` Skill contract、增强 `editorial_skill_runner.py` 上下文、明确字段 owner、加入 invariant validator、使用真实 Skill replay 验证 2026-07-01+ 内容库，并在 staging/test 04 / Topic Card 中验证用户可见字段一致。PM 决策点：是否接受 AR-020B，而不是继续 Round 4；是否确认内容质量验收必须以真实 Skill replay 为准；是否允许更新全局私有 `ai-account-editorial-director` 并建立同步/回滚策略。

### AR-020B 选题主编 Skill 与字段契约重构

- 类型：核心流程重构 / 主编 Skill 契约 / 内容质量验收
- 优先级：P1
- 状态：Superseded by AR-020C / AR-020D / AR-020E / Historical Evidence Retained
- 来源：AR-020 三轮 QA 触顶后，用户指出当前开发方向偏离真实需求：用户没有要求 PM report，目标是优化选题逻辑；项目已有 `ai-account-editorial-director` 主编 Skill，选题适配度应由 Skill 及其输入/输出契约负责，而不是继续在 deterministic/replay 脚本里补一套“像主编”的规则。2026-07-06 用户确认按 `docs/spikes/ar020_editorial_architecture_review.md` 的评审方案继续。
- 目标：让 03 原始内容进入候选池后，由真实 `ai-account-editorial-director` 主编判断 Austin-fit、来源转译、推荐动作和 04/Topic Card/06 会消费的主字段；确定性代码只负责来源治理、候选池、事实字段、fallback-only 兜底和一致性校验。
- 范围内：
  - 更新 repo mirror `skills/ai-account-editorial-director/SKILL.md` 的字段契约和判断标准，明确 Skill 必须输出或审查 `选题命题`、`一句话Brief`、`我要做的实验`、`我的工作流痛点`、`旧流程痛点`、`AI介入点`、`验证方式`、`可沉淀资产`、`我的思考点`、`重点体现`、`对应方向`、`推荐动作`、`今日建议级别`、`title_permission`、`可发布标题` 等主字段。
  - 增强 `editorial_skill_runner.py` 输入上下文，把来源治理结果、对标账号证据、AI Hot 重大性、账号方向、来源构成、市场验证、原始标题/账号/链接等作为 Skill 上下文，而不是让 Skill 只看简化后的 deterministic 字段。
  - 重构字段 owner：Skill output / Skill-reviewed evidence 是主字段来源；deterministic 创作函数只能作为 fallback-only，并且不能作为 PM Accepted 或内容质量验收依据。
  - 增加 invariant validator，阻断或降级字段间冲突，例如知识库/Obsidian/RAG 选题不能在主字段里残留 AI 视频/分镜/成片验收；AI Hot 进入候选必须有重大性和 Austin 角度；`生成脚本包` 必须有可执行实验与 title permission。
  - 增加真实 Skill replay 工具或模式，使用 2026-07-01 之后 production read-only 内容库验证真实 Skill judgment，而不是 deterministic replay 冒充主编。
  - 在 staging/test 04 / Topic Card preview 或测试卡中验证用户可见字段一致性；不能只看本地 CSV 或报告。
- 范围外：不处理 AR-026 污染来源写入和全量采集发布；不处理 AR-027 字段/标签/view 删除；不处理 AR-013；不跑真实 06/Codex 生成质量；不清理历史 03；不写生产 Feishu；不更新生产全局私有 Skill。
- 测试口径：
  - L1：Skill contract、runner context、fallback-only、invariant validator 单测。
  - L2：真实 Skill replay 覆盖 2026-07-01 之后内容库，输出可人工阅读的 selected/observe/reverse/invariant 报告和样例。
  - L3：staging/test 04 或 Topic Card preview/测试卡验证用户可见主字段，不允许 PM report 正确但主字段错配。
  - 回归：AR-013 compensation pool、AR-015 idempotency/unknown guard、Topic Card `--check-only`、Feishu retry/recovery、现有 `pre_merge_check.py`。
- 发布策略：先在 dev 完成 repo mirror Skill、runner、validator、replay 工具和测试；QA 通过并 PM 内容验收后，再单独决定是否同步 production global private `ai-account-editorial-director`、是否进入 RC 和生产发布。global Skill 同步、生产采集、生产 04 写入、真实 Topic Card、真实 06 生成都需要后续发布授权。
- 停止条件：如果真实 Skill replay 仍出现主字段错配、fallback 输出被当成内容质量通过、无法解释为什么高适配候选未选、或测试只覆盖报告不覆盖 04/Topic Card 主字段，则不得进入 PM Accepted。

- 开发回传：dev `7074aa2 feat: enforce AR-020B editorial field contract` 已 push。实现包括：更新 repo mirror `skills/ai-account-editorial-director/SKILL.md`，明确主编 Skill owns Austin-fit gate、来源转译、推荐动作/状态、实验卡主字段和标题权限；增强 `editorial_skill_runner.py`，向 Skill 传入 source governance evidence、对标来源、AI Hot 重大性、来源权重、市场验证、主题/转译 hint，并标记 `editorial_engine`、`fallback_only`、`not_editorial_quality`；新增 `topic_field_contract.py` 检查知识库 vs AI视频错配、AI Hot 重大性、生成脚本包就绪度、方向/实验/痛点一致性；更新 `push_today10_to_feishu.py`，04 可见候选只接受 real Skill、非 fallback、字段契约通过、实验动作可执行的行；新增 `topic_skill_replay_evaluation.py` 跑真实 Skill replay；新增 `test_ar020b_field_contract.py`；更新 `pre_merge_check.py` py_compile。
- 开发验证：真实 Skill replay 输出 `/private/tmp/ar020b_skill_replay_20260707_dev_v3`，`content_items=273`、`candidate_count=34`、`pre_skill_pool_count=16`、`skill_rows=16`、`actionable_count=4`、`observe_count=12`、`contract_failure_count=0`、`fallback_row_count=0`、`reverse_flags=0`、`writes_feishu=false`。关键样例显示：Codex+Obsidian 知识库纠偏为真实工作流改造且实验落到资料进入选题台和脚本包；多宫格故事板保留 AI导演工作流；Mx-Shell Skill 为可选候选；CI/CD Shell 降级暂存观察；泛增长/企业 AI 来源需补证据。
- PM 状态：已派测试线程执行 `AR-020B Skill Contract Review`。测试不得只看开发 replay report，必须复核 `skill_replay_rows.csv` / 04/Topic Card 会消费的主字段、fallback 标记、contract failures、反向漏选理由和 staging/test 可见字段边界。
- 独立 QA 回传：测试线程验证 dev `7074aa2`，结论为 `L0-L2 QA Passed / L3 Visible Field Validation Pending`。独立真实 Skill replay 输出 `/private/tmp/ar020b_skill_replay_qa_20260707`，使用 2026-07-02 至 2026-07-07 的 8 个 production read-only `content_items.csv`，结果 `content_items=273`、`candidate_count=34`、`pre_skill_pool_count=16`、`skill_rows=16`、`actionable_count=5`、`observe_count=11`、`contract_failure_count=0`、`fallback_row_count=0`、`reverse_flags=0`、`writes_feishu=false`。QA 抽查确认 Codex+Obsidian 知识库主字段不再残留 AI 视频/分镜/成片验收；多宫格故事板与 AI导演方向一致；Mx-Shell Skill 作为 AI导演/Skill 复跑候选；CI/CD Shell 降级暂存观察；AI Hot 3 条均为 observe，不进入 actionable。技术门禁和 `pre_merge_check.py` 均通过，未写生产、未发卡、未采集、未触发 06。
- PM 编辑复核：接受 L0-L2 的方向性改善，不接受完整完成。PM 抽查 `/private/tmp/ar020b_skill_replay_qa_20260707/skill_replay_rows.csv` 中 7 条本地可见映射：5 条 `生成脚本包` 主字段可进入 L3；2 条 `补证据 / 可选候选`（AI服务购买理由卡、FDE现场翻译环节）具备观察价值，但 `title_permission=内部测试标题`、无可发布标题。L3 通过标准必须验证它们在 04/Topic Card 中被清楚标识为补证据/可选候选或被降级，不得和 `生成脚本包` 候选呈现为同等可生成。
- L3 QA 回传：测试线程完成 `AR-020B L3 staging/test visible-field validation`，结论 `L3 Failed / Needs Topic Card UX Rework`，不是资源阻塞。验证环境为 dev `7074aa2`、专用测试 04 `tblR730iHAaz9NQ7`、个人/测试目标；run_id `ar020b_l3_20260707_134926`。测试 04 写入 7 条 `[AR-020B L3 TEST]` 记录：`recvoFd4xjjJhH`、`recvoFd4xj4YpF`、`recvoFd4xjtvO6`、`recvoFd4xjrYzk`、`recvoFd4xjR4PO`、`recvoFd4xjyRJO`、`recvoFd4xjjFO6`；真实测试 Topic Card 已发送并在 Feishu Web 可见。证据文件：写入摘要 `/private/tmp/ar020b_l3_visible_field_qa/ar020b_l3_20260707_134926_write_summary.json`，read-back CSV `/private/tmp/ar020b_l3_visible_field_qa/ar020b_l3_20260707_134926_readback.csv`，卡片 JSON `output/decision_cards/2026-07-07_ar020b_l3_20260707_134926_topic_decision_card.json`，DOM 证据 `/private/tmp/ar020b_l3_visible_field_qa/ar020b_l3_20260707_134926_feishu_dom_check.json`，截图 `/private/tmp/ar020b_l3_visible_field_qa/ar020b_l3_20260707_134926_feishu_messenger.png`。
- L3 通过点：Obsidian 样例在测试 04 read-back 中为信息雷达 / 内容资产 / `03 -> 04 -> 06` 工作流，无 AI video / 分镜残留；多宫格、Mx-Shell、AI 视频样例保留 AI导演 / 分镜 / 验收语义。生产边界安全：未写生产 Feishu、未发生产 Topic Card、未触发采集、未触发 06/Codex；生产 04、生产 06、production output/runtime output 对本轮 marker 命中 0。
- L3 阻断 1：`补证据 / 可选候选` 在 04 与 Topic Card 中没有被足够清楚地区分。两条 `补证据 / 可选候选` read-back 为 `状态=待判断`、`今日建议级别=可选候选`，仅有 `需要补的证据` 文本；卡片开头和按钮仍引导“勾选进入生成脚本包”，两条补证据行仍在同一个可勾选生成脚本包列表里，缺少用户可见 `补证据`、`不可直接生成`、`内部测试标题`、`缺发布标题` 或等价 caveat。不得把这类候选和 `生成脚本包` 候选同等呈现。
- L3 阻断 2：`push_today10_to_feishu.py --write` 在 `.env.staging.local` 下失败 `Missing Feishu table: 04 分析与选题`。health/sender 已能按 `FEISHU_TOPIC_TABLE_ID` 指向专用测试 04，但 04 写入 CLI 仍按表名解析，需补齐显式 `FEISHU_TOPIC_TABLE_ID` / `FEISHU_TOPIC_DECISION_TABLE_ID` 优先逻辑，保证 staging/test 可自然走专用测试 Base。
- L3 阻断 3：Topic Card build/send 受 AR-013 补偿池影响，混入旧测试记录 `[AR-018 TARGET TEST] 专用测试 Base 发卡目标探针`，卡片覆盖日期变为 `2026-07-05、2026-07-07`，干扰 AR-020B L3 样例纯度。需要 run-specific / test-isolation 模式，或明确测试候选隔离策略，避免旧测试候选混入验收卡。
- 当前动作：打回开发做窄返修。返修范围只限 Topic Card/04 可见 UX、staging writer 显式表路由、L3 run-specific/test-isolation；不得写生产、发生产卡、触发采集、触发 06/Codex、同步全局 Skill 或改动无关 AR-026/027。
- L3 窄返修回传：开发线程已完成并 push dev `a22c0fe fix: isolate AR-020B topic card qa flow`，建议 `Ready for L3 QA Recheck`。改动范围包括 `feishu_topic_decision_card.py`、`run_topic_decision_card_session.py`、`push_today10_to_feishu.py`、`topic_decision_fields.py` 和相关 tests。返修点：04 写入补齐 `推荐动作`、`title_permission`、`可发布标题`；Topic Card 拆分 `可生成候选` 与 `补证据/观察候选`，只有真实可生成候选进入多选框，补证据候选只展示判断和缺口，不进入 06；`candidate_ids` 只包含可生成候选，`supplement_candidate_ids` 单独保留审计，`display_candidate_ids` 用于已展示 ledger；`push_today10_to_feishu.py` 优先使用 `FEISHU_TOPIC_TABLE_ID` / `FEISHU_TOPIC_DECISION_TABLE_ID` 并加载 `AI_ACCOUNT_RADAR_ENV_FILE`；`feishu_topic_decision_card.py build/send` 与 `run_topic_decision_card_session.py` 新增/透传 `--strict-run-id` 和可重复 `--record-id`。
- 返修验证：staging/test 只读 preview 命令 `AI_ACCOUNT_RADAR_ENV_FILE=.env.staging.local ... feishu_topic_decision_card.py build --run-id ar020b_l3_20260707_134926 --limit 10 --strict-run-id --include-decided` 输出 `record_count=7`、`strict_run_id=true`、`coverage_dates=[2026-07-07]`、`candidate_ids=3`、`supplement_candidate_ids=4`；预览不含 `[AR-018 TARGET TEST]`，包含 `可生成候选：3 条｜补证据/观察候选：4 条` 和 `不会进入下方“生成脚本包”勾选列表`，多选框 placeholder 为 `生成脚本包：只显示可直接进入 06 的编号`。开发自测 40 tests OK，相关 `py_compile`、`git diff --check`、`pre_merge_check.py` 均通过。
- 当前动作：派测试线程重跑 L3。复测必须使用 `.env.staging.local` 专用测试 04 和个人/测试目标，优先使用 `--strict-run-id`；验收重点是 Feishu Web 卡片不混入旧 AR-018 候选、补证据/观察候选不在生成脚本包多选框、04 read-back 能看到推荐动作/title permission/可发布标题，且不触发生产或 06。
- L3 复测回传：测试线程验证 dev `a22c0fe`，结论 `L3 QA Passed / Waiting PM Review`。本轮使用新 run `ar020b_l3_retest_20260707_1415`，真实写入专用测试 04 `tblR730iHAaz9NQ7`，真实发送测试 Topic Card 到个人/测试目标，并在独立测试 Chrome / Feishu Web 抓取 DOM 和截图。输入 CSV `/private/tmp/ar020b_l3_retest_qa/ar020b_l3_retest_20260707_1415_today10.csv`，7 条记录：5 条 `推荐动作=生成脚本包`，2 条 `推荐动作=补证据 / 今日建议级别=可选候选`。测试卡 message_id 脱敏为 `om_x100b6...7f089a`。
- L3 复测证据：04 read-back 路径 `/private/tmp/ar020b_l3_retest_qa/ar020b_l3_retest_20260707_1415_readback.json` / `.csv`；卡片结构检查 `/private/tmp/ar020b_l3_retest_qa/ar020b_l3_retest_20260707_1415_card_structure_check.json`；Feishu DOM 证据 `/private/tmp/ar020b_l3_retest_qa/ar020b_l3_retest_20260707_1415_feishu_dom_check.json`；截图 `/private/tmp/ar020b_l3_retest_qa/ar020b_l3_retest_20260707_1415_feishu_messenger.png`；卡片 JSON `output/decision_cards/2026-07-07_ar020b_l3_retest_20260707_1415_topic_decision_card.json`。
- L3 复测结果：staging writer routing 通过，`push_today10_to_feishu.py --write` 输出 `table_id=tblR730iHAaz9NQ7`、`table_id_source=FEISHU_TOPIC_TABLE_ID`、`created_records=7`，并创建字段 `推荐动作`、`title_permission`、`可发布标题`；run-specific/test isolation 通过，`--strict-run-id` 输出 `record_count=7`、`coverage_dates=[2026-07-07]`、`has_ar018_in_card_json=false`；Topic Card UX 通过，卡片明确显示 `可生成候选：5 条｜补证据/观察候选：2 条`，两条补证据行显示不会进入下方生成脚本包勾选列表，交互 option record_id 只有 5 个可生成候选。04 read-back 显示 5 条可生成记录 `title_permission=可发布标题` 且 `可发布标题` 有值；2 条补证据记录 `title_permission=内部测试标题` 且 `可发布标题=null`，不在卡片多选 option 中。
- PM 验收修正：撤回 `PM Accepted / Waiting Release Planning`，状态改为 `L3 QA Passed / Waiting User Editorial Review`。原因：PM 刚才只验了 L3 返修目标是否通过，没有先回到用户原始需求验证“选题逻辑是否真的更适合 Austin 账号”，也没有把测试文件和真实样例交给用户判断。当前可确认的是：主编 Skill 契约、字段一致性、04/Topic Card 可见层和补证据隔离已通过测试；尚不能代表用户已接受选题质量。下一步必须把测试文件、样例标题、卡片截图/JSON、04 read-back 给用户审阅，再由用户/PM 决定是否进入 `PM Accepted / Waiting Release Planning`。
- PM 原始需求复核：基于 `/private/tmp/ar020b_skill_replay_qa_20260707/skill_replay_rows.csv`、`skill_actionable.csv`、`skill_observe.csv` 和 L3 retest 产物复核，PM 判断 AR-020B 的“主编选题逻辑 + 可见候选体验”达到可进入用户样例审阅的标准。依据：full replay 共 16 条 Skill rows，其中 5 条 `生成脚本包` 全部来自 `有效对标账号核心源 / 对标视频`，AI Hot 3 条全部停留在 observe 层，没有进入 actionable；5 条可生成候选集中在 `真实工作流改造` 与 `AI导演工作流`，能对应用户最初点名的 Codex/Skill/PPT 与 AIGC/导演工作流类适配方向；2 条补证据候选被明确隔离，不能进入生成脚本包多选框。PM 仍不直接标 `PM Accepted`，因为内容类需求必须先给用户看测试样例。
- 用户进一步复核：AR-020B 比之前好，但用户指出当前逻辑仍像黑盒，且本轮标题仍有模板化感。用户要求说明当前机制如何运行，并怀疑角度/标题是否仍被 Skill 或代码中的模板、预设角度牵引。PM 检查后判断：全局私有 Skill 本身已写清“不是标题模板器”和账号人设/案例判断，但 `editorial_skill_runner.py` 仍向 Skill 注入较强的 `Workflow Experiment Card` 结构、主题/转译 hint、母场景候选和字段契约；`topic_flow_rework.py` 也会提供固定主题转译角度。这些机制能防错，但会把标题推向“先测 / 能不能 / 验收 / 试一遍”一类相似骨架。AR-020B 因此不进入 PM Accepted，拆新评审 `AR-020C`。

### AR-020C 选题主编思考链与标题表达机制评审

- 类型：架构/产品评审 / 主编 Skill 思考链 / 标题表达质量
- 优先级：P1
- 状态：Architecture Review Done / Superseded by AR-020D / AR-020E
- 来源：AR-020B L3 通过后，用户指出当前选题逻辑仍是黑盒，标题仍偏模板化；用户期望不是继续修几条样例，而是让 `ai-account-editorial-director` 根据账号人设和案例，像用户本人一样判断“我会选哪些选题、从什么角度切入、起什么标题”。
- 目标：评审并给出最终方案，使选题链路从“代码 hint + Skill 填字段 + validator 防错”转向“Skill 先做主编自由判断和取舍，代码只提供事实证据与安全守卫”。评审必须解释当前运行链路、模板化来源、Skill/runner/validator/Topic Card 各自职责，并提出可验收的改造方案。
- 评审范围：
  - 当前 `03 -> rough candidate -> editorial_skill_runner.py -> ai-account-editorial-director -> topic_field_contract.py -> 04 / Topic Card` 的真实运行链路。
  - Skill 文档中的人设、案例、表达底线是否被 runner prompt 或代码 hint 稀释、覆盖或模板化。
  - `topic_flow_rework.py` / runner 的主题簇、转译角度、母场景候选是否应降级为事实证据或完全移出用户可见字段。
  - 标题生成是否应从 `我的真实矛盾 / 主编自由稿 / 点击钩子` 中自然抽取，而不是从“工作流实验命题卡”骨架生成。
  - PM/QA 后续如何验收：不仅看入选候选，还要看未选高适配候选、同批标题同构率、Skill 自由判断是否像用户本人、最终 04/Topic Card 是否能让用户看懂为什么选。
- 范围外：本评审不直接改代码、不写 Feishu、不发 Topic Card、不触发采集、不同步全局 Skill、不进入发布。
- 必须回答的问题：
  - 当前标题模板化主要来自 Skill 文档、runner prompt、`topic_flow_rework.py` hint、field contract，还是 LLM 输出习惯？
  - 哪些代码层预设角度必须保留为防错，哪些应从“角度建议”降级为“事实材料”？
  - Skill 是否需要改为先输出 `主编自由判断/为什么选/为什么不选/标题思路`，再由字段映射生成 04 主字段？
  - 怎样在测试里证明“像用户一样思考”，而不是只证明字段没错？
  - 现有 AR-020B 代码哪些可以保留，哪些必须重构或废弃？
- 评审产出：一份 docs-only review，包含现状图、问题根因、推荐方案、备选方案、字段/职责边界、测试验收样例、风险和需要用户确认的决策点。PM 汇总后再给用户确认方案，用户确认前不得进入实现。
- QA 评审设计回传：测试线程已完成 AR-020C QA design summary，结论是后续不能按“字段修复点”验收，而要按“用户能否看出主编像 Austin 一样做选择”验收。AR-020B L3 已证明 04/Topic Card 字段能正确分区，但当前样例仍暴露标题骨架同构和选择理由通用黑盒风险。
- QA 必测目标：系统必须让用户看到 1）为什么今天选这条；2）为什么它适合 Austin 而不是泛 AI 号；3）它从来源内容转成什么个人工作流/导演/服务复盘角度；4）为什么相近候选不选或只观察；5）标题为什么是这个表达，而不是模板句。这里输出的是可公开决策摘要 / decision trace，不是模型隐藏 chain-of-thought。
- QA 验收口径：
  - L0：审查 Skill 与 runner prompt 是否要求输出 source evidence、Austin fit rationale、selection tradeoff、near-miss reason、title rationale、anti-template self-check、recommended action；必须区分事实证据字段、Skill 主编判断字段、fallback-only 字段。
  - L1：新增反模板与反黑盒测试。标题 skeleton 和高频短语如 `先测/先拿/能不能/验收/试一遍/真正要看/拆一次/复盘` 需统计；同批单一骨架超过 25%-30%、任一模板词族覆盖超过 40%、observe 占位标题重复超过 2 条，应 fail 或至少 quality flag。选择理由必须包含具体 source evidence、Austin 场景、行动/实验、取舍原因；通用理由重复超过 3 条、理由不点名来源证据或候选差异，应 fail。
  - L2：使用 2026-07-01 后完整 read-only content library，输出 `candidate_universe`、`near_miss/high_fit_unselected`、`observe/rejected with reason`、`title_diversity_report`、`template_phrase_report`、`selection_tradeoff_report`；反漏选抽样至少 15-20 条，覆盖已选、未选、高分未选、竞品账号、AI Hot、技术过深样例。
  - L3：staging/test 04 + Topic Card 用户可见样例包应展示 `选题标题/可发布标题`、`一句话Brief`、`为什么选它`、`来源证据/来源账号`、`Austin切入角度`、`我要做的实验`、`验证方式`、`需要补的证据`、`推荐动作/今日建议级别`、`标题理由或标题风险`、`相似候选/未选原因摘要`，并分区展示“今日主推/可生成”“补证据/观察”“高适配但未选/为什么没选”或等价结构。
- 必须判失败：字段齐全但标题大片同构；理由大多是“改造我的具体流程”这类通用句；只解释已选、不解释高适配未选；near-miss 被隐藏；AI Hot 凭热度抢位；补证据/观察和可生成界限不清；04/Topic Card 没有展示“为什么选/为什么不选/标题为什么这样写”；deterministic fallback 被当作真实主编判断。
- 用户样例包口径：后续 PM 给用户看的最小包应是一页 summary + 三张表：最终可生成候选、观察/高适配未选候选、标题体感审查；附 1 张 Topic Card 截图和 CSV/Markdown 证据路径，不再把全量 16 行报告直接丢给用户。
- 开发架构评审回传：dev 已新增 docs-only `docs/spikes/ar020c_editorial_thinking_chain_review.md`，commit `944669b docs: review AR-020C editorial thinking chain` 已 push。评审结论：AR-020B 的 field contract、fallback 标记、real Skill replay、04/Topic Card 分区等 guardrail 应保留；但当前 runner prompt 强推 `Gate -> Workflow Experiment Card -> Title Packaging`，`topic_flow_rework.py` 的 `THEME_RULES` / `theme_topic_title()` / `align_topic_visible_fields()` 会在 Skill 前预设主题、转译角度和命题，导致代码 hint 站到主编前面，形成黑盒和标题同构。
- 开发推荐方案：采用两段式 `editorial_thinking -> field_mapping`。第一段让 Skill 输出自由主编判断：source_read、why_i_would_choose、why_i_would_not_choose、account_fit、source_to_me_translation、angle_options、chosen_angle、title_thinking、decision；第二段再把判断映射成 04 / Topic Card / 06 主字段。代码只准备 source facts、non-authoritative hints 和 guardrails，并校验结果；不得由 `topic_flow_rework.py` 或 runner hint 直接生成用户可见标题/命题/Brief。
- PM 决策点：1）是否采用方案 B 两段式；2）`主编判断摘要` / `标题思路` 写入 04/Topic Card 还是只放 QA 样例包；3）是否允许后续同步全局私有 Skill；4）标题同构阈值硬拦截还是 QA 风险提示；5）`topic_flow_rework.py` 主题簇是否保留为 `non_authoritative_hints` 并禁止进主字段；6）后续验收是否以 2026-07-01+ real Skill replay + staging/test Topic Card 样例包为准。
- 用户方案确认：用户已确认 PM 建议。实施口径固定为：采用两段式 `editorial_thinking -> field_mapping`；`主编判断摘要` / `标题思路` 以紧凑形式进入 04 / Topic Card；标题同构对 `生成脚本包` 可生成候选按硬拦截处理，对观察/补证据候选至少作为 QA 风险提示；发布前验收必须使用 2026-07-01+ real Skill replay、staging/test 04 / Topic Card 用户可见样例包，以及 PM 可读的一页 summary + 三张样例表 + 截图/路径证据。全局私有 Skill 同步只在 staging/test L3 和用户样例审阅后进入发布计划。
- 下一步：PM 派开发线程实现 AR-020C。禁止 PM 当前线程直接改实现；禁止生产写入、真实生产卡、采集、06/Codex、全局 Skill 同步或发布动作。
- 开发实现回传：dev 已提交并 push `1b73b9b feat: add AR-020C editorial thinking chain`。改动包括 repo mirror `ai-account-editorial-director` Skill contract、`editorial_skill_runner.py` 两段式输出、`topic_field_contract.py` 公开主编判断/标题同构/黑盒/hint leak 校验、`topic_flow_rework.py` hint 降级、`push_today10_to_feishu.py` / `feishu_topic_decision_card.py` 用户可见字段、`topic_skill_replay_evaluation.py` 样例包输出和相关测试。
- 开发 replay 证据：`/private/tmp/ar020c_skill_replay_20260707_dev/`。summary 显示 `content_items=273`、`candidate_count=34`、`pre_skill_pool_count=16`、`skill_rows=16`、`actionable_count=3`、`observe_count=13`、`contract_failure_count=0`、`fallback_row_count=0`、`reverse_flags=0`、`near_miss_count=0`、`title_quality_failure_count=0`、`writes_feishu=false`。PM 注意：`near_miss_count=0` 需要 QA 独立验证，不能只按报告自证通过。
- 开发样例包：`ar020c_user_sample_summary.md`、`skill_actionable.csv`、`skill_observe.csv`、`near_miss_high_fit_unselected.csv`、`title_body_check.csv`、`skill_replay_summary.json`。样例显示 Codex+Obsidian、故事板 2.0 为可生成；Mx-Shell Skill、Codex PPT、泛增长观点进入补证据/观察。
- PM 下一步：派 QA 做 L0-L3 独立验收。验收必须包含真实样例阅读、反模板/反黑盒审查、near-miss 反向抽样、staging/test 04 + Topic Card 可见字段验证；不得只看 `contract_failure=0`。
- 独立 QA 回传：测试线程验证 dev `1b73b9b`，结论为 `L3 Visible Field QA Passed / Overall QA Failed - Needs Narrow Rework`。L3 staging/test 真实可见体验通过：专用测试 04 `tblR730iHAaz9NQ7` 写入 6 条记录，真实测试 Topic Card 可见 `主编判断摘要`、`标题思路`、`可生成候选：3 条｜补证据/观察候选：3 条`，补证据记录不在生成脚本包多选项中，production 04/06/output/runtime markers 均为 0。
- QA 阻断点：1）L2 独立 real Skill replay 未完整复现，`codex exec` 长时间未完成，只能独立校验开发 replay 产物；2）`title_body_check.csv` 未捕捉观察池重复占位标题 `待补实验动作：写清输入材料、1-2个动作、输出物和通过/失败标准。`；3）`feishu_topic_decision_card.py send --strict-run-id` 在空候选时仍发送空卡，需要默认阻断或显式 `--allow-empty`；4）扩大跑 `scripts/test_content_sampler_recovery.py` 出现 1 个失败，需要明确是旧断言更新还是业务兼容风险；5）`near_miss_count=0` 仍需 PM/用户抽样复核，不能作为无漏选充分证明。
- 窄返修开发回传：dev 已提交并 push `a34ca84 fix: harden AR-020C replay and card guards`。修复内容包括 real Skill replay progress/error/timeout artifacts、observe/supplement 占位标题 warning、`send --strict-run-id` 空卡默认阻断并需显式 `--allow-empty`、以及 `推荐动作=生成脚本包` + `title_permission=内部测试标题` 不再作为可见可生成候选的 recovery 兼容语义。开发测试：42 tests OK、62 tests OK、py_compile OK、`git diff --check` OK、deterministic artifact probe OK、`pre_merge_check.py` OK；未写生产、未发卡、未触发采集/06、未同步全局 Skill。
- QA Recheck 回传：测试线程验证 dev `a34ca84`，结论为 `Blocked`。窄修点基本通过：失败 artifact 可诊断、observe/supplement 占位标题 warning 有覆盖、strict-run-id 空卡默认阻断、content sampler recovery 兼容测试恢复。但 2026-07-01+ full real Skill replay 仍在 240 秒 timeout，输出 `/private/tmp/ar020c_recheck_skill_replay_qa_20260708`，summary 为 `ok=false`、`stage=real_skill_replay`、`error_type=TimeoutExpired`、`content_items=327`、`candidate_count=47`、`pre_skill_pool_count=19`、`writes_feishu=false`。小候选池 replay `/private/tmp/ar020c_recheck_skill_replay_qa_small_20260708` 成功，只能作为辅助样例，不足以替代 full replay。
- Full replay strategy 开发回传：dev 已提交并 push `7251add fix: add AR-020C replay batching`。`topic_skill_replay_evaluation.py` 支持 `--batch-size`、`--batch-timeout-seconds`、`--resume`、`--aggregate-only`；每批写 `batches/batch_*/input.csv`、`skill_rows.csv`、`meta.json`，失败写 `error.json`；输出 `skill_replay_batches.json` 和 append history 版 `skill_replay_progress.csv`；聚合继续生成 `skill_replay_summary.json`、`skill_replay_rows.csv`、`skill_actionable.csv`、`skill_observe.csv`、`near_miss_high_fit_unselected.csv`、`title_body_check.csv`、`ar020c_user_sample_summary.md`。
- 开发验证：deterministic batch probe `/private/tmp/ar020c_batch_strategy_deterministic_probe` 成功；real Skill 小规模分批 probe `/private/tmp/ar020c_batch_strategy_real_skill_probe_escalated` 成功，`batch_size=1`、`batch_timeout_seconds=180`、`skill_rows=1`、`actionable_count=1`、`writes_feishu=false`；同目录 `--aggregate-only` 验证通过。测试：`test_topic_skill_replay_observability.py` 3 tests OK；扩展集 64 tests OK；py_compile、`git diff --check`、`pre_merge_check.py` 通过。该 probe 只证明 batch/artifact/aggregate 路径可跑通，不作为内容质量证明。
- Batched full replay QA 回传：测试线程验证 dev `7251add`，结论为 `QA Failed / Rework Needed`。full replay 可完成性已解决：`/private/tmp/ar020c_batched_full_replay_qa_20260708` 中 7/7 batches 成功，`content_items=327`、`candidate_count=47`、`pre_skill_pool_count=19`、`skill_rows=19`、`actionable_count=3`、`observe_count=15`、`rejected_count=1`、`fallback_row_count=0`、`writes_feishu=false`；`--resume` 与 `--aggregate-only` 验证通过。
- QA 阻断点：PM-facing artifacts 自洽失败。`skill_replay_rows.csv` 中 4 行 `title_quality_status=fail` / `field_contract_status=fail`，但 `skill_replay_summary.json` 报 `contract_failure_count=0`、`title_quality_failure_count=0`，`title_body_check.csv` 也全部 `pass`。这会误导 PM 以为全量样例无标题/合约失败，因此不能进入 PM 验收。另需修正文案：暂存观察行的批量标题风险不应写成“生成脚本包标题”。
- Aggregate consistency 开发回传：dev 已提交并 push `8422985 fix: align AR-020C replay aggregate counts`。使用 QA 现有 out-dir `/private/tmp/ar020c_batched_full_replay_qa_20260708` 与原 9 个 production read-only `content_items.csv` 执行 `--aggregate-only` 复算成功，不重跑 7 个 real Skill batches。复算后 `skill_replay_summary.json` 为 `ok=true`、`completed=true`、`stage=aggregate_success`、`quality_gate_ok=false`、`content_items=327`、`candidate_count=47`、`pre_skill_pool_count=19`、`skill_rows=19`、`actionable_count=3`、`observe_count=15`、`rejected_count=1`、`contract_failure_count=4`、`title_quality_failure_count=4`、`fallback_row_count=0`、`writes_feishu=false`。反查显示 `skill_replay_rows.csv`、`title_body_check.csv`、`skill_contract_failures.csv` 均为 4 条 fail，误导短语 `生成脚本包标题...` 命中 0。
- Aggregate consistency QA 回传：测试线程验证 dev `8422985`，结论为 `Aggregate Consistency QA Passed / Content Quality Gate Failed / Waiting PM Content Review`。`skill_replay_summary.json`、`skill_replay_rows.csv`、`title_body_check.csv`、`skill_contract_failures.csv` 计数已自洽，`quality_gate_ok=false` 语义清楚；4 个失败项分别为 Codex Word->PPT、Agent 有用性、企业第一个 AI 场景、Claude Cowork，均为暂存观察行，失败原因是标题里测试/验证/能不能类骨架占比过高。
- PM 内容复核：3 条 `生成脚本包` 候选均来自有效对标账号核心源，AI Hot 未进入可行动项，方向明显优于 AR-020 早期版本；但标题表达仍未达到用户原始需求。当前可生成标题里 `验收` 出现 2/3，观察层也继续集中在“先/测/会不会/验收”骨架。即使 4 个 fail 被挡在观察层，用户仍会在样例包/Topic Card 的观察池看到这类表达，不能把它视为可接受完成。
- 当前动作：PM 派开发线程做内容层窄返修：保留两段式主编判断和 guardrails，不放松质量门；重点修标题表达多样性、观察/补证据标题口径、`测试/验证/能不能/验收` 词族过度使用，以及 title thinking 如何自然解释标题选择。修复后需重新跑 batched real Skill replay 或至少跑足以覆盖受影响批次的真实 Skill replay，并由 QA 复测 full sample package。
- 内容质量返修开发回传：dev 已提交并 push `aa5c531 fix: improve AR-020C title expression`。本轮没有放松质量门，而是把标题表达约束从“单词禁用”调整为“模板骨架 / 同构反思壳”风险，并强化 repo mirror Skill 与 runner prompt：`选题命题 / 我的选题标题 / 可发布标题` 都是用户可见判断句，不是内部实验任务名；观察/补证据候选也要给可读的证据缺口摘要，避免 `先测/会不会/给我的提醒/我会把 X 翻译成` 这类同构壳。
- 开发 replay 证据：新的 full replay 输出目录为 `/private/tmp/ar020c_content_quality_full_replay_round2_20260709`。当前已完成 6/7 batches，最后 `batch_006` 因 `codex exec` usage limit 被外部额度拦截，未完成全量闭环。对 6 个成功 batch 做 `--aggregate-only` 后：`quality_gate_ok=true`、`contract_failure_count=0`、`title_quality_failure_count=0`、`title_quality_warning_count=2`、`fallback_row_count=0`、`writes_feishu=false`。这只能说明 18/19 rows 的部分质量门通过，不能作为 AR-020C full content QA 通过。
- 开发样例改善：可生成标题示例包括 `我做选题台后才发现，知识库最值钱的不是存资料，是留下为什么选它`、`我不想要会生成PPT的Codex，我要它接住一份可交付方案`、`Agent落地后，真正值钱的是那张做完事还能追责的任务记录`；观察层示例包括 `CI/CD Shell 有发布边界启发，但还缺我的自动化失败样例`、`MIRA 的实时世界模型有导演工作流启发，但商业交付还卡在可控镜头证据`。这些是正向信号，但需 QA resume 完整 replay 后再判断。
- 开发测试：窄集 22 tests OK；扩展集 68 tests OK；改动脚本 py_compile OK；`git diff --check` OK；`pre_merge_check.py` OK，Topic Card guard 为 `check_only=true` 且未发卡。边界：未写生产 Feishu、未发卡、未触发采集/06/Codex、未同步全局私有 Skill、未部署 SCF/runtime。
- 下一步：PM 派测试线程从 `/private/tmp/ar020c_content_quality_full_replay_round2_20260709` 使用 `--resume` 完成最后 batch，并复核 full 7/7 aggregate、`quality_gate_ok`、title/body check、sample summary、actionable/observe 样例和生产边界。若仍被 usage limit 拦截，状态应为 `Blocked / Usage Limit`，不得用 6/7 partial replay 作为内容质量通过证据。
- QA resume 回传：新测试线程 `019f4714-3f76-7bb1-b71f-08a41d9f8860` 已完成 dev `aa5c531 fix: improve AR-020C title expression` 的 full replay 复测，建议状态为 `QA Passed / Content Review Ready / Waiting PM Original-Requirement Review`，但明确不建议直接标 `PM Accepted`。输出目录 `/private/tmp/ar020c_content_quality_full_replay_round2_20260709` 已完成 7/7 real Skill replay batches，`completed_batch_count=7`、`failed_batch_count=0`，并执行 `--aggregate-only` 复算。
- QA full replay 证据：`skill_replay_summary.json` 显示 `ok=true`、`completed=true`、`stage=aggregate_success`、`quality_gate_ok=true`、`content_items=327`、`candidate_count=47`、`pre_skill_pool_count=19`、`skill_rows=19`、`actionable_count=7`、`observe_count=12`、`rejected_count=0`、`contract_failure_count=0`、`fallback_row_count=0`、`reverse_flags=0`、`near_miss_count=0`、`title_quality_failure_count=0`、`title_quality_warning_count=2`、`writes_feishu=false`。`skill_contract_failures.csv`、`skill_fallback_rows.csv`、`near_miss_high_fit_unselected.csv` 均为 0 rows。
- QA 内容样例：7 条 `生成脚本包` 候选中，核心样例包括 Codex+Obsidian 转成信息雷达复盘资产链路，发布标题 `我做选题台后才发现，知识库最值钱的不是存资料，是留下为什么选它`；多宫格故事板转成成片返修流程，发布标题 `分镜工具再省事，过不了成片返修就还没进交付`；Codex 可编辑 PPT 转成客户方案交付链路，发布标题 `我不想要会生成PPT的Codex，我要它接住一份可交付方案`；Agent 能力转成飞书执行台状态/追责记录，发布标题 `Agent落地后，真正值钱的是那张做完事还能追责的任务记录`；AI 音乐二创短片转成分镜验收流程，发布标题 `AI视频最难的不是画面变美，是把情绪和分镜交付出来`。
- PM 原始需求初审：本轮已明显优于 AR-020 早期版本，关键改善是对标账号内容进入主判断层，AI Hot 只在重大且能映射到 Austin 真实工作流时进入可生成，候选有 `主编判断摘要` 与 `标题思路`，标题不再大面积复用 `先测 / 会不会 / 验收 / 测试 / 验证 / 冒号反思壳`。但 PM 不直接标 `PM Accepted`，原因是用户明确要求看实际效果；需要把 `/private/tmp/ar020c_content_quality_full_replay_round2_20260709/ar020c_user_sample_summary.md`、`skill_actionable.csv` 和 2 条 observe warning 给用户做内容体感审阅。
- 残余风险：2 条 warning 均在 `暂存观察` 层，不会进入 `生成脚本包`，但标题仍偏内部任务口吻：`Agent Runtime这个词先放进我的任务运行时边界表里看`、`低数字化业务接 AI 之前，我先补一张任务台账和验收表`。PM 后续给用户汇报时必须把这两条作为观察层表达风险说明，不能隐藏在 `quality_gate_ok=true` 后面。
- 用户内容审阅反馈：用户认为这一轮“很不错”，并指出样例摘要里 `knowledge_base`、`ai_director` 等内部分类和 `|` 后原始标题/原始 caption 展示容易混淆。PM 解释后，用户进一步指出原始标题本身起得很好，建议系统可以模仿原始标题的表达能力。
- PM 判断：这是一个小范围标题表达增强，不推翻 AR-020C 主编判断逻辑。原始标题/对标账号标题是市场验证过的表达资产，不能被系统完全洗掉；但也不能直接照抄。应提取原始标题中的工具组合、结果承诺、场景词、学习承诺或冲突钩子，融合成 `原始标题钩子 + Austin 判断` 的可发布标题。例如 `Codex联动Obsidian，搭建超强知识库，手把手教程` 可以转成 `Codex+Obsidian搭知识库，最值钱的是留下为什么选它` 或 `Codex联动Obsidian，我想搭的不是知识库，是选题台的长期记忆`。
- 派发内容：PM 已准备并派发开发线程执行 `AR-020C Title Hook Rework - borrow original title hooks without copying`。范围限定为标题钩子借鉴与样例摘要展示 polish：新增/明确 `原始标题钩子`、`Austin改写理由`，清理样例摘要中原始 caption 脏文本展示；不重做主编逻辑、不改生产、不发卡、不采集、不触发 06、不同步 global Skill。完成后仍需 QA/PM 看样例，不直接 PM Accepted。
- 开发回传：dev 已提交并 push `c0dafe5 fix: borrow original title hooks in AR-020C`。改动集中在 repo mirror Skill、`editorial_skill_runner.py`、`topic_skill_replay_evaluation.py` 和对应测试：将原始标题作为标题钩子参考，输出 `原始标题钩子`、`Austin改写理由`；样例摘要改为中文内部分类，拆出原始标题/原始来源摘录并清洗长 caption。开发 aggregate-only 复算证明展示路径有效，但没有重新跑 fresh full real Skill replay，因此不能据此证明新 prompt 已影响真实标题生成。
- QA 下一步：独立验证 `c0dafe5`，必须以新的 out-dir 跑 2026-07-01+ full batched real Skill replay，确认新生成 rows 的 `原始标题钩子` / `Austin改写理由` 真实存在；抽查可发布标题是否保留原始市场入口但不照抄、是否仍符合 Austin 人设；检查样例摘要不再把内部 key、长 caption 或截断脏文本伪装成标题。QA 未通过前不进入 PM Accepted。
- QA fresh replay 回传：测试线程完成 `c0dafe5` 的 fresh 真实分批 replay，输出 `/private/tmp/ar020c_title_hook_fresh_replay_qa_20260710`，7/7 batches 成功、0 failed、`fallback_row_count=0`、`writes_feishu=false`，但 `quality_gate_ok=false`，`contract_failure_count=3`、`title_quality_failure_count=3`、`title_quality_warning_count=1`。结论 `QA Failed / Content Rework Needed`，不能进入 PM Accepted。
- PM 复核：原始标题钩子确实进入 19/19 条 fresh Skill rows，且 Codex+Obsidian、企业 AI 场景选择、AI 视频导演三条可生成标题没有照抄，方向正向；但多宫格故事板、Codex PPT、Claude Cowork 三条仍把 `能不能 / 验收 / 我想看的是` 等内部判断壳写进 `选题标题`，即使它们被降级为观察，也会进入用户可见样例/后续候选层，不能以“没有可发布标题”为由放过。
- 额外一致性问题：`skill_replay_batches.json` 的 `batch_005` note 写 Claude Cowork 为“今日最值得做”，最终结构化 row 却为 `暂存观察`；`engine_meta` 的“未调用外部 Skill”表述也容易与 real Skill replay 事实冲突。后续必须让 batch note 以最终 guard 后 rows 为准，或明确它只是 provisional，不得与用户可读状态冲突。
- PM 返修要求：不放松 quality gate。开发应重写观察/补证据命题为“原始标题钩子 + 业务矛盾/证据缺口”的自然句，保留 `验收/返修/交付` 作为领域事实但不构成 `能不能/我想看的是/先放进...` 内部任务壳；修复 batch note 与最终 rows 一致性；让用户样例包覆盖知识库、故事板、Codex PPT、Agent/飞书执行台、AI视频、AI Hot/观察六类样例。修后交 QA fresh replay 复测。
- 开发返修回传：dev 已提交并 push `7837bf8 fix: harden AR-020C observe title evidence`。变更强化观察/补证据候选的公开表达约束、扩展 task/reflection shell 检测、让 `batch_notes` 反映 final guard state、保留模型原始 note 为 `pre_guard_batch_notes`、修正执行语义说明，并将样例包扩至六类。相关 73 tests OK、py_compile、`git diff --check`、`pre_merge_check.py` 均通过。
- 开发 fresh replay 尝试未形成内容结论：`/private/tmp/ar020c_title_hook_content_rework_20260710_escalated` 在 `real_skill_replay_batches` 阶段因 Codex runtime/backend 无 completed batch outputs 而失败，已落 error/progress artifacts；这不是质量门通过或失败证据。QA 必须以新的 out-dir 重跑 fresh full batched replay，允许 `--resume` 保留已成功批次；QA 完成前不进入 PM Accepted。
- QA fresh replay 回传：测试线程基于 `7837bf8` 完成独立 fresh full replay，目录 `/private/tmp/ar020c_title_hook_content_qa_20260710`，7/7 batches 成功、0 failed、19 fresh rows、`fallback_row_count=0`、`writes_feishu=false`；batch final-state notes 与最终 rows 全部匹配，六类用户样例覆盖完成。但 `quality_gate_ok=false`，`contract_failure_count=3`、`title_quality_failure_count=3`、`title_quality_warning_count=1`，结论 `QA Failed / Content Rework Needed`。
- PM 复核拆分：故事板 `多宫格故事板的“一键成片”，要放进我的分镜返修流程里才算数` 与 Claude `Claude Cowork 的入口很热，但我更想把它改成内容团队的协作验收链路` 仍是用户可见内部改造壳，属于真实内容返修；MIRA 的任务口吻为 warning，也应重写。Agent `Agent真正有用的能力，是做完事以后留下可验收记录` 本身是自然前台判断，但因 `标题思路`/`验证方式` 中的 `能不能/验收` 工作语言被批量标题扫描带入 fail，属于 guard 扫描面的误伤。
- PM 返修口径：不降低质量门，不把观察层排除出审查。将 hard title/task-shell guard 限定在用户可见 `可发布标题 / 选题命题 / 选题标题` 表面；`我要做的实验`、`验证方式`、`标题思路` 可以做单独的非阻断表达审计，不能因正常实验语言把自然标题降级。重写故事板、Claude、MIRA 的前台命题，保留原始标题钩子和业务矛盾，不再使用 `要放进...才算数 / 我更想把它改成 / 先从...开始` 等内部工单壳。修后必须 fresh full replay。
- 轮次复盘：AR-020 原始实现阶段已在三轮 QA 后停止并转架构评审；AR-020C 架构方案确认后，至 `7837bf8` 已发生 7 次 QA 回传/复测尝试（其中包含 visible-field QA、full replay blocked、artifact consistency、内容质量门和 title hook/content QA）。尽管其中并非每次都是同一种内容失败，但它们都消耗了同一方案的 QA 反馈回路；PM 过去按技术子问题拆分处理，事实上绕开了用户“最多三轮测试”的边界。
- 用户流程纠正：用户要求开发在交测试前先确定改好，避免反复占用 QA/token。PM 已更新项目规则：开发须提交失败项一一对应的 fresh 自验包，PM 审核后才可派 QA；同一方案的 `Failed/Blocked/Partial/Artifact Inconsistent` 都计入三轮上限。当前不自动派发下一次开发/测试，先由 PM 向用户报告轮次与停线决定。
- 用户决定：用户明确要求“给开发提要求，让他确定改好了再交测试”。这授权一次最终开发自验收敛，不重置 AR-020C 的 QA 计数，也不自动承诺后续 QA。PM 状态改为 `Final Dev Self-Validation Dispatching`：开发只能在 fresh full replay `quality_gate_ok=true`、四条失败/误伤样例对照完成、batch final-state notes/六类样例包一致后回传 PM；PM 审核自验包后才决定是否占用一次最终独立 QA。

### AR-020D 人格化选题主编架构重构

- 类型：主编 Skill 运行时架构 / 人格风格参考 / 选题与标题生成链路
- 优先级：P1
- 状态：QA Round 3/3 Failed / Stop / Needs Product Decision
- 用户确认：案例库只用于 Austin 的人格、判断习惯和表达风格参考，不得作为选题事实来源、证据库或逐条候选的案例锚点。用户确认采用“完整人格/风格参考真实加载 -> 自由主编判断 -> 受约束字段映射”新架构；该架构与 AR-020C 标题补丁不同，QA 计数从新方案 `0/3` 开始。
- 核心规则：第一阶段只接收来源事实、原始标题及钩子、完整人格/风格参考；只输出选/不选、自然切入、拒绝的俗套角度、标题方向和最终标题。不得接收/生成旧 04 主字段、实验、验证、资产、母场景结论或 deterministic 标题 hint。第二阶段只能根据锁定的主编判断映射实验、验证、资产和 Feishu 字段，不能发明或覆盖标题/切入。
- 运行时/发布规则：测试必须显式使用隔离 test private Skill 并记录 Skill 与人格/风格参考 path/hash/embedded 状态；生产 global private Skill 只在 QA、用户样例审阅和发布计划均通过后由生产线程同步，并 read-back hash。repo mirror 是受版本控制的发布源，Skill 更新必须同步对应 Git 源仓库。
- 自验/QA：开发先提交结构自验包，包含实际 payload 分层、provenance、第一/二阶段角度不变量、六类样例 fresh full replay、无 fallback/无生产写入及测试；PM 审核后才派新方案第 1 次 QA。禁词、报告改写、field guard 放松、单样例修复不能作为通过依据。
- 开发自验回传：隔离 worktree 提交并 push `53d5fb7 feat: add AR-020D editorial decision architecture`。fresh replay 7/7、19 rows、persona/style 32KB 全文嵌入测试 Skill、0 fallback、0 Feishu write；Stage 1 payload 不含旧字段/实验/mother-scene hints，标题/角度/理由与 Stage 2 raw mapping 一致。样例标题较 AR-020C 自然。
- PM 独立证据复核：不接受 `Dev Architecture Self-Acceptance Passed`。最终 19 rows 中 Stage 1 选择/推荐状态仍可被 Stage 2/normalize 改写，例如何止维 AI 视频从 `select/生成脚本包` 变为 `暂存观察`，FDE 从 `observe/存素材` 变为 `暂存观察/补证据`；现有 invariant 未锁 `decision/recommendation_status`。另外 `今日最值得做` 上限只在单 batch 内执行，完整聚合出现 6 条，违反全局最多 3 条规则。
- 当前返修：开发留在自验阶段，不占 QA 槽。必须增加 Stage 1 canonical selection 锁、全候选 global editorial ranking（在 field mapping 前完成）、最终 normalize/guard 后再验 selection/action/title/angle/rationale 不变量；新 fresh full replay 要求全局 `今日最值得做<=3`、0 silent drift/guard downgrade、0 fallback、0 production write，再回 PM 复核。
- 开发返修回传：`0fbc386 fix: enforce AR-020D global selection locks` 已 push。新的 fresh replay `/private/tmp/ar020d_global_rank_self_validation_20260710_r6` 完成 7/7 Stage 1 batches、全日 global ranking、7/7 Stage 2 batches；19 rows，global/final `今日最值得做=3`，`stage2_selection_drift_count=0`、`guard_blocked_count=0`、0 fallback、0 writes。新增 global ranking input/output/ranked decisions、rank hash/id 和 final trace。
- PM 独立证据复核：通过开发自验门，允许派 AR-020D 第 1 次 QA。PM 只读核对 global ranking 真实输出 19 行、全局前三为 Codex+Obsidian 信息判断留痕、FDE 业务现场翻译、何止维 AI视频导演判断；前三均来自有效对标账号核心源。其余高适配项如 Codex PPT、Mx-Shell Skill、Agent能力、企业第一个AI场景、Claude Cowork 均有全局取舍理由；final trace 的 global/final level/action/title/angle/summary 一致，case evidence 字段为空。
- QA Round 1/3：已向固定 QA 线程派发一次性完整验证，禁止拆成多轮小测试。任务包含 L0 结构对抗、L1 回归、独立新目录 full real Skill replay、全局排序/不变量审计、原始需求内容复核，以及 L2 通过后在专用测试 04 + 个人测试目标执行 strict-run-id 真卡可见验证；不点击提交、不触发 06、不写生产、不同步 global production Skill。
- QA Round 1 结论：`QA Failed / Architecture Control Rework Needed`。L0 对抗探针证明 global ranking 不是严格一一对应：缺行会静默默认成可选候选，重复行会被后写覆盖；同时 raw Stage 2 对标题、动作、等级、公开摘要和标题思路的越权改写可在 normalization/reapply 后被洗回 invariant pass，未形成 guard block。L0 已失败，因此 QA 正确停止，没有运行 L2 fresh replay 或 L3 测试卡。
- 当前返修门：开发必须先增加 ranking bijection 硬校验和 raw Stage 2 drift preservation/blocker，并把 missing/duplicate/unknown/hash mismatch/tradeoff missing、visible summary/title/action/rank drift 等最小反例固化为测试。只有这些对抗测试和新的 7/7 fresh real Skill 自验全部通过，PM 复核后才允许启动 QA Round 2/3；不得用 normalization 重写、报告美化或标题 guard 兜底冒充修复。
- 开发返修当前状态：本地 82 tests、py_compile、diff check、pre-merge 均通过。用户已明确授权 replay 且授权记录有效，但平台安全审查仍以不可覆盖的私有组织数据外发规则拒绝 sandbox 外 Codex 执行，并禁止 workaround；因此 fresh 7/7 仍未运行。状态为 `Blocked / Needs Trusted Skill Replay Environment`，不再向用户重复请求授权。PM 已派不调用模型的离线架构实证：用既有 r6 真实 Skill artifacts 验证新 bijection/raw drift 门，并重放 QA 反例；该证据不替代 fresh replay，也不启动 QA Round 2。
- 离线架构实证：`/private/tmp/ar020d_arch_control_offline_validation_20260711` 已通过。旧 r6 真实 ranking 在可验证字段上 19/19 coverage、无重复/未知/mismatch、select tradeoff 完整、top=3；因旧产物缺新 `input_global_rank_hash`，严格新 validator 明确 fail，未补造通过。旧 Stage 2 raw 19/19 都包含新 contract 禁止的 owner-field authoring，新门禁在 normalize/reapply 后仍全部保持 invariant fail + guard_blocked；排名和 Stage2 注入反例全部按预期 fail。该结论仅为 `Offline Architecture Evidence Passed`，AR 仍 blocked。
- 用户确认运行架构修复：移除 Python 内 nested `codex exec`，改为 current Codex task / future outer Codex automation 直接执行 `Stage1 Editorial Decision -> Global Daily Ranking -> Stage2 Operational Mapping`；Python 仅负责 allowlisted input、阶段状态、hash/ownership validator、resume 和 artifacts。开发已派发到 isolated AR-020D worktree；继续保留 strict bijection/raw drift 门。开发线程须用当前任务本身完成新的 7/7 real Skill 自验，禁止第二模型会话/API/subagent；通过并经 PM 复核前不启动 QA Round 2/3。
- 开发提交 `662596e feat: add in-thread editorial state machine` 已 push；current-task 自验报告显示 7/7 Stage1、19/19 ranking、7/7 Stage2、quality gate pass、top=3、0 fallback/write/drift/guard/contract fail，样例方向正向。
- PM 证据复核未放行：fresh replay 使用的隔离 test Skill hash `31a0cd...` 与最终 repo mirror hash `8bc4cb...` 不一致，test Skill 缺最终 current-task protocol；同时 active `config/system_rules.yaml`、`docs/schedule_local.md` 仍把 nested/legacy CLI 描述为正式入口，`editorial_skill_runner.py --engine codex` 仍是默认帮助口径但实际会硬失败。已退回开发做 final-Skill hash 等价 fresh replay、active rules/docs/CLI migration closure；QA Round 2 未启动。
- Evidence closure：`1497cf8 fix: close editorial state machine runtime contract` 已 push。新目录 `/private/tmp/ar020d_current_task_evidence_closure_20260711` 使用 final Git Skill 的等价隔离副本，repo/test Skill SHA256 均为 `8bc4cb63...`；完整 persona/style reference 独立 hash、embedded=true、reference-only=true。Stage1 7/7、global ranking 19/19 strict bijection/top=3、Stage2 7/7、finalize completed，0 fallback/write/raw drift/selection drift/guard/contract/title failure。active config/docs 已迁移到 current-task state machine，旧 one-shot/nested Codex CLI 在业务 I/O 前 fail-fast。
- PM evidence review：通过进入 QA 的门，但不等于需求通过。PM 只读复核 final Skill hash、provenance、19 行 final trace、Top 3 来源与 5 条 warning；Top 3 全来自有效对标账号，AI Hot top=0，案例证据/锚点字段为空，5 条 warning 均为 `不做 / 不生成标题` 的内部占位字段。PM 侧 58 项针对性测试通过。已派固定 QA v2 执行 AR-020D Round 2/3 的 L0-L3 一次性验证。
- QA Round 2/3：独立 fresh current-task replay 与 staging/test visible flow 全通过。新证据目录 `/private/tmp/ar020d_qa_round2_current_task_20260711/evidence`；Stage1 7/7、ranking 19/19 strict bijection/top=3、Stage2 7/7、0 fallback/write/drift/guard/contract/title failure；112 tests passed。专用测试 04 写入 9 条，Topic Card 仅 3 条 global Top 进入 `生成脚本包` 选项，其余 6 条明确为补证据/观察；未点击提交、未触发 06，production marker=0。
- PM 原始需求复核：通过。PM 逐条复核 19 行、Top 3、7 条高适配非 Top、AI Hot gate、case evidence 字段与 Feishu Web 截图。最终 Top 3 分别为 Codex+Obsidian 选题判断长期记忆、Codex PPT 从 Word Brief 到可交付方案、AI 视频评论故事到可返修分镜；均借用来源钩子但未照抄或回到工具教程。非 Top 有具体证据缺口和全局取舍，案例库未作为选题证据。残余 watch：少量非 Top 仍用 `不是/而是` 或 `真正` 对比句式，但未进入直接生成 Top 3，不占用第 3 轮 QA。当前等待用户查看样例，不自动进入 RC/发布。
- 用户证据复核失败：用户指出 Storyboard 被无来源依据地强行翻成“返修”，没有说明为何用户应关注返修；Mx-Shell 样例误把作者/Skill 产品名当传播钩子，删除了真正的爆款入口《丧尸清道夫》；当前链路也没有用户此前要求的“打开原始来源并全网搜索相关信息后再判断”。Topic Card 虽字段很多，却没有展示可点击的具体原始文章/视频、完整原始标题、建议角度和内容结构，来源只显示作者/来源类型，决策价值低。PM 撤回上一条“原始需求复核通过”结论，不进入 RC，不派 Round 3，不下发开发；先做产品/架构方案确认。
- 已确认结构根因：Stage 1 只接收原始标题、短摘录和预提取 hook，明确排除来源链接；协议中不存在 source-open/web research 阶段。Skill 文档与示例高频锚定 `返修/验收`，使 AI导演偏好压过来源事实。`feishu_topic_decision_card.py` 虽写入 `原始来源标题/来源链接`，卡片展示只读取 `来源构成/来源权重类型`。下一方案需新增 research dossier + market-hook evidence，并把卡片从 debug/operational 字段堆叠改为来源与选题决策优先。
- Persona/Skill 深审：用户原始 `/00_资料库/04_案例库/我的案例库.docx` 从真实业务现场、案例叙述、喜欢/讨厌表达和 5 个“不要这样/应该这样”样例展开，包含 `爆肝三天`、`人堆人/会堆会/稿堆稿` 等具体活人表达。运行时 `persona-and-cases.md` 在原文前新增了“每条必须连接至少一个案例/母场景”的系统整理段，`persona-brief.md` 又把它压成五个母场景和高频固定矛盾，repo Skill 再重复同类示例。QA 运行上下文合计出现 `流程=97`、`交付=49`、`验收=40`、`返修=22`、`不是=127`、`真正=24`；`reference-only` 标签不能抵消正文的强指令，模型因此学到固定词和句壳，而非用户的观察方式。
- Persona 重构方向：active Stage 1 不再直接使用当前 AI 压缩版 mother-scene brief 或带系统前缀的 case file。应从原始 Word 拆成 `persona facts`、`raw style/judgment examples`、`experience archive`：前两者用于学习用户如何观察、取舍和表达；experience archive 不作为候选证据，不自动把题拉进已有案例。任何标题/角度里的强内容名词必须追溯到 source/research evidence，不能只因 persona 出现过就注入。
- 数量规则变更：用户撤销“每日最高 3 个 Top”。全局 ranking 仍用于排序和比较，但不得做数量截断；所有通过主编质量门的 `select/推荐制作` 候选都应进入当天卡片，数量可为 0..N。卡片超长时只允许分页/分卡，不允许编辑层截断。`最多3条` 目前硬编码于 Skill、runner validator、quality gate、config/docs/tests，后续方案必须一并移除。
- 用户确认方案：采用 `Research-grounded Editorial Director + Persona-native Topic Card` 方向。先做不计 QA 轮次的联合架构评审，不直接实现。开发评审须交 Persona 三层拆分、source-open/web research dossier、传播钩子证据、dynamic 0..N ranking、decision-first card mock、实现/迁移/Skill sync 范围；QA 评审须交对抗架构审查、Persona 泄漏测试、搜索质量门、0/1/3/7/12 动态数量和最终 Round 3 L0-L3 验收设计。两边只写 `/private/tmp` 评审产物，不改 repo/Feishu/生产。
- 联合架构评审结论：开发与 QA 独立结论一致。新链路必须是 `候选初筛 -> 打开具体来源 -> 全网研究 dossier -> 证据化传播钩子 -> Persona-native 主编判断 -> 0..N 排序 -> operational mapping -> decision-first 分页卡片`。原始 Word 是 Persona 权威源，但要拆成常驻事实、按判断动作检索的原始风格/判断样例、默认不进入候选上下文的经历档案；不得继续使用五母场景和固定句式作为 active selection instruction。
- PM 默认方案：在 04 物理新增且只新增 `研究摘要 / 受众钩子 / 内容结构` 三个用户可读字段，详细 evidence IDs/query ledger 留本地 dossier/audit；新闻/事件缓存 24h、产品更新 72h、常青内容 7d，来源内容 hash、实体或关键主张变化时立即失效；exact source 未打开或只有搜索摘要时只能 `补证据/观察`，不得 `推荐制作`；每页 5 条，多页消息共享一个 run manifest，页面动作只影响显式选择，未查看/未选择不得写成 `不做`。
- 最终 QA 门：开发先在 fresh evidence 中完成全部 shortlisted rows 的 exact-source-open + search、Persona 反事实隔离、0/1/3/7/12 无损分页和 staging/test 可见自验，经 PM 证据复核后才启动唯一剩余的 Round 3/3。Round 3 任一架构、研究、provenance、内容或分页动作失败即停止，无 Round 4。
- 用户最终确认：本次已构成 research/persona/card 的实质性重构，旧 AR-020D QA Round 1/2 归档，新实现 QA 从 `0/3` 重新计数。开发仍必须先完成 fresh 自验和 PM 证据复核，不能把新增轮次当作开发试错资源。
- 零 fallback 合同：旧 Skill、旧 persona 压缩层、Top3、deterministic/legacy engine、标题/摘要替代原文、研究失败后继续生成等 active fallback 必须删除，不得只降级或保留开关。exact source/研究/Skill/schema/hash 任一失败时对应候选 fail closed，不生成标题、不推荐、不进卡片；同批其余成功候选可继续，但 run 必须显式非全成功。回滚只依赖 Git/versioned artifact，不在 runtime 留旧实现备份。
- 开发自验 r1：`/private/tmp/ar020d_research_grounded_dev_self_validation_20260711_r1` 正确失败并留在开发，未 commit/push、QA 仍 0/3。19 条 shortlist 中 14 条为 Douyin；通用 trusted web surface 拒绝精确 Douyin video URL，开发遵守零 fallback，没有用 CSV/搜索摘要继续。另有旧 Top3/未选即不做回归尚待迁移。
- PM source-open 决策：使用项目既有专用 Douyin Chrome profile + 本地 CDP `http://127.0.0.1:9333` 作为精确单视频的受信只读入口，由 `start_douyin_cdp_chrome.py` 启动/复用；新增 exact-video opener 必须逐条打开真实 `/video/<id>` 页面，保存 canonical URL、页面 metadata/正文或字幕、content hash、截图和访问状态。不得退回主页采样、第三方聚合 API、纯 HTTP resolver 或旧采集 payload 顶替。登录/验证/正文不足时对应候选显式失败。
- 开发自验 r2：`/private/tmp/ar020d_research_grounded_dev_self_validation_20260712_r2` 已证明 Douyin CDP exact-source、16 条完整证据候选、0..N ranking、两页 10 条 staging card、118 Python + 5 Douyin Node + 28 receiver/SCF tests，但三条非 Douyin exact source 失败，整体正确标为 `completed_with_failures / ok=false`，未 commit/push、QA 仍 0/3。失败 URL 为两条 X status 与 Claude Cowork 官方页。
- PM 非 Douyin 通道决策：source-open 前按域名声明唯一主 adapter，不做失败后降级。`douyin.com` 固定 Douyin CDP；`x.com` 与 `claude.com` 等动态/受限页面固定 current-task trusted browser/Chrome exact-page adapter；普通静态文章固定 standard web-open adapter。adapter 名称、版本、session/profile 边界与页面证据必须进入 provenance。X/Claude 主 adapter 若仍无法读到 exact page，候选继续 fail closed；不得切换到搜索 snippet、镜像、聚合页或旧摘要。
- 开发 r3 进度：`/private/tmp/ar020d_research_grounded_dev_self_validation_20260712_r3` 已在同一 fresh run 完成 19/19 source-open，`source_open.status=completed`、`failure_count=0`。14 Douyin、3 trusted-browser、2 trusted-web 均按预声明唯一 adapter 运行并保存 DOM/截图/identity evidence；research、Stage1、dynamic ranking、Stage2、finalize 尚 pending，因此状态仍 `Development In Progress / Not Commit-Ready`，未 commit/push、QA 0/3。
- 开发提交：`c0356ca feat: ground editorial decisions in exact-source research` 已 push。r3 后半链完成 19/19 research、Stage1 7/7、0..N ranking 19/19、Stage2 7/7、10 recommended/8 observe/1 reject、0 writes/drift/contract/title failure，123 Python + 6 Douyin Node + 28 receiver/SCF tests 通过。
- PM evidence review：不通过，状态退回 `PM Evidence Review Failed / Dev Rework / QA 0/3`。阻断一：active repo 仍保留 `--engine deterministic`、`--allow-deterministic-fallback`、`fallback_after_error`、dead deterministic enrichment、fallback schema/CSV columns，与用户要求“删除而非降级保留”冲突。阻断二：Persona builder 只提取第7题五条固定 `我的思考点/重点体现`，检索 operations 为英文 hook type、样例正文为中文，实际每个候选都拿到同一五条 `我不是...而是.../返修/验收/交付` 句壳；`persona_counterfactual.json` 只有手写布尔结论，没有 paired outputs。阻断三：卡片 `研究摘要=受众钩子`、`Austin 角度` 显示栏目名而非 natural angle、Substack/Claude 原始标题为空、抖音 title/caption 未分离、分页按钮仍标 `本批都不选` 并携带 page candidate 的 `unselected_status=不做`。
- PM 返修门：彻底删除 active fallback 代码/CLI/schema/artifact 字段；从原始 Word 建立真正 verbatim、多样、按 judgment operation 变化的 style snippet pool，附 paragraph hash 和实际 paired counterfactual evidence；卡片明确分开查看原文、原始标题/原始发布文案、来源摘要、受众钩子、natural Austin angle、内容结构，并把 page-scoped no-selection 改为 `本页都不选`。必须新 out-dir fresh 19/19 全链重跑，不能 aggregate 旧 r3。
- 开发 r4 blocker：`/private/tmp/ar020d_research_grounded_dev_self_validation_20260712_r4` 已 fresh 重开 Douyin 14/14；5 条 non-Douyin exact page 已由 current-task browser 读到 exact URL/title/body，但截图接口超时，开发未复用旧图，正确停线。PM 修正过度门槛：source-open eligibility 的硬证据为 exact identity + fresh title/body/author + DOM artifact/content hash + browser provenance；截图是视觉审计附件，不是来源内容。截图失败必须写 `visual_capture_status=failed`/warning 且 path 为空，但在 DOM 硬证据完整时不阻断 source-open。L3 卡片可见验收仍必须提供实际截图。
- 开发 r4 续跑：截图合同修正后已完成 19/19 fresh source-open，并准备 19 条 research 输入；写入 fresh research specs 时被 Codex 工具额度硬中断，未形成 research/Stage1/ranking/Stage2/finalize 结论、未 commit/push、QA 仍 0/3。2026-07-12 22:54 额度窗口恢复后，PM 已要求固定开发线程从同一 r4 research stage 续跑，禁止重开或复用 r3；只有完整全链、零 fallback、Persona paired counterfactual、0..N 卡片与 staging 可见自验全部通过后才允许提交。
- 开发 r4 提交 `3e51bc1 feat: complete research-grounded editorial flow` 已 push，但 PM 独立证据复核不通过，QA 仍 0/3。结构证据的 19/19 source/research、0..N ranking、zero-fallback audit 和 paired counterfactual 目录成立；用户可见合同仍失败：14/14 Douyin 行把长 caption 写入原始标题且发布文案为空，卡片重复显示；19/19 研究摘要/受众钩子为英文且研究置信度为空；10 条推荐中 6 条仍集中于同类 `不是...是/不缺...缺` 对比句壳，Persona 检索仅有 2 种 operation profile；所谓最终 staging 截图实际显示旧 AR-020B 卡片，与当前 R4 DOM 不一致。已退回开发用新 fresh out-dir 返修，禁止 aggregate 旧 r4 或派 QA。
- 开发 r5 提交 `ae2de25 fix: close AR-020D visible evidence contracts` 已 push；PM 复核确认 title/caption 分离、中文摘要/钩子、置信度、Persona operation profile、标题句族门和当前 R5 两页截图均有实质改善，但核心 research 合同仍失败。`current_task_research_specs.json` 19/19 `results=[]`，查询全部只是 `复核精确来源：<同一URL>`，19/19 为 `no_accessible_corroboration / low`，却仍有 9 条进入推荐制作；《丧尸清道夫》还在无旁证/无 claim evidence 时使用“非职业创作者/为什么出圈”故事判断。exact source reread 不能代替全网搜索，状态继续 `PM Evidence Review Failed / Dev Rework / QA 0/3`。
- 开发 r6 进度：已新增真实外部研究资格硬门与 42 项对抗测试，exact-source-only、query-only、snippet、旧 dossier/model memory、缺 evidence ID 均不能推荐；已打开 OpenAI、Anthropic、Microsoft、AWS、McKinsey、CVPR、AP、TechRadar 等页面，正文不足/打开失败保持显式缺口。当前尚未生成新的 19 条 r6 dossier 或完成 Stage1/ranking/Stage2/card，未 commit/push。PM 已要求固定开发线程继续到完整 r6 自验，不能再以阶段性门禁测试作为完成回传。
- r6 source blocker：fresh Douyin 主通道对部分精确视频返回 `video_unavailable/visible_content_insufficient`，开发正确未用 r4/r5/CSV/snippet/model memory 替代，但错误地停止了整批后半链。PM 重新对齐已确认失败语义：同一主 adapter 仅一次有界重试；仍失败的候选显式隔离且不得进入任何下游，其余成功候选继续完整 research/Persona/Stage1/ranking/Stage2/card；整次 run 必须 `completed_with_failures / ok=false`，不是 full content success，也不是 fallback。
- r6 提交 `532bed4 fix: isolate AR-020D research failures` 已 push，candidate-level continuation、2 条失败下游隔离、11 推荐/3 页卡、外部 URL/evidence ID 和 completed_with_failures 语义成立；PM 仍不放行。11 条 `web-r6-*` 的所谓 DOM artifact 全部只有 4 行、219-356 bytes，只保存标题/发布者/URL和开发生成的一句 `Opened evidence`，没有真实页面正文/DOM或可核对原文片段；hash 只证明合成 claim 文件没变，不能证明页面支持主张。Persona audit 还把 actionable_count 写成 0，与独立标题报告的 11/27.27% 不一致。已退回 fresh raw-capture evidence 返修，QA 继续 0/3。
- r7 开发进度：`/private/tmp/ar020d_research_grounded_dev_self_validation_20260713_r7` 已按新门重新打开并保存真实外部页面 DOM、raw SHA256、页面级时间/通道、独立 supported claim 和 raw 中可匹配的 literal excerpt；16 条候选完成 research/Persona/Stage1/16 行动态排序/Stage2/finalize，3 条失效 Douyin 各尝试 2 次后完全隔离。run 正确保持 `ok=false / completed_with_failures`，survivor `quality_gate_ok=true`；9 条推荐已写专用测试 04 并向个人目标发送 2 页。当前仍缺两页 current r7 可见截图，开发未提交/未 push。PM 已续派只补截图同态证据和最终门禁，QA 保持 0/3。
- r7 提交 `0326c5e feat: enforce research-grounded editorial evidence` 已 push；PM 独立确认 16 个 dossier 中 15 个 opened external raw DOM 文件、hash 和 literal excerpt substring 全部自洽，最短正文也超过 1000 个 normalized chars，Persona actionable 统计为 9/22.22%。但 L3 不通过：`staging_r7_page1_dom_text.txt` / page2 DOM 与四张截图都显示旧 `[AR-020D R5 TEST]` 行；第一页还出现不在 r7 final/card manifest 的 Mx-Shell，Agent 行仍是旧 low-confidence/no-corroboration 文案。`staging_r7_visible_closure.json` 的 true 只证明标签/页数存在，不能证明内容身份。已退回 fresh isolated staging records + r7 final row 逐字段 read-back + strict record ID + visible snapshot hash 同态验证；QA 继续 0/3。
- r7 visible retest 提交 `43ff70e fix: verify AR-020D visible card identity` 已 push。PM 独立复核确认新 run `ar020d_r7_visible_retest2_20260713` 使用专用测试 04、9 条 create-only 新记录、strict explicit record IDs、2 页 5/4 卡片；原始 r7 actionable 到测试输入 11 个可见字段一致，截图/DOM 不含 R5/Mx-Shell，page snapshot hash 与 closure JSON 一致。仍未放行：原始 r7 Claude Cowork 行 `原始发布文案` 为空，但通用 writer 在映射时以 `来源内容` 回填为“人们如何使用Claude Cowork”；开发 validator 使用 writer 加工后的 `expected_rows`，因此把这次语义漂移验证成通过。必须删除 `原始发布文案` 的跨字段 fallback，缺失时留空/显示明确缺失提示，并让 closure 直接比较原始 r7 行 -> 04 read-back -> manifest -> DOM；修复前 QA 保持 0/3。
- r7 source-identity 提交 `11527d2 fix: preserve original publication identity` 已 push；PM 独立证据复核通过。`map_row()` 只接受同名 `原始发布文案`，卡片缺失时只显示 `平台未提供独立发布文案`；`validate_original_content_closure()` 直接从原始 r7 final rows 派生 expected semantic snapshot。新 run `ar020d_r7_source_identity_retest_20260713` 在专用测试 04 新建 9 条记录，严格 9 个 record ID 分 5/4 两页；PM 重跑原始行 -> read-back -> manifest -> DOM 得到 9/9、page hash `38a056...` / `5aa508...`。Claude 原始/04 `原始发布文案` 均为空，source semantics hash `fabb316...`；56 项针对性测试和 diff check 通过，production clean。状态推进到 `PM Evidence Review Passed / QA Round 1/3 Dispatching`，但不等于内容质量通过或 PM Accepted。
- 新架构 QA Round 1/3 回传：`QA Round 1/3 Failed / Development rework required / Round 2 blocked`。独立 mutation 证明 `validate_source_open()` 接受不存在的 `dom_text_path` 和任意 64 位 hash；r7 的 12 条 opened Douyin source 没有落盘 raw DOM/body。active path 另有四条跨字段替代：视频 caption 可退到 exact title、research summary 可退到 generic summary/public decision、原始标题可退到 legacy 来源标题。Persona counterfactual 通过人工 control merge 构造，而非独立 no-persona 执行。r7 source/research/finalize 均 partial，却同时暴露 `quality_gate_ok=true`；另有 1 条无 opened external result 的 dossier 仍 research eligible。通过项包括 15 条 external research raw hash/excerpt、9 条推荐 evidence IDs、0..N ranking、Stage2 drift、pagination 与当前 visible lineage。开发须一次性修复并完成 fresh consolidated self-validation；不做微复测，Round 2 暂停。
- Consolidated R8 / PM review：开发提交 `21e772c fix: consolidate AR-020D evidence controls`，fresh R8 19/19 exact-source raw 与 19/19 external research raw 均通过文件存在、SHA256、literal excerpt 独立重算；7 组 with/without Persona 输入只差 Persona、execution id 与 input hash 独立；Stage1/ranking/Stage2、12 条动态推荐和 5/5/2 staging DOM 闭环均成立。PM 未放行 Round 2：active PM/QA 报告仍把空 `原始来源标题` 从 `来源内容/来源标题` 回填，mutation 与 `ar020c_user_sample_summary.md` 均可复现；zero-fallback audit 只扫 engine 关键词而漏掉语义替代。`staging_r8_page1_top.png` 也不是第 1 页顶部，而是显示 MIRA/支付宝的第 3 页内容。返修必须删除 sample/progress/title-check/trace/self-acceptance 的全部来源跨字段替代，增加相应 mutation；截图闭环必须校验 page candidate count、首条 identity、run marker 与 manifest/DOM 一致。此返修属于开发证据门，不消耗 QA Round 2。
- QA Round 2/3 failure：目标 `1abc92b` 在 L0 跨语句变异门失败。顺序 assignment、嵌套 assignment 与手工 early return 可以实现同一跨 owner fallback，却未被旧审计识别；Round 2 因此停止，没有进入内容审查或 staging 流。
- Final dev/PM gate：`94e5713 fix: prove AR-020D semantic owner dataflow` 已 push。PM 独立新增顺序赋值、early return、嵌套 alias、`dict.get` default、`try/except`、NamedExpr 六类正例，全部被识别；独立 owner 分开输出的负控不误报。active static gate 为 0 violations，7/7 behavioral sentinel 通过；133 项 PM 侧聚合回归、py_compile、diff check、pre-merge（含 28 项 receiver tests）均通过，开发/生产 worktree clean。开发证据门通过，现已启动最终 QA Round 3/3；这不等于内容通过或 PM Accepted。
- Final QA contract：Round 3 必须一次性完成 L0 零 fallback/数据流/active surface 对抗，L1 全回归，L2 对 immutable R8 的 19 条 exact-source/research/Persona/ranking/12 推荐+7 观察逐条内容审查，以及 L3 在专用测试 04 + 个人测试目标创建 fresh 12 条、3 页 5/5/2 真卡闭环。必须验证 Storyboard 不再无证据发明“返修”、Mx-Shell 识别《丧尸清道夫》公开传播钩子、案例库仅作人格风格、0..N 无截断、精确来源可点击、未选保持 pending、生产 marker=0。任一项失败即 `QA Round 3/3 Failed / Stop`，无 Round 4。
- QA Round 3/3 result：`QA Round 3/3 Failed / Stop`。`94e5713` 的 L0 semantic owner/dataflow 与 L1 回归全部通过；L2 独立重算也证明 exact-source raw、research raw/hash/literal excerpt、19/19 ranking、12 推荐+7 观察、Persona 隔离和 0..N 均成立。但研究资格门只证明“摘录字符串存在”，没有证明摘录语义支持 `supported_claim`。Claude Cowork 的 TechRadar 摘录实际是 newsletter/member/navigation 文案，却被登记为“使用集中在业务运营和知识工作”，并支撑标题中的“最常”；AI use-case 与 FDE 行也存在目录/标题碎片不足以支持详细 claim。按最终轮规则 L3 未运行、无 Round 4。
- PM product decision：不得继续禁词、标题或 artifact 小修。若用户决定继续，应另立新的 claim-level evidence architecture：先把外部主张拆成原子 claim，再由不接收 Persona/标题的独立 verification state 对 literal excerpt 判定 `entailed / partial / unsupported / contradicted`；只有核心主张全部 entailed 的候选可进入 Stage1 推荐。Austin 的观点/假设必须与外部事实分栏，强比较、频率、因果和最高级表达必须引用对应 evidence ID；不支持则候选 typed failure/observe，不得自动补写或用弱证据继续。该事项必须由用户重新确认范围和测试合同，不能算 AR-020D Round 4。

### AR-020E 传播钩子与大胆主编表达校准

- 类型：主编内容策略 / 标题吸引力 / Skill 与运行契约校准
- 优先级：P1
- 状态：Released / Post-release Static QA Passed / Main Synced to Feature / Scheduled-Day Smoke Pending
- 来源：AR-020D Round 3 的证据语义门将自媒体标题按论文式 claim entailment 审查，用户明确认为结果过于保守，并确认采用 `Hook First / Aggressive by Default / Allow Hyperbole / No Fabricated Verifiable Facts` 新口径。该事项单独编号，不篡改 AR-020D Round 3 失败历史，也不是 Round 4。
- 用户可见目标：标题和自然 Austin 角度应优先保留原始传播入口、冲突、结果承诺、社会证明和好奇心，并明显提升点击欲；允许有观点、夸张、趋势判断、最高级语气、比喻和反问，不再要求每一句编辑表达都能从研究摘录逐字推出。
- 事实边界：精确数字、日期、直接引语、官方功能/声明、法律/医疗/金融/名誉类事实仍必须有对应来源，不得编造；普通编辑判断和修辞性夸张无需 claim-level entailment。不得继续实现 AR-020D 失败后提议的重型原子 claim verifier。
- 保留架构：exact-source open、fresh web research、Persona/style-only、zero fallback、semantic owner、动态 0..N ranking、无损分页、decision-first Topic Card 全部保留。来源或研究失败仍 fail closed，不得恢复旧 Skill、旧 Persona、deterministic/legacy、snippet/model-memory 或跨字段 substitution。
- 内容规则：产品名/作者名不自动等于钩子；必须优先识别陌生受众会点击的故事、冲突、结果、反常识、社会证明或风险。允许 `正在接管 / 已经开始 / 最值得 / 没人意识到 / 抢饭碗 / 一个人顶一支团队` 等强表达；禁止把内部任务、验证、返修、验收语言机械前置成标题，也禁止通过禁词表或单样例硬编码实现。
- 重点反例：Storyboard 不得无证据强行转成“返修”；Mx-Shell 必须识别《丧尸清道夫》、公开提示词和爆款社会证明，而非把陌生产品名当钩子；Claude Cowork 可以大胆写“开始接管没人愿意整理的运营工作”，但不能凭空声称精确占比或伪造官方统计。另需覆盖 Codex+Obsidian、Codex PPT、Agent、AI 视频和 MIRA。
- 开发门：开发必须先基于 immutable R8 source/research dossiers 重新运行 current-task 主编输出，对 12 条推荐和 7 条观察交付 before/after 主标题、自然角度及一份用户可读样例包；人工自审点击欲、具体性、人格感、原始钩子保留和任务卡腔。只有自验通过才可 commit/push，并回 PM 决定是否另开 AR-020E QA；开发不得自行派 QA。
- Skill/Git：repo mirror Skill 是受版本控制源，本轮 Skill 修改必须与代码一起进入 Git。global private Skill 本轮不得同步；发布阶段另做 hash sync/read-back 和回滚计划。
- 生产边界：不写 staging/production Feishu，不发卡、不点击、不采集、不触发 06、不部署 SCF/runtime、不改 global Skill。
- 开发首轮自验：`8f452b2 feat: calibrate hook-first editorial expression` 已 push；19 条输出中 Storyboard、Codex+Obsidian、Codex PPT、Agent、AI 视频、Claude Cowork、MIRA 等 18 条方向明显改善，代码/Skill/测试和 zero-fallback 结构门通过。但 PM 不接受开发报告的 19/19 pass。
- PM 阻断：Mx-Shell 行把“创作者在房地产公司做宣传”的职业背景写成 `一条地产宣传片`，同时从最终标题删除用户点名的公共钩子 `《丧尸清道夫》`；这是来源实体关系串义，不是允许的修辞夸张。现有 hard-fact policy 仍返回 pass/none。
- 自验机制阻断：19 条 `human_review` 六项布尔全部为 true，且使用同一条泛化 review note；`ar020e_expression_calibration.py` 只校验这些自报布尔值后直接写 `content_self_review=pass` 和 0 failures，未形成候选级真实复核。开发须把 post-generation review 与生成 payload 分离、绑定 decision output hash、逐条给出具体 note，并由该 artifact 派生 summary counts。
- 返修门：保持同一 immutable R8 dossiers 和 19 行范围，不重开研究、不改有效架构。修正 Mx-Shell 公共钩子与来源身份，复核军方/安全标题等外部事件事实的 hard-fact 声明；新增通用“人物职业背景不得变成作品身份”反例测试，禁止按名称/source ID 硬编码。返修完整自验和 PM 复核通过前不派 QA。
- 集中返修结果：`d075447 fix: separate AR-020E content review authority` 已 push。生成与 post-generation review 已拆为独立 artifact；review 绑定整批 decision-set hash、逐条 decision hash、标题和 source hook，缺行、重复、hash/title/hook mismatch、重复通用 note、人物背景冒充作品身份均 fail closed。19 条 review note 全部候选级唯一，summary 的 pass/fail 由独立 review 派生。
- PM 证据复核：全量 19 条通过 PM 内容复核。Mx-Shell 标题修正为 `《丧尸清道夫》火到好莱坞大佬全网找人，提示词还被全部公开了`，保留真正公共钩子并移除职业背景串义；Storyboard、军方、Claude Cowork、MIRA 等反例的事实与修辞边界可接受。PM 针对回归 47 tests OK、`git diff --check` 通过。报告：`/private/tmp/ar020e_pm_evidence_review_20260714_r2/PM_EVIDENCE_REVIEW.md`。
- QA 口径：只启动一次合并独立 QA，不重算 exact-source/research、不写 staging/Feishu、不发卡。QA 必须独立审阅全部 12 推荐+7 观察、对抗 review authority/hash/来源身份门，并以自身内容判断决定 pass/fail；失败返回一次完整报告，不自动进入微调复测循环。
- 合并 QA 结果：`AR-020E Consolidated QA Passed / Waiting PM Original-Requirement Acceptance`。L0 独立证明 generation 自报通过不能覆盖 review fail，缺行/重复/未知/hash/title/hook mismatch/重复 note/人物背景冒充作品身份均 fail closed；L1 为 167 Python + 28 Node tests 全过；L2 逐条人工审查 12 推荐+7 观察，19/19 可给用户查看。证据：`/private/tmp/ar020e_hook_first_qa_20260714/`。
- PM 原始需求接受：PM 复核 QA 覆盖与完整 19 行后接受 AR-020E。最终内容保留真实传播钩子和更大胆自然表达，且硬事实与编辑性修辞分开；标题句族最大占比 25%，不再由返修/验收/交付等任务卡词汇主导。状态进入 `PM Accepted / Ready for RC Planning`，但尚未合并、发布、同步 global Skill 或做生产验证。
- 发布准备决策：用户确认该需求可以准备发布。PM 已启动 RC 准备，但该授权只覆盖生产基线上的依赖审计、隔离 RC 组装、开发自验与后续 staging/test 全业务回归，不包含任何生产合并、Feishu 写入/发卡、global Skill sync、runtime/SCF 部署或生产 smoke。
- RC 关键风险：生产 `main` 到 `d075447` 之间包含大量 AR-020E 无关提交，禁止整条 `feature/next-production-flow` 直接合并。RC 必须从生产 `75801a8` 新建隔离分支，最小化移植 AR-020D/E 完整依赖，并证明真实生产 outer automation 能调用 current-task state machine；不能为了兼容恢复旧 nested/deterministic/fallback 路径。
- RC 计划：`/private/tmp/ar020e_release_prep_20260714/AR020E_RELEASE_PREP_PLAN.md`。
- RC 构建结果：生产基线 `75801a8` 上的隔离分支 `release/ar020e-rc-20260714` 已 push，HEAD=`45e858a`。RC 仅有 `0293c17` 与 `45e858a` 两个候选提交；63 个文件与 manifest 63/63 一致，forbidden paths=0；开发自验 196 Python + 20 Node 通过，outer entrypoint check-only 证明无写入/发卡/采集/06。证据：`/private/tmp/ar020e_release_candidate_20260714/`。
- 发布阻断保留：生产 04 仍缺 `研究摘要 / 受众钩子 / 研究置信度 / 内容结构`；global Skill hash 仍为旧值；09:15 `ai-04` automation 仍暂停且 prompt 是旧 Gate/Experiment/Top3 规则。RC 回归通过前不得执行任何一项生产变更。
- 下一门禁：固定 QA 对 RC 分支执行 full business regression，覆盖生产基线 scope、collection/defer、新 outer current-task entrypoint、exact-source/research/persona/0..N/Stage2/finalize、staging 04 四字段、证据优先卡片分页和 test callback、receiver/idempotency/06 guards、global Skill 临时包 hash。通过后才生成具体生产授权计划。
- RC 全回归结果：`AR-020E RC Full Regression Failed / Release Blocked`。L0 scope/manifest/runtime、L1 196 Python+20 Node、accepted 19-row 内容、test 04 四字段与 5/2 卡片分页均通过；但 staging page1 点击 `本页都不选` 后 `updated_count=0`，5 条显式候选仍全部 `待判断`。用户会看到按钮已提交但业务状态未变化，属于发布阻断。
- 根因：`feishu_topic_decision_card.py:915-916` 在 `force_no_selection=True` 时直接返回空 decisions，callback 仍记录 submission receipt。既定产品合同是：`本页都不选` 只把当前页可直接生成选项的显式 `candidate_ids` 标为 `不做`；补证据/观察展示项、其他页面、未见/未触碰候选保持原状态。普通“提交选择”仍只更新勾选项，未勾选项保持 pending。
- 修复门：仅在 RC 分支做集中修复；必须同时修复 zero-update receipt 风险，避免未写入却被记为已提交后无法重试。开发自验通过后提交/push 新 RC HEAD，再从头重跑 RC full regression；不得只做 callback 微测或请求生产授权。QA 报告：`/private/tmp/ar020e_rc_full_regression_20260714/AR020E_RC_FULL_REGRESSION_REPORT.md`。
- RC 修复结果：新 HEAD=`47793c2 fix: make page rejection callback atomic` 已 push。`本页都不选` 只映射当前页 explicit direct-generation IDs 为 `不做`；空/重复/页外 ID、record missing、run/snapshot mismatch 全页预检失败且 writes=0/receipt=0；成功写后才记 receipt，重复 callback 无二次写。normal submit 仍只更新勾选项。开发自验 200 Python +20 Node，targeted 100，semantic owner/check/pre-merge 全过。
- R2 门禁：固定 QA 必须对新 HEAD 从 L0 到 L3 完整重跑，使用全新 staging run，不复用上一轮失败记录，不清理旧证据。除页级拒绝外还必须补完 bounded exact-source/research/current-task fixture、独立 Skill temp sync/rollback、selection callback 隔离、顺序 PUT 传输中断后的 receipt-free retry convergence，以及全部生产只读边界。
- R2 派发：固定 QA v2 线程 `019f4714-3f76-7bb1-b71f-08a41d9f8860` 已成功接收 `47793c2` 的完整 RC Full Regression R2；要求从 L0-L3 使用全新 staging run 重跑，不做 callback-only 微复测。测试运行期间不申请或执行生产 04 schema、global Skill、ai-04 automation、production main 或 smoke 变更。
- R2 结果：`AR-020E RC Full Regression R2 Failed / Release Blocked`。页级 `本页都不选` 已按合同只更新当前页 5 条，证明上一轮 callback 修复有效；但 normal submit 的实际 test receiver 仍保留旧语义，把同页所有未勾选项写成 `不做`。fresh Flow B2 选择第 1 条提交后，page1 五条全部变成 `不做`、page2 两条保持 `待判断`，且选中项没有进入 `生成脚本包`。这是卡片合同与真实 receiver/SCF 运行时不兼容，不能发布。
- Receiver 返修门：RC 必须把 `cloud_functions/feishu-card-receiver/src/receiver.js`、`tencent-scf/index.js` 和旧语义测试纳入同一修复；normal submit 只写显式选中 ID，未选 ID 保持 pending；page no-selection 继续只拒绝本页显式 IDs。开发还必须更新 RC manifest/部署计划、构建带 hash 的 SCF 包，并只部署到隔离 test receiver 做真实 staging read-back；production receiver 禁止部署。完成后再从头做完整 RC 回归，不接受 receiver 单测或 callback 微复测代替。
- Receiver 开发结果：四个 scoped 文件已完成但保持未提交；267 Python、28 receiver/SCF Node、semantic owner 0/7、compile/check/pre-merge 全过。SCF 包 SHA256=`72d9cbde6f0574e29239f2bbb22786cc5e984010d2c4b43179210475e05c1a0d`、size=11,919 bytes。开发因浏览器控制面初始化失败，未能部署隔离 test receiver，也没有用旧 challenge 冒充新包已上线，因此尚不能 commit/push 或派 R3。
- Test SCF 门：固定云端执行线程只允许部署广州/default `feishu-topic-card-receiver-ar018-test`，URL `https://1408808729-cvt1fm72er.ap-guangzhou.tencentscf.com`；部署前保留旧代码/回滚包和函数配置，目标名或环境不匹配立即停止。禁止触碰 production `feishu-topic-card-receiver`。部署后需 challenge、代码/hash read-back 和 fresh staging synthetic selected-only/page-reject 证据，再回开发提交。
- Test SCF 阻塞：云端执行线程已复核新包 `72d9cb...` 与 11,919 bytes，并生成 RC base 回滚包 `811cbc...` / 11,470 bytes；但两个可控浏览器上下文均被腾讯云重定向到登录页，本机无 `tccli`/云凭证。未上传、未部署、未读回。该问题不是授权缺失，而是登录态失效；用户完成腾讯云登录后可从函数身份只读确认继续，不重做代码或测试。
- Login resume：用户已在固定云端执行任务的内置浏览器完成腾讯云登录；PM 已恢复同一任务，从测试函数 identity/config/deploy history 只读确认继续。仍只允许部署 `feishu-topic-card-receiver-ar018-test`，生产函数及其他生产表面保持禁止。
- Upload blocker / user decision：内置浏览器已确认 exact test function 与测试表，但不支持 file-input 注入，原生文件选择也无法安全控制，因此在部署前停止。用户明确不愿手工选文件，要求改用外部 Chrome 重新登录并自动上传；PM 已恢复同一云端任务使用外部 Chrome，禁止让用户拖拽/选择 zip。
- External Chrome result：外部 Chrome 打开 exact test function URL 后被腾讯云重定向到微信二维码登录；没有可复用 session、保存凭据或 passkey，`上次登录 微信` 仍停留二维码挑战。该认证必须由账号所有者扫码，不能自动绕过。候选 zip 未上传、Deploy 未点击；用户扫码后，文件上传及后续部署仍由云端线程自动完成。
- Auth resume：用户已完成外部 Chrome 微信扫码并确认登录；PM 已恢复同一云端线程，从 exact test function 身份复核、自动 file upload、部署和 read-back 继续。用户无需再操作。
- Test SCF deployed：`feishu-topic-card-receiver-ar018-test` 已于 `2026-07-15 10:05:42` 通过腾讯云控制台部署新包 `72d9cb...`。云端读回包含 `PAGE_NO_SELECTION_STATUS`、`selectionInputStatus` 和 empty-selection warning，旧 unchecked loop marker 无命中；challenge 通过，显式 test table readiness 为 58 fields、required 4/4。未触碰 production function。
- Dev resume：固定开发线程已收到部署证据，正在用 fresh staging create-only records 直接调用已部署 test URL 验证 selected-only、page-reject、empty/outside/stale fail-before-write 和 production/06/card 边界；真实 read-back 全过后才 commit/push 新 RC。
- Runtime failure：fresh run `ar020e_rc_r3_selected_only_20260715_1018` 的 selected-only 写集已正确收敛到唯一选中 ID，但测试表 `选择原因标签` 为 type=1 Text，receiver 仍发送数组 `[]`，飞书返回 `TextFieldConvFail`。五条记录全部保持 `待判断`，无部分写入/制作方向队列；开发按硬门停止，page-reject 和剩余 probes 未继续。
- 既有修复来源：仓库历史提交 `bed3b42 fix: normalize topic card reason tags` 已包含 Text/Multi-select 字段形态兼容，RC 依赖审计漏带。新返修必须在当前 selected-only dirty diff 上最小移植该逻辑，并强化为完整 fields pagination + owner field type=1/string、type=4/array；field missing/unsupported 直接 fail-before-write，不允许 unknown->text fallback。通过本地门后重新部署 test SCF，再用全新 run 完成全部 runtime flows。
- Schema local gate：当前四个 scoped 文件已完成严格字段形态兼容；fields metadata 使用 `page_size=100 + page_token`，type=1 写字符串、type=4 写数组，missing/unsupported 在 PUT 前失败；page no-selection 不读取/写原因字段。267 Python、32 receiver/SCF Node、semantic 0/7、compile/diff/pre-merge 全过。新包 SHA256=`34f929057f6ecf71ef5ee6454426423093215df6bb5e8b10cb2fbae8fc5e6061`、12,186 bytes，仍未 commit/push。
- Test SCF redeploy：固定云端线程已收到新包，复用 authenticated external Chrome，只重部署 `feishu-topic-card-receiver-ar018-test`。部署/read-back/challenge 通过后再恢复开发线程用全新 records 重跑所有 runtime flows。
- Schema-compatible test deploy：新包 `34f929...` 已于 `2026-07-15 10:36:05` 通过腾讯云控制台仅部署到 `feishu-topic-card-receiver-ar018-test` / Guangzhou/default。云端代码读回确认 `selectionReasonValue`、完整 fields 分页、type=1/type=4、missing/unsupported fail、`PAGE_NO_SELECTION_STATUS` 与 `selectionInputStatus`；旧 unchecked loop 无命中。challenge 和显式测试表 58 fields / required 4/4 通过，production function 未访问。
- Dev fresh runtime：固定开发线程已恢复，并被要求使用全新 create-only staging run/records，直接调用当前部署包验证 selected-only、0/2 tags 文本序列化、page rejection、empty/outside/stale/missing fail-before-write、receipt/retry、队列隔离和 production 04/06/card marker=0。真实 read-back 全过后才 commit/push RC；否则继续留开发，R3 不启动。
- Runtime closure：开发 fresh 7/7 runtime flows 全过。normal submit 只更新唯一选中项，空原因保持空、双标签按 Text 回读 `证据够、判断够强`；page rejection 只把三个显式 page IDs 写为 `不做`；unchecked/display/page2 均保持 `待判断`；empty/outside/stale/missing 均零业务写。RC 已 commit/push `aa0ce3d fix: normalize receiver selection field shapes`，本地与远端一致，生产 marker=0。
- Complete R3：固定 QA v2 已收到目标 `aa0ce3d` 的完整 RC Full Regression R3，必须重新覆盖 scope/runtime/content/current-task、receiver src/SCF parity、全量回归、两页以上真实 staging 卡、selected-only、page reject、idempotency/retry、DOM/截图和生产边界。开发 runtime evidence 只作前置证据，不得代替 QA；任何层失败继续 Release Blocked，通过也只进入 PM 生产授权计划，不自动发布。
- R3 pass：完整 L0-L3 已通过，不是 callback 微复测。RC scope/manifest 67/67；267 Python、32 receiver/SCF Node、6 Douyin Node；accepted 19 rows 与 fresh bounded current-task fixture；真实两页测试卡 selected-only、Text reason、page rejection、duplicate/retry、DOM/read-back、production marker=0 全部成立。状态进入 `Ready for PM Production Authorization Plan`，不是 released/production ready。
- 生产授权范围：生产 04 `tblz2CFc9eIa8bMG` 当前 35 fields，GET-only 确认恰缺 `研究摘要/受众钩子/研究置信度/内容结构`；测试同型四字段均为 type=1。生产 main 仍 clean `75801a8`；global Skill 仍旧 hash；production SCF 仍为 baseline 包 `34674fb...`；`ai/ai-04/ai-2` 当前全部 paused。拟一次授权完成四字段纯新增、main FF 到 `aa0ce3d`、Skill 备份/sync/read-back、production receiver 备份并部署 `34f929...`、更新 ai-04 prompt 并顺序恢复三 automation；任何门失败保持 paused 并按组件回滚。计划：`/private/tmp/ar020e_production_authorization_plan_20260715/AR020E_PRODUCTION_AUTHORIZATION_PLAN.md`。
- 用户生产授权：用户已明确回复“同意”。固定生产线程已接收完整执行单，按 preflight/backups -> 04 四字段 -> main FF/push -> global Skill backup/sync -> production SCF backup/deploy -> automation 更新/恢复 -> 只读 smoke 执行。发布过程不跑旧批次、不手动发卡、不触发 06；任何门失败保持 automation paused 并组件级回滚。状态在完整 handoff 前为 `Production Release Authorized / Running`。
- 生产发布结果：发布在 production `pre_merge_check.py` 门失败后按授权停止并回滚。业务门实际通过：syntax、semantic owner 7/7、receiver Node 32/32、outer entrypoint check-only 全 false；失败是该脚本硬编码 dev/release branch，且其 Topic Card probe 主动拒绝 production worktree。未作豁免。生产 04 四字段已成功纯新增并保留，0 record writes；production main 通过 normal revert 到 `8c245de`，tree 与原 baseline `75801a8` 完全一致；global Skill、production SCF、三个 automations 均未变且 paused。证据：`/private/tmp/ar020e_production_release_20260715_1042/RELEASE_FAILED_ROLLED_BACK.md`。
- RC2 返修门：不能直接 merge 旧 RC，因为 production history 已包含原提交及其 revert。固定开发线程必须从 `8c245de` 新建 isolated RC2，以 normal audited reapply 恢复 aa0ce3d 产品树，再新增显式 production-release gate：仅 clean production main、local/remote/expected HEAD 一致时可运行；Topic Card 只允许 `--check-only --no-notify` 且 parseable `sent=false`。默认 dev pre-merge 不弱化。RC2 自验/manifest/独立 release-gate QA 通过后重新请求生产授权。
- RC2 开发结果：新 branch `release/ar020e-rc2-20260715` @ `8362091` 已 push。`ad3da97` 通过 normal revert-of-reverts 恢复产品树，与 `aa0ce3d` 字节一致；最终只新增/修改 production gate、7 focused tests 和说明三文件。显式 production mode 要求 configured production root、clean main、local=origin/main=expected 40-char SHA；只运行 Topic Card `--check-only --no-notify`，并验证单 JSON、sent=false、无 write/notification marker、card artifacts 不变。273 Python、32 Node、semantic 7/7 与 fresh production-like fixture 通过。
- QA 门：固定 QA v2 已开始 Release-Gate QA，重点对抗 production-dir override、wrong root/branch/dirty/local-remote drift、malformed/multiple JSON、sent/write/notify true、artifact mutation 及 preflight 失败时 probe 不得运行。业务产品树已有 R3 完整通过，本轮不重复内容/真实卡片，避免无价值 token 消耗。QA 通过后重新生成授权单，旧授权不得沿用。
- Release-Gate QA 结果：目标 `8362091` 通过。独立 22/22 mutation、fresh bare-origin/main fixture、274 Python、32 receiver/SCF Node、semantic owner static=0/behavioral=7/7、compile/check/default pre-merge 全绿。QA 只验证新 production-release gate 与 lineage，不重跑已通过的业务 R3，也没有执行真实 production checkout 或任何外部写入。
- 生产现状：production local/remote main=`8c245de` 且 clean；04 已保留 39 fields 和四个 type=1 新字段，record writes=0；global Skill 仍旧 hash `154697...`，production SCF 仍 baseline `34674fb...`，`ai/ai-04/ai-2` 全部 paused。
- 再授权门：上一份授权在失败发布并回滚后已失效。新计划为 `/private/tmp/ar020e_production_reauthorization_plan_20260715/AR020E_PRODUCTION_REAUTHORIZATION_PLAN.md`，只读复核既有 schema 后，授权 main 到 `8362091`、显式 production-release gate、Skill sync/read-back、production SCF exact package 部署和三 automation 协议更新/顺序恢复；任何门失败保持 paused 并组件级回滚。
- 用户再授权：用户已明确回复“同意”，批准 SHA256=`86a7b16edf30686e7ea3f40a6a6cd3d73c94b945949995771f59b3a1d12a66a8` 的 RC2 生产再授权计划。固定生产线程 `019f2bc4-079e-7530-903e-484707590482` 已接收执行单；PM 不轮询盯进度，等待完整 release/rollback handoff 后独立核对真实 main/schema/Skill/SCF/automation 状态。
- RC2 生产结果：`Release Failed and Rolled Back / Automations Paused`。production main 曾 FF/push 到 `8362091` 且显式 production gate 全过；global Skill 目标 hash 和 production SCF approved package 部署/read-back/challenge 也全过。停止原因仅为官方 Codex automation tool 无法更新 paused `ai-04`：参数合同与文档不一致，修正参数后仍返回 `Failed to update automation`；TOML hash/content/status/cwd/schedule 均未变，未手改 TOML。
- 回滚闭环：production SCF 已回 baseline `34674fb...`，Skill 已回 `154697...`，Git 用 normal revert 到 local/remote `410e9d3` 且 tree 与 `8c245de` 相同；`ai/ai-04/ai-2` 全部 paused、定义 hash 未变。生产 04 保留 39 fields 与四个 type=1 字段，0 record writes/marker。证据：`/private/tmp/ar020e_rc2_production_release_20260715_1140/RC2_RELEASE_FAILED_ROLLED_BACK.md`。
- 下一门：不直接第三次发布。先按 `/private/tmp/ar020e_automation_control_surface_plan_20260715/AR020E_AUTOMATION_CONTROL_SURFACE_VALIDATION_PLAN.md` 创建一个永远 paused 的临时 automation，使用官方工具完成 create/read-back/update/read-back/delete，并证明三条正式 automation 完全不变。该隔离写探针需用户单独授权；通过后再重做发布顺序和新授权。
- 用户重建决策：用户手动确认 `ai/ai-04/ai-2` 三个旧任务无法打开，并明确要求“删掉重来”。PM 将隔离探针升级为完整安全重建：先创建三条 `[REBUILD]` paused replacement，验证 create/view/update/read-back 全部正常，再删除 exact old IDs 并改回原显示名；这样避免先删光后 create 仍失败。
- 重建计划：`/private/tmp/ar020e_automation_rebuild_plan_20260715/AR020E_AUTOMATION_REBUILD_PLAN.md`。08:00 与 10:00 保留现有 prompt；09:15 使用 RC2 `config/ar020e_outer_task_protocol.md`，source SHA256=`1c179dc7...`，移除旧 Gate/Experiment/Top3。全程禁止 resume/run、生产代码/Skill/SCF/Feishu/卡片/采集/06 变更。
- 首次 rebuild create 阻断：official create 使用旧 TOML 里的 UUID project binding `19c5df58-...` 返回 `Failed to create automation`，按门禁未生成 replacement、未删除 old task。PM 随后调用当前 `list_projects`，发现有效 project ID 已变为路径 `/Users/congcong/Desktop/AI/AI项目/AI账号工作流`；旧 UUID 是 legacy/stale binding，也解释了三个旧任务无法打开。已修订计划并在原授权范围内重试先建后删。
- 第二次 rebuild create 诊断：父目录 path-form project ID 已能创建 `ai-rebuild`，但 live read-back 将 CWD 固定为父目录，且 create 请求的 `PAUSED` 实际落成 `ACTIVE`。生产线程立即 official-update 为 paused；独立 `cwds=/.../ai_account_radar` 更新被 live API 拒绝，随后 official-delete replacement。无 run/memory，三个 old tasks 仍 byte-identical + paused。
- 当前根因与下一步：automation 的 CWD 由 Codex project registry 绑定，不能靠 automation update 单独覆盖。必须先把 `/Users/congcong/Desktop/AI/AI项目/AI账号工作流/ai_account_radar` 注册为独立 project，并在 `list_projects` 回读到该 exact path；否则继续 create 只会得到错误工作目录。注册成功后仍按先建后删执行，且每次 create 后立即强制 pause/read-back/no-run，再创建下一条。
- Automation 归属纠正：对比旧任务配置后确认，旧结构本来就是“父项目归属 + production 子目录 CWD”；此前注册 production 子项目是错误 workaround。现已将 `ai-rebuild / ai-04-rebuild / ai-rebuild-2` 的 target 恢复为父项目 `/Users/congcong/Desktop/AI/AI项目/AI账号工作流`，CWD 保持 `/Users/congcong/Desktop/AI/AI项目/AI账号工作流/ai_account_radar`；用户已手工移除误建子项目，live project list 只保留父项目，任务未因此运行。
- 最终生产发布：用户重新授权后，production 从安全回滚基线 normal revert-of-revert 到 `7c469babb6e69431b5aca0a26c2d1ef058210929`，tree 与 RC2 `8362091` byte-identical。动态 gate 使用 `--expected-head "$(git rev-parse HEAD)"` 通过；production 04 保持 39 fields、四个 AR-020E 字段均 type=1；global Skill `SKILL.md` 同步到 `9d364bb0...`；production receiver 部署 exact approved `34f929...` 包并通过 challenge/read-only health；三条现有 automation 仅做 status-only `PAUSED -> ACTIVE`，target/CWD/prompt/schedule 不变。发布过程没有生产业务记录、真实卡片、callback、采集、旧 run 或 06 写入。证据：`/private/tmp/ar020e_rc2_production_release_20260715_final/RELEASED.md`。
- 当前闭环状态：`Released / Post-release Static QA Passed / Main Synced to Feature / Awaiting First Scheduled-Day Smoke`。即时只读/检查模式发布后回归已通过；production main -> `feature/next-production-flow` 回灌已通过；PM 文档已更新。只有下一 scheduled day 的 08:00 采集、09:15 主编写回、10:00 Topic Card 守卫连续链路通过，并完成 QA 证据复核与 PM release acceptance 后，才能标记 `Release Closed / PM Accepted`。
- 即时发布后 QA：固定 QA v2 结论为 `Post-Release Static/Runtime Regression Passed`，并显式分离 `Scheduled-Day Business Flow = Pending`。production/RC2 tree byte-identical；274 Python、32 receiver src/SCF Node、semantic owner static=0/behavioral=7/7、py_compile/node/diff/dynamic production gate 全过；04 schema、global Skill、production SCF、outer entrypoint check-only、Topic Card check-only、三条 ACTIVE automation 和 no-write markers 均独立复核通过。未触发采集、业务写入、卡片、callback、06 或 automation run。报告：`/private/tmp/ar020e_post_release_readonly_qa_20260715/AR020E_POST_RELEASE_READONLY_QA_REPORT.md`。
- 明日 smoke 合同：08:00 必须有 outer scheduled log、`deferred_editorial=true`、same-day run_id 和非空 today_10；09:15 必须完成 exact source/research/current-task state machine/dynamic 0..N/Stage2 lock/finalize/04 read-back，partial failure 保持可见；10:00 必须使用同一 run_id 通过 freshness guard、lossless pagination 和 exact-link/semantic-owner 可见验证。三段 run_id 不一致或任一环失败都不能标 `Scheduled-Day Business Flow Passed`。
- Main 回灌：固定开发线程在隔离 worktree 完成 normal `--no-ff` merge，feature 从 `d075447` 推进到 local/remote `fbef226cb87bdb8b4c2dc56048d3e2d4862f35a7`，`origin/main@7c469ba` 已成为 feature 祖先。69 个 production release 文件中 68 个 blob 精确一致；唯一有意整合 `scripts/content_sampler.py` 保留 feature 来源治理/union CSV，并叠加 production duplicate-record 的运行日期/批次刷新。310 Python、32 Node、semantic 0/7、compile/node/diff/pre-merge 全过；原 dev PM docs 与 AR-020 脏改未被覆盖。证据：`/private/tmp/ar020e_main_to_feature_sync_20260715/MAIN_TO_FEATURE_SYNC.md`。

### AR-026 飞书 01 全量对标账号采集覆盖

- 类型：采集覆盖 / AR-020 上游依赖
- 优先级：P1
- 状态：Released / 01 Migration Passed / Automations Active / Awaiting Scheduled-Day Smoke
- 来源：AR-020 需求确认。用户指出飞书 01 里即使清掉截图污染账号，仍不止 12 个对标账号；用户需要的是全量账号采集，而不是生产默认 12 个账号抽样。
- 影响：如果上游只采 12 个账号，03 内容库天然缺失大量对标内容，AR-020 的选题转译和反向测试会建立在不完整内容库上，继续漏掉适合 Austin 账号的题。
- 目标：从飞书 01 获取有效对标账号白名单，清掉截图污染账号后，对剩余有效账号做全量采集覆盖；当前账号量只有几十个，暂不需要分批。若未来量级明显增大或触发平台风险，再升级为分批/频控策略。每次采集必须输出账号级覆盖报告。
- 范围：01 白名单读取、污染账号精确移除 dry-run、全量账号采集策略、频控/分批/timeout、账号级成功失败报告、03 写入覆盖验证。
- 不在范围：不直接改 AR-020 选题打分；不清理历史 03；不把截图之外的来源一并删除。
- 验证方式：staging/test 或只读/低风险生产 dry-run 先输出计划账号数；正式启用前必须验证计划账号数、实际尝试账号数、成功账号数、失败账号与原因、内容条数和 03 read-back。不得只用 `ok=true` 或默认 12 账号作为通过证据。
- 备注：AR-026 可以和 AR-020 同一开发计划里并行设计，但验收口径必须分开：AR-026 验证“内容库是否全量覆盖”，AR-020 验证“选题是否更适合 Austin”。2026-07-06 开发线程已提交并 push `8adce16`，新增 `topic_flow_rework.py` / `source_pool_governance.py`；`sync_source_sampling.py --dry-run` 显示当前有效对标账号 33 个、隔离账号 8 个。测试线程 Round 1 判定控制逻辑大体成立，但发现 CSV probe 会把 `video_links` JSON 字符串长度误算成视频数，且生产 01 当前仍显示 8 个污染来源为 enabled/current_aux_competitor。Round 2 开发提交 `07be5a5` 已修复 CSV probe 计数：`video_links` 为 stringified JSON/list 时先解析后计数；`/private/tmp/ar026_round2_csv_probe/source_governance_report.json` 中临时 CSV probe `video_links=[a,b]` 计数为 `2`。同时新增 `polluted_source_release_sync_plan.md`，说明 8 个污染源发布时如何从 current/enabled 切到 `quarantined_source` / inactive，历史 03 不动。`/private/tmp/ar026_round2_source_governance/source_governance_report.json` 显示 `planned_account_count=33`、`polluted_source_count=8`、`writes_feishu=false`、`touches_historical_03=false`。Round 2 独立 QA 通过：CSV/JSON 计数稳定；production read-only 01 report 显示有效对标账号 planned=33、污染源=8，release sync plan 明确只改 8 个污染源并不触碰历史 03。PM 注意：当前真实 artifact 仍来自 12 个尝试账号，不等于生产全量采集已经跑过；发布/恢复时必须按正常覆盖验证 planned/attempted/succeeded/failed 账号数和 03 read-back。
- 上线推进：production-base RC `release/ar020e-rc-ar026-20260715@0b5a98e` 的 scope、33-account check-only、01/03 GET-only、canonical login 和其余 fail-closed 合同均通过，但独立 Release QA 发现唯一阻断：实际 Node 层仍接受 `--account-limit 12/3` 并把 31 账号静默截断，daily 与 non-check-only outer 也未在副作用前拒绝正 cap。该 RC 已判失败，不得生产授权。PM 已退回一次集中返修，要求 outer/daily/Node 三层对任意正 cap 在 env、Feishu、cache、Chrome、output 前 typed nonzero，并从 production `178f047` 重建新 RC。
- RC2：fresh branch `release/ar020e-rc-ar026-capgate-20260715@5e733cd` 已完成集中返修与开发自验。outer normal、daily、Node 三层在参数解析/环境加载前共用正 cap 硬门；1/3/12/31、负数、畸形、空/缺值、equals alias、重复参数均 typed nonzero，Node 子集参数、`rows.slice` 截断和失败账号子集重试已物理删除。scheduled check-only 仍为 33=31+2。PM 已派一次完整 RC2 Release QA，不以 cap micro-recheck 代替全范围回归。
- RC2 Release QA：exact target `5e733cd1a8120185b6c2d35b3f277a2599155fea` 的 16-file scope/hash/apply、三层 cap 30/30、parser/defer/quarantine 9/9、lineage 10/10、310 Python、41 AR-026/031、129 AR-020D/E、32 receiver/SCF 与全静态门均通过。生产 01 fresh GET=51（8 target+43 untouched）、03=670/no-touch、canonical 9333 logged_in 均只读成立。结论 `Ready for PM Production Authorization`；真实 31 个 Douyin 账号采集仍只在下一个 scheduled day 验收。
- 生产授权：用户已明确回复“确认”。PM 已派固定生产线程按 audited plan 执行：三任务 status-only pause + fresh backup；production main exact `5e733cd` 发布与 dynamic/cap gate；仅迁移生产 01 精确 8 条为 quarantine 并 8/8 read-back、43 条 hash 不变；03 GET-only no-touch；canonical session read-back；三任务 status-only resume且不即时运行。任一 mismatch 保持 PAUSED 并按组件回滚；真实 33-account 业务验收仍留给下一 scheduled day。
- 生产结果：production main 已 fast-forward/push 到 `5e733cd1a8120185b6c2d35b3f277a2599155fea`，dynamic gate 与三层 positive-cap probe 通过。生产 01 仅精确 8 IDs 写为 `quarantined_source/停用/不参与主采样/low`，8/8 read-back 通过且 untouched-43 hash 仍为 `c69642b6...625`；03 迁移前后均 670 records、25 fields、hash=`73a9bc1f...933`。canonical PID 33282 仍 identity/session/logged_in verified；三任务仅 PAUSED->ACTIVE，未即时运行。状态为已发布、等待即时只读回归与下一 scheduled-day smoke，不能宣称真实33账号业务完成。
- Main 回灌：固定开发线程在隔离 worktree 正常 `--no-ff` merge `origin/main@5e733cd`，并合并并发 PM docs lineage，最终 feature local=remote=`e27fbedcf32b92f072e55b249780fc53ba76172f`；`origin/main` 与 PM docs 均为其祖先。16 个发布文件中 13 个 production-exact，3 个差异仅保留既有 feature current-task/editorial、automation worktree guard/failure QA 和更严格 no-side-effect tests，AR-026 已发布合同不变。343 Python、39+6 Douyin Node、32 receiver/SCF、semantic/pre-merge 均通过；production main 未改。
- 发布后异常：即时只读 QA 发现 production `output/spikes/douyin_cdp_source_watch_probe` 在 22:11:59-22:12:12 被真实刷新，artifact 明确 `check_only=false`、31 attempted、29 succeeded、2 failed，并写入 raw resolver 文件。未产生 scheduled outer log、new run、Feishu PUT、latest_write、card、06或script package，但触发来源尚未归因，因此发布后回归判失败，不能视为 scheduled smoke。PM 已按 stop discipline 派生产线程 status-only 暂停三任务并查 scheduler/process lineage，同时要求 QA 自审精确命令；Git、01迁移、03和Chrome保持不动，完成归因前不得恢复任务。
- Safety review：三任务已 status-only `ACTIVE -> PAUSED`，prompt/schedule/target/cwd/model 不变。release、QA、dev 的真实命令轨迹均排除自身触发；automation_runs 对三个 replacement IDs 为 0，且无 last_run_at、执行线程、memory 或 launcher command。22:10:04 恢复至 22:11:32 首个 incident file 的 88 秒只支持 catch-up 假设，不能证明。现状定为 `Unattributed / Keep Automations Paused / Needs Fix`；Git `5e733cd` 与已验证生产 01 迁移不回滚，真实 scheduled smoke 由 AR-032 阻断。
- PM 产品决策：用户明确认为本次误触不值得继续加重防护，接受其作为已知非阻断事件，并要求明天正常开始跑。AR-032 已取消，不再开发/RC/发布；PM 已授权三任务仅做 status-only 恢复 ACTIVE，不手动采集或补跑旧 schedule。明日仍按正常 08:00/09:15/10:00 链路验收，22:11 artifact 不计入 scheduled smoke。
- 最终恢复：`ai-rebuild`、`ai-04-rebuild`、`ai-rebuild-2` 已仅做 `PAUSED -> ACTIVE`；official view/read-back均ACTIVE，当前TOML与暂停前ACTIVE备份逐字节一致。08:00/09:15/10:00、model、reasoning、parent target、production cwd和prompt均不变；恢复后无automation run、logs/runs/latest_write/card/06/script package。后续不手动运行、不再自动暂停，等待明日正常scheduled chain。

### AR-031 固定抖音 Chrome Profile 与登录态硬门

- 类型：采集稳定 / 生产前置门禁
- 优先级：P1
- 状态：Hotfix Done / Canonical Logged In / Automations Active / Scheduled Smoke Pending
- 来源：2026-07-15 明日采集前只读审计。用户要求抖音保持已登录，并固定使用同一个浏览器登录态，不得每次运行随机寻找浏览器。
- 已确认根因：生产调用链固定请求 `127.0.0.1:9333`，但当前监听 PID 17170 实际绑定旧 RC worktree 的 `.local_services/douyin-chrome-profile`，不是 production profile；当前 Douyin DOM 有明确登录按钮，判定 `logged_out`。`start_douyin_cdp_chrome.py` 只检查 CDP 端口是否可用，不校验监听进程的真实 `--user-data-dir`，还会把请求 profile 误报为实际 profile；`daily_pipeline.py` 又把 Chrome start 和 Douyin probe 作为 optional step。
- 目标：建立唯一、worktree-independent 的持久化 Douyin Chrome profile；9333 已占用时必须验证 PID/binary/port/user-data-dir 精确匹配；增加不读取认证秘密的登录态预检；profile mismatch、logged_out、verification_required 或 indeterminate 均 fail closed 并写入 scheduled/daily 日志。
- 范围：canonical profile 配置、Chrome process/profile identity、CDP health、sanitized DOM login health、08:00 preflight、结构化失败证据、迁移/回滚 runbook 和回归测试。
- 不在范围：不导出 cookie/token/localStorage；不随机切换其他浏览器/端口/profile；不以 headless 或旧 profile 作为 fallback；不直接修改 AR-026 的来源治理和生产 01 数据；不运行生产采集作为开发验证。
- 验证方式：wrong-profile/unknown-process/logged-out/verification/ambiguous DOM 全部非零；正确 canonical profile + logged-in 才通过；daily pipeline 在 preflight 失败后不得启动 Douyin probe或把全量覆盖报告为成功；生产迁移后需 read-back actual user-data-dir、profile marker、CDP、login status，并在次日 07:45 执行单一 check-only 命令。
- 发布顺序：AR-031 dev self-validation -> 独立 QA -> hotfix production -> 备份并迁移/重新登录 canonical profile -> 07:45 check-only -> AR-026 RC/生产 01 migration -> 08:00 首次全量采集验收。
- 生产只读证据：`/private/tmp/ar020e_douyin_login_readonly_audit_20260715/DOUYIN_LOGIN_AUDIT.md`；审计未运行采集、未写 Feishu、未修改 browser/profile/automation。
- 开发与 QA：开发提交 `d9aab425d123f89115a4de28a48d1c93a1242873` 已建立 fixed 9333、canonical profile、marker+lsof identity、登录预检和 fail-closed partial 语义。独立 QA 的身份链、当前 9333 wrong-profile 反例及 `/private/tmp` 隔离 Chrome 正例均成立，但发现 `douyin_login_dom_probe.mjs` 用裸 `file://${process.argv[1]}` 判断 CLI 入口；中文路径下 URL 编码不一致导致 exit 0 且 stdout 为空，真实登录也只能得到 `indeterminate`。结论为 `Rework Required`；开发必须改用 Node 标准 URL/path API，并增加含中文和空格的真实 spawn CLI 回归后再进入 Hotfix RC。
- 集中返修：开发提交 `ffe93e4ff35fe4e7b95935f407ce1ba8de07c8be`，改用 `fileURLToPath + realpathSync + path.resolve`，增加严格单 JSON/exit parser 与中文、空格、macOS symlink 真实 spawn 回归。隔离 Chrome 已返回实际 `verification_required` 而非 malformed；当前 9333 仍只读返回旧 RC profile mismatch。开发自验通过，已派独立 QA recheck；QA 通过前不组 production Hotfix RC。
- QA recheck：Unicode CLI 与隔离 Chrome 真实执行已通过，但发现 Python 仅校验顶层 object；`markers` 为字符串等结构畸形时会在 `.items()` 抛 `AttributeError`，没有返回 typed failure。开发需补完整 payload schema 校验与对抗矩阵。QA 同时指出 `d9aab42..ffe93e4` 夹有 PM docs；PM 决定不重写历史，该项通过 code-only patch 和 RC manifest 显式排除 PM 文档，不视为产品代码 blocker。
- 最终返修：`aadfd99ad47c2e94d5e9f1414f0e0691ea84e79f` 对 state/markers/marker value/url/title/error 做严格 schema validation，所有 malformed case 均 typed fail，不再抛异常。开发重跑真实 Unicode CLI、临时 Chrome、当前 9333 反例和完整回归，并产出三段 code-only production patch manifest；已派最终独立 QA recheck，通过后可进入窄 Hotfix RC。
- PM 最终判断：QA 的唯一剩余异议是 empty stdout 返回更具体的 `empty_dom_probe_output`，而非统一命名为 `malformed_dom_probe_output`。该路径已 nonzero、`login_preflight_failed`、无异常且不可能 `session_verified`，满足用户目标与 fail-closed 安全边界；PM 将其列为非阻断内部分类差异，不再启动返修循环。`aadfd99` 接受进入基于 production `7c469ba` 的 code-only Hotfix RC。
- Hotfix RC：`release/ar020e-rc-ar031-hotfix-20260715` / `9893c6c9568ff0440ea7b79b6a2c493ab9bcc1ef` 已从 production `7c469ba` 通过三段 code-only patch 组装并 push；范围 8 runtime + 4 tests + 4 runtime docs。开发自验、真实 Unicode CLI、临时 Chrome、当前 9333 反例和 AR-020D/E adjacent regression 均通过，已派发布级 QA。真实 profile/login 与 automation 仍未改动。
- Release QA：RC 范围/hash/apply、292 Python、23 AR-031、129 AR-020D/E adjacent、7 Douyin Node、32 receiver/SCF、DOM mutation、当前 9333 mismatch 与临时 Chrome L2 全过。结论 `Ready for PM Production Authorization`；真实 canonical profile migration/login 尚未做，因此不是 Production Ready。
- 生产授权：用户已明确回复“确认”。PM 已派固定生产线程按硬顺序执行：暂停三任务并备份 -> 重读并正常停止 exact 9333 PID -> production main fast-forward 到 `9893c6c` 并跑 dynamic production gate -> canonical ASCII profile 迁移/前台登录 -> 仅在 `ok/session_verified/logged_in` 后恢复任务。禁止采集、Feishu、卡片、06、Skill、SCF 和 AR-026 数据迁移。
- 生产结果：代码已发布到 production `9893c6c`，dynamic gate 全过；旧 PID 17170 已正常停止，canonical profile 已从停止且无 lock 的旧 production profile 迁移，现 PID 33282、9333、marker+lsof identity verified。登录探针返回 `verification_required`，但无秘密 DOM 诊断证明命中的 iframe 为 `display:none/0x0/viewport=false`，同时存在两个可见 `/user/self` 账号入口且无可见登录 UI；这是隐藏 iframe 假阳性。三 automation 保持 PAUSED，无业务写入。已派可见性窄热修，前述 Git/profile 迁移不重做。
- Follow-up RC：feature `068aab5` 与 production-base RC `178f047` 仅改 4 个 DOM probe/parser/tests 文件。visible/effective marker 合同覆盖 display/visibility/opacity、尺寸、viewport 和隐藏祖先；当前真实 PID 33282 在 feature/RC 下均返回 `profile_identity_verified + session_verified + logged_in`，visible self=2、login=0、verification=0。RC 已 push，已派发布 QA；三任务仍 PAUSED，production 仍为 `9893c6c`。
- Follow-up Release QA：RC `178f04780ddc74b61befab04b02c87c951980ea6` 的 4-file scope/hash/apply、14 visibility mutations、7 diagnostics schema、293 Python、24 AR-031、129 AR-020D/E adjacent、7 Node、32 receiver/SCF 与 fresh real 9333 closure 全过。结论 `Ready for PM Production Authorization`；授权动作仅 Git-only release + gate + session read-back + status-only resume，不停止 PID 33282、不再迁移 profile。
- Follow-up 授权：用户已明确回复“确认”。PM 已派生产线程只执行 production `9893c6c -> 178f047` fast-forward/push、dynamic gate、当前 canonical 9333 session read-back 和三任务 status-only resume。禁止停止/重启 Chrome、复制/迁移 profile、运行 automation 或触发任何业务写入；失败仅回滚 follow-up code并保持 PAUSED。
- 最终发布：production main local=remote=`178f047`，dynamic gate 通过；canonical PID 33282 未停止或修改，identity verified、session verified、logged_in，visible self=2 且 visible login/verification=0。三任务仅 `PAUSED -> ACTIVE`，08:00/09:15/10:00 与 prompt/target/cwd 不变，无即时 run。AR-031 发布闭环完成；明日 07:45 和 scheduled-day smoke 仍独立待验。

### AR-032 Automation 激活补跑防护与执行 Lineage

- 类型：自动化安全 / 生产可观测性 / AR-026/031 发布后阻断
- 优先级：P1
- 状态：Cancelled by PM / No Release
- 来源：AR-026 发布后 22:11 出现非 check-only 31-account probe；release、QA、dev 命令轨迹均排除自身，Codex automation execution records 又缺失，无法证明或排除 `PAUSED -> ACTIVE` 后 catch-up 补跑。
- 目标：不信任 scheduler 是否正确记账，由 08:00 collection、09:15 editorial、10:00 Topic Card 三个业务入口独立判断 activation freshness；错过时刻后的启动只记录拒绝，不得触发采集、04、卡片或 06，并留下可追溯 execution lineage。
- 核心合同：统一 Asia/Shanghai start-window guard；允许路径生成 atomic activation decision；collection outer 发行短期一次性 lease，Node non-check-only 必须绑定 automation/run/date/head/PID lineage 并消费；editorial 与 Topic Card send 同样需要 owned activation decision。拒绝路径仅写无秘密 append-only telemetry，包含 scheduled_for、next_run_at、execution_id、PID/PPID、cwd/head、child command hash 和完成状态。
- 零后门：不提供 `--force`、`--run-now`、环境绕过、stale lease、旧 run、cache 或 alternate entrypoint fallback；PM 授权恢复不通过通用 CLI 绕过 scheduled path。
- 验证：fake-clock 覆盖 22:10 catch-up、窗口边界、时区/回拨、lease tamper/replay/PID/head/cwd/run mismatch、direct Node/editorial/card 调用、denied no-business-side-effect、completion lineage；保留 AR-026/031/020E 全回归。不得 live catch-up experiment。
- 生产边界：三 automation 保持 PAUSED；开发/QA 不改 live TOML，不采集、不写 Feishu、不发卡、不触发 06，不改 Skill/SCF/Chrome/profile/production Git。完成 feature 自验后从 production `5e733cd` 建窄 RC，再做独立 QA与新生产授权。
- PM 决策：用户认为单次误触可接受，不需要为 scheduler 不可归因继续做重型 activation/lease 重构；本需求停止，不提交、不建 RC、不发布。事件证据和未归因结论保留，明日正常 scheduled chain 继续作为业务验收。

### AR-027 飞书 01/03/04 标签和表格列业务清理

- 类型：数据模型治理 / 飞书表结构清理
- 优先级：P1
- 状态：Schema Audit QA Passed / Waiting PM Cleanup Decision
- 来源：AR-020 方案确认时，用户新增要求：对飞书 01、03、04 的飞书标签和表格列做一轮筛查，对业务没用的统统删掉，不用考虑对历史数据的影响。
- 影响：01/03/04 是采集、内容收件箱和今日候选核心表。无用字段/标签会增加主编判断、脚本维护、测试回归和人工查看成本，也可能让字段语义混乱、错误来源继续混入。
- 目标：审计 01/03/04 的字段、标签、视图展示和脚本引用，删除业务无用的字段/标签，保留当前流程真正依赖的字段，并形成可重复的 schema cleanup 工具或报告。
- 范围：飞书 01 来源与采样、03 内容收件箱、04 分析与选题；字段/标签/单多选选项/视图列；脚本引用审计；删除前 dry-run 清单；删除后 schema/read-back 验证。
- 不在范围：不清理历史 03 数据内容；不改 06 表；不改 SCF receiver 业务逻辑；不把字段清理和选题评分混为一个验收。
- 业务规则：用户已授权“不用考虑历史数据影响”，因此无用字段可以删除，而不是只隐藏或保留兼容历史；但执行前必须 dry-run，证明不会删除脚本仍在读写的必要字段，避免误删业务字段。
- 验证方式：开发线程先输出 01/03/04 字段清单、脚本引用矩阵、建议删除字段/标签、保留理由；测试线程复核 dry-run；生产发布时先备份 schema / 输出删除报告，再执行删除，并做 01 read、03 write/read-back、04 finalizer dry-run 或 staging 等价验证。
- 备注：AR-027 可与 AR-020/AR-026 同一轮方案和开发推进，但验收口径分开：AR-027 验证“表结构干净且业务链路未断”，AR-026 验证“全量对标账号采集”，AR-020 验证“选题质量提升”。2026-07-06 开发线程已提交并 push `8adce16`，新增 `feishu_schema_cleanup_audit.py`，默认 dry-run 扫描 01/03/04 字段、代码/config/docs 引用矩阵；`--write-feishu` 显式拒绝。测试线程 Round 1 判定工具 smoke 通过但不足以满足完整目标，缺 view、字段填充率和业务可用性。Round 2 开发提交 `07be5a5` 已增强 schema audit：输出字段/选项/视图/填充率/样例使用矩阵，字段含 `fill_count/fill_rate/sample_values`，选项含 `reference_count/usage_count/recommendation`，并输出 views 概览和 `cleanup_matrix`；`--write-feishu` 继续硬阻断。真实 production read-only 审计报告 `/private/tmp/ar027_round2_schema_cleanup_production_readonly/feishu_schema_cleanup_dry_run.json`：01 字段 22、样本 51/51、视图 4、字段 delete 0、字段 manual review 1（`记录类型`，真实 36/51 有值，不能自动删）、option delete 4、option manual review 2；03 字段 25、样本 500/599、视图 4、option delete 2、option manual review 1；04 字段 35、样本 229/229、视图 7、字段/选项 delete 0。Round 2 独立 QA 通过：production read-only report 已包含字段/选项/view、样本记录、fill rate、sample values 和 cleanup_matrix，`--write-feishu` 仍硬阻断。PM 决策点：01 的 `记录类型` 与仍有真实 usage 的选项需人工确认；view 只能先列出并人工复核，不能自动删除。

### AR-021 腾讯云 SCF receiver 标准 CLI 部署通道

- 类型：发布工程 / 生产稳定
- 优先级：P2
- 状态：Backlog / Needs Plan
- 来源：2026-07-05 生产发布恢复过程中，production SCF receiver 部署卡在“本机无 `tccli` / 无自动部署脚本”，只能依赖腾讯云控制台手工上传 zip。用户确认本轮继续用控制台发布，但提出如果每次登录麻烦，可以考虑增加 CLI 通道。
- 影响：当前 SCF receiver 发布依赖人工控制台上传，容易出现无法部署、部署错函数、无法记录包 hash、无法自动 smoke、测试/生产函数混淆等问题。每次发布 AR-013/AR-015/卡片回调相关能力时，都会让发布窗口变长，并增加人为失误风险。
- 发布策略：不纳入当前 2026-07-05 发布窗口；后续在 `feature/next-production-flow` 中先设计方案，再开发标准化部署命令。不得把 CLI 凭证、SecretId、SecretKey 写入仓库或日志；不得直接复用生产部署作为测试。
- 验证方式：先与用户确认详细方案后再派发开发。最小目标应包括：分环境配置（test / production）、本地打包、zip hash 输出、部署前确认目标函数名/地域/命名空间、部署后 receiver challenge / health check、部署记录落盘、失败时不继续后续发布步骤。测试必须先部署 test SCF receiver 并验证真实测试卡回调，再由生产线程在发布窗口执行 production 部署 smoke。
- 备注：本需求是发布通道建设，不改变 receiver 业务逻辑，也不替代 AR-018 测试 App / 测试 Base 隔离。当前发布恢复仍按用户授权使用腾讯云控制台上传 production SCF 包；AR-021 后续解决“以后不用每次靠网页登录”的问题。

### AR-022 `run_topic_card_if_fresh.py --no-notify` 语义修正

- 类型：生产稳定 / 发布工程 hotfix
- 优先级：P1
- 状态：Hotfix Done / Synced to Dev
- 来源：2026-07-05 发布后最小 smoke 中，生产线程运行 `run_topic_card_if_fresh.py --no-notify`，发现该参数并不代表 dry-run/只读，仍会进入真实发送路径。飞书因同一 uuid 返回旧 message_id，没有产生新可见卡，但 ledger 增加了重复 pending/succeeded 记录。
- 影响：当前 `--no-notify` 名称容易让生产线程、测试线程或 PM 误以为它是只读 guard。未来 smoke、恢复排查或人工验证时，如果再次在生产目录运行，可能重复发送 Topic Card、污染 ledger、制造“删除卡又被复用 message_id”的混乱状态。
- 发布策略：建议走小 hotfix main，并同步回 `feature/next-production-flow`。不要混入其它发布内容。修复前，生产 smoke 不得再把 `--no-notify` 当只读检查；如需检查 fresh 状态，应使用只读函数/新增 `--check-only`。
- 验证方式：先确认真实期望语义：`--no-notify` 只是不发通知，还是应停止发送；建议新增显式 `--check-only` 作为真正只读入口，返回 fresh/sent-would-run/reason/run_id/candidate_count，但不调用 sender、不写 ledger、不写 decision card。单测需 mock sender 并断言 check-only 不调用发送；生产 smoke 只读验证不产生新 message_id、不新增 ledger send 记录。
- 备注：本需求不影响本轮发布主体收口；它是发布 smoke 过程中暴露的后续安全缺口。当前误发卡已删除，本次 smoke 未产生新可见卡。2026-07-05 production hotfix `3631bf2 fix: add topic card check-only guard` 已完成并 push：新增真正只读 `--check-only`，`pre_merge_check.py` 改用 `--check-only`；production 实跑 `--check-only` 输出 `sent=false`、`would_send=true`、`check_only=true`、`reason=fresh`、`run_id=run_20260705_102318`、`candidate_count=1`，且前后 ledger / decision card hash 和 mtime 不变；清理 16:03:32/16:03:33 两条误触发重复 ledger，保留 15:31 原始事故审计记录；备份 `output/logs/ar022_ledger_cleanup_backup_20260705.jsonl`，报告 `output/logs/ar022_ledger_cleanup_report_20260705.json`。dev 已通过 `c58dc57 fix: add topic card check-only guard` 同步并 push，RC 本地 `b63146b` 已同步未 push。

### AR-023 2026-07-06 抖音对标采集 Chrome CDP 启动失败

- 类型：生产恢复 / 采集稳定 / hotfix
- 优先级：P1
- 状态：Recovered / Hotfix Done / Synced to Dev / QA Passed / PM Accepted
- 来源：2026-07-06 08:00 定时任务启动成功，但抖音对标采集失败。用户反馈“Chrome 一直在用”，要求恢复生产数据，并修复找不到 Chrome 的根因，而不是只做最小恢复或只加 fallback。
- 影响：当天 8 点 run `run_20260706_080330` 已生成并写入 03，但抖音 CDP 两步均为 `optional_failed`，导致对标抖音来源缺失；这会进一步影响 AR-020 所关注的“对标账号内容进不来选题”问题。
- 已知证据：`daily_pipeline_2026-07-06.json` 中 `start/reuse background Douyin Chrome CDP` 返回 `launch_failed_or_not_ready`，stderr 为 `Unable to find application named 'Google Chrome'`；后续 `fetch daily Douyin homepage title/caption samples through Chrome CDP` 返回 `cdp_unavailable`，无法连接 `http://127.0.0.1:9333`；`/Applications/Google Chrome.app` 存在，Info.plist 显示 `CFBundleDisplayName=Google Chrome`；当前普通 Chrome 与测试 Chrome `--remote-debugging-port=9227` 在跑，但生产抖音专用 `--remote-debugging-port=9333` 未监听；`.local_services/douyin-chrome-profile` 存在。
- 恢复目标：同日恢复 2026-07-06 生产数据，补回抖音对标采样，重新形成同日 03/04/latest/logs 一致状态；不得只把 Chrome 拉起来就算完成。
- 根因修复目标：消除脚本对 macOS LaunchServices 应用名查找的脆弱依赖。需要明确为什么定时任务环境下 `open -na "Google Chrome"` 会失败，并把启动策略改成稳定、可测、可诊断的方式，例如显式二进制路径、bundle id、启动前预检或 LaunchServices 修复；不能只在旧逻辑后面“加 fallback”并忽略根因。
- 发布策略：生产线程先处理生产恢复与 hotfix main；修复后同步回 `feature/next-production-flow`。生产恢复写入仅限今天同日 run / 同日产物；不得写旧日期数据，不得绕过 Topic Card fresh guard。
- 验证方式：生产线程必须先只读记录当前 03/04/latest/logs 状态；启动专用 Douyin Chrome CDP 并验证 9333；补跑同日抖音采样；恢复 03/04/latest/logs；做 Feishu read-back / consistency；如果涉及卡片，只能走 fresh guard 或 `--check-only` 报告，不得手工绕过。hotfix 必须有本地测试或函数探针覆盖：Chrome app 存在但 LaunchServices 名称查找失败时仍可稳定启动；错误信息能指向可行动修复。
- 备注：本需求不是 AR-020 选题逻辑重构本身，但它会影响 AR-020 的对标账号数据基础。2026-07-06 生产恢复已完成并 hotfix main：production commit `6a4efed fix: launch douyin chrome by app path` 已 push。根因是原脚本依赖 `/usr/bin/open -na "Google Chrome"` 的 LaunchServices 应用名查找，定时/自动化上下文返回 `Unable to find application named 'Google Chrome'`；hotfix 改为 hidden/normal 生产 GUI 启动使用显式 Chrome app path，并保留 `CHROME_APP_PATH` / `CHROME_BINARY` 诊断。恢复 run `run_20260706_085249`：Douyin CDP step `ok=true`，source cache `status=ok`；03 `items=45`、`today_candidates=6`、`updated_existing=45`，其中抖音/对标视频 3 条来自 `ami.moment`；04 consistency `ok=true`、`local_rows=5`、`feishu_rows=5`；latest/logs/00 已收口；Topic Card 未发送，`--check-only` 返回 `reason=no_feishu_04_candidates_for_run`。dev 已同步并 push：`4f49826 fix: launch douyin chrome by app path`，与 production `6a4efed` 对应文件一致。测试线程只读复核通过：production/dev 两个 hotfix 文件 SHA256 一致，恢复 run / 03 / 04 / latest / logs / 00 证据一致，`output/decision_cards` 无 2026-07-06 新增卡片，production 与 runtime 06 输出无 2026-07-06 新包。PM 验收结论：用户目标“恢复生产数据 + 修复找不到 Chrome 根因”已达成；未声称全 50 账号覆盖验证。后续改进：Douyin CDP probe 需要 account-level timeout/progress telemetry，并在 AR-020 中继续治理对标来源质量与选题逻辑。

### AR-024 2026-07-06 抖音补采只恢复 3 条的根因与完整恢复

- 类型：生产恢复 / 采集覆盖 / hotfix
- 优先级：P1
- 状态：Recovered / QA Passed / PM Accepted
- 来源：AR-023 恢复后，测试证据显示抖音/对标视频只补回 3 条，且均来自 `ami.moment`。用户要求继续查明为什么只采集 3 条，并修复根因，而不是只接受最小恢复。
- 影响：如果补采只覆盖少数账号，03 内容收件箱仍不能代表用户配置的对标账号池，AR-020 所关注的“对标账号内容进不来选题”会被上游数据缺失继续放大。
- 已知线索：AR-023 生产恢复为了避免长时间不可见挂起，曾使用 `douyin-account-limit=3`、`douyin-video-limit=3`、`douyin-retries=1`；恢复结果只有 3 条抖音/对标视频内容，均来自 `ami.moment`。这可能是恢复命令限流导致，也可能叠加账号级 login/verification、采集脚本提前退出、账号池配置污染或无账号级可观测性导致的“看似成功但覆盖不足”。
- 恢复目标：查清“只 3 条”的第一性根因；在同日 2026-07-06 安全补采更多有效抖音对标账号内容；将 03/04/latest/logs/00 重新收口到新的同日 run 或明确记录为什么不能安全扩大；不得只做最小样例验证。
- 根因修复目标：如果是人为恢复参数限流，需要给恢复流程增加覆盖门槛/确认，避免再次把低覆盖恢复误判为完整恢复；如果是账号级失败，需要增加 account-level 失败列表、timeout/progress telemetry 和可行动错误；如果是配置污染或账号池错误，需要隔离非用户确认对标账号并回流到 AR-020。
- 发布策略：生产线程先做只读诊断，再按同日安全恢复执行。若需要代码 hotfix，走 production main 小修并同步 dev；若只是恢复参数/流程问题，也要更新 PM 记录和恢复 checklist。
- 验证方式：必须输出账号级覆盖表：计划账号数、实际尝试账号数、成功账号数、每个成功账号产物数、失败账号和失败原因；补采后 read-back 03 中抖音/对标视频条数和来源分布；04 consistency / latest / daily/scheduled logs / 00 主控台收口；Topic Card 只能用 `--check-only` 报告，不得发卡；06/Codex 不得触发。
- 备注：本需求与 AR-023 不冲突。AR-023 PM 已验收 Chrome 启动根因修复；AR-024 重新打开的是“数据恢复完整性”和“覆盖不足防复发”。生产线程已完成同日完整性恢复，未发现需要新增代码 hotfix：根因是 AR-023 为快速恢复人为使用 `--douyin-account-limit 3 --douyin-video-limit 3 --douyin-retries 1`，只尝试前三个账号；账号池实际有 39 个可选抖音账号，生产 daily 默认覆盖 12 个。AR-024 已按 12 账号重新补跑，生成 `run_20260706_092517`：成功 11 个账号、每个 3 条，共 33 条抖音浅层对标内容；`ami.moment` 因 `needs_login_or_verification` 未采信。03 `items=75`、`抖音=33`、`created_records=3`、`updated_existing=72`；04 finalizer 一致性 `ok=true`，2 条今日最值得做均被 5 日内重复去重跳过，`feishu_rows=0`；daily/scheduled logs 均 `ok=true`、`run_id=run_20260706_092517`、`recovered_ok=true`；00 refresh `ok=true`；Topic Card `--check-only` 返回 `reason=no_feishu_04_candidates_for_run`，未发卡；无 06/Codex 触发。账号级明细落盘：`output/spikes/douyin_cdp_source_watch_probe/cdp_probe_results.json` 与 `.csv`。测试线程只读复核通过：12 个尝试账号、11 个成功账号、33 条抖音素材、latest/logs/00 一致、无发卡、无 06/Codex。PM 验收接受：本轮完成的是生产默认 12 账号恢复与根因解释；不把它表述为 39/50 全量账号巡检。后续如要追全量账号覆盖或修复 `ami.moment` 登录/可信加载，需要另开任务。

### AR-025 生产恢复口径与验收规范

- 类型：PM 治理 / 生产恢复规范
- 优先级：P1
- 状态：Backlog / Needs Spec
- 来源：AR-023/AR-024 复盘。用户指出“生产恢复得有规矩”，且从未说过只做最小恢复，PM/生产线程不应自作主张把低覆盖恢复当作完成。
- 影响：没有明确恢复口径时，生产线程可能为了快速止血使用低账号数、低 retry、部分数据源或样例恢复，PM 又把“链路 ok”误判为“生产数据恢复”。这会让用户以为数据完整，实际仍缺上游覆盖，后续选题/卡片/06 都可能建立在不完整数据上。
- 目标：形成一套可复用的生产恢复规范，明确“恢复生产数据”默认等于正常业务口径恢复，而不是最小样例恢复；只有用户明确授权时才允许 limited/partial recovery。
- 范围：恢复前口径定义、覆盖目标、停止条件、风险授权；恢复中降级标识；恢复后覆盖证据、read-back、latest/logs/控制台一致性、发卡/06 边界；测试线程复核；PM acceptance 规则。
- 不在范围：不修改当前采集算法；不补跑历史数据；不重构 AR-020 选题逻辑；不把 AR-023/AR-024 已收口事项重新打开。
- 参考案例：AR-023 Chrome/CDP 启动失败恢复时人为限流 3 个账号，导致只有 3 条抖音内容；AR-024 证明生产默认 12 账号恢复可补到 33 条，并暴露 `ami.moment` 账号级登录/可信加载风险。
- 当前已固化：`docs/pm_operating_rules.md` 已新增“生产恢复规则”；全局 `multi-agent-pm-orchestrator` Skill 已新增 `Production Recovery Control`；长期记忆已新增 production recovery note。
- 验证方式：后续完成规范后，应以 AR-023/AR-024 为反例回放，检查规范能否阻止“低覆盖恢复被 PM Accepted”；再用一个虚拟生产恢复任务卡验证生产线程/测试线程/PM 三方职责清楚。
- 备注：这是治理类事项，不进入业务发布；后续由 PM 与用户一起细化方案后，再决定是否需要把规范转成脚本 checklist、preflight 或恢复报告模板。

### AR-028 2026-07-08 制作方向卡发送失败与腾讯云报警关联

- 类型：生产问题 / 卡片队列恢复 / 云端告警排查
- 优先级：P0
- 状态：Hotfix Deployed / Observe / Needs Logging Follow-up / No Card Re-send
- 来源：用户反馈 2026-07-08 第一张 Topic Card 选择后，第二张制作方向卡一直未发出，同时收到腾讯云监控报警，怀疑两者相关。
- 影响：用户已选择的 2 条候选停留在生产 04 的制作方向卡失败状态，无法进入用户补充制作方向和后续 06 ready 流程；如果队列/SCF 仍异常，后续生产卡片选择也可能继续卡在第二张卡发送阶段。
- 生产诊断结论：生产线程只读排查定位为 `D) queue triggered but SCF/Feishu send failed/interrupted`。第一张 Topic Card 已发送，用户选择 callback 已到达并成功写回生产 04；两个选中记录进入制作方向卡队列后被推进到 `发送中`，但发送任务未完成，随后 stuck detector 标记为 `发送失败`，错误为 `停留在发送中超过 15 分钟，可能上次定时发送中断`，`制作方向卡发送时间` 为空。
- 证据摘要：第一张卡 run_id `run_20260708_082027`，card JSON `output/decision_cards/2026-07-08_run_20260708_082027_topic_decision_card.json`，message_id `om_x100b6beeb7a11894c3f85b238cd05aa`；4 个候选 record_id 为 `recvoJXB7PB4s4`、`recvoJXB7Pz6Vn`、`recvoJXB7PZTPK`、`recvoJXB7PXuPV`。生产 04 read-back 显示 `recvoJXB7PB4s4` 与 `recvoJXB7PZTPK` 为 `状态=生成脚本包`、`选择提交批次=run_20260708_082027:ad38ec136bff`、`选择提交时间=2026-07-08T02:28:49.943Z`、`制作方向卡状态=发送失败`、`制作方向卡错误=停留在发送中超过 15 分钟，可能上次定时发送中断`；另两条为 `状态=不做`。
- 腾讯云报警关联判断：高度疑似相关，但告警具体指标未能在本机只读确认。生产 receiver challenge 当前正常，说明函数 URL 现在可达；但本机缺 `tccli` 且当前 Chrome 未提供腾讯云告警/日志页面，无法只读确认告警时间、资源、指标、阈值和函数日志。若用户收到的告警资源为 `feishu-topic-card-receiver` 或 `send-production-direction-cards`，时间在 10:30-10:50 左右，应判为相关；否则需告警截图或控制台只读证据补证。
- 生产边界：诊断期间未写生产、未重发卡、未触发队列、未点击卡片、未部署、未改代码；只做 Feishu GET/read-back、SCF challenge、文件日志读取。runtime watcher 10:02-10:48 均为 `ready_topics count=0`，未发现 06/Codex 生成；`output/script_packages`、`output/script_packages_latest_write`、runtime `output/script_execution_packages` 10:00 后无新文件。
- 用户新决定：今天这两张制作方向卡不补发、不 requeue、不清错触发发送；目标改为查清问题并修复根因，避免后续第二张卡再次卡住。用户明确表示腾讯云此前已授权，需要打开控制台/日志就打开，不要反复询问授权。
- 授权边界：允许生产线程打开腾讯云控制台/告警/函数日志做只读排查；允许在明确根因后执行不产生卡片副作用的最小生产修复，例如函数配置、代码 hotfix、部署或调度修复，并做不发卡 smoke。仍然禁止补发今天两张卡、重发第一张 Topic Card、点击卡片、触发会发送今天制作方向卡的队列、触发 06/Codex。
- 需要补证：腾讯云告警/函数日志需确认报警资源、指标、时间窗、错误/超时堆栈，判断是 `send-production-direction-cards` 逻辑异常、SCF 超时/资源限制、Feishu send API 失败、调度中断还是环境变量/权限问题。
- 验证方式：修复后必须验证 receiver/SCF health、方向卡发送 runner 的非发送 dry-run/空队列安全行为或等价 smoke；确认今天两条失败记录未被补发；确认未触发 06/Codex，生产 06/output/runtime 无新增；如有代码/config 变更，需记录 hash/部署时间/回滚路径并同步 dev。
- 生产 hotfix 回传：production `main` 已提交并 push `75801a8 fix: bound direction card feishu requests`。结构性根因为方向卡队列发送器先将记录标为 `发送中`，再调用 Feishu `/im/v1/messages`，成功后才写 `已发送/制作方向卡发送时间`；原生产代码对云函数内 Feishu `fetch()` 没有子超时，如果 Feishu send POST 或后续写回卡住直到 SCF 平台终止，`catch` 来不及运行，就会留下 `发送中 -> 15 分钟后发送失败`。
- hotfix 内容：`cloud_functions/feishu-card-receiver/src/receiver.js` 与 `tencent-scf/index.js` 新增 `FEISHU_API_TIMEOUT_MS` / `FEISHU_REQUEST_TIMEOUT_MS`，默认 8000ms；单次 Feishu API 卡住会 abort 并进入现有失败处理，错误带 method/path；新增方向卡发送 hang 回归测试，断言不会停留 `发送中` 而会写 `发送失败`。
- 测试证据：`npm test` in `cloud_functions/feishu-card-receiver` 20/20 pass；`node --check cloud_functions/feishu-card-receiver/tencent-scf/index.js` pass；`git diff --check` pass；receiver health/read-only 生产 04 检查 ok。
- 部署阻塞：本地 SCF zip 已生成但未安全部署到腾讯云 production function：`cloud_functions/feishu-card-receiver/dist/tencent-scf-feishu-card-receiver.zip`，SHA256 `34674fb06805777c5bbf5f79f3a94dc3033cc524cae5dfcde12ee72007af8845`。控制台找到 `本地上传zip包` 入口，但文件选择/上传导致 Chrome 控制接口失稳，生产线程未继续盲点生产控制台。因此云端 production SCF 仍可能是旧代码。
- 当前边界：今天两条记录 `recvoJXB7PB4s4`、`recvoJXB7PZTPK` 仍保持 `制作方向卡状态=发送失败`，未 requeue、未清错、未补发；10:00 后未产生新卡片或 06/Codex 输出。
- dev sync 回传：开发线程已将 production hotfix `75801a8` 回流 `feature/next-production-flow`，dev commit `418b32b fix: bound direction card feishu requests` 已 push。同步方式为 cherry-pick 后最小合并测试冲突；hotfix 核心逻辑已包含 `DEFAULT_FEISHU_API_TIMEOUT_MS`、`fetchWithTimeout`、Feishu request timeout error wrapping、receiver/SCF hanging direction send tests。dev `npm test` in `cloud_functions/feishu-card-receiver` 28 tests pass；`node --check`、`git diff --check`、`pre_merge_check.py` 通过；未写生产、未发卡、未部署 SCF、未触发采集或 06/Codex。
- production SCF 部署回传：生产线程已通过腾讯云控制台把 `75801a8` 对应 zip 部署到 production `feishu-topic-card-receiver`，广州区 / `default` namespace，function URL `https://1408808729-084yhdmeep.ap-guangzhou.tencentscf.com`；部署日志显示 `2026-07-08 13:28:25` 来源 `控制台`；线上代码页搜索 `DEFAULT_FEISHU_API_TIMEOUT_MS` 命中 `const DEFAULT_FEISHU_API_TIMEOUT_MS = 8000;`。本地包 SHA256 为 `34674fb06805777c5bbf5f79f3a94dc3033cc524cae5dfcde12ee72007af8845`。
- 部署后 smoke：`check_feishu_card_cloud_receiver.py` 返回 `ok=true`；receiver challenge ok；production 04 `tblz2CFc9eIa8bMG` read ok。今天两条 selected record `recvoJXB7PB4s4`、`recvoJXB7PZTPK` 仍为 `状态=生成脚本包`、`制作方向卡状态=发送失败`、`制作方向卡发送时间` 空、错误仍为 stuck message；未 requeue、未清错、未补发、未触发队列。`output/decision_cards` 10:02 后无新增；`output/script_packages`、`output/script_packages_latest_write`、runtime `output/script_execution_packages` 10:00 后无新增，确认无 06/Codex。
- 下一步：观察下一次真实第一张卡选择后的第二张制作方向卡；若再失败，应看到更具体的 `制作方向卡错误`，而不是只靠 15 分钟 stuck detector 泛化失败。另排 SCF 日志投递/告警字段化配置，补齐以后 RCA 的云端证据链。

### AR-029 腾讯云 SCF 日志投递与方向卡告警可观测性

- 类型：生产可观测性 / 云端日志 / 告警治理
- 优先级：P1
- 状态：Backlog / Needs Plan
- 来源：AR-028 RCA，以及 AR-016 飞书 03 update 读超时的剩余观测缺口。腾讯云控制台 `feishu-topic-card-receiver` 的“日志查询”显示函数尚未进行日志配置，导致 2026-07-08 方向卡发送中断无法回溯 stack/timeout；本机链路也需要能把请求 telemetry 与 sleep/network 恢复窗口关联，而不是只靠 04 状态和代码结构推断。
- 影响：如果未来 production SCF 再次因超时、Feishu API、环境变量、权限、调度或平台中断失败，PM/生产线程仍缺少云端调用日志、函数堆栈、执行时长、请求 ID 和告警字段，无法快速区分代码缺陷、Feishu 下游异常或云平台中断。
- 目标：为 production `feishu-topic-card-receiver` 配置可审计的日志投递/保留策略和告警字段化，让 card callback、`send-production-direction-cards`、stuck detector、Feishu send timeout 等关键路径至少能按时间窗、action、record_id/message_id 摘要、错误类型和 request id 查询；同时把现有 Feishu 请求 telemetry 与本机 sleep/network 恢复窗口形成可关联证据。
- 范围：腾讯云 SCF 日志投递配置、日志保留/脱敏策略、告警指标/阈值梳理、最小只读查询验证、失败报告模板。不得记录 token、secret、完整 payload、用户隐私或飞书敏感 ID 明文。
- 不在范围：不改变 receiver 业务逻辑；不补发 2026-07-08 两张制作方向卡；不触发真实 06/Codex；不替代 AR-021 CLI 部署通道。
- 验证方式：配置后用无业务副作用的 receiver challenge 或等价安全请求验证日志可查询；确认日志中不含 token/secret；输出可复用的“报警 -> 云端日志 -> 本机请求 telemetry -> record 状态”排查步骤。
- 发布编排：AR-029 与 AR-030 组成一个 `Production Reliability Pack`，共用 RC、全量回归和发布窗口；AR-029 保持独立验收，必须先提供可用于重试决策的日志与请求证据。

### AR-030 制作方向卡发送安全重试与状态未知恢复

- 类型：生产稳定 / 非幂等发送 / 恢复能力
- 优先级：P1
- 状态：Backlog / Needs Architecture Review
- 来源：AR-028 复盘。用户指出：如果系统已经知道制作方向卡发送失败，除了写失败记录外，首先应该考虑重试。PM 进一步确认：这是正确方向，但前提是区分“明确未发送、可安全重试”和“飞书可能已创建消息但响应丢失、状态未知”，避免重复发送第二张制作方向卡。
- 影响：当前 AR-028 hotfix 只解决“不要卡死在发送中、失败要尽快可见”；它没有解决安全自动重试。后续若 Feishu send API transient 失败且明确没有副作用，系统仍可能只落失败记录而不恢复；若盲目 retry，又可能让用户收到重复制作方向卡。
- 目标：为 `send-production-direction-cards` 建立安全重试和状态未知恢复机制：明确可重试错误自动有限重试；状态未知不自动重发，而是进入可审计、可人工恢复的状态；所有路径都能在 04 和 ledger 中解释清楚。
- 建议方向：发送前写 intent/operation id/message uuid；发送成功写 receipt；发送超时或网络中断后先做 read-back / message 查询 / ledger reconciliation；只有确认未发送或错误类型明确安全时才 retry；无法确认时标记 `发送状态未知` 或等价状态，阻断自动重发并给 PM/生产线程恢复入口。
- 范围：制作方向卡 Feishu message send、direction card queue 状态机、04 字段状态/错误文案、ledger/read-back/reconciliation、有限 retry 策略、staging/test 真实发卡验证、生产 no-duplicate guard。
- 不在范围：不补发 2026-07-08 两张已失败制作方向卡；不改变 Topic Card 第一张发卡策略；不触发真实 06/Codex；不替代 AR-029 SCF 日志投递；不把所有 Feishu POST 都盲目改成自动 retry。
- 验证方式：先做架构评审，明确飞书消息是否支持可靠 uuid/idempotency 查询；再用 mock 单测覆盖 `明确失败 -> retry`、`timeout but message exists -> no duplicate`、`timeout unknown -> 状态未知`、`max retry -> 失败可恢复`；staging/test 用个人测试目标验证不会重复发送；发布后 production smoke 只做空队列/只读健康，不对 2026-07-08 失败记录 requeue。
- 与 AR-029 区别：AR-029 是云端可观测性，回答“SCF 里到底发生了什么”；AR-030 是业务恢复能力，回答“失败后能不能安全自动恢复”。两者互补，但不能混成一个任务。
- 发布编排：与 AR-029 共用一个 `Production Reliability Pack` 发布计划，但状态机、重试安全、unknown、幂等和 no-duplicate 必须独立验收；不得因共用 RC 而互相代替通过。

### AR-014 飞书写入链路 RCA 与系统性防复发

- 类型：生产稳定 / RCA
- 优先级：P1
- 状态：Hotfix Done / Synced to Dev
- 来源：用户指出 AR-012 虽已恢复，但生产问题根因和后续如何避免相同问题尚未完整排查。
- 影响：AR-012 已解决 2026-07-04 当日恢复和 `content_sampler.py` 局部超时重试/恢复入口，但尚未完整确认飞书写入链路的系统性风险：哪些写入仍无 retry/backoff、哪些阶段仍可能在写出可恢复日志前失败、失败通知是否足够用户可见、次日定时任务是否有自动恢复或清晰跳过策略。
- 发布策略：生产稳定优先；用户已授权生产稳定性 hotfix。该 hotfix 不再处理 2026-07-04 数据恢复，而是治理飞书写入链路的 retry/backoff、checkpoint、失败可见和幂等恢复。
- 验证方式：只读审计 AR-012 前后日志、`push_to_feishu.request_json` 全局调用点、03/04/00/06/通知写入路径、`content_sampler_log` 落盘时机、失败通知内容和恢复命令可见性；输出 RCA 报告，明确已被 hotfix 覆盖的风险、未覆盖风险、优先级和是否需要再次 hotfix。稳定性 hotfix Ready 前必须有本地单测/函数探针覆盖 transient timeout retry、非幂等 batch_create 不盲目 retry、失败日志可恢复、关键写入路径不回退。
- 关联分支/提交：`00036d9`, `a0e62b3`
- 备注：RCA 确认直接根因是飞书 03 单条 update 请求 30 秒读超时；放大原因是写 03 期间缺少 per-record retry/backoff、`content_sampler_log.json` 在网络写完后才落盘、缺少从已有 run 产物补写 03 的安全入口、失败通知/日志不足以直接恢复。AR-012 hotfix 已覆盖直接故障路径：03 单条 update 3 次 retry/backoff、已有 run 恢复 CLI、pending/partial/success 日志镜像，但这只解决“数据后续可恢复”和 03 update 局部稳定，不等于生产链路稳定。2026-07-04 AR-014 生产 hotfix 已完成并 push：公共 `push_to_feishu.request_json()` 增加 transient 错误分类和安全 retry，默认只对 GET/PUT/PATCH/DELETE 重试，POST 默认不重试；通知失败会落 `delivery_status=unknown` 本地 JSONL；03 update 复用公共 transient 分类并避免双层 retry。非幂等 batch_create / 真实发卡不盲目 retry，后续仍需 checkpoint/read-back/idempotency 设计。开发线程已通过 `a0e62b3 chore: sync AR-014 feishu retry hotfix` 同步回 `feature/next-production-flow`。

### AR-015 非幂等飞书写入 checkpoint / read-back / idempotency 设计

- 类型：生产稳定 / 技术债
- 优先级：P1
- 状态：Released / Minimal Smoke Passed
- 来源：AR-014 hotfix 后遗留风险：`batch_create`、真实发卡、06 create 类 POST 等非幂等动作不能盲目 retry，否则可能重复创建记录、重复发送卡片或造成状态不一致。
- 影响：AR-014 已让安全/幂等请求具备 transient retry，但非幂等写入遇到超时仍只能标记“状态未知”。如果没有 checkpoint、read-back 或 idempotency key，后续仍可能出现“实际已创建/已发送，但本地不知道”的断点，尤其影响批量新增、真实选题卡发送、06 记录创建、04 标记已生成等链路。
- 发布策略：跟随 `feature/next-production-flow` 做设计和 staging/test 验证；若生产再次出现非幂等状态未知事故，可升级为 hotfix main。不得用真实生产发卡或生产表写入做试验。
- 验证方式：先做方案审计和函数级测试：为 `batch_create` 设计先读后写、业务唯一键、写后 read-back 或分批 checkpoint；为真实发卡设计发送前本地 intent、发送后 receipt、超时后状态未知告警、根因归类和人工确认策略；为 06 create 类 POST 设计 record-id/read-back/重复检测。staging/test 需使用隔离表、测试通知目标和测试卡片，验证不会重复创建、不会重复发送。任何 `unknown` 不能只作为终态沉淀，必须保留可定位证据、恢复建议和后续优化入口。
- 关联分支/提交：方案 `c715e44 docs: design AR-015 feishu idempotency plan`；实现 `cbf4a9a feat: add feishu idempotency ledger`
- 备注：本需求不替代 AR-013 补偿池。AR-013 解决“失败当天候选如何后续补发/补看”，AR-015 解决“非幂等飞书动作超时后如何判断是否已发生、如何避免重复执行”。优先级建议高于一般体验优化，但不要求今天继续抢 hotfix，除非生产出现新的非幂等状态未知事故。2026-07-04 方案设计已完成：核心路线是业务唯一键 + intent/receipt ledger + read-back + unknown 守卫，不扩大 `POST` retry。用户补充确认：`unknown` 是安全刹车，不是结束状态；后续必须定位根因并做优化。2026-07-04 PM 决定启动 Phase 1：优先做 04 `batch_create` 与 Topic Card 发送 intent/receipt/read-back/unknown guard，不碰生产写入，不混入 AR-013。开发提交 `cbf4a9a`，测试线程独立 QA 通过；2026-07-05 PM 复核后纳入本轮 RC，RC Full Regression 已通过。发布准备项：确认 `output/feishu_write_ledger/` 可写，发布前无 blocking unknown；发布后如出现 `unknown_*`，发卡 guard 应阻断并按 ledger/read-back 恢复，而不是人工绕过。

### AR-016 2026-07-04 飞书 03 update 读超时深层根因定位

- 类型：生产问题 / 深层 RCA
- 优先级：P1
- 状态：RCA Complete / Residual Scope Merged into AR-029
- 来源：用户追问 AR-012/AR-014 事故根因仍不完整：当前只证明了 `socket.timeout: The read operation timed out`，尚未判断为什么超时，是本机网络波动、飞书 API 服务端慢、请求/记录特征、代理/VPN、系统睡眠/网络切换，还是链路缺少请求级观测。
- 影响：如果只把“飞书超时”当根因，会漏掉真正可优化点；后续即使已有 retry，也可能重复出现长尾慢请求、API 抖动或本机网络异常，只是被 retry 掩盖。
- 发布策略：生产只读诊断优先；不写生产业务表、不发卡、不触发采集。若只读 RCA 发现代码观测缺口，可作为 dev 任务补请求级 latency/operation log；若发现生产环境配置或网络问题，再走生产运维修复。
- 验证方式：已完成生产只读 RCA：直接故障定位到 03 单条 `PUT /bitable/v1/apps/{app}/tables/{table}/records/{record_id}` 已发出后，在读取 HTTPS 响应状态行阶段超过 30 秒；没有 429/5xx/固定坏记录证据；事故窗口与 macOS Maintenance Sleep / DarkWake / 网络恢复高度重合。后续需补 Feishu 请求级 telemetry，记录 method/path/table/record_id/payload_size/duration/attempt/error_kind/status_unknown/local network snapshot。
- 关联分支/提交：
- 备注：AR-014 已解决“同类 transient timeout 不应轻易打断链路”的稳定性问题；AR-016 RCA 结论是：已证实卡在 HTTPS 响应状态行读取；高概率是本机 Maintenance Sleep / DarkWake 后网络栈恢复窗口导致单点长尾读超时；无法证实飞书服务端是否内部处理慢、失败记录是哪一条、当时实际路由是否经过 VPN/分流。AR-005 keepawake 和 AR-017 请求级 telemetry 已覆盖直接防复发动作；剩余跨端日志关联与告警证据统一并入 AR-029，不再以 AR-016 独立排队。

### AR-017 Feishu 请求级 telemetry

- 类型：生产稳定 / 可观测性
- 优先级：P1
- 状态：Hotfix Done / Dev Equivalent
- 来源：AR-016 观测缺口：事故日志没有记录每个 Feishu 请求的 method/path/table/record_id/payload_size/duration/attempt/error_kind/local network snapshot，导致只能间接判断 timeout 深层原因。
- 影响：没有请求级 telemetry 时，未来即使 retry 生效，也可能把长尾超时、飞书慢响应、路由/VPN 抖动或特定记录问题掩盖掉；PM/生产线程无法判断应继续优化网络、请求层、record payload 还是飞书状态处理。
- 发布策略：hotfix main；用户已授权今晚同步生产。不得记录 token、cookie、完整 payload、用户隐私内容；生产发布仅做最小代码同步和只读/函数级 smoke，不做真实 Feishu 写入探针。
- 验证方式：函数级测试模拟成功、timeout、HTTPError、retry 多 attempt，检查 telemetry 只记录脱敏 method/path/table/record_id/payload_size/duration/attempt/error_kind/status_code/status_unknown；staging/test 或只读生产 smoke 仅验证日志文件可写和不含敏感信息，不做生产写入探针。
- 关联分支/提交：dev `08685fb feat: add feishu request telemetry`、`6eaf223 fix: redact feishu telemetry paths`；production `70e16c8 feat: add feishu request telemetry`、`9e2faf3 fix: redact feishu telemetry paths`
- 备注：排期建议在 AR-005 配置 hotfix 完成后立刻推进。AR-005 先处理“明天生产窗口必须完整唤醒”；AR-017 再处理“如果仍异常，必须能定位到请求级根因”。AR-017 不替代 AR-015，前者是观测，后者是非幂等动作的 checkpoint/read-back/idempotency。2026-07-04 测试线程 QA 主体验证通过，但发现两项非阻断风险：`records/batch_create` 被误识别为 record_id；旧 warning/exception 仍可能打印 raw path。PM 门禁已要求窄返修；开发提交 `6eaf223` 后测试线程 Round 2 复测通过。生产线程已通过 cherry-pick 同步并 push `70e16c8`、`9e2faf3`，未写生产表、未发卡、未触发采集；开发线程已确认 dev 与 production hotfix patch-equivalent，无需重复 cherry-pick。下一步是明天生产窗口只读观察。

### AR-033 Partial Collection Downstream Usability + Persistent Editorial Skill Release Manifest

- 类型：生产链路恢复 / collection-to-editorial contract / Skill release provenance
- 优先级：P0
- 状态：Development In Progress / Needs RC + QA
- 来源：2026-07-16 生产 run `run_20260716_080311`。08:00 collection 的 canonical 9333/profile/login 通过，31 个抖音账号全部 attempted，29 succeeded、2 failed；失败账号 `铁锤人`、`歸藏 guizang.ai` 均 `artifact_count=0` 且被隔离。03 已写入，`today_10_topics.csv` 9 行存在，但 outer scheduled log 因 account-level partial 标记 `failed_or_partial`，10:00 Topic Card 因 `today_daily_pipeline_log_not_ok` 跳过；09:15 check-only 通过但缺少 Git-managed persistent Skill release manifest，外层任务无法机器验证 release evidence。
- 目标：保留 `full_collection_success=false/completed_with_failures`，新增独立 `downstream_usable` 合同，允许账号级 typed failure 隔离后成功候选进入 09:15；同时增加持久 Skill release manifest，使 `ar020e_daily_editorial_entrypoint.py --check-only` 自动验证 repo/global/manifest 三方一致。10:00 Topic Card 改为要求同日 exact run、`downstream_usable=true`、09:15 finalization/04 latest_write/read-back green 和原有 freshness/owner/card guards。
- 边界：不得重跑采集、不得修改历史 03、不得写 04/发卡/触发 06；开发线程只产出代码、测试、production-base narrow RC 和今日恢复 read-only/check-only 证据。发布后恢复 04 写入和个人选题卡发送需由生产/PM 授权线程执行。
- 验收：对抗测试覆盖 29/31 isolated partial usable、failed rows leak、plan incomplete、login/global preflight fail、lineage mismatch、zero candidate、card guard 等；manifest 测试覆盖 exact pass、missing/malformed/unknown/hash mismatch/repo-global drift；RC 从 production `5e733cd` 逐 hunk 组装，排除 PM docs 和无关 AR。
- 2026-07-16 发布结果：fresh RC `e6f04c547d70745c65b88d08aa2c4a9694b732fa` 已通过独立 QA 并发布到 production main；downstream usability、Skill release manifest 和 source commit identity gate 均已生效。
- 今日恢复阻断：授权输入 `output/runs/run_20260716_080311/today_10_topics.csv` 为 9 行，但 current-task state machine 的 `prepare-source-open` 从 `content_items.csv` 重算为 8 行，并替换/遗漏 source identity。生产线程已在 04/card 前停止，04 对该 run 仍为 0，Topic Card 未发送。
- AR-033B 用户确认：新增 exact same-day candidate input 模式，直接绑定 9 行 CSV 的 run/date/order/URL/fingerprint/file SHA，不调用 shortlist/resampling，不允许缺失、额外、重复、重排、替换或 URL 漂移。候选级 source/research 失败保持 typed visible，且不得以其他候选补位。
- AR-033B 当前状态：`Released / Recovery Blocked`。fresh RC=`8af084621d01e639c54b5dc847a6439ce96fd8bd` 已发布到 production main；exact 9 行 source-open 5/9、research 3/9、Stage1 3/3、ranking 3/3 后，Stage2 因 AIHOT actionable 候选要求 `AIHOT重大性说明`、但 Stage1/Stage2 均没有合法 owner 而系统性阻断。04 仍为 0，Topic Card 未发送，三条 automation 保持 PAUSED。
- Automation 联动：三条任务当前 PAUSED；用户手工改成 projectless 后 cwd 实际变为 `~`，不可直接恢复。后续只允许通过 official automation control 保留 projectless、当前模型、prompt、schedule 等字段，仅将 cwd 改回 production repo；若官方接口不支持则停止报告，不手改 TOML、不重新创建项目。

### AR-034 Full-Source Ingestion Closure + Canonical WeChat Provider Session + AIHOT Owner

- 类型：生产数据正确性 / 多来源采集闭环 / 固定认证运行时 / editorial owner contract
- 优先级：P0
- 状态：AR-034D Production Authorized / Running / Automations Remain Paused
- 来源：对 `run_20260716_080311` 的生产只读复核发现，抖音 probe 实际为 31 attempted、29 succeeded、2 failed，并产出 87 条有效成功账号 items；但 `daily_pipeline.py` 把 account-partial 映射为 `optional_failed=true`，随后整份 Douyin manual artifact 未进入 combined input。最终 `content_items.csv` 为 AIHOT 53、公众号 5、抖音 0；`today_10_topics.csv` 为 AIHOT 8、公众号 1、抖音 0。Feishu 03 同 run 关联记录为 AIHOT 36、公众号历史记录 5、抖音 0。因此当前 9 条不是全源比较结果，不得继续作为 04/Topic Card 恢复输入。
- 公众号根因：唯一 active 公众号源的 provider 缓存仍是 2026-06-11 至 2026-06-16 的 5 篇旧文章；`ai-radar-wewe-rss` 自至少 2026-07-10 起反复报告 `暂无可用读书账号!`。现有 readiness 只证明 HTTP 能返回可解析缓存，没有证明账号可用、feed 刷新成功、内容新鲜或本次新增。因此旧缓存被错误记为今日采集。
- 抖音闭环：`completed_with_failures` 必须保留所有成功账号的有效 items，只隔离失败账号；成功 artifact fingerprint 必须可审计地进入 combined input、`content_items.csv`、Feishu 03 和 shortlist universe。`downstream_usable` 必须验证逐层来源闭环，任一成功 artifact 丢失即 false；对外状态必须明确为 partial，不能称全量成功。
- 公众号 freshness 合同：新增 `updated_with_new_items / updated_no_new_items / stale_cache / login_required / provider_failed`。只有 provider 账号健康且本轮刷新有可核验时间/版本证据时，结果才可作为本日来源；确实无新文章时允许 `updated_no_new_items`，不能为了凑数量复用旧缓存。历史文章可保留，但不得标记为今日新采集。
- 公众号固定登录运行时：daily automation 只检查固定 `wewe-rss` provider 的账号和更新健康，不自行寻找、启动或切换浏览器。需要重新认证时才使用独立于抖音 9333 的固定端口、固定 canonical Chrome profile、profile identity marker、PID/WebSocket/open-file proof，并只打开本机 `wewe-rss` 管理/登录页。不得读取或导出 cookie/token/localStorage，不得随机使用任意线程浏览器。profile、provider data dir 与迁移方案必须脱离 worktree 且可备份回滚；真实迁移/扫码另需生产授权。
- AIHOT owner：Stage1 是 evidence-bound `aihot_significance_rationale` 及 evidence IDs 的唯一语义 owner；Stage2 只能 locked pass-through 到 `AIHOT重大性说明`，新增、改写、丢失或 deterministic fallback 均 fail closed。不得放宽正式字段合同或手改恢复 artifacts。
- 恢复策略：保留原 run 和错误 9 行作为事故证据，不在其上继续 Stage2。允许复用已保存的 87 条抖音成功 artifacts，但必须排除 5 条陈旧公众号缓存；公众号在授权生产线程完成固定认证/刷新后，以真实 fresh result 与同日 AIHOT snapshot 构建版本化 recovery run。先证明各来源进入 comparison universe，再由质量排序自然产生 0..N，不设置来源数量配额。
- 验收：开发须从当前 production base 组装窄 RC；测试覆盖 partial-success ingestion、fingerprint 逐层 bijection、failed-account zero artifact、stale/login/provider/empty freshness 状态、wrong browser/profile/port fail-closed、AIHOT owner mutation、AR-020E/031/033 adjacent regression。独立 QA 通过后，生产授权顺序为 provider migration/login/read-back、fresh WeChat refresh、recovery run、03 exact write/read-back、current-task editorial、04/read-back、card check-only/一次个人发送、official cwd repair、status-only resume。
- 边界：开发与 QA 不运行真实采集、不写生产 Feishu、不发卡、不触发 06、不改 automation/Chrome/profile/provider data/global Skill/SCF/production Git；任何生产认证、迁移、刷新、写入与恢复必须另行授权。
- RC7 开发回传：`release/ar034-rc7-20260716@fe09651b2b1cf6457f398b0253ddaa435abcd610`，parent=`8af084621d01e639c54b5dc847a6439ce96fd8bd`，28-file patch SHA=`acccdfb479335077904a67ec10d10b9f2632b791ac4a6a0aa007ceabe0c94afb`，tree=`e2b215428502d4b8691c4f7752da04cfbb03f9a3`。Git/manifest/真实 87 行只读复算与 8 项专项单测均可复核，但 PM evidence gate 未通过，未派 QA。
- RC7 blocker 1：legacy validator 以调用者传入的 daily log 反推 `production_root`，未绑定 configured production root。PM 用 `/private/tmp` 自造同构 daily/probe/manual 三件套调用公共 CLI，得到 exit 0 与 `legacy_attestation_verified=true`；因此当前“canonical path”只约束目录形状，不能证明是生产原件，locked report 也可由同一伪造根生成。
- RC7 blocker 2：daily step 的 `returncode/optional_returncode` 通过裸 `int()` 转换。PM 将 `returncode` 改为非数字字符串后，公共 CLI exit 1 并输出 traceback，没有返回单一 JSON typed failure。任何 malformed evidence 都必须 fail closed 且机器可读。
- AR-034B 窄返修门：公共 CLI 必须把 legacy 三件套绑定到 configured production root（production 模式不得由输入路径自推；测试可使用显式注入的 expected root），并在初检与 prewrite revalidation 两次都检查；arbitrary temp root、root override、symlink/alias 必须阻断。所有外部 evidence 字段须 exact type/schema 校验，malformed int/list/bool/null/unknown key 均返回 typed nonzero JSON，不得 traceback。RC6 native validator与其26-file合同保持字节/行为不放宽；完成 fresh RC8 与完整回归后再做独立 QA。
- RC8 开发回传：`release/ar034-rc8-20260716@af0e4e520cefcacb0efa770992a34a2778b9d36f`，parent=`8af084621d01e639c54b5dc847a6439ce96fd8bd`，28-file patch SHA=`abea1284baf80e0c687373dcc65ac149ee67388719f9e2ba47cdb822c7b556dd`，tree=`fc278ad966acc6e1f24e28082f98570986caef33`。arbitrary evidence root 与 malformed schema 两个 RC7 阻断已关闭；真实 87 行 initial/locked 公共 CLI 独立通过。
- RC8 剩余 blocker：公共 CLI 在定义 configured root 时先执行 `(ROOT.parent / "ai_account_radar").resolve()`，导致 raw configured path 是否为 symlink 的事实在 validator 前丢失。PM 构造相邻拓扑，让 `ai_account_radar` symlink 指向伪造 production tree；公共 CLI exit 0 并返回 `legacy_attestation_verified=true`。因此 RC8 不满足已明确的 symlink/alias 阻断门，未派 QA。
- RC9 单点门：保留 raw configured path，不得在身份检查前 resolve；使用 `lstat` 或 directory fd `O_DIRECTORY|O_NOFOLLOW + fstat` 验证 directory、非 symlink、current UID、唯一 canonical realpath，再把已验证 root传给 initial/locked validator。公共 CLI 相邻 configured-root symlink、path swap、alias都必须 typed fail；正常真实 production root仍通过。RC8 schema/type修复与RC6合同不重做、不放宽。
- PM 威胁边界纠正：上述 RC9 单点门已取消。它把受信 production 线程执行的一次性历史迁移，扩展成防同一 Unix 用户主动替换相邻项目目录的攻击模型，超出用户目标和本次生产恢复边界。真实 production root 已只读确认是固定普通目录、非 symlink，production main clean；RC8 已绑定该固定根，并对真实原件完成 initial + locked prewrite 两次重开校验。因此 RC8 恢复为独立 QA 候选。
- RC9 历史：停止消息到达前已产生 feature `9a739d82cce3f8e60c942abb4f4de1d70e107015` 与 RC9 `87e16909271bb10dc4ecd276f8cf9422ae0048e8`。两者仅保留审计历史，不作为 PM 验收或发布候选，不回滚、不改写、不继续测试。
- 接受的残余风险：同一受信 Unix runtime identity 若主动替换项目目录或代码，可绕过纯路径身份检查；这与现有 HMAC key 的已接受本机信任边界一致。本轮 QA 不再构造同用户恶意 root replacement/symlink 攻击；重点验证实际 production root、旧原件 31/31、29/2、87 行闭环、prewrite 重验、RC6 native 合同、WeChat signed refresh、AIHOT owner 和全回归。
- RC8 独立 QA：fresh clone 的28/28 scope/manifest/patch/apply/tree通过；真实 production originals initial + locked prewrite均为31/31、29/2、87 items，失败账号零产物，manual SHA=`5af4d08662fddc7b09f8c0c906288cf36f6ade5d9ee01fad5270932ba001f496`，三原件前后SHA/size/mtime不变。独立8/8 legacy mutation、AR-034 50/50、Python 387/387、receiver 32/32、Douyin Node 8/8、semantic 7/7及supported pre-merge全部通过。状态进入新的生产授权请求，不沿用RC6授权。
- 用户生产授权：已确认 RC8 `af0e4e520cefcacb0efa770992a34a2778b9d36f` 的28-file生产发布与恢复计划。允许暂停/备份、Git release/gate、canonical WeWe key与一次bounded signed refresh、旧87条initial+locked revalidation、版本化full-source与03 exact write/read-back、current-task/04 exact write/read-back、card check-only后一次个人发送、official projectless cwd repair与status-only resume。禁止Douyin重采集、旧错误9行、public async refresh、手工receipt、schema/callback/06/global Skill/SCF/Chrome/profile/raw TOML。任一gate/read-back不一致立即停止并保持automation PAUSED。
- 生产 preflight：RC8 scope/legacy 31/29/2/87通过，但在任何Git/key/refresh/Feishu写入前发现真实 provider仍挂载 production repo `.local_services/wewe-rss/data -> /app/data`，RC8只接受 canonical `~/.codex/ai-account-radar-runtime/providers/wewe-rss/data`；同时容器内仅有masked `AUTH_CODE`，主机环境缺少adapter要求的`WEWE_RSS_AUTH_CODE`。因此状态降为Provider Runtime Blocked，生产零变化，三automation保持PAUSED。
- 下一授权：按已发布候选内 `docs/wewe_rss_runtime_runbook.md` 的Authorized migration执行一次可回滚迁移：停止exact provider、离线完整备份/复制并校验SQLite到canonical data、用同镜像和canonical mount重建容器、安全把现有private auth接到host runtime env、metadata/read-only health通过后再从RC8 Phase 0重启。不得输出secret、不得扫码/切随机浏览器；若登录失效，另停在login_required等待用户交互。
- 用户确认 provider migration，并明确授权迁移 read-back 通过后无需再次确认，自动继续既有 RC8 发布与版本化全源恢复。迁移只改变 provider data mount/auth wiring：保持三 automation PAUSED，normal-stop exact container，离线复制/校验 SQLite 到 canonical runtime，用同容器名/镜像/端口重建并只读核验 mount、DB、account/feed 与 masked auth presence；不得在迁移阶段刷新。若健康状态为 `login_required`，只允许固定 9334 canonical 登录入口并停线等待，不得随机找浏览器。
- 自动续跑边界：迁移健康通过后从 RC8 Phase 0 fresh 重启，执行 Git release/gate、canonical key、一次 bounded signed refresh、旧 Douyin 87 条 initial+locked revalidation、versioned full-source/03/watermark、current-task/04/read-back、card check-only 后一次个人发送、official projectless cwd repair 与 status-only resume。任何 scope/mount/DB/auth/receipt/Feishu/card/cwd/read-back mismatch 均立即停止、保持 PAUSED，并仅回滚失败组件。
- Provider migration 结果：Passed。exact container 已迁到 canonical data mount，before/after DB SHA、SQLite integrity、1 account、1 active feed、48 articles 完全一致；host/container auth 均仅以 masked presence 核验并使用 current UID + 0600。旧 repo-local 容器保留为 stopped rollback anchor，production Git仍为clean `8af0846`，RC8未发布，key/refresh/03/04/card均未发生。
- 当前登录门：RC8 check-only 返回 `login_required`，且 `refresh_requested=false`、`secret_material_read=false`、`secrets_exposed=false`。下一步只允许从 exact RC8 worktree运行固定 `start_wewe_rss_admin_chrome.py --foreground`：9334 + canonical profile + local `/dash`；不得发现/切换随机浏览器。若出现QR/SMS/MFA，只能由账号所有者在该固定窗口完成。登录后必须以check-only读回 `ok=true/status=refresh_required`、active account/feed一致且仍无refresh，才可自动继续既有RC8授权。
- 用户已确认 fixed 9334 login authorization。固定生产线程可启动 exact RC8 launcher 并完成非秘密 UI 操作；若出现QR/SMS/MFA，则停在同一 canonical 窗口等待账号所有者完成。读回 green 后无需再次确认，自动继续既有 RC8 Phase 0；任何 profile/listener/provider/account/feed/check-only mismatch 保持PAUSED并停止。
- Fixed login 执行结果：9334 PID `72440`、canonical profile、listener/marker/WebSocket/open-file proof全部通过，唯一页面为 `http://127.0.0.1:4000/dash/login`。平台要求账号所有者在当前固定窗口完成登录；未读取/截图QR、账号身份、cookie、token或localStorage。provider check-only仍为 `login_required` 且零refresh/secret read，RC8未发布，三任务PAUSED。
- Secret injection 结果：本机安全审查拒绝自动填入 existing `WEWE_RSS_AUTH_CODE`，因为可能在既有 protected wiring 之外产生 plaintext。未读取、输出、落盘或复制secret，也未尝试clipboard/AppleScript绕过。推荐的唯一自动路径是用户明确授权“本机内存读取 + CDP直接填入固定local页面”，并继续禁止落盘、日志、clipboard和任何secret回显。
- 用户已明确授权受控内存注入：仅允许本机进程内存读取existing owner-only auth并通过CDP填入fixed 9334 local页面；继续禁止disk/log/clipboard/screenshot/回显/临时secret文件。production线程已派发；登录后必须先通过零refresh check-only，才可自动续跑既有RC8授权。
- 受控注入结果：admin auth已接受，fixed 9334从 `/dash/login` 进入 `/dash`，secret全程未落盘、输出、clipboard或截图。但provider内部账号仍为status=0，check-only继续 `login_required`；这是公众号账号会话层，不是admin auth层。RC8未发布、三任务PAUSED。已派只读审计确认正式re-login/reactivation入口，禁止把dashboard刷新按钮盲当登录动作。
- Reauth RCA：exact provider在上游返回 `WeReadError401` 时自动将account status置0，文章抓取只选择status=1账号；直接改status不是reauth。唯一受支持续期路径为fixed 9334 `/dash/accounts` 的“添加读书账号”：UI生成二维码、账号所有者扫码、UI polling后按account id upsert新token/name/status=1。未点击、未生成二维码、未调用mutation。
- 最小授权计划：`/private/tmp/ar034b_wewe_reauth_readonly_20260716_2200/plan/PROVIDER_ACCOUNT_REAUTH_AUTHORIZATION_PLAN.md`，SHA256=`12c7641c7f3f920c8dcfa92669ef76d8554bd407de331f39282ad24b6985b7cb`。只允许fixed窗口一次add-account + owner QR；禁止手改DB/status、直接API、refresh或其他browser/profile。green门为DB/feed/article identity无漂移、active account>=1、provider check-only=`refresh_required`且零refresh。
- 用户已明确授权公众号账号重新登录。production线程只可在fixed 9334 `/dash/accounts` 点击一次“添加读书账号”并等待owner扫码；仅允许UI自身polling和按account id upsert。二维码内容、账号身份和认证secret不得读取或记录。green后自动回fresh RC8 Phase 0；失败保持三任务PAUSED。
- Reauth/release结果：fixed 9334一次add-account经owner扫码及UI polling/upsert成功，account status1=1；provider check-only=`ok=true/status=refresh_required`且零refresh。RC8已从production `8af0846` fast-forward/push到 `af0e4e520cefcacb0efa770992a34a2778b9d36f`，dynamic gate通过；HMAC key按0700/0600原子provision，secret未输出。
- Date-boundary stop：当前已是2026-07-17，原授权universe绑定2026-07-16 Douyin+AIHOT+same-day WeChat。为避免把7/17 refresh混入7/16批次，production在signed refresh前停止；无refresh/03/04/card/collection/06，三任务保持PAUSED。推荐关闭7/16恢复，改为一次完整7/17同日全源运行。
- 用户已确认一次完整2026-07-17同日生产运行：新run全量Douyin、same-day AIHOT、一次signed WeChat refresh，闭环后写03、执行current-task并写04、read-back后一次personal Topic Card；不复用/改写7/16 run，不触发06。业务完成后仅通过official control修复production cwd并status-only恢复三任务；任一gate失败保持PAUSED。
- 7/17执行结果：run=`run_20260717_093104` 的唯一一次signed WeChat refresh成功，48->67 articles，new_item_count=19，receipt SHA=`617754496d...`，signature/live DB closure全绿。因first-release canonical `health/last_success.json` 缺失，gate把有效receipt归类 `stale_cache`；Douyin/AIHOT尚未启动，无03/04/card/06，三任务PAUSED。
- 一次性修复计划：`/private/tmp/ar034b_same_day_20260717_093048/final/WATERMARK_BASELINE_REPAIR_AUTHORIZATION_PLAN.md`，SHA256=`97e2fc503aefa99be567b3fc180523ad012b606075a8ddb588ce827ab83e5736`。仅原子安装刷新前基线payload并read-back同hash，再用现有receipt验证 `updated_with_new_items/19`，继续同run；明确禁止第二次refresh。
- 用户已明确同意一次性修复watermark并继续同一run。production线程只可安装source/target SHA=`83fd50f1...`的精确pre-refresh baseline，使用existing signed receipt SHA=`61775449...` check-only验证19条新增后继续 `run_20260717_093104`；禁止第二次refresh或修改receipt/DB/backup。任一gate失败保持PAUSED。
- Watermark repair结果：canonical baseline已按SHA=`83fd50f1...`原子安装，existing receipt check-only=`updated_with_new_items`、19 new、67 articles，零第二次refresh。随后WeChat fulltext read probe首次因timeout返回0 items；PM将同状态同run的一次只读retry纳入原完整运行授权，不再额外要求用户确认。仅重试一次，仍失败即保持PAUSED，禁止第二次refresh/重试或旧cache补位。
- Bounded retry结果：严格一次retry后provider fulltext JSON在约49,149,586 bytes处截断，返回 `parse_failed:JSONDecodeError:Unterminated string`，items/fulltext_items=0。不是updated_no_new，无法形成truthful current result；未第二次read retry/refresh，未启动Douyin/AIHOT，无03/04/card/06，三任务PAUSED。
- AR-034C已派dev：修复为receipt/feed/DB identity/revision/pre-refresh watermark绑定的bounded current-feed读取，优先canonical SQLite只读精确区间或正式分页接口，不再吞整份49MB JSON；禁止旧cache/历史DB行/其他来源补位。须fresh production-base RC + full QA后才能继续同 `run_20260717_093104`，仍禁止第二次refresh。
- AR-034C dev结果：feature=`5a983699aadd3a159673f31bdc6caa392503f217`；fresh RC=`b7530452f5059dd02c274b32e5adb73d7dc68e72`，base=`af0e4e520cefcacb0efa770992a34a2778b9d36f`，5-file patch SHA=`b4cb2a2ab8959aac2f29870881faa65608af380a6bbb23a94da4fedfeeed0403`，tree/apply parity=`1314a57a...`。实现按receipt计划19条并逐篇 `limit=1&page=N&mode=fulltext`，单页8MB上限，check-only零provider request；全回归通过。
- Independent QA已派：验证5/5 scope、whole-feed巨型JSON active path不可达、19条分页及identity/post-read closure、截断/partial/stale/drift mutations、production receipt check-only零请求与完整回归。QA不得调用production fulltext/refresh/collection/Feishu/card/automation；production与三任务保持PAUSED。
- AR-034C QA结果：Passed，fresh RC `b7530452...`、5/5 manifest/patch/apply/tree、whole-feed不可达、limit=1分页及post-read closure、19/19 mutations、production receipt check-only零请求、full Python393/receiver32/Douyin/semantic/pre_merge全绿；零生产动作。建议Ready for PM Production Authorization。
- 精确生产计划：`/private/tmp/ar034c_independent_qa_20260717/AR034C_PRODUCTION_AUTHORIZATION_PLAN.md`，SHA256=`e6567babbd94ccb684b2b677e9b513818980b4dfd3a8b17904306ebd600255bc`。发布5文件后只允许19个bounded provider pages、禁止update/refresh；19/19与receipt/DB复核通过后继续同run的Douyin/AIHOT/03/editorial/04/一次personal card/cwd/status-only resume。
- 用户已明确同意AR-034C生产发布并继续同一run。production线程按计划SHA=`e6567bab...`执行exact 5-file release、dynamic gate、check-only、一次19-page bounded read；green后继续 `run_20260717_093104` 的full-source/03/current-task/04/一次personal card/cwd/status-only resume。继续禁止第二次refresh、whole-feed、7/16数据和06。
- AR-034C production结果：released clean `b7530452f5059dd02c274b32e5adb73d7dc68e72`，dynamic/check-only green；唯一一次19-page read在 `current_feed_fulltext_insufficient` 停止，输出原子为空，无下游写。失败telemetry未记录page/article/length，无法判断具体条目；无第二次refresh，三任务PAUSED。
- PM根因：reader硬编码 `MIN_FULLTEXT_CHARS=800`，任一短文就全批失败；字符数不是全文真实性证明，合法短文/图文也可能少于800。AR-034D已派dev：以receipt-bound identity/结构完整性判断truth，短文作为有效current item并标non-blocking质量；真实page错误候选级隔离并显性partial，系统级receipt/DB/plan drift仍全批fail closed。新增安全page/article/length/reason telemetry，禁止正文/secret回显。
- PM过度防御评估：真实问题包括账号失效、provider巨型JSON截断和first-run watermark缺失；但返工被PM指令放大。主要错误是把内容质量阈值、候选级读取失败、可逆只读动作都提升为system hard stop；每个单点又拆成独立RC+full QA，且在真实provider payload未先验证前用理想fixture设计合同。AR-034D已立即暂停，避免继续叠加规则。
- 收敛原则：只有run/date/source identity、stale/cross-run substitution、secret、external write/read-back、system receipt/DB/plan drift是硬门；短文、单篇读取失败、账号partial属于item/account partial，保留成功结果并显性non-green，不阻断无关候选。可逆只读动作在既有目标授权内自动执行；先补可定位telemetry和真实等价fixture，再只做一个窄RC与一次QA，不再micro-RC循环。
- 用户确认继续后，AR-034D已按收敛合同重新派固定dev线程：复用并审计两个未提交草稿；短正文truthful success+质量标注，真实单篇错误candidate-local partial，system drift才整批hard fail；partial不推进after-success watermark但成功项可进入同run。只允许一个production-base窄RC，不派QA、不触碰生产。
- 项目 `docs/pm_operating_rules.md` 已同步最新门禁强度、只读授权与返工收敛规则；全局 `multi-agent-pm-orchestrator` Skill同步更新model override、failure scope、truth/quality、read-only autonomy与single-root-cause RC/QA规则，并推送独立Skill Git仓库。
- AR-034D 单一RC开发自验通过：feature product=`c01b13703c3729fddff2d5191b4cd5eaa778ae22`；RC=`release/ar034d-rc-20260717@d88d0e5eb812d3a69ef816161446d0d8f1ca05e6`，parent=`b7530452...`，仅3 files，patch SHA=`6797475ff5378cc703cf73d23075a171b1be52cb96b528b3c92e09fd0a29f879`。Python 395、AR034 gate 58、focused 8、receiver 32、Douyin 39、semantic 7/7及compile/pre-merge通过；0生产动作。
- PM代码级复核确认：short/image-led使用`short_text`质量标注但保留truthful artifact；item failure为零artifact并保留成功项；partial可downstream但`full_collection_success=false`，watermark gate要求full success；receipt/DB/plan post-read drift在committed output前硬失败。已派唯一一次独立QA，不新增门禁、不调用production provider/Feishu/automation。
- 唯一一次Independent Full QA通过：fresh exact RC、3/3 manifest/apply/tree、19=16+3 mixed partial、7/7 all-short truthful、item-local/system drift、安全telemetry、Python 395、receiver 32、Douyin 39、semantic 7/7及pre-merge全部通过；0生产副作用。状态只进入production authorization，不称released/recovered。
- 生产授权计划：`/private/tmp/ar034d_production_authorization_20260717/AR034D_PRODUCTION_AUTHORIZATION_PLAN.md`，SHA256=`4e7a9ab78cc5faeb534e74d689923d5b5e107a12e7fcdf79764bbe4286d1033f`。精确3-file release + existing signed refresh的一次19-page read + same-run source/03/current-task/04/一次personal card；禁止second refresh/read retry/06/Skill/SCF/automation change。partial不推进watermark。
- 用户已明确回复“确认”，production线程已接收精确计划SHA=`4e7a9ab7...`并开始执行。三automation整个任务保持PAUSED且definition/status不变；不指定model/thinking，不轮询。任一system/read-back/card guard失败即停止并按组件回滚。
- AR-034D production：Git release与唯一一次bounded WeChat read均通过；WeChat 19/19、Douyin 87成功产物、AIHOT 56均已形成同日local artifacts。03前阻塞不是数据丢失，而是source fingerprint `douyin_cdp_*`经sampler归一化为16位canonical fingerprint后，旧lineage gate仍要求指纹字面相等。PM只读核算确认87/87 URL、账号、标题一一对应，source/canonical fingerprint各87个唯一值、映射无碰撞；03/04/card未写，三automation保持PAUSED。
- AR-034E待确认：在归一化边界显式持久化source fingerprint -> canonical fingerprint双向唯一映射；comparison与Feishu 03使用canonical identity，source identity保留为provenance。不得改fingerprint算法、不得URL-only全局降级、不得重采集/第二次refresh/read。仅一个production-base窄RC与一次independent QA，之后复用现有19 WeChat + 87 Douyin + 56 AIHOT继续03。计划=`/private/tmp/ar034e_identity_mapping_plan_20260717/AR034E_IDENTITY_MAPPING_PLAN.md`，SHA256=`c23fe579c1153e13f85aa7bbd5c85fa7cf302f823fbb501c2a4b5f68bced684b`。
- AR-034E开发授权：用户已明确回复“确认”。固定dev线程按计划SHA=`c23fe579...`只实现source->canonical双向唯一映射、canonical comparison/03/read-back identity与对抗测试；从production `d88d0e5...`组装一个窄RC。禁止重采集、provider request、第二refresh/read、Feishu/card/automation/06和新安全架构；开发完成只回传PM，不自行派QA。
- AR-034E RC：Dev Self-Validation Passed。feature=`953d3ec...` + prewrite sentinel=`4178fc7...`；RC=`release/ar034e-rc-20260717@ad708bea96934e1906f04ce339c6c3dfbd6476a7`，base=`d88d0e5...`，tree=`613721ea...`，精确3 files，patch SHA=`2ba0e3ba...`。真实87行映射、canonical comparison/03 plan与完整回归通过，0生产动作。PM evidence review接受进入唯一一次independent QA，并要求额外验证combined+content协同URL/title漂移是否仍与source manual原件比对阻断；该项属于已确认来源真实性合同，不是新增门禁。
- AR-034E QA：Failed / Development Rework Required。协同修改combined+content的URL/title/source_type时错误通过，证明source manual虽重开但未作为这些字段的truth owner；另有RC direct parent不是production base、manifest缺逐文件SHA256。当前真实87映射仍一致且生产0动作。三项属于同一已确认交付合同，已一次性回派dev：从重开的manual建立完整source truth map，补writer-call sentinel，fresh exact-parent RC与完整manifest；不拆micro-recheck、不进入production。
- AR-034E RC2 PM evidence review：`46030c8...` 已关闭协同URL/title/source_type、exact-parent和manifest SHA三项历史阻断，但未进入QA。PM从Git对象审计发现3-path口径掩盖了path内scope污染：`content_sampler.py` 同时改动选题角度、评分、今日优先级、Skill review pool等非AR-034E生产行为，`source_ingestion_lineage.py` 还带入非本需求的legacy production-root identity变更；这与“只修source->canonical identity、无quality-gate change”冲突。RC2不得发布或派QA。固定dev线程须从production `d88d0e5...` fresh exact-parent按hunk仅移植AR-034E mapping、writer前复验与对应测试，排除全部feature-only/旧RC9/editorial差异；产出一个完整manifest的新RC后再走一次full independent QA，不拆micro-recheck。
- AR-034E RC3：PM evidence review Passed / Independent Full QA Dispatched。fresh RC=`release/ar034e-rc3-20260717@746501b22ff9f5a36262ee39388e688460aa58ac`，direct parent=`d88d0e5...`，tree=`3f645cf2...`，patch SHA=`0849c408...`，manifest SHA=`300472d5...`。Git hunk仅触及AR-034E的manual truth、source->canonical mapping、writer前复验、canonical 03/read-back及对应测试；14个禁入editorial/legacy函数与production字节一致。协同五类漂移writer calls=0，真实87/87映射保持。当前固定QA线程执行一次fresh clone完整QA；不生产、不调用Feishu/provider、不改automation。
- AR-034E RC3 QA Passed / PM Acceptance Failed：QA独立证明87条Douyin source->canonical合同、hunk scope和回归均通过，但未覆盖真实full-ledger writer口径。生产writer对162 items（87 Douyin+19 WeChat+56 AIHOT）返回全部162个ordered fingerprints；RC3 post-write gate却要求整份read-back严格等于87个Douyin canonical fingerprints。本地RC3等价探针以87+75合法其他来源重放，得到`feishu_03_readback_identity_mismatch`。继续会在03已写后误判失败，故RC3不得发布。已一次性回派同根因收口：保留writer全162验证，再对其有序列表投影87个Douyin canonical并严格比对；补真实162形态、缺失/重复/错序/错run/source-fingerprint substitution测试，保持manual truth与writer前门不变。证据=`/private/tmp/ar034e_rc3_pm_acceptance_20260717/PM_ACCEPTANCE_BLOCKER.md`，SHA=`77c953fc...`。
- 首次开发回传：feature=`43a7d747b8a30522e27e285ef52a620dd8efe3cc`，production-base RC=`11fab145b0efccce7ff75a458f700606a9f4e183`，21-file patch SHA=`03072f758cb28bee3a6c3e680b5ed581e2dff8aedebf13b66ed98a26ed5534de`。RC lineage/tree/remote/patch 可复核，但 PM 对抗审查发现两个 active-path 阻断，因此未派 QA。
- 阻断一：`downstream_usability_report()` 不要求 source artifact / combined / content / 03 / comparison lineage 通过。独立探针在完全没有下游 artifact 证据、只有 probe 自报 coverage 和 9 条其他来源候选时仍返回 `downstream_usable=true`；原 Douyin 87 条丢失事故可重现。修复必须把 exact run/file hash/account+item lineage/bijection/03 read-back 变为 mandatory checks，missing/stale manual artifact typed fail，`today_candidates_nonempty` 不得替代来源闭环。
- 阻断二：WeChat `refresh_revision == previous_success_revision` 且无新文章时仍返回 `updated_no_new_items + ok=true`。当前 watermark 未保存上次 refresh timestamp/attempt identity，不能证明本轮刷新发生；24 小时内旧缓存可被误接受。修复必须绑定 current run refresh attempt 或独立前进的 revision/timestamp，并覆盖 unchanged/old cache 集成反例。
- 审计补充：首次 RC manifest 的 `rc_head` 为短 SHA `11fab14`；fresh RC manifest 必须记录并验证 exact 40-char head。返修完成后再做完整独立 QA，不做 micro-recheck。
- 集中返修：feature=`a10f7b1f53fce6ca3d0419ae9ff59a0b6527dcda`；fresh RC=`release/ar034-rc2-20260716@41cb9904b3cf4b36c4b94d85c91e54abb733779c`，parent=`8af084621d01e639c54b5dc847a6439ce96fd8bd`，25-file patch SHA=`8f308719e68d8e2eb9822da54da81b76b73a0cdb5850fd4e6e759a464b98b5f5`，tree/apply tree=`eabbf195255e234e2b35109d3b0d5b52be62a114`。PM 原两个反例已独立重放：无 ingestion closure 时七项 mandatory reason 阻断；unchanged refresh 返回 `stale_cache`；exact 40-char manifest verifier 通过。现已派完整独立 QA。
- 当前 recoverability：固定 provider 源码只暴露异步 `GET /feeds/:id?update=true`，没有 caller-bound completion receipt；fresh RC 正确返回 `refresh_surface_unverifiable/provider_failed`。QA 必须把“fail-closed 架构通过”与“生产可恢复”分开结论，并判断后续是否仍需 receipt-capable local adapter 代码，而不能把迁移/登录本身视为足够。
- Independent QA：A 类 architecture/control 通过，Douyin ingestion closure、WeChat current-run freshness/fixed runtime、AIHOT semantic owner 和完整回归均通过；B 类 production recoverability 阻断。当前 adapter 永久返回 `provider_failed/refresh_surface_unverifiable`，migration + reauth 本身不足，故不申请生产授权。
- Receipt adapter 最终开发：固定 provider URL/data dir，不启动 provider/browser；按 active feed 建立原子 exclusive lease，记录 caller run/attempt；请求异步 update 前保存 canonical DB before snapshot，再有界轮询 provider DB/可验证完成字段，只有每个 feed 的 completion predicate 与 after snapshot 证明本 caller 完成时才原子写 durable receipt。timeout、并发、stale lease、DB busy、账号掉线、feed failure、partial feed completion 均 typed fail 且不推进 watermark。若 provider 源码无法证明任何字段在完成后才更新，则不得用时间猜测成功，须选择可验证的新 provider/adapter surface。
- Fresh RC 门：receipt schema/identity/hash、lease crash recovery、before/after per-feed snapshot、bounded polling、no-new/new-items、N-feed partial、watermark post-03 read-back、scheduled no-browser/no-provider-start 全部对抗；production-base fresh RC 完整 QA 通过后才允许申请 canonical data migration、reauth 和一次真实 refresh smoke。
- Receipt RC3：feature=`a7c198a401b0acc911456e19d43f43a8c176b188`；fresh RC=`release/ar034-rc3-20260716@d23ee694a15499f927922eed68a6aadc6578c161`，parent=`8af084621d01e639c54b5dc847a6439ce96fd8bd`；25-file patch SHA=`0f16151ea7cdd898e40c964dbc608bc83e61127d2e31a283d5432e3a77ea4455`，tree/apply tree=`1d2b6df49a5e9dde2a6c319964f0d48c650dec33`。固定 tRPC mutation、exclusive lease、DB polling 与开发 fake-provider 回归已具备，但 PM evidence gate 未通过，未派 QA。
- RC3 阻断：`validate_refresh_receipt()` 未限制 receipt 必须位于 canonical `health/receipts`，也未把 `per_feed` 的 feed ID、before/after sync、completion/new-count 与 receipt 的 feed set 和 before/after snapshots 逐条重算。独立探针用 canonical receipts 之外的手工 JSON，并令 `per_feed.feed_id` 与 live feed 不一致，verifier 仍接受，最终分类为 `updated_no_new_items`。这违反 release plan 的“禁止 manual receipt construction”，会让非 adapter 生成的声明冒充 caller-bound completion proof。
- RC3 返修门：receipt path 必须由 canonical health root + exact sanitized run/attempt 文件名推导并 realpath 校验，拒绝 symlink/path escape/任意外部路径；schema 必须 exact/typed；verifier 必须从 before/after snapshots 重新计算 ordered feed set、逐 feed completion、aggregate rollback/new count、revision/refreshed_at，并与 live DB 一致。另补 forged external receipt、wrong per-feed ID、duplicate/missing/extra/reordered feed、fake before、sync/new-count/revision/time drift、symlink swap 对抗。产出 fresh RC 后再做完整 QA，不接受 micro-recheck。
- Receipt RC4：feature=`7e5ebab9f41d47f4d986c8889ddec9e02db01771`；fresh RC=`release/ar034-rc4-20260716@9868002c97e419a74fd0cb86c253037f40ff42f3`，parent=`8af084621d01e639c54b5dc847a6439ce96fd8bd`；patch SHA=`46b340b3a49333f981ddff990c17595d6cc49cd22b051992dbacd50d611ef11b`，tree/apply tree=`90de1d04e8f68b1399f30ce828e2b6109886e868`。RC3 的外部 path、symlink/hard-link、wrong per-feed 和 relational drift 已关闭，但 PM evidence gate 仍未通过。
- RC4 深层阻断：lease record、attempt lineage、receipt 虽为 canonical O_EXCL/单链接文件，仍只是同一权限域内可从零手工构造的三份 JSON。独立探针以合法 32-char attempt、canonical 三路径、完整精确 schema/hash/before/after/live DB parity 构造三件套，verifier 接受并分类为 `updated_no_new_items`。PID/host、started/requested 和 fake before 可由构造者声明，未证明 provider tRPC 收到本次 caller nonce，也没有不可伪造签名。
- Caller-bound 最终门：不能再用第四份普通 JSON 互相哈希。必须二选一并先做 architecture evidence：A) provider 在受保护的同步 refresh transaction 中接收并持久化随机 attempt nonce/receipt identity，verifier 从 canonical provider DB 读回；或 B) adapter 使用独立 runtime signing key 对 lease/attempt/receipt 链签名，verifier 验签，key 不进入 repo/stdout/artifact/log，测试明确说明同 Unix user 任意代码仍是信任边界。无论选择哪种，必须把 real before snapshot 与不可伪造 attestation 绑定，并让手工 canonical trio probe typed fail。随后 fresh RC 再进入 full QA。
- Receipt RC5：选择 B Dedicated Runtime HMAC Signature。feature=`11ea9805fefb0d005c87d959b439c0d6fea77cd7`；fresh RC=`release/ar034-rc5-20260716@5c0c203c781aeb50d9ce2c6b04ad4b313a059a49`，parent=`8af084621d01e639c54b5dc847a6439ce96fd8bd`；patch SHA=`94d00404c995c1a747822d3e733757be72043bcf5c7bd20252f62b9d619309fc`，tree/apply tree=`c171f6b7b579375afa0be9a92270741e161c1b86`。key 位于 health/data 之外，scheduled 不生成；lease/attempt/receipt 均签名，verifier 先验签再做 RC4 关系校验；无 key/错 key/错签名 fail。该 architecture 与“同 Unix runtime identity 为最终信任边界”一致，PM 接受方向。
- RC5 窄阻断一：adapter 成功路径实际读取 HMAC key 和 `WEWE_RSS_AUTH_CODE`，health verifier 也读取 HMAC key，但两者仍输出 `secrets_read=false`。这把“未泄露 secret”错误写成“未读取 secret”，属于机器证据失真。须拆为准确字段，例如 `secret_material_read=true`、`secrets_exposed=false`；check-only 未读 key/auth 时才可 `secret_material_read=false`。
- RC5 窄阻断二：`load_attestation_key()` 只用 path `lstat` 检查 regular/single-link/mode，再调用 `read_bytes()`；没有验证 `st_uid == os.getuid()`，也存在 lstat/read TOCTOU。须用 `os.open(O_RDONLY|O_NOFOLLOW)` + `fstat` 在同一 fd 上验证 regular、single-link、current uid、mode 0600/无 group-other access，再从 fd 读取；canonical secrets parent 需 current uid + 非 group/world writable，并在 release plan 中 read-back。完成 fresh RC 后才派 full QA。
- Receipt RC6：feature=`726ff23c6d0552140cb167f0b1398c0296fc4790`；fresh RC=`release/ar034-rc6-20260716@0353e723bc3dc719299fd4962d302a291e6ab714`，parent=`8af084621d01e639c54b5dc847a6439ce96fd8bd`；patch SHA=`97904e1dca8b0ef3917b2feeb6f5210974d7615f695510d9597980016c5dbe1b`，tree/apply tree=`d67398ecafb02358411a93084ecfe490003ba3d7`。secret evidence 改为 `secret_material_read` + `secrets_exposed`；parent/key 使用 directory fd + O_NOFOLLOW + fstat 验 current UID/mode/nlink，并从同一 key fd读取。
- PM evidence review：Passed。PM 独立 focused 21/21；原 canonical trio 在 RC6 module 下以 `refresh_attestation_key_unavailable` typed nonzero，未进入 classifier；patch SHA和 diff check通过。HMAC信任边界、fd owner contract与审计字段均符合任务，现派完整独立 QA。Production 尚无 key，故当前仍不是 production ready。
- QA 门：fresh clone重算26-file manifest/patch/apply/parity；独立 fake provider + isolated key覆盖无签名 canonical trio、wrong key/signature、fd owner/parent/symlink/hardlink/TOCTOU、secret evidence、signed relational/live DB、lease/crash/replay；同时完整复核 Douyin ingestion closure、WeChat fixed runtime/no-browser、AIHOT owner、AR033B/031/Topic Card/watermark与全回归。不得 provision production key或真实 refresh。
- RC6 scope correction：QA发现派发口径误写为25 files，而RC diff、combined patch和release manifest从一开始均为26 files。唯一口径差异文件是 `scripts/test_ar034_wewe_receipt_adapter.py`，为302行AR-034 receipt/key专项测试，需求相关、非运行时、非禁入范围。PM明确接受实际26-file scope；不重出RC，不静默改写历史，并以本条作为后续生产授权的精确范围。
- RC6 Independent QA：A=`RC architecture/control Passed`；B=`Production release/recoverability Blocked pending explicit production authorization`。26/26 scope/hash/apply/tree parity、signed receipt/key/fd contract、provider protected tRPC语义、Douyin 29/31 partial ingestion closure、WeChat watermark/fixed runtime、AIHOT owner和完整回归均通过；production key、真实refresh、Feishu、automation与production Git变更均为0。
- 生产授权计划：保持三条automation PAUSED；先Git-only发布并动态gate/read-back，再单独授权provision canonical HMAC key并只验证0700/0600与runtime UID；验证provider auth/config后，在exclusive lease下执行一次bounded signed refresh，独立复核signed trio/all-feed/live DB；仅在full-source closure成立后执行03 exact write/read-back、watermark、版本化current-task recovery、04/read-back、Topic Card check-only与一次个人发送；最后通过official control修production cwd并status-only resume。任一scope/key/receipt/DB/03/watermark/card mismatch立即停止并保持PAUSED。
- 用户生产授权：已明确确认上述完整计划，授权精确26-file Git发布、canonical HMAC key provision、一次真实bounded signed WeChat refresh、版本化full-source recovery、03/04精确写入与回读、一次个人Topic Card，以及official projectless cwd repair/status-only resume。禁止重用原错误9行、禁止Douyin重采集、禁止06/callback/schema/Skill/SCF/Chrome/profile/手改automation TOML；组件失败按边界停止，不口头豁免。
- 生产 preflight：Blocked before writes。保存的Douyin probe可证明31 planned/attempted、29 success、2 failure、失败账号artifact=0和87行manual，但旧版probe没有顶层 `run_id` / `manual_artifact`，87行本身也没有逐行 `运行批次`。RC6 `ar034_recovery_check.py --check-only` 正确返回 `manual_artifact_identity_missing`；production仍clean `8af0846`，key absent、refresh/Feishu/card/automation change均为0。
- 独立证据复核：daily log exact run=`run_20260716_080311`；唯一Douyin step使用canonical 9333、account-limit=0、video-limit=3、retries=2，started_at=08:03:13、daily generated_at=08:08:10。probe与resolver manual均为current UID、regular/single-link，mtime约08:07:39；probe resolver精确指向该manual，hash=`5af4d08662fddc7b09f8c0c906288cf36f6ade5d9ee01fad5270932ba001f496`、size=100159、rows=87。daily log只保留截断stdout，不能把它伪称完整probe byte proof。
- AR-034B建议：新增显式 legacy lineage attestation，不修改原probe/manual、不手工补字段、不重采集。只有调用者同时提供canonical daily log、expected source run和旧artifact路径时，代码才从single step command/status/time window、file identity、resolver path、hash/size/row count、coverage/account/item lineage重算source identity；输出带source run与ordered fingerprints的验证报告，后续write前再次读原artifact复核。正常新产物仍走RC6原生identity，legacy路径不可自动fallback。须从production base构建包含RC6+AR-034B的fresh RC7，完整QA后重新申请生产授权。
- 用户确认：批准AR-034B进入开发。开发只实现显式legacy attestation、对抗测试与fresh production-base RC7；不运行生产、不写/改旧artifact、不重采集、不自行派QA。旧RC6生产授权已因scope变化失效，RC7完整QA后必须重新申请。
