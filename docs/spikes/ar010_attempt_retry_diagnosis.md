# AR-010 06 重复生成诊断

更新时间：2026-07-03

## 目标

诊断并最小修复 06 测试/生成链路中真实 `codex exec` 样例经常 attempt 2 / 生成两次的问题，降低 token 和时间浪费。本任务不重新打开 AR-009 内容方法。

## 根因证据

代码审计定位到 `scripts/codex_script_package_runner.py`：

- `qa_status_of()` 把输出状态归一为 `pass` / `revise` / `blocked`。
- `generate_package_with_retry()` 原逻辑是：只要 `qa_status == revise`，且还没达到 `MAX_REVISE_ATTEMPTS`，就记录 `qa_revise_retry` 并进入下一轮。
- runner prompt 又要求测试/返修阶段不要自评 `pass`，应标为草稿、待 PM 验收、待 QA。

因此 `revise` 同时承担了两种语义：

1. 内容真的需要模型继续重写。
2. 当前阶段不自评通过，需要 PM/测试线程/用户人工验收。

AR-009 真实测试 Skill 输出属于第二种：两条真实 `codex exec` 均为 `qa_status=revise`，但原因主要是测试/返修阶段不自评 pass、发布前核验和素材提醒。旧 runner 无法区分，固定触发 attempt 2。

## 修复策略

新增 `should_retry_package()`，把输出状态和重试决策拆开：

- `qa_status=pass`：停止。
- `qa_status=blocked`：停止，不自动重试。
- `qa_status=revise`：默认停止，视为 `revise_waiting_external_qa`。
- 只有出现明确重试信号才进入下一轮：
  - `qa_result` 明确包含 `需要重写`、`必须重写`、`结构不可用`、`脚本不可用`、`缺少关键输入`、`事实无法成立`、`内部状态边界进入` 等硬问题。
  - `full_markdown` 的用户可见创作区段中出现内部状态边界或内部抽象词，例如 `如果当天没有生成 06`、`选题系统复盘`、`沉淀资产`。
  - `codex exec` / JSON / schema / 命令异常抛错时，在未达到 max attempts 前重试。

同时更新 prompt，明确：

- `qa_status=revise` 可以表示待 PM/QA 人工验收，不等于自动重试。
- 只有 `qa_result` 明确要求重写，或用户可见内容混入内部边界时，runner 才会再生成。
- attempt history 中的 `retry` 表示 runner 是否实际进入下一轮；如果最后一轮仍有硬性重试理由但已达到 `MAX_REVISE_ATTEMPTS`，`retry=false`，`retry_reason` 记录为 `max_attempts_reached:<原始原因>`。

## 测试证据

新增 `scripts/test_codex_script_package_runner_retry.py`，覆盖：

- 普通 `qa_status=revise` / 待 PM QA 不触发 attempt 2。
- `qa_result` 明确 `需要重写` 时触发 retry。
- 用户可见 `口播全文` 出现内部状态边界时触发 retry。
- `codex exec` / JSON schema 类异常会重试并可在第二轮成功。
- `MAX_REVISE_ATTEMPTS` 仍被尊重。

本轮用 stub/mock 复现，不跑真实 `codex exec`，理由是：

- 根因来自 runner 纯控制流，不需要再消耗真实模型调用证明。
- mock 已覆盖 AR-009 真实输出对应的“revise 但待人工验收”语义。
- 真实重试条件通过合成 package 覆盖，避免写飞书、发卡或触发生产链路。

## 验收边界

- 本修复只改变 runner 是否自动二次生成，不改变 AR-009 Skill 内容方法。
- 生产全局 Skill 未修改。
- 不写生产业务表，不创建生产飞书文档，不发真实卡片，不触发生产采集。
- 如果后续测试线程要做真实验证，应使用隔离 `-ar009-test` Skill 和 `/private/tmp` 输出，且不加 `--write-feishu`。

## 剩余风险

- `qa_result` 的重试关键词是保守启发式；如果模型用新表述说明必须重写，可能不会自动 retry，需要后续按日志补模式。
- 用户可见区段扫描依赖 Markdown 标题；如果模型把创作内容塞进非标准标题区域，可能只被人工 QA 发现。
- 当前 schema 没有独立 `needs_retry` 字段。若未来需要更精确控制，可在 schema 中新增显式 retry 字段，但那会扩大接口改动，本轮先不做。
