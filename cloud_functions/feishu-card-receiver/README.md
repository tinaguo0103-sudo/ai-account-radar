# Feishu Card Receiver 云函数

这个目录是 `04 分析与选题` 交互式卡片的云端 receiver。

它接收飞书开放平台的 `card.action.trigger` 事件，把用户在卡片里勾选的选题回写到 `04 分析与选题`，替代本机常驻 `serve-long-connection`。

## 做什么

- 响应飞书事件订阅 URL 校验 `challenge`。
- 如果配置了 `FEISHU_VERIFICATION_TOKEN`，则校验飞书回调 token。
- 解析卡片里的 `event.action.form_value`。
- 支持两个动作：
  - `submit_topic_decisions`：选中的记录写为 `进入Brief`，未选中写为 `不做`。
  - `submit_no_selection`：本批全部写为 `不做`。
- 回写字段：
  - `状态`
  - `学习状态 = 待学习`
  - `选择原因标签`
  - `人工一句话判断`
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
DRY_RUN=true
```

说明：

- 如果不填 `FEISHU_TOPIC_TABLE_ID`，receiver 会自动在 Base 里找 `04 分析与选题`，兼容旧名 `03 分析与选题`。
- 如果飞书开放平台配置了 Verification Token，云函数也要填同一个 `FEISHU_VERIFICATION_TOKEN`；如果平台未配置，则可以不填。
- 本地测试可以设置 `DRY_RUN=true`，云端生产不要设置。
- 当前版本不支持加密回调。如果飞书开放平台事件订阅启用了 Encrypt Key，需要先关闭事件加密，或后续补解密逻辑。

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

## 部署方式

这个包的核心文件是 `src/receiver.js`，导出了标准的 `fetch(request, env)`，适合部署到 Cloudflare Workers、Vercel Edge Function 或其他支持 Web Fetch API 的云函数平台。

如果选择腾讯云函数、阿里云函数、火山引擎函数等 Node HTTP 运行时，需要做一层薄适配：把平台的 request/body 转成标准 `Request`，再调用 `handleRequest(request, env)`。

## 腾讯云 SCF 部署

当前推荐生产路径是腾讯云 SCF「事件函数 + 函数 URL」。不要新建 API 网关触发器。

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
- 鉴权：飞书开放平台 URL 校验阶段需要能直接访问，先使用免鉴权 URL；如需回调 token 校验，在飞书开放平台和云函数环境变量里同时配置 `FEISHU_VERIFICATION_TOKEN`。

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

## Vercel 部署

当前目录已经包含 Vercel 入口：

```text
api/feishu-card-receiver.js
vercel.json
```

部署根目录请选择：

```text
ai_account_radar/cloud_functions/feishu-card-receiver
```

推荐用 Vercel CLI：

```bash
cd ai_account_radar/cloud_functions/feishu-card-receiver
npx vercel login
npx vercel
```

首次部署时按提示选择：

- Set up and deploy? `Y`
- Which scope? 选择你的账号或团队
- Link to existing project? 通常选 `N`
- Project name? 可用 `feishu-card-receiver`
- Directory? 直接回车，使用当前目录

部署成功后，在 Vercel 项目里添加环境变量：

```bash
npx vercel env add FEISHU_APP_ID production
npx vercel env add FEISHU_APP_SECRET production
npx vercel env add FEISHU_BASE_APP_TOKEN production
```

可选：

```bash
npx vercel env add FEISHU_TOPIC_TABLE_ID production
npx vercel env add FEISHU_API_BASE_URL production
npx vercel env add FEISHU_VERIFICATION_TOKEN production
```

环境变量加完后重新部署生产环境：

```bash
npx vercel --prod
```

最终回调地址使用：

```text
https://你的项目域名.vercel.app/api/feishu-card-receiver
```

如果使用根路径也可以，因为 `vercel.json` 已经把 `/` rewrite 到 API：

```text
https://你的项目域名.vercel.app/
```

## 飞书开放平台配置

1. 打开当前应用的飞书开放平台后台。
2. 进入「事件与回调」。
3. 事件订阅方式选择 HTTP 回调。
4. 回调地址填写云函数 HTTPS URL。
5. 订阅新版卡片事件 `card.action.trigger`。
6. 不要启用事件加密，除非已经补了 Encrypt Key 解密逻辑。
7. 发布应用版本。

## 日常健康检查

本地健康检查不会写表，只验证云函数 URL 校验和飞书 `04` 表读取权限：

```bash
.venv/bin/python scripts/check_feishu_card_cloud_receiver.py --url <云函数URL>
```

如果在本地 `.env.local` 写入：

```bash
FEISHU_CARD_RECEIVER_URL=https://你的云函数域名
```

可直接运行：

```bash
.venv/bin/python scripts/check_feishu_card_cloud_receiver.py
```

## 和本机 receiver 的关系

- 本机 `serve-long-connection --write` 只作为开发调试兜底。
- 生产点击回写走云函数。
- 每日定时发卡片可以继续由本地脚本、Codex 自动化、服务器 cron 或云平台定时任务触发。

## 后续可增强

- 增加 Encrypt Key 解密。
- 增加独立回调日志表。
- 提交成功后自动触发选择学习。
- 进入Brief后自动触发 05 Brief 生成。
