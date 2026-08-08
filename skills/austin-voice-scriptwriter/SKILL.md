---
name: austin-voice-scriptwriter
description: 将已确认的 same-run rich Topic Card 写成自然、可连续朗读、由当前事实驱动的 Austin spoken-only 正文。不负责选题筛选、事实核验、外部写入或启动其他进程。
---

# Austin Voice Scriptwriter

## 权威边界

- 当前 same-run rich Topic Card 是事实唯一来源。来源标题、人物动作、公开事实、细节和事实边界只能按输入使用。
- Austin 的人格边界、判断习惯和编辑动作来自用户-owned authority。开始每题 draft 前，当前 outer Codex 必须直接读取 `references/austin_private_context_reading.md` 指定的真实用户人设、原始案例、原始样稿、before/after edit pairs 和批准稿模块；这些原文只存在于当前题的临时上下文，不进入 handoff、checkpoint 或结果。
- 编辑参考不是当前题事实，也不是 title、hook、structure、unique_judgment 的蓝图。最终四个字段和正文必须在当前题写作阶段共同形成。
- 不把来源作者、客户、团队或建议场景改写成 Austin 已经发生的经历。材料不足时返回 item-local `material_or_angle_insufficiency`。

## 作者与 AI 的分工

- 作者拥有当前题真正值得说的中心判断、取舍和情绪姿态；AI 负责把已成立的判断讲清楚、补必要背景、整理转折并提出可删改的版本。
- 不用 AI 补造第一手经历、客户结果、团队成果或实测数字。没有真实第一人称材料时，可以直接论证、讲来源人物、做产品观察或给出明确建议。
- 先读当前题的事实，再按内容自然选择表达运动。可以从一个观察、一个人物动作、一个产品细节、一次公开事件的后果、一个对照或一个直接判断开始；这些只是可能性，不是文章类型字段、模板或 gate。

## Austin-owned 编辑参考与真实私有上下文

runtime 提供的 `austin_owned_editing_reference` 只包含安全的模块化 edit delta，例如：

- 把顺滑的系统解释改成有主语的判断，再回到当前题的可见细节；
- 让某个限制、动作或异常结果真正改变论证，而不是堆事实；
- 把中性的报告句改成有站位的口语句，但不虚构经历；
- 删除泛化总结，让后果、选择或一个未解决的事实推动收束；
- 用句子长短、停顿和回头制造呼吸，而不是套固定金句。

这些是可选的编辑动作，不要求每篇全部出现，不规定顺序，也不允许被保存为文章类型或质量分数。它们不能替代真实私有上下文；当前 outer Codex 必须直接阅读 allowlist 中的原文，再按当前题吸收适合的站位、细节选择、呼吸和收束。不得把完整 Austin 范文、私有案例正文或第三方写作人格注入成当前题模板。

真实读取契约见 `references/austin_private_context_reading.md`。Python 的 path/open/hash/read ledger 只证明来源可用，不证明 writer 已看到原文；每题 draft 前的直接读取才是写作上下文。

## 一次主观复读

当前 outer Codex 对当前题做一次初稿和一次完整主观复读，必要时只重写当前题一次：

1. 这篇的开头、论证移动和停点是否真的来自当前题？
2. 有没有变成泛 AI 博主能原样说出的评论、工作流说明或验收报告？
3. 有没有为了显得完整而补出事实、经历、统一结尾或同义重复？

这不是数字、字数、段落、问句、关键词或固定结构检查。完成一题并提交后才进入下一题；恢复时不重生已完成题。

## 输出与禁止

- 只返回 `topic_id/title/hook/structure/body`，或当前题的 typed `material_or_angle_insufficiency` failure。
- provenance、missing evidence 和 cannot-claim 留在静默上下文，不写成公开核验话术。
- 不读取 `references/private/three_round_learning.md` 作为正常输入，不读取 evidence playbook、PRD、Task Card、生产手册、测试/报告或任何系统/拒绝稿；不运行 selector，不把完整范文路由成模板，不启动 nested Codex、第二 Agent、独立模型 API、watcher 或其他进程；不启动 `codex exec`。
