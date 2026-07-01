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

## 测试环境与预合并验证

涉及飞书写入、字段、卡片、状态流转、通知或外部服务的功能，合并前优先使用 staging/test 飞书 Base 或测试表验证。测试数据必须与生产 Base / 生产业务表隔离；不要求单独测试群，如果复用正式通知目标，测试消息必须明确标记为“测试”，且不能触发真实业务动作。

本地环境文件支持三种模式：

```bash
# 默认生产本机配置，生产目录使用
python3 scripts/check_feishu_card_cloud_receiver.py --skip-receiver

# 按环境名加载 .env.staging.local / .env.staging
AI_ACCOUNT_RADAR_ENV=staging python3 scripts/check_feishu_card_cloud_receiver.py --skip-receiver

# 显式加载某个测试配置文件，不回退读取 .env.local
AI_ACCOUNT_RADAR_ENV_FILE=.env.staging.local python3 scripts/check_feishu_card_cloud_receiver.py --skip-receiver
```

合并前在开发目录运行：

```bash
python3 scripts/pre_merge_check.py
```

这只是基础门禁检查。做完整预合并测试时，再加 full-smoke：

```bash
python3 scripts/pre_merge_check.py --full-smoke
```

`--full-smoke` 会用本地手动样例跑一条完整 dry-run 链路：

```text
手动样例 -> 内容拆解 -> 今日候选 -> deterministic 主编层 -> 飞书写入 dry-run
```

它不会写飞书业务表，也不会发送选题卡。默认使用 deterministic 主编层，是为了让预合并门禁稳定、快速、可重复；需要验证全局私有 Skill / Codex CLI 时，单独运行 `daily_pipeline.py` dry-run 重型测试。

如果已经配置 staging/test 飞书 Base，可加只读飞书检查：

```bash
python3 scripts/pre_merge_check.py --env-file .env.staging.local --feishu-read
```

`--feishu-read` 默认只读检查 `04 分析与选题` 和 `06 完整脚本与制作包`。需要指定更多表时可重复传：

```bash
python3 scripts/pre_merge_check.py --env-file .env.staging.local --feishu-read --table-key topic_decision --table-key script_package
```

需要验证失败 QA 通知链路时，可以发送一条明确标记的测试通知：

```bash
python3 scripts/pre_merge_check.py --env-file .env.staging.local --notify-smoke
```

如果暂时没有 staging/test Base，可以用生产 `.env.local` 做只读检查或测试通知，但必须满足：

- 不写 `03 / 04 / 06` 正式业务表；
- 不发送真实选题卡；
- 测试通知必须带 `【测试】` 标记；
- 不触发真实生产采集写入。

预合并检查会确认：

- 开发目录在 `feature/next-production-flow`；
- 生产目录在 `main` 且干净；
- 关键脚本可以通过 Python 编译；
- 失败 QA 规则能命中典型错误；
- 10:00 发卡入口在开发目录会被 worktree 守卫拦截；
- 可选：staging/test 飞书 Base 的关键表可读。
- 可选：完整 dry-run 链路能跑通且不写飞书。
- 可选：失败 QA 测试通知能送达。

## 自动化入口保护

两个 Codex automation 入口已经加了 worktree 守卫：

```text
scripts/run_daily_collection_job.py
scripts/run_topic_card_if_fresh.py
```

默认规则：

- 只允许在生产目录运行。
- 只允许在生产分支运行，默认是 `main`。
- 如果误在 `ai_account_radar_dev/` 或 `feature/next-production-flow` 上触发，会在采集、发卡或写飞书之前停止。

可选配置：

```text
AI_ACCOUNT_RADAR_PRODUCTION_DIR=/Users/congcong/Desktop/AI/AI项目/AI账号工作流/ai_account_radar
AI_ACCOUNT_RADAR_DEV_DIR=/Users/congcong/Desktop/AI/AI项目/AI账号工作流/ai_account_radar_dev
AI_ACCOUNT_RADAR_AUTOMATION_BRANCHES=main
```

开发目录里需要刻意演练这两个入口时，必须显式加：

```bash
python3 scripts/run_daily_collection_job.py --allow-non-production-worktree --no-notify
python3 scripts/run_topic_card_if_fresh.py --allow-non-production-worktree --send-dry-run --no-notify
```

日常开发验证仍优先跑更底层的 dry-run 脚本，不直接跑生产 automation 入口。

## 失败 QA 规则诊断

生产入口失败时会先走规则诊断，不依赖 LLM：

```text
scripts/automation_failure_qa.py
```

当前接入范围：

- `08:00 每日全源采集`：采集失败会在飞书通知里给出 QA 结论、影响、可能原因、建议处理和日志证据。
- `10:00 每日选题卡发送`：新鲜度守卫、发卡失败、worktree 守卫都会输出 QA。
- `06 完整脚本与制作包 watcher`：runner 失败会输出 QA，并对同一个错误去重，避免每 5 分钟重复通知。

当前规则覆盖：

- worktree / 分支错误；
- 飞书环境变量缺失；
- 飞书 Base 权限、token、消息发送、写入一致性；
- 用户可见飞书文件夹 `1770040 no folder permission`；
- 网络、DNS、本地服务端口；
- Docker / `wewe-rss`；
- 抖音登录验证 / Chrome CDP；
- AIHOT 抓取异常；
- Codex CLI / 私有 Skill / 超时；
- 候选为空、latest_write 过期、run_id 不一致。

开发时可直接诊断某个 JSON 日志：

```bash
python3 scripts/automation_failure_qa.py --task "08:00 每日全源采集" --log output/logs/scheduled_daily_collection_YYYY-MM-DD.json
```

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
