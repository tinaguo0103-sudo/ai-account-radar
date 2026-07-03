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
