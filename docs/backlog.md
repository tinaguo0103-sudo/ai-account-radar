# AI账号雷达需求池

这个文件是需求和生产优化的轻量入口。任何新想法、生产问题、hotfix、版本合并前缺口，先进入这里；再由 `docs/release_board.md` 决定发布路径。

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

### AR-001 生产连续两天不稳定，先不要发布 dev 大功能

- 类型：生产稳定
- 优先级：P0
- 状态：Next
- 来源：生产观察
- 影响：如果生产链路继续不稳定，合并大功能会增加排障变量。
- 发布策略：优先 hotfix main；暂缓合并 `feature/next-production-flow`。
- 验证方式：生产目录最小 smoke、当天定时任务日志、失败 QA 通知是否可读。
- 关联分支/提交：
- 备注：这是当前发布节奏的总门控；生产稳定前，大功能只在 dev/staging 继续验证。

### AR-002 dev 大功能合并前完整预合并验证

- 类型：发布准备
- 优先级：P1
- 状态：Next
- 来源：`feature/next-production-flow`
- 影响：学习闭环、06 完成卡、失败 QA、SCF receiver 都涉及飞书和定时任务，必须避免合并后才发现生产问题。
- 发布策略：跟随 `feature/next-production-flow`，等生产稳定后进入 release candidate。
- 验证方式：`scripts/pre_merge_check.py`、staging/test 表写入、receiver/SCF Node 测试、生产只读检查、合并后最小 production smoke。
- 关联分支/提交：`feature/next-production-flow`
- 备注：不能写生产业务表；测试表、测试文件夹、个人 open_id 必须隔离。

### AR-003 学习确认卡上线前部署腾讯云 SCF receiver

- 类型：生产发布
- 优先级：P1
- 状态：Next
- 来源：学习闭环功能
- 影响：代码合并但 SCF 未部署时，飞书学习卡按钮仍可能走旧 receiver，导致确认失败。
- 发布策略：跟随 `feature/next-production-flow`；合并后部署 SCF，再启用生产学习卡。
- 验证方式：`node --test cloud_functions/feishu-card-receiver/test/receiver.test.mjs cloud_functions/feishu-card-receiver/test/tencent-scf-entry.test.mjs`，部署后 `check_feishu_card_cloud_receiver.py`，再做最小 production smoke。
- 关联分支/提交：`a656159`, `eb7f9a5`
- 备注：健康检查只证明 URL 和读权限；不能替代卡片 action 写回测试。

### AR-004 飞书用户 OAuth refresh token 需要重新授权

- 类型：生产/测试基础设施
- 优先级：P2
- 状态：Inbox
- 来源：运行 `setup_learning_test_env.py` 时出现 `invalid_grant`
- 影响：不能自动刷新个人 open_id 或创建用户可见测试文件夹；已有 staging 配置仍可用。
- 发布策略：不影响代码发布；需要用户授权时单独处理。
- 验证方式：重新运行 `scripts/feishu_user_oauth.py --timeout-seconds 240`，再跑测试环境 setup。
- 关联分支/提交：
- 备注：后续需要授权时，Codex 直接找用户要。

### AR-005 生产唤醒/保活机制上线

- 类型：生产稳定
- 优先级：P1
- 状态：Next
- 来源：生产定时任务不稳定风险
- 影响：Mac 睡眠、断网或空闲可能导致 08:00 任务延迟或失败。
- 发布策略：可作为独立生产稳定优化发布；不依赖学习闭环。
- 验证方式：`python3 scripts/install_production_keepawake.py --dry-run --install --configure-wake`，正式安装后 `--status`。
- 关联分支/提交：`cff709a`, `eb7f9a5`
- 备注：现在必须显式 `--install` 才会安装，避免误运行。

### AR-006 学习闭环生产启用

- 类型：新功能
- 优先级：P2
- 状态：Staging Tested
- 来源：学习闭环计划
- 影响：把 04/06 反馈沉淀为学习日结、确认卡和 Skill 草稿。
- 发布策略：跟随 `feature/next-production-flow`，不抢在生产稳定修复前发布。
- 验证方式：staging/test 04/06/08 全流程；空样本跳过；Skill 草稿 `草稿已生成 -> 已同步`。
- 关联分支/提交：`57fd01e`, `a656159`, `2645827`, `eb7f9a5`
- 备注：默认不自动修改全局私有 Skill。

### AR-007 PM 对话统一统筹开发与生产线程

- 类型：项目治理
- 优先级：P1
- 状态：Next
- 来源：用户希望后续只和 PM / 发布控制对话沟通，由 PM 拆解需求、指挥开发/生产对话、更新项目管理文档并汇报结论。
- 影响：减少用户在多个 Codex 对话之间手工转述，降低开发分支和生产分支混用风险；如果线程指挥失效，可能导致任务状态不可见或发布门禁遗漏。
- 发布策略：暂缓（不进入业务代码发布；作为 PM 作业机制立即试运行）
- 验证方式：PM 对话能识别开发线程和生产线程；任务卡可发送到对应线程；执行线程完成后主动回传 `PM交接摘要` 到 PM 线程，PM 再读回最终回复并更新 `docs/backlog.md` / `docs/release_board.md` / `docs/thread_handoff_log.md`。
- 关联分支/提交：
- 备注：当前识别到的开发线程为 `019f1de3-f3f2-71d2-ae63-a74cd38f8474`，生产线程为 `019ee85b-ed34-7133-b440-3bf73382d101`，PM 线程为 `019f2649-423f-7812-8efc-af6dd02eb511`。涉及真实生产写入、SCF、通知或 OAuth 时仍需按门禁要求向用户明确授权。执行线程不能只做对话结论交接，关键证据和下一步必须沉淀到共享文档；长任务默认主动回传 PM 线程，PM 不在当前对话阻塞等待。

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
