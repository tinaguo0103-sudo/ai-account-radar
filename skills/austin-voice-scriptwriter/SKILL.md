---
name: austin-voice-scriptwriter
description: 将已确认的 same-run rich Topic Card 写成自然、可连续朗读、接近 Austin 判断习惯的 spoken-only 正文。不负责选题筛选、事实核验、外部写入或启动其他进程。
---

# Austin Voice Scriptwriter

这个 Skill 只处理当前题“怎么说出来”。当前 rich Topic Card 是事实来源；Git-owned modular editing
reference 只提醒编辑动作，不提供完整范文、private case/persona 或当前题角度。

## 写作边界

- 先从当前题的事实、人物、产品变化、结果或矛盾找到一个真正具体的判断，再决定怎么开始。
- 可以从来源故事、直接观点、产品观察、现象解释、时间线、对照、建议或其他适合题目的形式推进。
  没有固定开场、固定问题链、固定动作数、固定段落顺序或固定结尾。
- 直接讲题目支持的事实和判断。公开人物的行为可以自然归属；不能把来源作者、客户或团队经历
  改写成 Austin 本人的经历。
- 没有 Austin 真实经历时，不默认使用第一人称实测、复合场景或“我做了一个实验”。假设只能
  在自然帮助解释题目且不会伪装真实结果时使用；材料不足就返回 item-local material_or_angle_insufficiency。
- provenance、source verification、missing evidence 和 cannot-claim 留在静默 context，不写成
  “公开信息显示”“根据来源”“目前可验证”等审核说明。边界只有在它本身是题目冲突时才自然出现。

## Modular editing reference

只把模块当成编辑问题。writer packet 不提供预写的 editorial title、hook、structure 或 unique_judgment；
最终字段必须由当前题的事实、细节和真实角度共同生成：

- 判断是否先于解释；
- 句子和停顿是否像人在说话；
- 转折是否让当前题的理解发生变化；
- 结尾是否只能属于当前题。

模块不是大纲，不要求每篇都显性出现，也不应被扩写成统一的“工作流—实验—责任”文章。

## 一次主观复读

当前 outer Codex 在同一上下文完成初稿后只做一次全文复读，问：

1. 泛 AI 博主能不能把这篇原样说出来？
2. 它是否滑回流程、责任、验收或工具功能解释？
3. 开头、论证移动和收束是否来自这条题？

必要时只重写当前题一次。质量判断不使用字数、句数、关键词、段落、问题数量、步骤数量或
固定句式 gate。

## 输出与禁止

- 返回简单 `topic_id/title/hook/structure/body`；body 必须能直接进入提词器继续修改。
- 不读取 `references/private/three_round_learning.md` 或其他 private case/persona 资料作为正常运行
  输入；不注入完整 Austin 原稿。
- 不启动 `codex exec`、第二 Agent、独立模型 API、watcher 或其他进程。
- 不生成完整制作包、镜头表、发布说明、Feishu 写入或用户不可见的第二结果。
