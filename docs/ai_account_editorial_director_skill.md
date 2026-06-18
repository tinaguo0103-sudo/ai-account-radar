# AI Account Editorial Director Skill

`ai-account-editorial-director` 是本项目的全局选题编辑 Skill，用来把 AIHOT、公众号全文、抖音对标内容、对标账号结构和本地候选池，转成更贴近用户人设的选题判断。

它不是采集脚本，也不是自动成稿器。它负责“编辑判断”：这条内容能不能变成我的选题、应该接到哪个真实场景、我能讲什么、重点体现什么、是否值得进入 Brief。

## 全局安装位置

当前已安装到：

```text
/Users/congcong/.codex/skills/ai-account-editorial-director
```

因为它在 `~/.codex/skills/` 下，所以不是项目私有 Skill，可以在其他 Codex 对话和其他项目里调用。

## Skill 文件结构

```text
ai-account-editorial-director/
├── SKILL.md
├── agents/
│   └── openai.yaml
└── references/
    └── persona-and-cases.md
```

- `SKILL.md`：触发条件、工作流、输出字段、禁用表达、质量门槛。
- `references/persona-and-cases.md`：人设、四个方向、案例库、表达风格、选题公式和改写样例。
- `agents/openai.yaml`：用于技能列表展示的名称、描述和默认提示。

## 什么时候用

适合在这些步骤使用：

- `03 内容收件箱` 已经有 AIHOT、公众号全文或抖音对标内容，需要判断是否值得进候选。
- `04 分析与选题` 里候选太泛、AI味重，需要重写成“我的场景拆解 + 我的思考点 + 重点体现”。
- 需要把对标内容吸收成自己的表达，而不是在标题里出现其他博主名字。
- 需要判断一条内容是 `今日最值得做`、`可选候选`、`暂存观察` 还是 `不建议制作`。

## 输出字段

Skill 默认按业务可读字段输出：

- 候选状态
- 推荐等级
- 可发布标题
- 对应方向
- 一句话Brief
- 我的场景拆解
- 我的思考点
- 重点体现
- 可调用案例
- 内容核心冲突
- 视频呈现方式
- 证据强度
- 推荐动作
- 不建议做原因

这些字段可以进入 `04 分析与选题`，也可以先作为本地调试输出。代码仍可保留更多中间字段，但飞书前台应该优先展示这些业务字段。

## 分享和迁移

如果要单独分享这个 Skill，可以复制整个目录：

```bash
cp -R /Users/congcong/.codex/skills/ai-account-editorial-director ./ai-account-editorial-director
```

别人安装时，把目录放到自己的全局 Skills 目录：

```bash
mkdir -p ~/.codex/skills
cp -R ./ai-account-editorial-director ~/.codex/skills/
```

安装后，在新对话里可以显式调用：

```text
Use $ai-account-editorial-director to turn these sources into persona-grounded topic candidates.
```

## 和代码的边界

代码负责：

- 采集 AIHOT、公众号、抖音、RSS、网页；
- 标准化 ContentItem；
- 去重、运行批次、写入飞书；
- 做第一层候选筛选和排序。

Skill 负责：

- 判断这条内容能否接到用户真实业务现场；
- 把热点/对标内容改写成用户自己的语言；
- 调用案例库；
- 降低 AI 味和模板味；
- 输出能直接给人审查的选题字段。

后续如果要把 Skill 串进自动链路，建议新增一个独立编辑层，例如：

```text
ContentItem -> code 初筛 -> ai-account-editorial-director -> 04 分析与选题
```

不要把 Skill 的人设判断继续拆成大量硬编码标题模板。

## 当前接入状态

当前已经新增 `scripts/editorial_skill_runner.py` 作为 Skill 主编层执行脚本。它默认调用本机已登录的 Codex CLI，在只读模式下读取全局 Skill 和案例库参考，对 `content_sampler.py` 输出的候选重新做一轮批量主编判断，并补齐飞书前台可读字段：

- `一句话Brief`
- `我的场景拆解`
- `我的思考点`
- `重点体现`
- `可调用案例`
- `内容核心冲突`
- `视频呈现方式`
- `证据强度`
- `Skill编辑层`

这样 `daily_pipeline.py` 的实际链路已经变为：

```text
采集 -> 标准化 -> 代码初筛 -> Skill字段契约主编层 -> 04 分析与选题
```

`--engine deterministic` 只作为显式离线应急选项，不是默认生产路径。后续如果要换成 OpenAI API 或其他模型服务，也应保持输入输出字段不变。
