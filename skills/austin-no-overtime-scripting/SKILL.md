---
name: austin-no-overtime-scripting
description: 将 WEB-010 已确认选题的 same-run rich Topic Card 编排为 spoken-only 批次输入，并由当前 outer Codex 在同一上下文应用 austin-voice-scriptwriter，返回简单完整口播结果。不负责完整制作包、外部队列、表格回写、发布或启动其他进程。
---

# Austin不加班脚本 Skill

本 Skill 在 WEB-010 正常链只负责一件事：把同一 run 已选中的 rich Topic Cards
整理成一个口播批次，让当前 outer Codex 直接写出每题完整正文。

## 正常运行模型

- 唯一 AI owner 是当前 outer Codex。
- 本 Skill 每个批次应用一次，`austin-voice-scriptwriter` 每个批次应用一次。
- 两份 Skill 都在当前上下文直接读取和执行，不启动新进程或其他 Agent。
- 每题独立成稿，但整个批次只返回一次简单结果。
- 输出只包含 `topic_id/title/hook/structure/body` 和 item-local failures。

本节是 WEB-010 `scripts_required` 的完整执行合同。历史完整制作包、外部队列、表格索引和
单条 runner 已退出正常运行，也不是本 Skill 可执行的资源路径。

## 输入

每个 selected topic 使用 public handoff 已提供的 same-run facts：

- topic identity、title、hook、structure、selection reason；
- unique judgment、persona fit；
- source title/summary/URL、公开事实和 provenance；
- pain、old workflow、AI intervention、experiment、validation；
- available/missing evidence、fact boundary、cannot-claim；
- 存在时的 caption、ASR、screen facts、keyframes 和 unresolved。

不读取或要求 human supplement、production direction、完整制作包字段或历史 run 替代。
optional facts 缺失时保持缺失，不从模型记忆或其他 topic 补齐。

## 完整口播内部方法

本节恢复 last-known-usable 的完整口播展开过程，但不恢复旧制作包或外部执行链。详细方法
见 `prompts/spoken_body_method.md`；它是当前 Skill 的内部生成步骤，不是额外用户产物。

1. **逐题聚焦**：一次只处理一个 rich Topic Card。完整保留这条题的来源事实、独有判断、
   场景线索、理解证据和不能声称；不得先把多题压成一组摘要，也不得从其他题借场景。
2. **分段规划语义**：先在内部规划一条自然的 3-5 分钟口播如何推进，明确冲突、旧流程、
   关键转折、动作或实验、判断、后果和收束各自承担什么信息。时长只描述内容展开程度，
   不是字符下限、固定段数或统一时间轴。
3. **完整正文起草**：在当前上下文应用 `austin-voice-scriptwriter`，一次写完这题可连续朗读
   的正文。不能先写批次摘要，再把摘要扩成几段；每个动作必须带来新的信息或后果。
4. **提词器视角复读**：从头朗读正文，检查开场是否进入具体现场、转折是否听得懂、抽象
   判断后是否有动作、连续句是否便于换气，以及只看口播能否跟上故事。只调整正文，不输出
   镜头表、制作说明或另一份提词器文件。
5. **逐题内容 QA 和必要重写**：先核对题目独有事实与判断，再检查场景、冲突、旧流程、
   动作/实验、后果和自然收束是否完整。缺的是内容时重写相关段落或整篇，不能靠同义反复、
   空泛过渡或追加模板段落补长度。正文重写改变叙事推进时，同步复读并更新该题的
   `title/hook/structure`，让这些用户可见字段描述最终正文，而不是保留改写前的旧骨架。
   单题失败保持 item-local，其余题继续。
6. **完成一题再进入下一题**：多选题仍是一次 batch、一次 Voice Skill 应用，但每题都必须
   独立走完上述五步。最后才做全批 identity 覆盖和叙事同构检查。

## 批次提交

1. 把事实约束留在静默生成/QA context，不机械写入口播。
2. 只返回每题 `title/hook/structure/body` 与 item-local failures；提交前确认 structure 与最终
   body 的场景、转折和动作顺序一致。
3. 当前 outer Codex 直接通读全批完整正文，检查身份覆盖、题目独有展开、事实边界和批次
   模板重复；不委托新的执行者。
4. 字符数、问句数、步骤数和段落数只记录为诊断，不能决定生成或通过。

## 编排原则

- 不从短标题重新理解选题；必须承接 rich Topic Card 的判断与 same-run facts。
- Voice Skill 负责口播风格，本 Skill 不复制问句、步骤、开头或结尾模板。
- private style/case context 只作可选发散；0 case match 是正常输入。
- reasonable hypothetical/composite scene 和 illustrative data 可以进入写作材料，但不能
  冒充真实客户、团队、本人实测或第三方验证结果。
- 来源、事实缺失和不能声称用于后台约束；不把口播写成审核说明。
- 不生成素材、镜头、剪辑、发布、QA 包或完整制作包。

## 结果检查

当前 outer Codex 在同一上下文直接完成：

- selected topic 与 script identity exact coverage；
- 五题或多题并排结构检查；
- unsupported actual-result 检查；
- private wording leakage 检查；
- simple schema 校验；
- public submit 后 exact replay no-churn 检查。

质量判断必须阅读全文，字符数、问句数、步骤数和关键词只能作为诊断。

## 禁止

- 不启动新的 Codex 进程、第二 Agent、daemon 或后台任务。
- 不进入外部队列、表格回写、通知、卡片或发布路径。
- 不恢复 human supplement、production direction 或完整制作包。
- 不使用旧 run、latest、历史脚本或 deterministic draft 替代当前结果。
- 不新增 per-script runtime state、identity service、permission gate、receipt 或第二 authority。
