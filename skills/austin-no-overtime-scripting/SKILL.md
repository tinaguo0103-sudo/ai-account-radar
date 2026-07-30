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

## 批次工作流

1. 将每题 rich Topic Card 压缩成写作材料，不改变 identity，不合并不同题。
2. 把事实约束放在静默生成/QA context，不机械写入口播。
3. 在当前上下文应用 candidate `austin-voice-scriptwriter` 的题目驱动规则，为每题写
   `title/hook/structure/body`。
4. 当前 outer Codex 直接通读全批次，检查身份覆盖、题目独有场景、动作、后果、提词器
   可读性、事实边界和批次模板重复；不委托新的执行者。
5. 只提交 schema-valid simple result。单题无法安全成稿时记录 item-local failure，其余题
   继续。

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
