# AR-009 用户人工确认样例

本文件用于弥补 AR-009 QA 回传中缺少用户可直接确认样例的问题。技术 QA 已通过，但发布状态应等待用户确认真实输出内容。

## 样例路径

- dev 生成输出：`output/ar009_scene_regression/2026-07-02/recvoaOc5dJfbS_xAI_Voice_Agent_Builder出来后，我想重看AI口播能不能进入视频交付/full_script_execution_package.md`
- dev 生成输出：`output/ar009_scene_regression/2026-07-02/recvoaOc5dT6vv_Codex+Obsidian知识库这个选题，我会反过来检查自己的信息雷达有没有沉淀资产/full_script_execution_package.md`
- 测试线程独立重跑输出：`/private/tmp/ar009_qa_scene_regression/2026-07-02/recvoaOc5dJfbS_xAI_Voice_Agent_Builder出来后，我想重看AI口播能不能进入视频交付/full_script_execution_package.md`
- 测试线程独立重跑输出：`/private/tmp/ar009_qa_scene_regression/2026-07-02/recvoaOc5dT6vv_Codex+Obsidian知识库这个选题，我会反过来检查自己的信息雷达有没有沉淀资产/full_script_execution_package.md`

## 人工确认点

### xAI Voice Agent 样例

- 开场结构：`00:00-00:35｜先给真实场景`
- 关键片段：`我会拿一段 30 秒口播脚本来看：声音生成以后，能不能接上角色语气、分镜节奏、字幕长度和返修验收。`
- 确认点：是否比原来更像真实视频交付场景，而不是先讲 Voice Agent 概念。

### Codex+Obsidian 知识库样例

- 开场结构：`00:00-00:35｜先给真实场景`
- 关键片段：`我会拿自己信息雷达里的一条内容来看：它从 03 收件箱进来，到了 04 变成选题判断，最后有没有在 06 留下脚本路径、证据和复盘线索。`
- 对标转译片段：`它表面在讲 Codex 和 Obsidian 怎么搭知识库。但我不会照着讲教程，我会把它转成自己的内容生产复盘。`
- 确认点：知识库概念是否没有在开头前置，是否先从账号内真实流程问题进入。

## 当前 PM 判断

- 技术 QA：通过。
- 用户人工确认：待确认。
- 发布状态：不得直接视为生产可用；仍需用户确认真实样例、全局私有 Skill 同步策略和最小 production smoke。
