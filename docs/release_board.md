# AI账号雷达发布看板

这个文件按“能不能发布”和“从哪里发布”组织工作。需求细节放在 `docs/backlog.md`。

## Production 当前状态

- 生产目录：`/Users/congcong/Desktop/AI/AI项目/AI账号工作流/ai_account_radar`
- 生产分支：`main`
- 开发目录：`/Users/congcong/Desktop/AI/AI项目/AI账号工作流/ai_account_radar_dev`
- 开发分支：`feature/next-production-flow`
- 当前判断：2026-07-03 生产 08:00 采集和 10:00 选题卡发送已恢复成功；`AR-008` 已通过生产只读诊断，确认为旧日志残留且真实 06 记录已修复同步。仍建议至少再观察一个完整生产日，再考虑 dev 大功能 release candidate。
- 当前主门控：`AR-001`

## PM Coordination Lane

用于用户只和 PM / 发布控制对话沟通时，由 PM 对话统一拆任务、发送给开发或生产线程、读回结果并更新需求/发布状态。这个 lane 不代表业务代码发布。跨线程派发和读回必须同步沉淀到 `docs/thread_handoff_log.md`。

| ID | 标题 | 优先级 | 状态 | 发布路径 | 验证 |
|---|---|---:|---|---|---|
| AR-007 | PM 对话统一统筹开发与生产线程 | P1 | In Dev | 暂缓；PM 流程试运行 | 线程识别 + 主动回传验证 + 交接日志更新 |

当前固定线程：

- PM / 发布控制线程：`019f2649-423f-7812-8efc-af6dd02eb511`
- 开发分支线程：`019f1de3-f3f2-71d2-ae63-a74cd38f8474`
- 测试验证线程：`019f269e-e26b-74d2-8ba1-a606edef1171`
- 生产分支线程：`019ee85b-ed34-7133-b440-3bf73382d101`

## QA Lane

用于独立验证开发线程交付结果。测试线程默认不改代码，只做测试计划、对抗性审查、回归验证、staging/test 验证和证据整理。Bug 返修最多 3 轮，超过后回到 PM 做取舍。

| ID | 标题 | 优先级 | 状态 | 验证路径 | 当前轮次 |
|---|---|---:|---|---|---:|
| AR-009 | 06 口播稿从泛化结构转向场景化表达 | P2 | Dev In Progress / Test Plan Ready | 开发交付后由测试线程用昨日两条样例独立回归 | 0/3 |

## Hotfix Lane

适合优先于 dev 大功能发布的小修或生产稳定优化。

| ID | 标题 | 优先级 | 状态 | 发布路径 | 验证 |
|---|---|---:|---|---|---|
| AR-001 | 生产连续两天不稳定，先不要发布 dev 大功能 | P0 | Next | hotfix main | production smoke + 定时日志 |
| AR-005 | 生产唤醒/保活机制上线 | P1 | Next | 可独立 hotfix | dry-run + status |

## Released / Resolved

| ID | 标题 | 优先级 | 状态 | 发布路径 | 验证 |
|---|---|---:|---|---|---|
| AR-008 | 06 watcher 飞书文档同步读取 `.env.local` 权限失败 | P1 | Released | 已在生产 main/runtime 修复 | 生产只读日志 + 06 记录读回 |

## Next Feature Release

当前 dev 分支的大功能包。只有生产稳定且本区全部 Ready 后，才考虑合并。

| ID | 标题 | 优先级 | 状态 | 发布路径 | 验证 |
|---|---|---:|---|---|---|
| AR-002 | dev 大功能合并前完整预合并验证 | P1 | Next | feature/next-production-flow -> main | staging + pre_merge + smoke |
| AR-003 | 学习确认卡上线前部署腾讯云 SCF receiver | P1 | Next | feature/next-production-flow -> main + SCF 部署 | Node + receiver health + smoke |
| AR-006 | 学习闭环生产启用 | P2 | Staging Tested | feature/next-production-flow -> main | staging 04/06/08 |
| AR-009 | 06 口播稿从泛化结构转向场景化表达 | P2 | In Dev | feature/next-production-flow -> main | 昨日两条样例改前/改后回归 |

## Blocked / Watch

| ID | 标题 | 原因 | 下一步 |
|---|---|---|---|
| AR-004 | 飞书用户 OAuth refresh token 需要重新授权 | refresh token 已撤销，重建测试环境时会卡住 | 需要时向用户要授权 |

## Release Candidate 检查清单

任何从 dev 合并到生产前，必须逐项确认：

- `docs/backlog.md` 中本次 release 涉及需求状态已更新。
- `docs/release_board.md` 中本次 release lane 明确。
- dev worktree 干净，分支是 `feature/next-production-flow`。
- production worktree 干净，分支是 `main`。
- `python3 scripts/pre_merge_check.py` 通过。
- 涉及飞书写入/卡片/SCF/定时任务的功能已在 staging/test 表验证。
- 不写生产业务表、不发真实选题卡、不写生产文档文件夹。
- 合并后生产只通过 `git pull` 更新。
- 更新后只做最小 production smoke。
- smoke 失败时先诊断，再最小修复或回滚。

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
