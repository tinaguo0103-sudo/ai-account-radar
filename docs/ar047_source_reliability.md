# AR-047 来源渠道可靠性合同

## 结论

来源可靠性不是“失败后继续”一个布尔值，而是三件可运营的事：

1. 运行前知道哪些账号身份可执行，错误配置不得进入浏览器。
2. 运行后知道每个账号最后成功、连续失败和需要采取的动作。
3. 正式来源只有一条当前路径；失败时贡献 0 行，不用旧文章、旧作品或其他来源补位。

## Douyin 单一路径

正式账号必须同时满足：

- `platform=抖音`，启用并参与主采样；
- URL host 是 `douyin.com` 或 `www.douyin.com`；
- path 是 `/user/<sec_user_id>` 且 identity 非空；
- account name 与 identity 在本次计划内均不重复。

错误平台、空 URL、缺 identity 和重复 identity 在浏览器前形成
`invalid_configuration`，artifact=0，并要求在来源管理页修复或停用。
Feishu 01 只保留迁移前历史，不是 normal runtime 配置入口。

所有合法账号先各执行一次。第一轮结束后，仅
`douyin_works_response_timeout` 进入一次 delayed tail retry。
配置、登录/验证、账号内容错误和 shared runtime failure 不重试。
继续使用 fixed 9333 exact page、自有 XHR 和 exact account binding；不直调
Douyin API，不读取旧 artifact。

## Douyin Health Authority

跨 run 健康事实只有一个 authority：

- AR-047 的 JSON health candidate 已被 WEB-008-CORE 的单一来源权威取代。正常 runtime
  只读写 `output/state/source_control.sqlite3`：`source_run_events` 是运行事实，
  `account_health_current` 在同一事务内派生。JSON ledger/projection 不再是 normal runtime authority。
- run projection：`output/runs/<run_id>/sources/douyin/account_health.json`

durable 文件先原子提交并 exact read-back。run projection 只能从已提交的
durable 字节按 exact `run_id|source_id` event keys 派生，记录 durable path、
schema 和 SHA256。它是可重建的 run evidence，不是第二 authority。投影写入
失败时 durable 事实不回滚，结果必须显式
`health_projection_write_failed`；相同 run 重试从 durable 重建，不增加 event
或改变健康计数。digest/event keys 不一致的旧投影不得被当作 authority。

每个账号输出：

```text
source_id
account_name
platform
configured_identity
verified_identity
enabled
priority
last_attempt
last_success
current_outcome
failure_class
consecutive_failures
rolling_success
action_required
recovery
```

状态只能由 run event 派生：

- 配置错误立即 `action_required=true`；
- transient failure 连续 3 次后才要求动作；
- 首次 transient 不自动禁用；
- `success` 或 `updated_no_new_items` 自动清零连续失败；
- rolling success 至少 3 个样本才显示，窗口最多 10 个 run；
- 同 run/source event 覆盖写，重复执行不增加事件。

WEB-008 只读 SQLite durable health authority；run result/projection 只用于
exact-run drill-down，不能与 durable 合并出第二套健康事实。它不得写健康值，
也不得成为第二配置源。配置与健康的唯一 authority 都是
`output/state/source_control.sqlite3`；Feishu 01 仅允许显式、只读、可重复的
一次性迁移，不在 normal runtime 调用图且无 fallback。

Hosted 网站只生产 versioned pending command。`source_command_bridge.py` 从
runtime env 读取 Site URL 与 machine bearer，原子 claim 后只调用 loopback
source-control domain service，不直接写 SQLite；commit exact read-back 后再回写
receipt/projection。receipt 失败时相同 command 通过 `applied_commands` reconcile，
不重复应用。bridge 离线不改变 SQLite plan，也不把 pending 显示为 applied。

## WeChat 单一路径

WeWe 已归档，退出唯一正式来源。其历史 DB/文章可保留，但不得补当天。

当前正式候选是：

```text
public discovery
-> exact mp.weixin.qq.com article URL
-> direct js_content full-text parser
```

配置位于 `config/wechat_public_fulltext_sources.json`。当前只启用
`数字生命卡兹克`，每轮最多一篇。验收必须同时满足：

- discovery account exact；
- URL host exact `mp.weixin.qq.com`；
- parser account/title 与 discovery exact；
- 正文不少于 500 字；
- 发布时间、指纹和正文可读；
- seen URL 返回 `updated_no_new_items`，不重复；
- discovery/fulltext 任一步失败时 WeChat=0，其他来源继续。

`we-mp-rss` 当前仍需要扫码/授权和新服务安装，不属于正常无交互执行面。
不运行 WeWe 与新路径双写，不保留 runtime fallback。

## Hard Stops

- wrong run/date；
- shared Douyin runtime failure；
- failed account artifact 非 0；
- identity collision 导致账号归属不确定；
- wrong SQLite authority/instance/database identity；
- duplicate/destructive write；
- bounded reconciliation 后外部状态仍 unknown；
- secret 暴露；
- 0 safe downstream survivor。

单账号、单 feed、单文章失败均局部隔离并贡献 0 行。

## Release Boundary

- 08:00 public entrypoint 不变，Prompt 无需修改。
- Production 发布前独立 QA 必须重跑 31/2-invalid/tail-retry/health 矩阵。
- Production cutover 只通过已核验的 SQLite migration/revision 发布；不得回写
  Feishu 01。“铁锤人教AI”“歸藏”使用已核验 exact Douyin identity 并保持启用。
- 第一次正常次日 08:00 是真实 Douyin/WeChat 生产证明；不以 Dev mock 代替。
