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

## PM Review 返修：方法不能落成固定结构

PM Review `ab0349d` 不通过，原因成立：上一版虽然把概念/工具型判断写成“内部素材”，但 deterministic fallback 仍用固定 6 段口播和固定分段执行表承接，导致两条样例共享同一套章节名、动作段和过渡句。

复现到的问题：

- 两条样例的 `口播全文` 都有 `真实痛点`、`旧流程`、`这条真正要做什么`、`三个动作`、`前后对比`、`边界和收尾`。
- 两条样例都出现 `如果真要拿「...」来拍，就不能只看工具介绍`、`围绕「...」，我先看三个动作`、`能不能继续做，最后看的是...`、`最后还是回到我自己判断...`。
- `分段执行方案` 直接复用口播段落标题，因此把同构骨架继续放大到拍摄执行层。

根因判断：

- `austin-voice-scriptwriter/scripts/austin_voice.py` 的 deterministic renderer 仍把 Austin 风格等同于固定六段结构。
- `austin-no-overtime-scripting/scripts/austin_scripting.py` 的 `full_package_outline()` 又额外生成一套固定 `开场钩子/真实痛点/核心判断/实操主线/失败和修正/收尾判断`。
- 测试此前只拦重复长句和禁词，没有拦跨样例重复章节标题、推进骨架和分段执行表段落名。

本轮修正：

- voice deterministic fallback 改为从输入材料派生段落标题：旧方式、痛点、AI 介入、证据、判断和待补素材都来自 Topic Card，而不是固定章节名。
- 移除 PM 点名的固定过渡句，不再写统一“三个动作”段。
- `full_package_outline()` 改为从实际口播段落派生视频结构，避免执行包层另造一套固定结构。
- runner prompt 和 Skill 文档明确：仓库 deterministic fallback 只做格式、安全和字段兜底，不代表最终 Austin 风格质量验收；真实质量要看测试 Skill / 私有 Skill 的实际输出和人工样例。
- 新增测试：同时检查跨样例口播章节标题、分段执行表段落名和 PM 点名固定句。

Round 2 本地输出：

- `/private/tmp/ar009_method_rework_round2/2026-07-03/recvoaOc5dJfbS_xAI_Voice_Agent_Builder出来后，我想重看AI口播能不能进入视频交付/full_script_execution_package.md`
- `/private/tmp/ar009_method_rework_round2/2026-07-03/recvoaOc5dT6vv_Codex+Obsidian知识库这个选题，我会反过来检查自己的信息雷达有没有留下后面能用的东西/full_script_execution_package.md`

Round 2 反同构统计：

- 共享 `口播全文` 子章节标题：0。
- 共享 `分段执行方案` 段落名：0。
- PM 点名固定句命中：0，包括 `如果真要拿`、`这条真正要做什么`、`围绕「...」我先看三个动作`、`能不能继续做，最后看的是`、`最后还是回到我自己判断`。
- `如果当天还没生成06` 仍只在发布前核验 / QA 发布前提醒中出现；未进入口播、拍摄和素材区。

仍需注意：

- 这仍是 deterministic 本地渲染，不是最终内容质量证明。
- 真实 `codex exec` 测试应继续使用 `austin-no-overtime-scripting-ar009-test` / `austin-voice-scriptwriter-ar009-test`，由 PM 决定是否派测试线程复测。

## PM Review 返修：fallback 不再作为内容质量样例

PM Review `51bc938` 不通过，原因成立：上一轮清掉了旧固定标题和旧固定句，但 Round 2 fallback 仍生成了一套新的共用推进句。这说明问题不在某几句话，而在 deterministic fallback 仍承担“完整 Austin 风格口播样例”的角色。

复现到的问题：

- Round 2 两条样例都出现 `我先不讲「...」是什么`、`这一段不急着解释工具`、`这个动作不求完整演示`、`不让它变成整条视频的主角`、`如果这一段只剩「...」听起来新`、`拍之前我至少还要补`、`补不上，这条就先停在草稿`。
- 这些句子同时进入 `口播全文`、`视频结构` 和 `分段执行方案`，说明 fallback 仍在把内部方法判断展开成可见口播结构。

根因判断：

- `austin_voice.py` 的 deterministic renderer 只要继续输出完整风格化口播，就会自然生成跨样例推进模板；换一批句子不能解决。
- deterministic fallback 适合验证字段完整、内部边界、禁词、事实提醒和 Markdown 结构，不适合证明“像不像用户”。
- AR-009 的内容质量验收应转到真实 `codex exec` 路径，并显式调用 `austin-no-overtime-scripting-ar009-test` / `austin-voice-scriptwriter-ar009-test`。

本轮修正：

- `austin_voice.py` fallback 改为 `fallback_draft / not_style_qa`，只输出旧方式、真实卡点、核心观点、AI 介入、证据、待补和事实边界等字段化草稿。
- 06 执行包 QA 增加提醒：`fallback_draft / not_style_qa` 只做字段、格式和安全兜底；PM/用户内容质量验收必须走 `codex exec + -ar009-test` 私有测试 Skill。
- 测试新增本轮 7 类固定推进句防线，并断言 fallback/报告不得自称 Austin 风格或内容质量通过。

真实测试 Skill 验收路径：

```bash
SCRIPT_PACKAGE_SKILL_NAME=austin-no-overtime-scripting-ar009-test \
SCRIPT_PACKAGE_VOICE_SKILL_NAME=austin-voice-scriptwriter-ar009-test \
SCRIPT_PACKAGE_OUTPUT_ROOT=/private/tmp/ar009_method_rework_codex_exec_dev \
python3 scripts/codex_script_package_runner.py --record-id <record_id> --limit 1 --no-completion-card
```

注意：不加 `--write-feishu`，不得写生产表、发真实卡、创建生产飞书文档或触发生产采集。若开发线程无法安全跑完整 `codex exec`，由 PM 派测试线程使用同一组环境变量复测。

本轮开发线程真实验证：

- 先用 `AI_ACCOUNT_RADAR_ENV=staging ... --skip-codex` 做只读队列预检，staging 中没有这两个生产 record，返回 `count: 0`，未写入。
- 随后绕过飞书队列，直接用 fixture 调用 runner 的 `generate_package_with_retry()`；环境变量显式设置 `SCRIPT_PACKAGE_SKILL_NAME=austin-no-overtime-scripting-ar009-test`、`SCRIPT_PACKAGE_VOICE_SKILL_NAME=austin-voice-scriptwriter-ar009-test`、`SCRIPT_PACKAGE_OUTPUT_ROOT=/private/tmp/ar009_method_rework_codex_exec_dev`。
- 两条真实 `codex exec` 均完成，没有 `--write-feishu`，没有发送完成卡，没有创建生产飞书文档。

真实 `codex exec` 输出：

- `/private/tmp/ar009_method_rework_codex_exec_dev/2026-07-03_xAI_Voice_Agent_Builder出来后_我想重看AI口播能不能进入视频交付_完整脚本与制作包.md`
- `/private/tmp/ar009_method_rework_codex_exec_dev/2026-07-03_Codex+Obsidian知识库这个选题_我会反过来检查自己的信息雷达有没有沉淀资产_完整脚本与制作包.md`

真实输出检查：

- 两份真实输出不含 `fallback_draft` / `not_style_qa`，说明内容质量样例来自测试 Skill + `codex exec`，不是 deterministic fallback。
- xAI 输出主线回到 AI 口播进入视频交付，覆盖角色语气、分镜节奏、字幕长度、返修入口和最终成片验收；未声称 xAI Builder 已开放或已验证。
- 知识库输出从“资料存了但写内容时仍要重新找、重新判断、重新组织”的真实痛点切入，再用“流转单”解释知识库；不是从概念或工具教程切入。
- 知识库中的 `如果当天完整稿没有真实生成到最后一步` 只出现在 `发布前核验` / QA 风险区域；未进入口播全文、视频结构、素材清单或发布包草稿。
- `沉淀资产` 未进入用户可见正文内容；仅作为原始题目保留在文件名路径中。
