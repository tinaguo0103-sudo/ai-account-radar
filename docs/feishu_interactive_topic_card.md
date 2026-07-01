# 飞书交互式选题速选卡 v0.2

目标：保留 `04 分析与选题` 作为事实源，但不再让用户在多维表格卡片视图里逐条点开阅读。每天把最新一批候选压成一张飞书交互卡片，用户在卡片内一次完成选择，提交后回写 `04`；如果选中了题，系统再发第二张“制作方向补充卡”，让用户逐条补真实案例、讲法方向和不要讲什么。这个补充是建议字段，不必填写。

## 为什么不用普通多维表格自动化

飞书多维表格自带自动化适合做提醒、字段变化通知、把单条记录字段插入消息；但它不是最适合做“多条候选一次勾选并回写”的决策界面。

官方卡片能力里更贴合这个场景的是应用机器人交互卡片：

- 发送消息接口支持 `interactive` 卡片消息。
- 卡片 2.0 的表单容器支持在前端录入一批表单项后点击一次提交。
- 表单提交通过 `card.action.trigger` 回调把 `form_value` 发给服务端。
- 回调服务再把用户选择写回 `04 分析与选题`。

## 当前实现

脚本：`scripts/feishu_topic_decision_card.py`

它支持六个动作：

- `build`：从 `04` 最新批次读取候选，生成一张卡片 JSON 预览。
- `send --dry-run`：生成卡片但不发送。
- `send`：通过应用机器人发送一张交互卡片。
- `apply`：把卡片提交后的 `form_value` 回写到 `04`，默认 dry-run，只有加 `--write` 才真正写飞书。
- `serve-long-connection`：启动飞书官方 SDK 长连接，接收 `card.action.trigger` 并复用 `apply` 的回写逻辑。只作为历史开发调试入口，不作为日常生产路径。
- `serve-callback`：最小 HTTP 回调接收服务，作为本地 HTTP Webhook 调试入口。

日常生产路径是：本机或自动化只负责 `send` 第一张选题卡。用户点击后的 `card.action.trigger` 由腾讯云 SCF receiver 接收并回写 `04`；如果有选中记录，receiver 只写入显式队列字段，不在同一个回调里发送第二张卡。腾讯云定时触发器 `send-production-direction-cards` 每 5 分钟扫描 `制作方向卡状态=待发送` 的记录并发送第二张制作方向卡。第二张卡提交后，receiver 会把 `制作方向卡状态` 写为 `已提交`；有非空补充时同时写回 `04 / 我的制作补充`，留空则只表示“已确认但不额外补充”。腾讯云 SCF 代码在 `cloud_functions/feishu-card-receiver/`。

卡片不是由云函数常驻监听。飞书保存卡片和回调地址；用户什么时候点击，飞书什么时候请求腾讯云函数。卡片可以在聊天里积攒，但新卡从生成时开始只保留 5 天有效期，超过 5 天提交会被 receiver 拦截。

2026-06-26 回调耗时实测后，补充一个边界：旧方案里的第一张卡“回写选择 + 继续发送第二张制作方向卡”在腾讯云 SCF 上仍可能超过飞书客户端等待窗口，导致前端显示“提交错误”但后端实际已经回写。已经做了三项优化：卡片 value 携带候选快照、receiver 缓存 token/table_id、云端配置 `FEISHU_TOPIC_TABLE_ID`。但腾讯云 SCF 会等待未完成的后台 HTTP 任务，不能把“发送第二张卡”伪装成真正异步。

同日又验证了两个替代路径：

- “回调直接返回第二张卡，让原消息原地更新”：失败。裸 HTTP 回调下飞书 Web 端会卡在提交中，虽然 04 状态已经写回，但客户端不完成提交。因此生产代码不保留这条路径。
- “保留两张卡，第一张卡提交后延后发送第二张卡”：通过。第一条测试记录写为 `生成脚本包`，未选记录写为 `不做`；第二张卡出现后，制作方向可写回 `我的制作补充`。

2026-06-29 已落地异步队列：第一张卡只把选中记录写为 `生成脚本包`，并补充 `制作方向卡状态=待发送 / 选择提交批次 / 选择提交时间`。第二张卡由腾讯云定时触发器每 5 分钟扫描最近 5 天内的显式队列发送，发送成功后写 `制作方向卡状态=已发送` 和 `制作方向卡发送时间`；第二张卡提交后写 `制作方向卡状态=已提交`。旧状态不再兼容，避免历史测试数据误触发。这个设计避免扫描历史记录，也避免第一张卡回调承担发第二张卡的耗时。

## 卡片内的用户操作

一张卡片包含多条候选速览，每条候选显示：

- 标题
- 方向与 AI 味风险
- 一句话 Brief
- 要做的实验
- 可展示证据
- 需要补的证据

卡片底部是一次提交表单：

- 进入脚本与制作：只勾选值得继续写口播稿和制作包的编号。
- 推进原因标签：用结构化标签记录为什么选。
- 手工原因：标签覆盖不了时，写一句真实判断。
- 提交选择：已选进入 `脚本与制作`，未选自动写为 `不做`。
- 本批都不选：无视已选编号，整张卡片的候选都写为 `不做`；如果填写了手工原因，会作为本批不选原因写入。

提交后回写字段：

- `状态`
- `选择原因标签`
- `人工一句话判断`
- `学习状态 = 待学习`
- `制作方向卡状态 = 待发送`（仅已选记录）
- `选择提交批次`（仅已选记录）
- `选择提交时间`（仅已选记录）

规则：用户选中的记录写为 `生成脚本包`；同一张卡片中未选中的记录写为 `不做`。这样用户只需要做“选中值得推进的内容”这一个动作，不需要分别判断暂存和不做。

如果第一张卡片选中了至少一条记录，腾讯云 SCF receiver 会把已选题放入第二张卡发送队列；定时触发器最多 5 分钟内发出第二张卡片。第二张卡片只做一件事：给每个已选题补一句制作方向，例如“用 AI账号信息雷达案例讲，重点讲选题判断，不要讲成工具教程”。提交后写回：

- `我的制作补充`

这条补充后续优先级高于系统自动匹配的私有案例；如果留空，后续生成脚本时再由全局私有 Skill 按案例库建议。

回调层按“一张卡只处理一次”设计：

- 第一张选题卡提交前，receiver 会读取候选记录当前 `状态`。只有仍为 `待判断` 或空状态的候选可以被这张卡处理；一旦这张卡把候选写为 `生成脚本包` 或 `不做`，后续重复提交同一张卡会被拦截。
- 第二张制作方向卡提交前，receiver 会读取候选记录当前 `我的制作补充`。如果已经保存过制作方向，重复提交会被拦截，避免旧卡覆盖新判断。
- 两张卡都会携带 `card_issued_at` 和 `card_expires_at`。默认 5 天后提交无效，前端卡片正文也会显示这个提醒。

## 本地预览

```bash
python3 scripts/feishu_topic_decision_card.py build --limit 7
```

输出：

- `output/decision_cards/YYYY-MM-DD_<run_id>_topic_decision_card.json`
- `output/decision_cards/latest_topic_decision_card.json`

## 模拟回写

```bash
python3 scripts/feishu_topic_decision_card.py apply \
  --form-value-json '{"script_package_records":["recxxxx"],"positive_reason_tags":["有真实业务现场"]}'
```

默认不会写飞书，只会输出将要更新的字段。确认后再加：

```bash
python3 scripts/feishu_topic_decision_card.py apply \
  --form-value-json '{"script_package_records":["recxxxx"],"positive_reason_tags":["有真实业务现场"]}' \
  --write
```

## 发送卡片

需要在本地环境补充接收目标：

```bash
FEISHU_CARD_RECEIVE_TARGETS=open_id:ou_xxx
```

如果要同时发给多个账号或群，用英文逗号分隔：

```bash
FEISHU_CARD_RECEIVE_TARGETS=open_id:ou_xxx,chat_id:oc_xxx
```

第二张制作方向卡默认复用同一批接收目标。如果希望第二张卡发到不同群或不同个人，可以单独设置：

```bash
FEISHU_PRODUCTION_DIRECTION_RECEIVE_TARGETS=chat_id:oc_xxx
```

发送前建议先 dry-run：

```bash
python3 scripts/feishu_topic_decision_card.py send --dry-run --limit 7
```

真正发送：

```bash
python3 scripts/feishu_topic_decision_card.py send --limit 7
```

日常也可以直接用一键会话脚本：

```bash
.venv/bin/python scripts/run_topic_decision_card_session.py --limit 7
```

它只发送卡片，不启动本机 receiver，也不等待点击。用户在飞书里提交选择后，腾讯云 SCF receiver 会自动回写 `04`。

## 接收卡片提交

推荐使用腾讯云 SCF receiver，而不是本机长连接。当前生产路径需要飞书开放平台把 `card.action.trigger` HTTP 回调地址配置到腾讯云 SCF 函数 URL。

用户在第一张卡片里点击“提交选择”或“本批都不选”后，腾讯云 SCF receiver 会把这些字段写回 `04 分析与选题`：

- `状态`
- `选择原因标签`
- `学习状态 = 待学习`
- `人工一句话判断`

用户在第二张制作方向卡里点击“保存制作方向”后，腾讯云 SCF receiver 会写回：

- `我的制作补充`
- `制作方向卡状态 = 已提交`

健康检查不写表，只验证两件事：腾讯云 SCF 函数能响应飞书 URL 校验，当前本地飞书凭证能读到 `04`。

```bash
.venv/bin/python scripts/check_feishu_card_cloud_receiver.py --url <腾讯云SCF函数URL>
```

如果把腾讯云 SCF 函数 URL 写入本地 `.env.local` 的 `FEISHU_TENCENT_SCF_URL`，可直接运行：

```bash
.venv/bin/python scripts/check_feishu_card_cloud_receiver.py
```

本机长连接只保留为历史开发调试入口，日常不使用：

```bash
.venv/bin/python scripts/feishu_topic_decision_card.py serve-long-connection --write
```

## 飞书后台配置

要让飞书卡片里的提交按钮真实回写，需要在飞书开发者后台完成一次应用配置：

- 开启机器人能力。
- 开通 `获取与发送单聊、群组消息 / im:message`。
- 在 `事件与回调 / 回调配置` 中把订阅方式设为 HTTP 回调，并填写腾讯云 SCF 函数 URL。
- 订阅新版卡片回调 `card.action.trigger`，不需要订阅旧版 `card.action.trigger_v1`。
- 不启用事件加密，除非腾讯云 SCF receiver 已补 Encrypt Key 解密逻辑。
- 创建并发布应用版本。
- 让接收用户在机器人可用范围内，或把机器人加入目标群。

这部分不是表格字段结构问题，而是飞书应用机器人能力问题。腾讯云 SCF receiver 配好后，飞书卡片才能把一次勾选的多条结果回写到 `04`。
