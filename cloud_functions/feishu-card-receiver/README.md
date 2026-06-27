# Feishu Card Receiver 腾讯云 SCF

这个目录包含两类腾讯云 SCF 函数：

1. `feishu-card-receiver`：`04 分析与选题` 交互式卡片的云端 receiver。
2. `script-package-runner`：每天定时扫描 `04`，把已确认选题生成 `06 完整脚本与制作包`。

它接收飞书开放平台的 `card.action.trigger` 事件，把用户在卡片里勾选的选题回写到 `04 分析与选题`，并在有选中记录时继续发送“制作方向补充卡”，替代本机常驻 `serve-long-connection`。

## 卡片 receiver 做什么

- 响应飞书事件订阅 URL 校验 `challenge`。
- 如果配置了 `FEISHU_VERIFICATION_TOKEN`，则校验飞书回调 token。
- 解析卡片里的 `event.action.form_value`。
- 支持三个动作：
  - `submit_topic_decisions`：选中的记录写为 `进入Brief`，未选中写为 `不做`。
  - `submit_no_selection`：本批全部写为 `不做`。
  - `submit_production_directions`：把第二张卡片里的逐条制作方向/真实案例/讲法建议写回 `我的制作补充`。
- 回写字段：
  - `状态`
  - `学习状态 = 待学习`
  - `选择原因标签`
  - `人工一句话判断`
  - `我的制作补充`
- 通过“读当前字段再比较”的方式避免重复提交重复写入。

## 环境变量

不要把真实值提交到 Git。

必填：

```bash
FEISHU_APP_ID=cli_xxx
FEISHU_APP_SECRET=xxx
FEISHU_BASE_APP_TOKEN=xxx
```

可选：

```bash
FEISHU_TOPIC_TABLE_ID=tbl_xxx
FEISHU_API_BASE_URL=https://open.feishu.cn
FEISHU_VERIFICATION_TOKEN=xxx
FEISHU_CARD_RECEIVE_TARGETS=open_id:ou_xxx,chat_id:oc_xxx
FEISHU_PRODUCTION_DIRECTION_RECEIVE_TARGETS=chat_id:oc_xxx
SEND_PRODUCTION_DIRECTION_CARD=true
DEFER_PRODUCTION_DIRECTION_CARD=true
FEISHU_CARD_EXPIRE_DAYS=5
DRY_RUN=true
```

说明：

- 如果不填 `FEISHU_TOPIC_TABLE_ID`，receiver 会自动在 Base 里找 `04 分析与选题`，兼容旧名 `03 分析与选题`。
- 如果飞书开放平台配置了 Verification Token，腾讯云 SCF 环境变量也要填同一个 `FEISHU_VERIFICATION_TOKEN`；如果平台未配置，则可以不填。
- `FEISHU_CARD_RECEIVE_TARGETS` 是第一张选题卡和第二张制作方向卡的默认接收目标。
- `FEISHU_PRODUCTION_DIRECTION_RECEIVE_TARGETS` 可选；如果设置，第二张卡只发到这里。
- `SEND_PRODUCTION_DIRECTION_CARD=false` 可临时关闭第二张卡。
- `DEFER_PRODUCTION_DIRECTION_CARD=true` 会把第二张卡发送任务从主逻辑中拆出；但腾讯云 SCF Node.js 运行时仍可能等待未完成的异步 HTTP 任务，因此它不是严格的异步队列。
- `FEISHU_CARD_EXPIRE_DAYS` 默认是 5。新生成的选题卡和制作方向卡都会携带发卡时间和过期时间，超过有效期提交会直接拦截。
- 本地测试可以设置 `DRY_RUN=true`，云端生产不要设置。
- 当前版本不支持加密回调。如果飞书开放平台事件订阅启用了 Encrypt Key，需要先关闭事件加密，或后续补解密逻辑。

`script-package-runner` 额外支持：

```bash
FEISHU_SCRIPT_PACKAGE_TABLE_ID=tbl_xxx
AUSTIN_SCRIPT_PACKAGE_LIMIT=3
AUSTIN_SCRIPT_PACKAGE_DRY_RUN=false
```

说明：

- `FEISHU_SCRIPT_PACKAGE_TABLE_ID` 建议填写 `06 完整脚本与制作包` 的 table_id，减少定时函数每次查表。
- `AUSTIN_SCRIPT_PACKAGE_LIMIT` 默认 3，表示每次最多生成 3 条，避免一次积压太多时超时。
- `AUSTIN_SCRIPT_PACKAGE_DRY_RUN=true` 只扫描不写表，联调时使用；生产定时任务必须为 `false` 或不设置。
- 云端没有本地文件系统作为阅读入口，第一版会把完整 Markdown 写入 `06` 字段 `完整脚本与执行包`，`本地文档` 字段写明“腾讯云SCF生成”。后续可接腾讯 COS 或飞书云文档。

## 卡片提交保护

- 云函数不是常驻监听进程；每次用户点击卡片，飞书才调用一次腾讯云 SCF。
- 第一张选题卡只允许处理一次。receiver 写入前会读取候选记录当前 `状态`，只有 `待判断` 或空状态可被处理；如果旧卡再次提交，已变成 `进入Brief` 或 `不做` 的记录会触发拦截。
- 第二张制作方向卡只允许保存一次。卡片里的“真实案例 / 讲法方向 / 不要讲什么”是建议字段，不必填写；留空时不写 `我的制作补充`，后续脚本生成会按私有案例库建议。receiver 写入前会读取 `我的制作补充`，已有内容时不允许旧卡覆盖。
- 新卡默认 5 天过期。过期卡提交时返回提醒，不写表、不发第二张卡。

## 回调耗时结论

2026-06-26 实测：飞书前端出现“提交错误”但 receiver 已收到，主要不是写表失败，而是卡片回调链路耗时过长。

- 新版选题卡会把候选快照写入按钮 value，receiver 可跳过读取整张 `04` 表。
- receiver 会缓存 `tenant_access_token` 和 `04` 表 table_id；云端建议配置 `FEISHU_TOPIC_TABLE_ID`。
- 纯回写路径约 2.5 秒，warm 状态约 1.9 秒。
- “回写后继续发送第二张制作方向卡”路径约 4 秒以上；腾讯云 SCF 会等待后台发卡 HTTP 任务，因此 `DEFER_PRODUCTION_DIRECTION_CARD` 不能完全消除这段等待。
- 曾验证过“第一张卡提交后，回调直接返回第二张卡内容，让飞书原消息原地更新”的方案。裸 HTTP 回调在飞书 Web 端会卡在提交中，虽然 04 状态已经写回，但客户端不完成提交，因此该路径已从生产代码移除。
- 同日实测普通两步路径可用：第一张卡选择后写回 `04`，第二张“补充制作方向”卡作为新消息出现；第二张卡提交后可写回 `我的制作补充`。

当前推荐继续保留两张卡，不合并。若后续仍看到飞书前端报“提交错误”，下一步应把第二张卡发送拆成真正独立的异步链路，而不是继续在同一个回调里压榨耗时：

- 方案 A：第一张卡只负责选择和回写，第二张制作方向卡由独立云函数、定时扫描任务或云队列发送。
- 方案 B：第一张卡提交后只写状态和一个“待补制作方向”的标记；另一个云端任务扫描这个标记并发送第二张卡。

## 本地测试

```bash
cd ai_account_radar/cloud_functions/feishu-card-receiver
npm test
```

本地启动 HTTP receiver：

```bash
cd ai_account_radar/cloud_functions/feishu-card-receiver
PORT=8787 npm start
```

模拟 URL 校验：

```bash
curl -sS http://127.0.0.1:8787 \
  -H 'content-type: application/json' \
  -d '{"challenge":"hello"}'
```

预期：

```json
{"challenge":"hello"}
```

## 生产部署边界

生产只走腾讯云 SCF。卡片 receiver 使用「事件函数 + 函数 URL」，脚本包 runner 使用「事件函数 + 定时触发器」。仓库不再保留其他云平台入口，避免部署路径分叉。

这个包的核心文件是 `src/receiver.js`，腾讯云专用入口是 `tencent-scf/index.js`。本地 `npm start` 只用于开发调试，不作为生产 receiver。

## 腾讯云 SCF 部署：卡片 Receiver

当前生产路径是腾讯云 SCF「事件函数 + 函数 URL」。不要新建 API 网关触发器。

本目录已提供腾讯云专用入口：

```text
tencent-scf/index.js
```

它导出腾讯云 Node.js 事件函数入口 `index.main_handler`，会把函数 URL 请求里的 `event.body` 解析为飞书回调 payload，并返回标准的 `statusCode/headers/body`。

打包：

```bash
cd ai_account_radar/cloud_functions/feishu-card-receiver
mkdir -p dist
zip -j dist/tencent-scf-feishu-card-receiver.zip tencent-scf/index.js
```

本地最小验证：

```bash
cp tencent-scf/index.js /tmp/tencent-scf-index.js
node -e "const h=require('/tmp/tencent-scf-index.js'); h.main_handler({httpMethod:'POST',body:JSON.stringify({challenge:'hello'})}).then(r=>console.log(JSON.stringify(r)))"
```

预期返回：

```json
{"statusCode":200,"headers":{"content-type":"application/json; charset=utf-8"},"body":"{\"challenge\":\"hello\"}"}
```

控制台创建建议：

- 函数类型：事件函数。
- 运行环境：Node.js 20 或 Node.js 18。
- 提交方法：本地上传 zip 包。
- 执行方法：`index.main_handler`。
- 公网访问：创建后启用「函数 URL」。
- 鉴权：飞书开放平台 URL 校验阶段需要能直接访问，先使用免鉴权 URL；如需回调 token 校验，在飞书开放平台和腾讯云 SCF 环境变量里同时配置 `FEISHU_VERIFICATION_TOKEN`。

环境变量同上，生产至少填写：

```bash
FEISHU_APP_ID=cli_xxx
FEISHU_APP_SECRET=xxx
FEISHU_BASE_APP_TOKEN=xxx
```

如果飞书开放平台配置了 Verification Token，再填写：

```bash
FEISHU_VERIFICATION_TOKEN=xxx
```

说明：`dist/` 是本地部署包输出目录，已被 `.gitignore` 忽略。

## 腾讯云 SCF 部署：06 定时生成 Runner

这个函数不需要函数 URL，也不接飞书开放平台事件。它由腾讯云「定时触发器」每天唤起。

打包：

```bash
cd ai_account_radar/cloud_functions/feishu-card-receiver
npm run package:tencent-scf:script-runner
```

上传包：

```text
dist/tencent-scf-script-package-runner.zip
```

控制台创建建议：

- 函数类型：事件函数。
- 运行环境：Node.js 20 或 Node.js 18。
- 提交方法：本地上传 zip 包。
- 执行方法：`index.main_handler`。
- 触发器：定时触发器，例如每天 09:30 或每天 21:30。
- 函数 URL：不需要开启。
- 超时时间：建议先设 60 秒。
- 内存：建议先设 256MB 或 512MB。

生产环境变量至少填写：

```bash
FEISHU_APP_ID=cli_xxx
FEISHU_APP_SECRET=xxx
FEISHU_BASE_APP_TOKEN=xxx
FEISHU_TOPIC_TABLE_ID=tbl_xxx
FEISHU_SCRIPT_PACKAGE_TABLE_ID=tbl_xxx
AUSTIN_SCRIPT_PACKAGE_LIMIT=3
AUSTIN_SCRIPT_PACKAGE_DRY_RUN=false
```

本地最小验证：

```bash
cd ai_account_radar/cloud_functions/feishu-card-receiver
npm test
npm run package:tencent-scf:script-runner
```

上传前 dry-run 建议：

```bash
AUSTIN_SCRIPT_PACKAGE_DRY_RUN=true \
FEISHU_APP_ID=cli_xxx \
FEISHU_APP_SECRET=xxx \
FEISHU_BASE_APP_TOKEN=xxx \
node -e "const h=require('./src/script_package_runner.cjs'); h.runScriptPackageJob({}, process.env).then(r=>console.log(JSON.stringify(r,null,2)))"
```

正式行为：

- 扫描 `04 分析与选题`。
- 只处理状态为 `进入Brief / 本周做`，且 `是否已生成脚本稿 != 是` 的记录。
- 为每条创建 `06 完整脚本与制作包` 记录。
- 完整 Markdown 写入 `完整脚本与执行包`。
- 创建成功后，把 `04` 的 `是否已生成脚本稿` 标记为 `是`。

幂等边界：

- 只有成功写入 06 后才标记 04。
- 重复触发时，已标记 `是否已生成脚本稿 = 是` 的记录不会再次生成。
- 如果中途失败，未标记的记录会在下一次定时触发时重试。

## 飞书开放平台配置

1. 打开当前应用的飞书开放平台后台。
2. 进入「事件与回调」。
3. 事件订阅方式选择 HTTP 回调。
4. 回调地址填写腾讯云 SCF 函数 URL。
5. 订阅新版卡片事件 `card.action.trigger`。
6. 不要启用事件加密，除非已经补了 Encrypt Key 解密逻辑。
7. 发布应用版本。

## 日常健康检查

本地健康检查不会写表，只验证腾讯云 SCF 函数 URL 校验和飞书 `04` 表读取权限：

```bash
.venv/bin/python scripts/check_feishu_card_cloud_receiver.py --url <腾讯云SCF函数URL>
```

如果在本地 `.env.local` 写入：

```bash
FEISHU_TENCENT_SCF_URL=https://你的腾讯云SCF函数URL
```

可直接运行：

```bash
.venv/bin/python scripts/check_feishu_card_cloud_receiver.py
```

## 和本机 receiver / 本机脚本的关系

- 本机 `serve-long-connection --write` 只作为开发调试兜底。
- 生产点击回写走腾讯云 SCF。
- 生产脚本包生成走腾讯云定时 runner。
- 本机 `content_ops_pipeline.py --write-feishu` 只作为补跑、对比和私有 Skill 高质量版本调试，不作为无人值守生产定时入口。

## 后续可增强

- 增加 Encrypt Key 解密。
- 增加独立回调日志表。
- 提交成功后自动触发选择学习。
- 把 `06` 的完整 Markdown 从字段迁移到腾讯 COS 或飞书云文档。
- 为云端 runner 增加私有风格/案例配置注入，减少云端保底版和本机私有 Skill 版本之间的表达差异。
