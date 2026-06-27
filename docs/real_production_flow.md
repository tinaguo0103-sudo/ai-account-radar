# AI账号真实生产流程

这份文档是当前内容生产链路的唯一阅读入口。后续改流程时，先改这里，再同步 README、`docs/system_map.md`、Skill 和脚本。

## 当前主链路

```text
AIHOT / 公众号全文 / 抖音主页标题文案 / URL投喂
-> 03 内容收件箱
-> 04 分析与选题
-> 交互式选题卡：选择要推进的选题
-> 制作方向补充卡：补真实案例、讲法、边界和不要讲的内容
-> 本机 launchd 定时任务：调用 Codex 生成完整脚本与制作包
-> 06 完整脚本与制作包
-> 本地 full_script_execution_package.md
-> 人工拍摄、剪辑、发布
-> 07 资产与复盘
```

## 表边界

- `04 分析与选题`：负责判断这条内容为什么值得做、切入点是什么、验证方式是什么、能沉淀什么资产。
- `06 完整脚本与制作包`：保存脚本包记录、摘要和本地文档路径；完整正文统一看本地 `full_script_execution_package.md`，不塞进飞书长字段。
- `07 资产与复盘`：发布后沉淀复盘、可复刻角度和下一轮选题规则。

## 生成脚本包

正式无人值守生成：

- macOS `launchd` 定时运行 `scripts/codex_script_package_runner.py`。
- runner 先扫描 `04`，只有发现待生成记录时才调用本机 `codex exec`。
- 只处理状态为 `进入Brief` / `本周做`，且 `是否已生成脚本稿 != 是` 的记录。
- 生成成功后创建 `06 完整脚本与制作包`，并把原 `04` 记录标记为 `是否已生成脚本稿 = 是`。
- 完整 Markdown 写入本地 `output/script_execution_packages/YYYY-MM-DD/.../full_script_execution_package.md`；飞书 `06` 只保存摘要、路径、素材提醒、发布前核验和 QA。

立即补跑：

```bash
python3 scripts/codex_script_package_runner.py --write-feishu --limit 2 --max-age-days 5
```

只检查队列、不调用 Codex：

```bash
python3 scripts/codex_script_package_runner.py --skip-codex --limit 2 --max-age-days 5
```

本机批量补跑/对比：

```bash
python3 scripts/content_ops_pipeline.py --write-feishu
```

单条补跑：

```bash
python3 scripts/generate_script_execution_package.py --record-id <04_record_id> --write-feishu
```

本机生成后会发生三件事：

- 本地生成 `output/script_execution_packages/YYYY-MM-DD/.../full_script_execution_package.md`。
- 飞书 `06 完整脚本与制作包` 新增一条轻量记录。
- 飞书 `04 分析与选题` 的 `是否已生成脚本稿` 标记为 `是`，避免重复生成。

## 自动化边界

- 腾讯云卡片 receiver 只负责接收第一张选题卡和第二张制作方向补充卡，并写回 `04`。
- 本机 launchd 只负责定时运行 `codex_script_package_runner.py`；真正写作由本机已登录的 Codex CLI 完成。
- 锁屏但不睡眠、不断网时可以运行；睡眠、关机、断网时不会运行，恢复后等下一次定时触发或手动补跑。
- 本轮不做任务拆分、自动剪辑或自动发布；这些能力以后单独设计，不再恢复 `05` 中间层。

## QA 语义

- `pass`：脚本包成立，可进入拍摄准备；素材提醒和发布前核验仍然需要人工处理。
- `revise`：脚本包本身缺关键判断、关键证据或结构不成立，需要修订。
- `blocked`：`04` 必填字段缺失，无法可靠生成脚本包。

普通的截图、录屏、发布前回看原文属于提醒，不应自动把可用脚本包降级为 `revise`。
