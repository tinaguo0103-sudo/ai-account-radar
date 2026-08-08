---
name: austin-no-overtime-scripting
description: 将 WEB-010 已确认的 same-run rich Topic Card 逐题交给当前 outer Codex，结合 Austin-owned 编辑参考生成 spoken-only 简单结果。不负责完整制作包、外部队列、表格回写、发布或启动其他进程。
---

# Austin 不加班脚本

## 正常运行模型

- 当前 outer Codex 是唯一 AI owner，直接应用本 Skill 和 `austin-voice-scriptwriter`。
- public runtime 一次只暴露一个 selected rich Topic Card。当前题提交 script 或 typed material failure 后，下一题才会暴露；checkpoint 从首个未完成题恢复，不重生已完成题。
- runtime 在每题前实际读取 approved Austin persona/case authority，并只向当前题提供安全的 Austin-owned modular editing reference 和 `read_status/source_id/role/sha256` ledger。私有正文不进入 checkpoint、Website、脚本或报告。
- 最终只形成一个 simple scripts result；不启动新进程、nested Codex、第二 Agent、独立模型 API、selector 或第二 authority。

## 当前题输入

writer packet 只包含 exact topic identity、source title/facts/details、fact boundary、不能声称的部分和简短入选原因。editorial 的 title、hook、structure、unique_judgment 不得作为成稿蓝图；最终字段由当前题写作阶段共同完成。

只使用当前题事实。可从来源故事、直接论证、产品观察、公开人物动作、时间线、对照、建议或其他自然形态推进。表达形态不保存为业务字段，不分配固定文章类型，不以关键词、字数、段落、问句、步骤或模板判断质量。

## 写作与复读

1. 先找这题自己的判断和真正的材料支点，而不是套一篇安全的 AI 评论。
2. 用当前材料能承受的细节、动作、后果或停点推进；没有 Austin 真实经历时，不写成 Austin 亲历，也不默认写“我做了一个实验”或补一个复合现场。
3. outer Codex 完成当前题初稿，再做一次从头到尾的主观复读，必要时只重写一次。检查是否滑回咨询说明、统一工作流/交付/责任收束，或与上一题共享母版。
4. 每题完成后再提交，保持选题身份、事实边界和简单 schema。

Austin-owned reference 的模块只是编辑动作：判断、呼吸、细节转向、作者站位、后果和题目专属停点。它不是固定 outline，也不是当前题事实。

## 失败和禁止

- 材料或作者角度不足时，返回当前题 `material_or_angle_insufficiency`，不生成模板化替代稿，不污染其他题。
- 不恢复 human supplement、production direction、完整制作包、Feishu、旧异步 runner 或 legacy selector。
- 不读取 legacy three-round reference 作为正常生成输入；不复制任何第三方 Skill 的人格、口癖、范文、模板、禁词或数字自检。
