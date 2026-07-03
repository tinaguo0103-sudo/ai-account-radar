# PM 派发队列

这个文件用于记录 PM 线程准备派发、但不能立即发送给执行线程的任务。目标是实现“排队等待现有任务完成”，避免直接给 active 线程发送消息导致上下文被打断。

## 使用规则

- PM 线程是唯一派发者；开发、测试、生产线程之间不得互相下达新任务。
- 如果目标线程是 active / in progress，PM 不调用 `send_message_to_thread`，而是把任务记录为 `Queued / Waiting Dispatch`。
- 如果目标线程 idle，PM 可以派发任务，并把队列状态更新为 `Dispatched`。
- 只有 P0 生产事故、用户明确要求中止、或任务标注 `emergency interrupt` 时，PM 才能打断 active 线程。
- 当前用户拥有的 Codex 线程工具 `send_message_to_thread` 没有原生队列参数；队列语义由本文件、线程状态读回和 PM 事件触发检查共同实现。
- 不做固定频率轮询，避免无效消耗 token。只有收到执行线程回传、用户发来新指令、PM 处理发布/需求事项，或 PM 明确需要推进队列时，才检查并派发下一项。
- 队列任务必须包含目标线程、任务 ID、派发条件、禁止事项和验收口径。
- 派发后仍要在 `docs/thread_handoff_log.md` 记录真实派发和读回结果。

## 状态定义

- `Queued / Waiting Dispatch`：已排队，等待目标线程空闲。
- `Dispatched`：已发送给目标线程，等待回传。
- `Blocked / Need Authorization`：需要用户授权或外部资源。
- `Cancelled`：PM 或用户取消。
- `Completed`：已派发并完成闭环，详情转入 `docs/thread_handoff_log.md`。

## 当前队列

当前无待派发任务。
