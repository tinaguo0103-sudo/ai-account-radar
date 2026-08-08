---
name: austin-no-overtime-scripting
description: 将 WEB-010 已确认选题的 same-run rich Topic Card 交给当前 outer Codex，逐题完成内容驱动的口播起草、一次主观复读和简单结果提交。不负责完整制作包、外部队列、表格回写、发布或启动其他进程。
---

# Austin不加班脚本 Skill

本 Skill 只负责把已选中的 rich Topic Card 变成可直接继续修改的 spoken-only 结果。它保留逐题
checkpoint，但不把所有题目压成同一篇文章，也不把任何叙事原型存成业务字段。

## 正常运行模型

- 唯一 AI owner 是当前 outer Codex；不启动新进程、第二 Agent 或独立模型 API。
- 当前 outer Codex 直接应用本 Skill 与 `austin-voice-scriptwriter`，一题完成后才提交并进入下一题。
- public runtime 每次只暴露一个 selected rich Topic Card 和一份小型 style-only modular editing reference。
- 不存在 semantic reference selector、full-body exemplar injection、private case/persona routing 或对应的 CLI/state/receipt。
- 输出只包含每题 `topic_id/title/hook/structure/body`，以及确实无法成稿时的 item-local `material_insufficiency` failure。

## 输入边界

当前 Topic Card 是唯一事实输入，包含 same-run 的标题、公开来源、理解摘要、主编判断、事实边界
和可用场景线索。可选的 editing reference 只提供判断、呼吸、转折和收束的改稿问题；它不是当前
题目的事实、不是完整范文，也不指定文章顺序。

- 不读取或要求 human supplement、production direction、完整制作包字段或历史 run 替代。
- optional facts 缺失时保持缺失；不得用模型记忆、私有案例或另一题内容补齐。
- 没有 Austin 真实经历时，不默认写“我做了一个实验”或复合第一人称现场。优先使用来源真实
  人物/事件、直接论证、产品观察、真实方法或明确建议。
- 如果材料撑不起题目独有且可读的正文，返回当前题的 `material_insufficiency`，让其他题继续。

## 内容驱动方法

1. **找这题自己的中心**：先判断观众真正要重新理解什么，以及题目里哪条事实、人物或后果能
   支撑这个判断。不要从统一模板或工具介绍开始。
2. **选择自然表达形态**：按材料决定是来源故事、直接观点、产品体验、现象观察、解释、对照、
   推荐或其他更合适的推进方式。没有任何一种形态是每题必需的。
3. **完整起草**：每段只在确实推进当前论证、事实、转折或后果时存在；不把短摘要靠同义反复
   拉长，不用统一的工作流/责任/验收文章替代题目本身。
4. **一次主观复读**：当前 outer Codex 从头读到尾，只问：这像不像泛 AI 博主会说的话？是否
   又滑回流程、责任或验收说明？当前题的论证是否真的移动？必要时只重写当前题一次。
5. **提交再继续**：title、hook、structure 必须描述最终正文，但 structure 只是用户可见摘要，
   不是叙事 hard gate。提交前一题的脚本或 typed failure 后，下一题才会暴露；恢复从首个未完成题继续。

## 参考模块

Git-owned modular editing reference 只总结已批准 Austin 文本和原始案例中的编辑动作：判断先于
解释、口语呼吸与转折、具体后果、题目自己的收束。它不包含完整正文，不读取 private case/persona，
不选择题目角度，不提供 fallback。

## 质量与安全

- 事实边界在静默生成/复读 context 中约束正文；不要把 provenance、缺字段或核验过程写成口播。
- 不把建议案例写成 Austin、客户、团队或本人已经发生的实测结果。
- 字符、词数、段落、问句、步骤、关键词和固定结尾只能诊断，不能决定生成或通过。
- 当前 batch 完成后只做 identity、事实边界、泄漏和叙事同构的阅读全文检查；不委托新的执行者。

## 禁止

- 不启动 `codex exec`、第二 Agent、daemon、watcher、后台任务或外部队列。
- 不恢复 human supplement、production direction、Feishu、完整制作包或旧异步 runner。
- 不使用旧 run、latest、历史脚本或 deterministic draft 替代当前结果。
- 不新增 per-script runtime identity/state、permission gate、receipt 或第二 authority。
