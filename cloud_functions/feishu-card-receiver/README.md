# Feishu Card Receiver 腾讯云 SCF

这个目录只包含 `04 分析与选题` 交互式卡片的腾讯云 SCF receiver。

它接收飞书开放平台的 `card.action.trigger` 事件，把用户在卡片里勾选的选题回写到 `04 分析与选题`，并在有选中记录时继续发送“制作方向补充卡”，替代本机常驻 `serve-long-connection`。

`06 完整脚本与制作包` 不在腾讯云生成。当前生产路径是本机 launchd 定时运行 `scripts/codex_script_package_runner.py`，由本机 `codex exec` 和全局私有 Skill 生成完整 Markdown，再写入飞书 `06` 的轻量记录。

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

卡片点击回写生产只走腾讯云 SCF。仓库不再保留其他云平台入口，避免部署路径分叉。

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
- 生产脚本包生成走本机 launchd + `codex_script_package_runner.py`，因为这一步需要 Codex 和全局私有 Skill。
- 本机 `content_ops_pipeline.py --write-feishu` 只作为确定性补跑、对比和调试；高质量自动生成优先走 `codex_script_package_runner.py`。

## 后续可增强

- 增加 Encrypt Key 解密。
- 增加独立回调日志表。
- 提交成功后自动触发选择学习。
- 为第二张制作方向卡增加真正异步发送队列，减少飞书前端提交等待。
