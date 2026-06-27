# AI账号真实生产流程

这份文档是当前内容生产链路的唯一阅读入口。后续改流程时，先改这里，再同步 README、`docs/system_map.md`、Skill 和脚本。

## 当前主链路

```text
AIHOT / 公众号全文 / 抖音主页标题文案 / URL投喂
-> 03 内容收件箱
-> 04 分析与选题
-> 交互式选题卡：选择要推进的选题
-> 制作方向补充卡：补真实案例、讲法、边界和不要讲的内容
-> content_ops_pipeline.py --write-feishu
-> 本地 full_script_execution_package.md
-> 06 完整脚本与制作包
-> 人工拍摄、剪辑、发布
-> 07 资产与复盘
```

## 表边界

- `04 分析与选题`：负责判断这条内容为什么值得做、切入点是什么、验证方式是什么、能沉淀什么资产。
- `06 完整脚本与制作包`：保存脚本包轻量记录，完整口播稿和制作执行方案看本地 Markdown。
- `07 资产与复盘`：发布后沉淀复盘、可复刻角度和下一轮选题规则。

## 生成脚本包

批量生成：

```bash
python3 scripts/content_ops_pipeline.py --write-feishu
```

单条补跑：

```bash
python3 scripts/generate_script_execution_package.py --record-id <04_record_id> --write-feishu
```

生成后会发生三件事：

- 本地生成 `output/script_execution_packages/YYYY-MM-DD/.../full_script_execution_package.md`。
- 飞书 `06 完整脚本与制作包` 新增一条轻量记录。
- 飞书 `04 分析与选题` 的 `是否已生成脚本稿` 标记为 `是`，避免重复生成。

## 自动化边界

- 腾讯云卡片 receiver 只负责接收第一张选题卡和第二张制作方向补充卡，并写回 `04`。
- 脚本包生成仍由本地 `content_ops_pipeline.py` 执行，因为它依赖全局私有 Skill 和本地 Markdown 输出。
- 本轮不做任务拆分、自动剪辑或自动发布；这些能力以后单独设计，不再恢复 `05` 中间层。

## QA 语义

- `pass`：脚本包成立，可进入拍摄准备；素材提醒和发布前核验仍然需要人工处理。
- `revise`：脚本包本身缺关键判断、关键证据或结构不成立，需要修订。
- `blocked`：`04` 必填字段缺失，无法可靠生成脚本包。

普通的截图、录屏、发布前回看原文属于提醒，不应自动把可用脚本包降级为 `revise`。
