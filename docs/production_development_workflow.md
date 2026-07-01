# 生产与开发工作区说明

本项目当前已经进入生产观察期。为了避免 Codex 定时任务跑到半成品代码，生产目录和开发目录必须分开使用。

## 目录分工

生产目录：

```text
/Users/congcong/Desktop/AI/AI项目/AI账号工作流/ai_account_radar
```

用途：

- 承接 Codex automation 定时任务。
- 每天 08:00 跑全源采集。
- 每天 10:00 发选题卡。
- 只做生产观察、紧急修复和必要配置校验。
- 不直接开发新功能。

开发目录：

```text
/Users/congcong/Desktop/AI/AI项目/AI账号工作流/ai_account_radar_dev
```

用途：

- 开发新功能。
- 调整脚本、Skill 镜像、飞书字段、卡片样式、06 执行包逻辑。
- 运行 dry-run、局部测试和回归检查。
- 稳定后再合并回 `main`。

## Git 状态

当前锁定的生产版本：

```text
tag: prod-2026-07-01-scheduled-v1
commit: 24515d7 feat: notify on schedule exceptions
```

生产目录分支：

```text
main
```

开发目录分支：

```text
feature/next-production-flow
```

## Codex 定时任务

Codex automation 指向生产目录：

```text
/Users/congcong/Desktop/AI/AI项目/AI账号工作流/ai_account_radar
```

现有任务：

```text
08:00 AI账号雷达 每日全源采集
10:00 AI账号雷达 每日选题卡发送
```

因此不要在生产目录切换到开发分支，也不要在生产目录留下未完成代码。

## 新功能开发规则

1. 新功能只在开发目录做。
2. 生产目录保持干净，观察两天生产稳定性。
3. 开发目录可以正常提交到 `feature/next-production-flow`。
4. 新功能测试通过后，再通过 PR 或本地 merge 合并回 `main`。
5. 合并回 `main` 后，再让生产目录 `git pull` 到新版本。
6. 如果生产链路出问题，优先在生产目录做最小紧急修复，并同步回开发分支。

## 新对话建议

建议新开一个 Codex 对话继续开发新功能，并明确使用开发目录：

```text
请在以下开发 worktree 中继续 AI账号信息雷达的新功能开发：
/Users/congcong/Desktop/AI/AI项目/AI账号工作流/ai_account_radar_dev

生产目录为：
/Users/congcong/Desktop/AI/AI项目/AI账号工作流/ai_account_radar

生产目录当前由 Codex automation 使用，请不要在生产目录开发新功能。
当前开发分支是 feature/next-production-flow。
```

当前这条生产配置对话可以继续用于生产观察、故障排查和定时任务状态确认。
