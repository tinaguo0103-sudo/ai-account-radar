---
name: austin-no-overtime-scripting
description: 将 WEB-010 已确认的 same-run rich Topic Card 逐题交给独立 bounded writer child，应用 Austin voice method 生成 spoken-only 简单结果。不负责完整制作包、外部队列、表格回写或发布。
---

# Austin 不加班脚本

## Legacy-only boundary

本文件及其 references 仅为历史兼容保留，不属于 normal scripts runtime。正常
writer child 只加载 `austin-voice-scriptwriter`；controller、writer prompt、writer
packet、diagnostic ledger 和 release protocol 均不得读取或列出本 Skill。不要把本文件
的写作阶段、Semantic Plan 或共享结构复制到 controller 或 voice Skill。

## Historical superseded model

以下内容只解释旧 checkpoint / 旧文档的形状，不能作为当前 writer child 的执行合同。
当前 normal runtime 不加载本 Skill；当前 writer child 只加载
`austin-voice-scriptwriter`。

- 每个 selected topic 由 controller 启动一个新的 bounded writer child；该 child 是本题唯一 AI owner，直接应用本 Skill 和 `austin-voice-scriptwriter`。外层 controller 不写主编判断或正文。
- public runtime 一次只暴露一个 selected rich Topic Card。当前题提交 script 或 typed material failure 后，下一题才会暴露；checkpoint 从首个未完成题恢复，不重生已完成题。
- Facts-first draft 前只读取当前 Topic Card 的 exact identity、source facts/details、fact boundary、cannot-claim 和 short selection reason。禁止在 draft 形成前读取任何 persona、case、sample、approved script、editing delta 或风格 checklist。
- Draft 完成后，writer child 才通过 Austin Voice Skill 读取 allowlist 中的真实用户人设、原始案例/样稿和 before/after edit pairs，做一次 Author Edit。批准稿模块只能做局部编辑对照，不能供应当前题的开场、论证运动或结尾。
- 最终只形成一个 simple scripts result。Writer child 不得递归启动 Codex、第二 Agent、独立模型 API、selector、retrieval system 或第二 authority，也不得执行浏览器、发布或业务写入。

## Historical packet shape (not emitted now)

writer packet 只包含 exact topic identity、source title/facts/details、fact boundary、不能声称的部分和简短入选原因。editorial 的 title、hook、structure、unique_judgment 不得作为成稿蓝图；最终字段由当前题写作阶段共同完成。

只使用当前题事实。可从来源故事、直接论证、产品观察、公开人物动作、时间线、对照、建议或其他自然形态推进。表达形态不保存为业务字段，不分配固定文章类型，不以关键词、字数、段落、问句、步骤或模板判断质量。

## Historical method (not emitted now)

1. 只用当前题事实先形成完整 draft，并共同决定 title/hook/structure/body；如果只有泛角度，返回 item-local `material_or_angle_insufficiency`。
2. draft 完成后再读 Austin-owned private context，作者编辑只改站位、口语呼吸、措辞、删减、连接和自然收束，保留 topic identity、facts、central thesis 和 argument movement。
3. Author Edit 不制造新的“不是 X 而是 Y”反转，不改成 workflow/responsibility/acceptance/system commentary，不虚构经历、客户结果或实测数字。
4. 每题完成后再提交，保持选题身份、事实边界和简单 schema；提交前不暴露下一题。

Austin-owned private context 只在 draft 完成后用于 Author Edit，不是当前题事实、opening、argument blueprint 或统一 outline。

## Legacy constraints

- 材料或作者角度不足时，返回当前题 `material_or_angle_insufficiency`，不生成模板化替代稿，不污染其他题。
- 不恢复 human supplement、production direction、完整制作包、Feishu、旧异步 runner 或 legacy selector。
- 不读取 legacy three-round reference 作为正常生成输入；不复制任何第三方 Skill 的人格、口癖、范文、模板、禁词或数字自检。不得使用 phrase ban、numeric gate、template classifier 或 anti-template self-check prompt。
