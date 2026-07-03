# AR-009 返修对比：稳定风格基线 + 当前选题搜索融合

日期：2026-07-03  
分支：`feature/next-production-flow`

## 结论

本轮没有继续沿用上一版 `019f484 feat: make 06 scripts scene-first` 的新风格体系，而是回到 `austin-voice-scriptwriter-v0.1` 的稳定六段基线：真实痛点、旧流程、这条真正要做什么、三个动作、前后对比、边界收尾。

新增能力只作为输入素材进入 06：

- 当前选题搜索/同类来源摘要。
- 对标/同类表达模式拆解。
- 保留/丢弃/融合说明。
- 知识库、RAG、Agent 等概念的浅显解释。
- 风格基线保护说明。

## 上一轮为什么更 AI

上一轮 `019f484` 的问题不是“没有场景”，而是把场景规则做成了新的显性章节和硬模板：

- 口播标题从稳定基线的 `真实痛点` 改成 `先给真实场景`，再新增 `对标拆解后再转译`，听感更像写作框架说明。
- `workflow_object`、`concrete_scene`、`benchmark_translation` 直接重建一套对象化表达，容易把稿子推成“我来拆一个方法论”。
- 测试奖励的是 `场景化表达规则`、`对标拆解后再转译` 等结构词，反而没有验证搜索证据是否进入生成、是否融合进最终口播。
- 知识库问题只被转成“先讲 03/04/06 场景”，但没有用普通人能听懂的例子解释知识库到底解决什么。

本轮修正：保留稳定章节名，只在段落里自然吸收检索素材和浅显解释。

## 搜索来源

本轮没有找到可核验的 `xAI Voice Agent Builder` 官方来源，因此不声称该产品能力已验证，只把 xAI 相关能力放入发布前核验。

可用同类来源：

- [OpenAI Realtime and audio docs](https://developers.openai.com/api/docs/guides/realtime)：voice-agent session 是音频/文本输入、模型响应、工具调用、会话事件一起运转。
- [ElevenLabs ElevenAgents quickstart](https://elevenlabs.io/docs/eleven-agents/quickstart)：同类语音 Agent 会同时涉及 voice/language、knowledge/RAG、tools、personalization。
- [Obsidian Internal links](https://obsidian.md/help/links)：笔记可链接到文件、标题、块，并支持显示文本。
- [Obsidian Graph view](https://obsidian.md/help/plugins/graph)：Graph view 用节点和链接展示笔记关系。
- [How People Manage Knowledge in their "Second Brains"](https://arxiv.org/abs/2509.20187)：个人知识库的核心是记录、组织、检索和未来复用；检索策略会影响内容如何构建和维护。

## 样例一：xAI Voice Agent Builder / AI口播交付

改前稳定基线片段：

> 很多人现在用 Agent 做项目，最痛苦的不是它不会执行，而是它执行完以后，你反而更不确定了。

上一轮失败片段：

> 我会拿一段 30 秒口播脚本来看：声音生成以后，能不能接上角色语气、分镜节奏、字幕长度和返修验收。

本轮改后片段：

> 很多人现在做 AI 口播，最痛苦的不是声音生成不出来，而是放进成片以后，角色、字幕、分镜和返修都接不住。

> 我会参考同类内容里这个讲法：同类 voice-agent 内容常用“几分钟搭好一个能接电话/能对话的 agent”开场，但容易停在工具体验 / 更适合 Austin 的转译是：别先问声音像不像真人，先问它能不能接住角色、分镜、字幕和返修。

融合说明：

- 保留：voice agent 从声音 demo 进入工作流节点的趋势。
- 丢弃：未核验的 xAI 具体能力。
- 融合：30 秒口播脚本、角色语气、分镜节奏、字幕长度、返修验收。
- 浅显解释：声音只是素材，不是成片；像演员念出台词后，还要看角色、镜头、字幕和剪辑节奏能不能接住。

## 样例二：Codex + Obsidian 知识库 / 内容资产沉淀

改前稳定基线片段：

> 很多人现在用 Agent 做项目，最痛苦的不是它不会执行，而是它执行完以后，你反而更不确定了。

上一轮失败片段：

> 我会拿自己信息雷达里的一条内容来看：它从 03 收件箱进来，到了 04 变成选题判断，最后有没有在 06 留下脚本路径、证据和复盘线索。

本轮改后片段：

> 很多人现在做内容生产，最痛苦的不是资料不够多，而是资料进来以后，写内容时还是要重新找、重新判断、重新组织。

> 知识库不是一个大仓库，更像给每条素材贴一张流转单：它从哪里来、为什么值得看、最后变成哪条选题或脚本，下次才能被叫回来用。

> 但最后会收回到我的表达：保留链接、关系、可检索这三个信息点 / 丢弃插件安装、库结构教程 / 融合到信息雷达 03 收件箱 -> 04 选题判断 -> 06 脚本包路径。

融合说明：

- 保留：链接、关系、可检索。
- 丢弃：插件安装、库结构教程、百科式知识库定义。
- 融合：信息雷达 `03 收件箱 -> 04 选题判断 -> 06 脚本包路径`。
- 浅显解释：知识库不是资料仓库，而是让素材后续能被选题、脚本、复盘继续调用。

## 生成产物

本轮只做本地 dev 输出，没有写生产飞书、没有发真实选题卡、没有创建生产飞书文档。

- `output/ar009_rework_regression/2026-07-02/recvoaOc5dJfbS_xAI_Voice_Agent_Builder出来后，我想重看AI口播能不能进入视频交付/full_script_execution_package.md`
- `output/ar009_rework_regression/2026-07-02/recvoaOc5dT6vv_Codex+Obsidian知识库这个选题，我会反过来检查自己的信息雷达有没有沉淀资产/full_script_execution_package.md`

## 仍需人工确认

- `xAI Voice Agent Builder` 是否真实开放、能力边界、价格和访问方式仍未核验，不能写成已验证事实。
- 两条样例都还需要真实截图/录屏素材：口播验收表、分镜/字幕节点、`03 -> 04 -> 06` 路径截图。
- 当前本地 deterministic renderer 已能验证输入融合；真实 `codex exec` 生产稿还需要 PM 后续安排测试线程独立验收。
