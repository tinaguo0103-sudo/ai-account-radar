# AR-015 非幂等飞书写入 checkpoint / read-back / idempotency 方案

日期：2026-07-04

状态：Design Ready for PM

## 结论

AR-014 已经把公共 transient retry 收紧到安全边界：默认只重试 `GET / PUT / PATCH / DELETE`，`POST` 默认不重试。这个方向正确，因为当前剩余风险不是“多 retry 几次”能解决，而是非幂等动作在超时后会进入状态未知：远端可能已经创建记录、创建文档或发送卡片，但本地没有拿到 `record_id`、`document_id` 或 `message_id`。

AR-015 应分两层落地：

1. 先补最高风险点的业务幂等和可恢复日志：03/04 `batch_create`、Topic Card 发送。
2. 再抽公共 ledger/helper，把 06 文档创建、06 记录创建、完成卡发送、字段/视图创建纳入同一套 intent -> send -> receipt -> read-back -> unknown 语义。

核心原则：宁可显性进入 `unknown` 并阻断自动重跑，也不能为了“看起来稳定”盲目重试非幂等 `POST`。

## 审计范围

本次只读审计以下文件，没有写生产数据、没有发卡、没有触发采集：

- `scripts/push_to_feishu.py`
- `scripts/content_sampler.py`
- `scripts/push_today10_to_feishu.py`
- `scripts/run_topic_card_if_fresh.py`
- `scripts/feishu_topic_decision_card.py`
- `scripts/codex_script_package_runner.py`
- `scripts/feishu_automation_notify.py`
- `scripts/test_feishu_request_retry.py`
- `scripts/test_content_sampler_recovery.py`

## 现有写入点风险分层

### 1. 安全/幂等请求

代表动作：

- `GET` 列表读取、字段读取、视图读取。
- `PUT / PATCH / DELETE` 已知资源更新：03 单条 `record_id` update、04 选题状态 update、视图配置 patch。

当前状态：

- AR-014 后，公共 `request_json()` 默认允许 `GET / PUT / PATCH / DELETE` transient retry。
- `content_sampler.update_record_fields()` 对已有 03 记录的 `PUT` 有局部 3 次 retry，且调用公共层 `retry=False`，避免双层 backoff。

恢复策略：

- 这类请求可以自动 retry。
- 达到 max attempts 后仍失败，记录 `status_unknown` 或 `failed_after_retry`，但由于目标资源 ID 已知，后续可安全重跑同一 update。

### 2. 非幂等创建

代表动作：

- `push_to_feishu.batch_create_records()` 初始化 Base 或表时批量新增。
- `content_sampler.batch_create_records()` 向 03 内容收件箱新增素材。
- `push_today10_to_feishu.batch_create()` 向 04 分析与选题新增今日候选。
- `codex_script_package_runner.create_script_package_record()` 向 06 完整脚本与制作包新增记录。
- `codex_script_package_runner.create_feishu_document()` 创建飞书文档，以及向文档插入 blocks。
- `ensure_fields()` / `ensure_text_fields()` / 视图创建这类表结构初始化动作。

当前状态：

- `batch_create` 类 `POST` 不会默认 retry，这是正确的保守边界。
- 03 和 04 已有“写前 read-back + 唯一字段匹配”的一部分能力：03 用 `内容指纹 / 链接` 匹配，04 用 `推荐日期 + 原始来源标题` 或 `推荐日期 + 选题标题` 匹配。
- 但 `batch_create` 超时后，当前脚本通常拿不到远端返回，不能确认本批哪些已创建。
- 06 单条 create record 和 create doc 目前缺少统一业务唯一键、intent、receipt、read-back。
- 字段/视图创建前会 list existing，但如果 create 超时，下一轮一般可通过名称 read-back 找到，风险低于业务记录/消息。

恢复策略：

- 新增前必须有业务唯一键。例如：
  - 03：`content_fingerprint` 优先，退化为规范化 URL。
  - 04：`run_id + 推荐日期 + 原始来源标题`，无来源标题时用 `run_id + 推荐日期 + 选题标题`。
  - 06：`source_topic_record_id + runner_version + topic_title` 或新增显式 `幂等键` 字段。
  - 文档：`date_slug + topic_record_id + topic_title`，并写本地 doc intent。
- 每次非幂等 create 前写本地 intent，至少包含：`operation_id`、`kind`、`business_key`、`target`、`payload_hash`、`run_id`、`created_at`、`status=pending`。
- create 成功后写 receipt，至少包含远端 ID、URL、返回摘要、`status=succeeded`。
- transient 失败后不自动重试，先 read-back：
  - 能用业务唯一键或标题/字段组合找到唯一远端对象：补 receipt，标 `recovered_by_read_back`。
  - 找不到：标 `unknown_not_found`，允许人工确认后安全重跑。
  - 找到多个：标 `unknown_ambiguous`，禁止自动重跑，要求人工处理。

### 3. 真实外发动作

代表动作：

- `feishu_topic_decision_card.send_card()` 发送每日 Topic Card。
- `codex_script_package_runner.send_completion_card()` 发送 06 完成反馈卡。
- `feishu_automation_notify.notify()` 发送失败通知/跳过通知。

当前状态：

- Topic Card 和 06 完成卡都使用飞书消息 `uuid`，这有助于服务端去重，但本地超时后仍可能没有 `message_id`。
- Card callback 处理已有 `callback_receipts.jsonl`，可以避免同一次表单回调重复改 04。
- AR-014 后，通知失败会持久化 `delivery_status=unknown`，并重新抛错，不盲目 retry。

恢复策略：

- 外发前写 `send_intent`：`kind`、`run_id`、`receive_id_type`、`receive_id`、`uuid`、`payload_hash`、`preview_path`。
- 发送成功后写 `send_receipt`：`message_id`、`uuid`、`sent_at`、`status=succeeded`。
- 发送 transient 失败时：
  - 默认不自动重发。
  - 本地标 `delivery_status=unknown`。
  - 用户可见 QA 必须说明：可能已送达，也可能未送达；不要手工绕过守卫再发同一批。
- 如果飞书 API 支持按 `uuid` 或 `message_id` 查询，应优先实现 read-back；如果不支持，只能人工确认聊天窗口后再运行明确的 recovery 命令。

### 4. 组合状态流转

代表动作：

- 06 链路：本地 Markdown -> 飞书文档 -> 06 表记录 -> 04 标记已生成 -> 06 完成卡。
- 每日候选链路：03 写入 -> 04 写入/校验 -> latest_write/log 刷新 -> 10:00 发卡守卫 -> 发卡。

当前状态：

- 06 链路里 create doc、create 06 record、mark 04、send completion card 是串联执行；中间任意一步 unknown 都可能导致后续状态不一致。
- 03/04 链路已有 latest/log，但 AR-012 的恢复主要解决当天恢复和 03 update，不等于非幂等 create 已完整可恢复。

恢复策略：

- 组合链路必须使用阶段 ledger，而不是只看最终 print。
- 每个阶段都要有可读状态：
  - `pending`
  - `succeeded`
  - `recovered_by_read_back`
  - `unknown_not_found`
  - `unknown_ambiguous`
  - `failed_before_send`
- 后续阶段只能在前序阶段 `succeeded` 或 `recovered_by_read_back` 后继续。
- 遇到 `unknown_*` 时停止后续自动动作，并进入失败 QA/通知。

## 状态未知时的处理矩阵

| 动作 | 可否自动 retry | 优先恢复方式 | 自动恢复条件 | 不可自动恢复时 |
|---|---:|---|---|---|
| 已知 `record_id` 的 `PUT/PATCH` | 可以 | 继续 retry 或重跑同一 update | 目标 ID 已知 | 记录失败并可安全重跑 |
| 03 内容新增 `batch_create` | 不盲目 retry | 按 `内容指纹/链接` read-back | 每个业务键唯一匹配 | 写 unknown ledger，人工确认/恢复命令 |
| 04 候选新增 `batch_create` | 不盲目 retry | 按 `run_id + 推荐日期 + 来源/标题` read-back | 每个业务键唯一匹配 | 写 unknown ledger，禁止发卡 |
| 06 记录 create | 不盲目 retry | 按 `topic_record_id + title + version` read-back | 找到唯一 06 记录 | 写 unknown，暂停标记 04 和发完成卡 |
| 飞书文档 create | 不盲目 retry | 按本地 intent title / folder read-back | 找到唯一文档 | 写 unknown，06 表记录标“文档状态未知”或暂停 |
| 文档 blocks append | 不盲目 retry | 读取文档 blocks 或用本地 package hash | 能确认内容完整 | 标文档同步 unknown，不发送完成卡 |
| Topic Card 发送 | 不自动重发 | 按 uuid/message_id read-back；否则人工看聊天 | 确认唯一 message | 写 delivery unknown，不补发、不绕过 |
| 自动化通知发送 | 不自动重发 | 本地 failure JSONL + 人工确认 | 确认未送达且用户批准 | 维持 unknown，避免重复通知 |

## 分阶段落地建议

### Phase 1：最小可用切片

优先解决两个最高风险点：

1. 04 `batch_create` + 发卡前守卫
   - 给 04 写入增加本地 `write_ledger`。
   - 每条候选写前生成业务键。
   - `batch_create` transient unknown 后立即 read-back。
   - 只要 04 create 仍有 unknown，`run_topic_card_if_fresh.py` 必须跳过发卡，并提示恢复命令。

2. Topic Card 发送 intent/receipt
   - `feishu_topic_decision_card.send_card()` 发送前写 intent。
   - 成功后写 receipt。
   - 超时后写 `delivery_status=unknown`，不要自动重发。
   - 输出/通知明确告诉用户：不要手动绕过守卫重发同一 `run_id`。

这两个点优先级最高，因为 04 候选和卡片是用户最直接感知链路：重复创建会污染候选池，重复发卡会直接打扰用户。

### Phase 2：03 与 06 纳入同一套 ledger

1. 03 `content_sampler.batch_create_records()`
   - 利用已有 `内容指纹 / 链接` 作为业务键。
   - `batch_create` 改成分块前写 intent，失败后按 key read-back，并生成 `created / recovered / unknown` 明细。
   - `content_sampler_log.json` 增加 unknown count 和恢复命令。

2. 06 create record / create doc
   - 为 06 表增加或复用幂等键字段，建议先新增字段：`幂等键`、`来源选题record_id`、`生成批次`。
   - 文档创建 intent 单独记录在 `output/script_package_ledger/`。
   - 06 完成卡只发送 `created_script_package_id + feishu_document_url` 都确定的记录；有 unknown 时发送失败 QA 或跳过完成卡。

### Phase 3：公共 helper / ledger

抽一个公共模块，例如 `scripts/feishu_idempotency.py`：

- `operation_key(kind, business_key, payload) -> operation_id`
- `write_intent(operation)`
- `write_receipt(operation_id, remote_id, response)`
- `mark_unknown(operation_id, reason)`
- `read_back_unique(list_fn, business_key_fn)`
- `guard_no_unknown(kind, run_id)`

ledger 建议位置：

- 生产运行：`output/feishu_write_ledger/YYYY-MM-DD/*.jsonl`
- latest 摘要：`output/latest_write/feishu_write_ledger_summary.json`
- 06 链路：可附加 `output/script_packages_latest_write/` 下的 run summary。

ledger 字段建议：

```json
{
  "operation_id": "sha1(kind|target|business_key|payload_hash)",
  "kind": "topic_card_send",
  "run_id": "run_20260704_080730",
  "target": "04 分析与选题",
  "business_key": "2026-07-04|run_...|source_title",
  "payload_hash": "sha1:...",
  "status": "pending|succeeded|recovered_by_read_back|unknown_not_found|unknown_ambiguous|failed_before_send",
  "remote_id": "",
  "created_at": "",
  "updated_at": "",
  "error": "",
  "recovery_hint": ""
}
```

## 事故复发 hotfix 切片

如果明天生产再次出现非幂等 unknown，不建议直接扩大 retry。最小 hotfix 应这样做：

1. 对出事链路加本地 intent/unknown 记录。
2. 加只读 read-back 命令，按业务键判断远端是否已经发生。
3. 加守卫：存在 unknown 时，不继续后续真实外发或状态推进。
4. 加一条针对性单测：模拟 `POST` 超时后 read-back 找到/找不到/找到多个。

这比“把 POST retry=True”更慢一点，但不会制造重复记录或重复卡片。

## 测试方案

### 函数级测试

- mock `feishu.request_json()`：
  - `POST batch_create` 第一次抛 transient，read-back 找到唯一记录，应标 `recovered_by_read_back`。
  - read-back 找不到，应标 `unknown_not_found`，不进入发卡。
  - read-back 找到多个，应标 `unknown_ambiguous`，不自动修复。
  - `PUT` 仍可 retry，现有 AR-014 测试不回退。
- mock Topic Card send：
  - 发送前生成 intent。
  - 成功后写 receipt。
  - transient 后写 `delivery_status=unknown`，不重发。
- 组合链路测试：
  - 06 doc unknown 时不得 create 06 record 或不得发送完成卡。
  - 06 record unknown 时不得 mark 04 generated。

### staging/test 飞书验证

必须使用隔离资源：

- staging/test Base 或测试表，不写生产 03/04/06。
- 测试通知目标默认个人 open_id。
- 测试卡片标题带 `【测试】`，不得触发真实业务动作。
- 测试文档写入测试文件夹，不写生产脚本包文件夹。

建议 staging 用例：

1. 04 测试表写入同一批候选两次：第二次应 update 或 skip，不重复 create。
2. 人工模拟/monkeypatch 第一次 create 后本地超时，再 read-back：应恢复 receipt。
3. Topic Card 测试发送到个人：成功路径有 receipt；失败路径只产生 unknown，不自动重发。
4. 06 测试表 + 测试文件夹：doc/create record/mark source/card 每一步 unknown 都能停止后续动作。

### production smoke 边界

生产只做只读或最小 smoke：

- 检查 ledger 文件可写。
- 检查 `run_topic_card_if_fresh.py --send-dry-run` 仍不真实发送。
- 检查存在 unknown 时真实发卡守卫会跳过。
- 不写生产业务表、不发真实卡、不触发采集。

## 和 AR-013 的边界

AR-013 解决的是“候选当天没发出去，后续如何补看/补发”的产品策略。

AR-015 解决的是“非幂等飞书动作超时后，如何判断远端到底发生没发生，以及如何避免重复创建/重复发送”的技术稳定性。

两者不要混做：

- AR-015 可以给 AR-013 提供可信的 `sent / not_sent / unknown` 状态。
- AR-013 不应该绕过 AR-015 的 unknown 守卫去补发。

## PM/用户需要确认

1. Phase 1 是否优先锁定 04 `batch_create` 和 Topic Card 发送。
2. 是否接受新增本地 ledger 文件作为生产恢复依据。
3. 06 表是否允许新增 `幂等键 / 来源选题record_id / 生成批次` 这类字段，先在 test 表验证。
4. Topic Card 发送 unknown 时，默认策略是否为“不自动补发，等待人工确认”。

## 建议下一步

AR-015 可以进入 `Ready for Dev`，但第一轮实现要非常窄：

- 不改生产表。
- 不改 AR-013。
- 先用 mock 测试把 intent/receipt/read-back/unknown 语义跑通。
- staging/test 表验证 04 create 和测试卡片发送后，再决定是否发布到生产。
