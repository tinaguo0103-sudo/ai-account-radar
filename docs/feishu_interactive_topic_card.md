# 飞书交互式选题速选卡 v0.2

目标：保留 `04 分析与选题` 作为事实源，但不再让用户在多维表格卡片视图里逐条点开阅读。每天把最新一批候选压成一张飞书交互卡片，用户在卡片内一次完成选择，提交后回写 `04`；如果选中了题，系统再发第二张“制作方向补充卡”，让用户逐条补案例和写法方向。

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
- `serve-long-connection`：启动飞书官方 SDK 长连接，接收 `card.action.trigger` 并复用 `apply` 的回写逻辑。只作为开发调试兜底，不作为日常生产路径。
- `serve-callback`：最小 HTTP 回调接收服务，作为本地 HTTP Webhook 调试入口。

日常生产路径是：本机或自动化只负责 `send` 第一张选题卡。用户点击后的 `card.action.trigger` 由云函数 receiver 接收并回写 `04`；如果有选中记录，receiver 会继续发送第二张制作方向卡。第二张卡提交后，receiver 把每条补充写回 `04 / 我的制作补充`。云函数代码在 `cloud_functions/feishu-card-receiver/`。

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

规则：用户选中的记录写为 `进入Brief`；同一张卡片中未选中的记录写为 `不做`。这样用户只需要做“选中值得推进的内容”这一个动作，不需要分别判断暂存和不做。

如果第一张卡片选中了至少一条记录，云函数会继续发第二张卡片。第二张卡片只做一件事：给每个已选题补一句制作方向，例如“用 AI账号信息雷达案例讲，重点讲选题判断，不要讲成工具教程”。提交后写回：

- `我的制作补充`

这条补充后续优先级高于系统自动匹配的私有案例；如果留空，后续生成脚本时再由全局私有 Skill 按案例库建议。

回调层会记录提交指纹，同一张卡片的完全相同提交只处理一次。用户重复点击同一个提交按钮时，后端会提示“这次提交已经处理过”，不会重复写表。若用户改了选择或原因后再次提交，会按新的提交处理。

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
  --form-value-json '{"enter_brief_records":["recxxxx"],"positive_reason_tags":["有真实业务现场"]}'
```

默认不会写飞书，只会输出将要更新的字段。确认后再加：

```bash
python3 scripts/feishu_topic_decision_card.py apply \
  --form-value-json '{"enter_brief_records":["recxxxx"],"positive_reason_tags":["有真实业务现场"]}' \
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

它只发送卡片，不启动本机 receiver，也不等待点击。用户在飞书里提交选择后，云函数 receiver 会自动回写 `04`。

## 接收卡片提交

推荐使用云函数 receiver，而不是本机长连接。当前生产路径需要飞书开放平台把 `card.action.trigger` HTTP 回调地址配置到云函数 URL。

用户在第一张卡片里点击“提交选择”或“本批都不选”后，云函数 receiver 会把这些字段写回 `04 分析与选题`：

- `状态`
- `选择原因标签`
- `学习状态 = 待学习`
- `人工一句话判断`

用户在第二张制作方向卡里点击“保存制作方向”后，云函数 receiver 会写回：

- `我的制作补充`

健康检查不写表，只验证两件事：云函数能响应飞书 URL 校验，当前本地飞书凭证能读到 `04`。

```bash
.venv/bin/python scripts/check_feishu_card_cloud_receiver.py --url <云函数URL>
```

如果把云函数 URL 写入本地 `.env.local` 的 `FEISHU_CARD_RECEIVER_URL`，可直接运行：

```bash
.venv/bin/python scripts/check_feishu_card_cloud_receiver.py
```

本机长连接仍可用于开发兜底：

```bash
.venv/bin/python scripts/feishu_topic_decision_card.py serve-long-connection --write
```

## 飞书后台配置

要让飞书卡片里的提交按钮真实回写，需要在飞书开发者后台完成一次应用配置：

- 开启机器人能力。
- 开通 `获取与发送单聊、群组消息 / im:message`。
- 在 `事件与回调 / 回调配置` 中把订阅方式设为 HTTP 回调，并填写云函数 HTTPS URL。
- 订阅新版卡片回调 `card.action.trigger`，不需要订阅旧版 `card.action.trigger_v1`。
- 不启用事件加密，除非云函数已补 Encrypt Key 解密逻辑。
- 创建并发布应用版本。
- 让接收用户在机器人可用范围内，或把机器人加入目标群。

这部分不是 `04/05` 表格结构问题，而是飞书应用机器人能力问题。云函数 receiver 配好后，飞书卡片才能把一次勾选的多条结果回写到 `04`。
