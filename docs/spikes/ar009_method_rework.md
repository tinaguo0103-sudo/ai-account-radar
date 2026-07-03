# AR-009 方法型 Skill 优化记录

## 本轮目标

这轮不是修两个样例，而是给 `06 完整脚本与制作包` 增加概念/工具型选题的生成前判断方法。知识库和 xAI 只作为回归样例，不能变成固定规则、固定段落或固定句式。

## 审计判断

- 当前 Skill 容易让概念直接成为主角，主要发生在三层：runner prompt 只要求“概念浅显解释”，但没有要求先判断旧方式和真实卡点；`austin-no-overtime-scripting` 的 `lead_hook()` 有知识库/口播的概念优先钩子；deterministic fallback 的工作流对象会把知识库类输入归成内部抽象对象。
- 用户给的替代工具和音频例子如果写进 Skill，会再次把方法变成样例仿写。本轮只保留抽象判断框架，不写入具体例子。
- `austin-voice-scriptwriter` 应继续保护原有 Austin 口播基线：先真实痛点、旧流程、动作、人工判断和边界；搜索/对标/概念解释只能作为素材，不控制正文结构。

## 改动原则

- 新增的是“生成前判断框架”：旧方式/普通替代方式、真实工作卡点、为什么现在需要概念/工具、人话解释素材、当前工具/热点只是落地案例、回到本人工作流和证据。
- 该框架只进入输入上下文、报告和候选素材，不要求最终口播逐条显性覆盖。
- 不新增 `knowledge_base_opening()`、`tts_opening()` 这类样例专用正文函数。
- 不把用户举例写成固定规则；不同选题仍由输入里的真实旧流程、痛点、素材和 Austin 风格基线决定讲法。

## 本地回归

本轮用 `-ar009-test` 测试 Skill 副本做了本地 deterministic 渲染，未跑真实 `codex exec`。真实私有 Skill 路径仍需 PM 派测试线程复测。

输出路径：

- `/private/tmp/ar009_method_rework_local/2026-07-03/recvoaOc5dJfbS_xAI_Voice_Agent_Builder出来后，我想重看AI口播能不能进入视频交付/full_script_execution_package.md`
- `/private/tmp/ar009_method_rework_local/2026-07-03/recvoaOc5dT6vv_Codex+Obsidian知识库这个选题，我会反过来检查自己的信息雷达有没有留下后面能用的东西/full_script_execution_package.md`

关键变化：

- xAI 样例开头从工具/概念切入改为：以前脚本、配音、字幕、分镜和剪辑验收是分开的，生成声音不等于可交付。
- 知识库样例开头从概念切入改为：资料存进去以后，写内容时还是要重新找、重新判断、重新组织。
- `概念/工具型生成前判断` 出现在生成输入里，并标注为内部素材、不要逐条念、不要固定段落顺序。
- `如果当天还没生成06` 只保留在发布前核验和 QA 发布前提醒；`沉淀资产` 未出现在本地输出。

## 测试防线

- runner prompt 测试覆盖测试 Skill 路由、内部边界、QA draft，以及概念/工具型生成前判断的非模板化表述。
- scene rules 测试覆盖生成输入中的方法框架、开头不裸奔工具/概念、不新增样例专用 helper、不写入用户举例、xAI 题眼、反同构、内部边界和用户可见文案清洗。

## 剩余风险

- 本地 deterministic 渲染只能验证仓库镜像和测试 Skill 副本的规则，不等于真实 `codex exec` 口播质量。
- 真实私有 Skill 仍可能把“方法判断”解释成固定结构，需要测试线程用 `austin-no-overtime-scripting-ar009-test` / `austin-voice-scriptwriter-ar009-test` 跑完整路径复测。
