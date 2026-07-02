# AI账号真实生产流程

这份文档是当前内容生产链路的唯一阅读入口。后续改流程时，先改这里，再同步 README、`docs/system_map.md`、Skill 和脚本。

## 当前主链路

```text
AIHOT / 公众号全文 / 抖音主页标题文案 / URL投喂
-> 03 内容收件箱
-> 04 分析与选题
-> 交互式选题卡：选择要推进的选题
-> 腾讯云定时发送制作方向补充卡：补真实案例、讲法、边界和不要讲的内容
-> 本机轻量 watcher：有待生成记录时调用 Codex 生成完整脚本与制作包
-> 06 完整脚本与制作包
-> 本地平铺 Markdown 脚本包
-> 人工拍摄、剪辑、发布
-> 07 资产与复盘
```

## 表边界

- `04 分析与选题`：负责判断这条内容为什么值得做、切入点是什么、验证方式是什么、能沉淀什么资产。
- `06 完整脚本与制作包`：保存脚本包记录、摘要和文档入口；完整正文优先看 `飞书文档` 链接，本地平铺 Markdown 是备份和后续自动化输入，不塞进飞书长字段。`文档同步状态 / 文档同步错误` 用来提醒飞书文档或用户可见文件夹是否异常。
- `07 资产与复盘`：发布后沉淀复盘、可复刻角度和下一轮选题规则。

## 当前自动化状态

- 已自动化：第一张选题卡由腾讯云 SCF receiver 接收并写回 `04`，选中记录会写入 `制作方向卡状态=待发送`；腾讯云定时触发器每 5 分钟只扫描最近 5 天内的显式待发送队列并发送第二张制作方向补充卡；第二张卡提交后继续由 receiver 写回 `04 / 我的制作补充`，并把 `制作方向卡状态` 改为 `已提交`。本机轻量 watcher 扫描近 5 天待生成记录，空队列不调用 Codex，有待生成记录时才调用 Codex 生成 `06`。
- 仍需单独触发或另设定时：运行 `daily_pipeline.py` 生成当天 `03/04` 候选，以及运行 `run_topic_decision_card_session.py` 发送第一张选题卡。
- 如果要做到真正每天无人值守，下一步应给“生成候选”和“发送选题卡”也安装本机定时任务；否则当前链路从“卡片已发出、用户已选择”之后是自动的。

## 生成脚本包

正式生成：

- 本机轻量 watcher 运行 `scripts/watch_script_package_queue.py`。
- watcher 调用 runner 先扫描 `04`，只有发现待生成记录时才调用本机 `codex exec`。
- 只处理人工状态为 `生成脚本包`、`制作方向卡状态=已提交`（或历史记录已填写 `我的制作补充`），且 `是否已生成脚本稿` 为空、`否` 或 `未生成` 的记录；旧状态不再触发脚本包生成，也不再作为 fallback。
- 第一轮 QA 为 `revise` 时，runner 会把上一轮 QA 原因、开头钩子和核心观点传回 Codex 自动重写 1 次。
- 最终 `pass` 后创建 `06 完整脚本与制作包`，并把原 `04` 记录标记为 `是否已生成脚本稿 = 是`。
- 第二轮仍 `revise` 或直接 `blocked` 时，也会创建 `06` 异常记录，但把原 `04` 标记为 `是否已生成脚本稿 = 需人工处理`，避免 watcher 重复消耗；人工补充后清空该字段即可重跑。
- 完整 Markdown 从项目文档库根目录的 `06 完整脚本与制作包/YYYY-MM-DD_选题标题_完整脚本与制作包.md` 打开；后台真实写入 runtime 并通过软链接暴露到项目根目录。若配置 `FEISHU_SCRIPT_PACKAGE_VISIBLE_FOLDER_TOKEN`，runner 会优先用用户可见飞书文件夹创建文档并写入 `06 / 飞书文档`；若只配置旧的 `FEISHU_SCRIPT_PACKAGE_FOLDER_TOKEN`，它只能作为应用空间兼容路径，06 会在 `文档同步状态` 提醒“非正常用户文件夹入口”。

立即补跑：

```bash
python3 scripts/codex_script_package_runner.py --write-feishu --limit 2 --max-age-days 5
```

只检查队列、不调用 Codex：

```bash
python3 scripts/codex_script_package_runner.py --skip-codex --limit 2 --max-age-days 5
```

指定单条立即生成：

```bash
python3 scripts/codex_script_package_runner.py --write-feishu --record-id <04_record_id>
```

本机生成后会发生三件事：

- 本地生成 `06 完整脚本与制作包/YYYY-MM-DD_选题标题_完整脚本与制作包.md`。
- 飞书文档优先生成到用户可见的 `AI账号信息雷达 / 06完整脚本与制作包` 子文件夹，并在飞书 `06 完整脚本与制作包` 新增一条轻量记录；如果当前 token 无权写入用户可见文件夹，06 会保留本地文档并写出同步报警。
- 如果显式配置了 `FEISHU_SCRIPT_PACKAGE_FEEDBACK_RECEIVE_TARGETS`，runner 会把本轮所有 `06` 交付文档汇总成一张飞书完成卡。卡片里只展示飞书文档链接，不展示本地路径；如果某条没有飞书文档 URL，就不进入完成卡。卡片可直接提交质量反馈，回写 `06 / 人工质量反馈、质量问题标签、人工修改意见、反馈时间、反馈来源、内容学习状态`。测试时必须使用测试表、测试文件夹和个人接收目标，不复用正式群目标。
- 飞书 `04 分析与选题` 的 `是否已生成脚本稿` 标记为 `是` 或 `需人工处理`，避免重复生成。

用户可见飞书文件夹配置：

- `FEISHU_SCRIPT_PACKAGE_VISIBLE_FOLDER_TOKEN`：用户在普通飞书云盘里能看到的 `06完整脚本与制作包` 子文件夹 token。
- `FEISHU_SCRIPT_PACKAGE_VISIBLE_FOLDER_URL`：同一个子文件夹的浏览器链接，写入 06 的 `飞书文件夹`。
- `FEISHU_SCRIPT_PACKAGE_FEEDBACK_RECEIVE_TARGETS`：06 完成反馈卡接收目标，格式如 `open_id:xxx` 或 `chat_id:xxx`，多个用英文逗号分隔。预合并/测试环境应指向个人，不要指向正式群。
- `FEISHU_SCRIPT_PACKAGE_USER_ACCESS_TOKEN` / `FEISHU_USER_ACCESS_TOKEN`：写用户可见云盘文件夹时需要。tenant/app token 已验证会被飞书返回 `1770040 no folder permission`，即使应用 API 权限已经开通，也不能代表用户写入普通云盘文件夹。
- 旧 `FEISHU_SCRIPT_PACKAGE_FOLDER_TOKEN` 只作为应用空间兼容路径，不视为用户可见正常文件夹入口。

本机授权用户身份：

```bash
python3 scripts/feishu_user_oauth.py --timeout-seconds 240
```

如果需要由脚本自动创建测试文件夹，飞书开发者后台还必须开通并发布 `drive:drive` 和 `space:folder:create`，否则 OAuth 页面会显示错误码 `20027`。

授权成功后脚本会把用户 access token 和 refresh token 写入 `.env.local`。这份文件只保存在本机，不提交 Git。首次授权前，开发者后台必须开通 `offline_access`（持续访问已授权的数据）；如果没有开通，飞书授权页会提示错误码 `20027`，脚本也不会保存只有 2 小时有效期的短期 token。

生产运行有两份本地环境文件：生产仓库 `.env.local` 和 `~/.codex/ai-account-radar-runtime/.env.local`。二者通过 runtime 里的 `RUNTIME_SOURCE.txt` 绑定，`feishu_user_oauth.py` 和 `codex_script_package_runner.py` 刷新用户 token 时会同步写回两边；`install_script_package_watcher_launch_agent.py --sync-runtime-only` 同步 runtime 前会先比较两边 token 的过期时间，保留更新的一份，避免旧 refresh token 覆盖新 token。若飞书返回 `invalid_grant`、refresh token revoked 或类似授权错误，runner 会继续保留本地 Markdown 和 06 记录，并尝试发送“飞书文档同步授权失效”通知。

## 自动化边界

- 腾讯云卡片 receiver 负责两件事：接收第一张/第二张卡片回调并写回 `04`；通过名为 `send-production-direction-cards` 的 5 分钟定时触发器，把 `制作方向卡状态=待发送` 的选中记录发成第二张补充卡。发送前状态为 `发送中`，成功后为 `已发送`，第二张卡提交后为 `已提交`，失败会写入 `制作方向卡错误`。
- 本机轻量 watcher 只负责按需运行 `codex_script_package_runner.py`；真正写作由本机已登录的 Codex CLI 和全局私有 Skill 完成。
- 锁屏但不睡眠、不断网时可以运行；睡眠唤醒后通常会继续运行；重启后登录系统会自动拉起。关机、睡眠、断网期间不会生成，但腾讯云已写回的选择会留在 `04`，恢复后补生成。
- 本轮不做任务拆分、自动剪辑或自动发布；这些能力以后单独设计，不再恢复 `05` 中间层。

## QA 语义

- `pass`：脚本包成立，可进入拍摄准备；素材提醒和发布前核验仍然需要人工处理。
- `revise`：脚本包本身缺关键判断、关键证据或结构不成立；系统会自动重跑 1 次，第二轮仍不通过才进入人工处理。
- `blocked`：`04` 必填字段缺失，无法可靠生成脚本包。

普通的截图、录屏、发布前回看原文属于提醒，不应自动把可用脚本包降级为 `revise`。
