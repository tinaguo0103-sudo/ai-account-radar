# AR-009 场景化表达回归摘要

日期：2026-07-03

## 目标

把 06 口播稿从“结构稳定但表达泛化”，推进到“先给账号内真实场景，再引出方法/工具”。本轮只改 dev 仓库镜像和确定性回归，不同步全局私有 Skill，不写生产飞书。

## 样例

使用 2026-07-02 两个实际选题做本地回归：

- `recvoaOc5dJfbS`：xAI Voice Agent Builder 出来后，我想重看 AI 口播能不能进入视频交付
- `recvoaOc5dT6vv`：Codex+Obsidian 知识库这个选题，我会反过来检查自己的信息雷达有没有沉淀资产

回归 fixture：

```text
skills/austin-no-overtime-scripting/examples/ar009_20260702_scene_regression.json
```

## 改前问题

- xAI 口播稿结构完整，但容易从工具热点进入，表达重点偏“AI 口播/Voice Agent 工具是什么”。
- 知识库稿虽然已经比纯教程好，但仍需要更强约束：不能先讲“做知识库”，必须先讲内容生产现场里的查找、判断、脚本路径和复盘问题。
- 对标内容的处理需要显性分两步：先拆对标表达表面在讲什么，再说明 Austin 怎么转译到自己的账号场景。

## 改后变化

### xAI Voice Agent 样例

改后开头从具体交付现场进入：

```text
我会拿一段 30 秒口播脚本来看：声音生成以后，能不能接上角色语气、分镜节奏、字幕长度和返修验收。
```

改后对标转译显性化：

```text
它表面在讲一个 Voice Agent 工具。但我不会只讲配音功能，我会转成视频交付问题：声音能不能被角色、分镜、字幕和返修流程接住。
```

### Codex+Obsidian 知识库样例

改后一屏钩子先给路径场景：

```text
先拿一条内容走完03到04再到06，再谈它是不是知识库资产。
```

改后口播第一段先讲账号内路径：

```text
我会拿自己信息雷达里的一条内容来看：它从 03 收件箱进来，到了 04 变成选题判断，最后有没有在 06 留下脚本路径、证据和复盘线索。
```

## 本地生成命令

```bash
AUSTIN_VOICE_SCRIPT_SKILL_DIR=skills/austin-voice-scriptwriter \
PYTHONPATH=skills/austin-no-overtime-scripting/scripts \
python3 skills/austin-no-overtime-scripting/scripts/batch_render.py \
  --input skills/austin-no-overtime-scripting/examples/ar009_20260702_scene_regression.json \
  --output-root output/ar009_scene_regression \
  --run-date 2026-07-02
```

生成位置：

```text
output/ar009_scene_regression/2026-07-02/
```

## 覆盖规则

- 先场景后概念。
- 先账号内真实问题后方法。
- 对标表达拆解后再转译。
- 必须写出个人使用体验、具体场景和细节。
- 知识库类选题必须先抛场景，再引出知识库作为解决方案。

## 仍不足

- 本轮只验证仓库脱敏镜像和 deterministic 生成器，未同步全局私有 Skill。
- 正式生产 watcher 默认读取全局私有 Skill；上线前需要由 PM 决定同步策略，再做最小 production smoke。
- 本轮未写飞书测试表、未创建飞书文档；因为任务目标是本地表达规则回归，不涉及外部写入。
- 仍需测试线程独立回归，确认改后稿没有新的口吻问题或过度模板化。
