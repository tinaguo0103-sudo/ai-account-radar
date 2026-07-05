# AR-009 测试 Skill 环境说明

更新时间：2026-07-03

## 目标

为 AR-009 内容质量复测建立隔离的本机 Skill 测试环境。测试阶段不修改生产全局 Skill；测试通过并进入发布时，再由发布流程决定是否同步生产 Skill。

## 测试 Skill 路径

- `austin-no-overtime-scripting-ar009-test`
  - `/Users/congcong/.codex/skills/austin-no-overtime-scripting-ar009-test`
- `austin-voice-scriptwriter-ar009-test`
  - `/Users/congcong/.codex/skills/austin-voice-scriptwriter-ar009-test`

## 同步方式

1. 从当前生产全局 Skill 只读复制到 `-ar009-test` 测试目录，保留私有参考资料和运行结构。
2. 仅在测试目录内覆盖仓库中 AR-009 本轮修正后的文件：
   - `skills/austin-no-overtime-scripting/SKILL.md`
   - `skills/austin-no-overtime-scripting/scripts/austin_scripting.py`
   - `skills/austin-voice-scriptwriter/SKILL.md`
   - `skills/austin-voice-scriptwriter/scripts/austin_voice.py`
3. 测试目录内做名称隔离：
   - `SKILL.md` 的 `name:` 改为 `-ar009-test` 名称。
   - `austin-no-overtime-scripting-ar009-test/scripts/austin_scripting.py` 的 `VOICE_SKILL_NAME` 指向 `austin-voice-scriptwriter-ar009-test`。

生产全局 Skill 原目录未修改：

- `/Users/congcong/.codex/skills/austin-no-overtime-scripting`
- `/Users/congcong/.codex/skills/austin-voice-scriptwriter`

## 本轮测试副本修正

- 知识库口播中的标题表达会把 `沉淀资产` 转成 `后面能用的东西`，避免内部抽象词进入对镜头口播。
- `如果当天还没生成06` 这类内部状态边界只允许作为 QA/发布前核验边界，不进入口播、拍摄前待办、素材清单或剪辑节奏。
- 搜索/对标/知识库解释仍作为素材和报告存在，不作为固定口播正文骨架。

## 第 1/3 轮返修记录

触发原因：测试线程使用 `-ar009-test` 私有测试 Skill 跑真实 `codex exec` / watcher 等价路径后，知识库样例仍有内部状态边界和 `沉淀资产` 用户可见残留。

失败来源定位：

- `如果当天/今天没有生成 06`、`没有完整生成到最后一步`、`选题系统复盘` 属于输入 Topic Card 的证据边界，但真实生成路径把它扩写进了拍摄前待办、口播正文和素材清单。
- `沉淀资产` 来自 Topic Card 标题、主编判断和执行包生成时的二次文案，上一轮只处理了狭义 `口播全文`，没有覆盖开头钩子、封面大字、简介、置顶评论和 QA 原因。
- runner prompt 和 Skill 文档没有明确说明“用户可见创作内容”的清洗范围，导致真实 `codex exec` 仍可能把内部词带进完整包。

修复策略：

- runner prompt 增加硬性要求：内部状态边界只能留在 `发布前核验`、`QA 风险与防错` 或 `发布前提醒`，不得进入开头钩子、拍摄前待办、视频结构、口播、分段执行、录屏素材、剪辑交接和发布包草稿。
- `austin-no-overtime-scripting` / `austin-voice-scriptwriter` 测试 Skill 文档同步写入同一条边界。
- deterministic fallback 增加用户可见文本清洗：`沉淀资产` 转成 `留下后面能用的东西` / `后面能用的东西`，并扩展内部状态边界匹配。
- 回归测试改为检查最终 Markdown 用户可见区段，而不是只检查 `口播全文`。

本轮本地验证输出：

- `/private/tmp/ar009_test_skill_rework_round1/2026-07-03/recvoaOc5dT6vv_Codex+Obsidian知识库这个选题，我会反过来检查自己的信息雷达有没有留下后面能用的东西/full_script_execution_package.md`
- `/private/tmp/ar009_test_skill_rework_round1/2026-07-03/recvoaOc5dJfbS_xAI_Voice_Agent_Builder出来后，我想重看AI口播能不能进入视频交付/full_script_execution_package.md`

验证边界：本轮开发线程没有跑完整真实 `codex exec`；只做了仓库镜像单测、测试 Skill 副本本地渲染和静态检查。后续仍需 PM 派测试线程用 `-ar009-test` 私有测试 Skill 复测真实路径。

## Runner 调用方式

默认不设置环境变量时，仍使用生产 Skill 名称：

```bash
python3 scripts/codex_script_package_runner.py
```

AR-009 复测时显式指定测试 Skill：

```bash
SCRIPT_PACKAGE_SKILL_NAME=austin-no-overtime-scripting-ar009-test \
SCRIPT_PACKAGE_VOICE_SKILL_NAME=austin-voice-scriptwriter-ar009-test \
python3 scripts/codex_script_package_runner.py
```

这只改变 `codex exec` prompt 中要求使用的 Skill 名称，不改变生产默认行为。

## 验证口径

- 测试 Skill 目录存在，且 `SKILL.md name` 均带 `-ar009-test`。
- 测试 no-overtime Skill 内部 voice 引用指向 `austin-voice-scriptwriter-ar009-test`。
- runner prompt 在设置环境变量时引用测试 Skill；未设置时引用生产 Skill。
- 生产全局 Skill 关键文件修改时间保持不变。
- 不写飞书、不发卡、不创建生产文档、不触发生产采集。
