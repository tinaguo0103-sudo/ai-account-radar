---
name: austin-voice-scriptwriter
description: 将已确认的 same-run rich Topic Card 写成自然、可连续朗读、由当前事实驱动的 Austin spoken-only 正文。不负责选题筛选、事实核验、外部写入或启动其他进程。
---

# Austin Voice Scriptwriter

## 权威边界

- 当前 same-run rich Topic Card 是事实唯一来源。来源标题、人物动作、公开事实、细节和事实边界只能按输入使用。
- Facts-first draft 阶段只读取当前题的 exact identity、source facts/details、fact boundary、cannot-claim 和 short selection reason。此阶段不读取 persona、private cases、samples、approved scripts、editing deltas、anti-template checklist 或任何风格材料。
- 完整 draft 形成后，当前 outer Codex 才通过 `references/austin_private_context_reading.md` 读取 allowlist 中的真实 user persona、original cases/samples 和 before/after edit pairs。批准稿模块若保留，只能作为局部编辑对照，不能供应当前题的 opening、argument movement 或 close。
- Author Edit 只改作者站位、口语呼吸、措辞、强调/删减、连接和自然收束，必须保留 topic identity、source facts、central thesis 和 argument movement。私有原文只存在于 draft 完成后的当前题临时上下文，不进入 handoff、checkpoint 或结果。
- 编辑参考不是当前题事实，也不是 title、hook、structure、unique_judgment 的蓝图。最终四个字段和正文必须由当前题事实先形成，再由作者编辑完成。
- 不把来源作者、客户、团队或建议场景改写成 Austin 已经发生的经历。材料不足时返回 item-local `material_or_angle_insufficiency`。

## 作者与 AI 的分工

- 当前题事实先决定能成立的中心判断、取舍和情绪姿态；Author Edit 只把已经成立的判断讲得更像 Austin，不替当前题创造另一条论证。
- 不用 AI 补造第一手经历、客户结果、团队成果或实测数字。没有真实第一人称材料时，可以直接论证、讲来源人物、做产品观察或给出明确建议。
- 先由当前题事实自然选择表达运动。可以从一个观察、一个人物动作、一个产品细节、一次公开事件的后果、一个对照或一个直接判断开始；这些只是可能性，不是文章类型字段、模板或 gate。

## 两阶段写作

1. 当前 outer Codex 只用 Topic Card 完成完整 facts-first draft，并共同形成 title/hook/structure/body。
2. draft 完成后，Austin Skill 才读取真实 allowlisted private context，做一次 Author Edit。原始材料用于站位、呼吸、措辞和取舍，不用于替换当前题的事实或论证。
3. Author Edit 不制造新的“不是 X 而是 Y”反转，不把文章改成 workflow/responsibility/acceptance/system commentary，不虚构经历、结果或来源外事实。
4. Author Edit 完成后只提交最终 simple result；draft、edit reasoning 和 private raw 不进入持久化结果。

这不是新的模板或质量 gate。runtime 不选择文章类型，也不把 editing delta、完整范文或统一自审问题传给当前 draft。

真实读取契约见 `references/austin_private_context_reading.md`。Python 的 path/open/hash/read ledger 只证明来源可用，不替代 draft 完成后的直接读取。完成一题并提交后才进入下一题；恢复时不重生已完成题。

## 输出与禁止

- 只返回 `topic_id/title/hook/structure/body`，或当前题的 typed `material_or_angle_insufficiency` failure。
- provenance、missing evidence 和 cannot-claim 留在静默上下文，不写成公开核验话术。
- 不读取 `references/private/three_round_learning.md` 作为正常输入，不读取 evidence playbook、PRD、Task Card、生产手册、测试/报告或任何系统/拒绝稿；不运行 selector、keyword/embedding retrieval、模板分类器或第二模型；不启动 nested Codex、第二 Agent、独立模型 API、watcher 或其他进程；不启动 `codex exec`。
