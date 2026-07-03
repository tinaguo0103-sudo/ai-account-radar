# AI账号雷达发布看板

这个文件按“能不能发布”和“从哪里发布”组织工作。需求细节放在 `docs/backlog.md`。

## Production 当前状态

- 生产目录：`/Users/congcong/Desktop/AI/AI项目/AI账号工作流/ai_account_radar`
- 生产分支：`main`
- 开发目录：`/Users/congcong/Desktop/AI/AI项目/AI账号工作流/ai_account_radar_dev`
- 开发分支：`feature/next-production-flow`
- 当前判断：2026-07-03 生产 08:00 采集和 10:00 选题卡发送已恢复成功；06 watcher 出现 `.env.local` 权限错误，需先诊断 `AR-008`，暂不因为单日恢复就合并 dev 大功能。
- 当前主门控：`AR-001` + `AR-008`

## PM Coordination Lane

用于用户只和 PM / 发布控制对话沟通时，由 PM 对话统一拆任务、发送给开发或生产线程、读回结果并更新需求/发布状态。这个 lane 不代表业务代码发布。

| ID | 标题 | 优先级 | 状态 | 发布路径 | 验证 |
|---|---|---:|---|---|---|
| AR-007 | PM 对话统一统筹开发与生产线程 | P1 | Next | 暂缓；PM 流程试运行 | 线程识别 + 任务卡发送 + 结果读回 + 文档更新 |

当前固定线程：

- PM / 发布控制线程：`019f2649-423f-7812-8efc-af6dd02eb511`
- 开发分支线程：`019f1de3-f3f2-71d2-ae63-a74cd38f8474`
- 生产分支线程：`019ee85b-ed34-7133-b440-3bf73382d101`

## Hotfix Lane

适合优先于 dev 大功能发布的小修或生产稳定优化。

| ID | 标题 | 优先级 | 状态 | 发布路径 | 验证 |
|---|---|---:|---|---|---|
| AR-001 | 生产连续两天不稳定，先不要发布 dev 大功能 | P0 | Next | hotfix main | production smoke + 定时日志 |
| AR-005 | 生产唤醒/保活机制上线 | P1 | Next | 可独立 hotfix | dry-run + status |
| AR-008 | 06 watcher 飞书文档同步读取 `.env.local` 权限失败 | P1 | Inbox | 先诊断；必要时 hotfix main | 生产只读日志 + staging/test 06 写入验证 |

## Next Feature Release

当前 dev 分支的大功能包。只有生产稳定且本区全部 Ready 后，才考虑合并。

| ID | 标题 | 优先级 | 状态 | 发布路径 | 验证 |
|---|---|---:|---|---|---|
| AR-002 | dev 大功能合并前完整预合并验证 | P1 | Next | feature/next-production-flow -> main | staging + pre_merge + smoke |
| AR-003 | 学习确认卡上线前部署腾讯云 SCF receiver | P1 | Next | feature/next-production-flow -> main + SCF 部署 | Node + receiver health + smoke |
| AR-006 | 学习闭环生产启用 | P2 | Staging Tested | feature/next-production-flow -> main | staging 04/06/08 |

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
