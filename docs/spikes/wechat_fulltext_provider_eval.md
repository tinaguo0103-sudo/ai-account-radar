# 微信公众号全文源本地验证 POC

## 测试目标

验证是否能用免费自建方案稳定拿到微信公众号全文，并把已跑通的本地 `wewe-rss` 作为 P1 显式全文 provider 接入现有管道。默认 `daily_pipeline.py` 不拉取本地 provider；只有显式参数启用时才进入 `03 内容收件箱` 与 `04 今日Top10` 候选。

测试账号：`数字生命卡兹克`

## 2026-06-16 结论更新

`we-mp-rss` 已降级，不再作为 P1 主路线。原因是它需要公众号平台扫码授权，不适合当前“专用微信小号订阅内容”的使用约束，也不建议绑定用户已有公众号主体。

后续主路线调整为：

1. `Wechat2RSS` 公共 feed 继续作为发现源。
2. `wewe-rss` 作为普通微信 / 微信读书侧全文源优先验证。
3. 如果 `wewe-rss` 后续不稳定，则保留“公共 feed 发现 + 02 URL 投喂补全文”的低风险方案。
4. `we-mp-rss` 不再阻塞后续推进。

## 用户依赖

- Docker Desktop：已安装，但当前 Docker 拉取镜像被本机 credential store / Keychain 异常阻塞。
- 专用微信小号：需要用户在本机 Web UI 扫码授权，不应把二维码、cookie、token、账号密码发到聊天或提交到仓库。
- 微信读书登录：仅 `wewe-rss` 需要。
- 低频策略：只测试 `数字生命卡兹克`，不要高频刷新，不批量订阅公众号，不批量抓历史。

## 环境检查

| 项目 | 结果 |
| --- | --- |
| Docker | `Docker version 29.1.3` |
| Docker Compose | `Docker Compose version v2.40.3-desktop.1` |
| Python | `Python 3.9.6` |
| Node | `v24.12.0` |
| pnpm | 未安装 |

## we-mp-rss 测试结果

### 文档结论

`we-mp-rss` 曾被列为全文候选：它定位为微信公众号订阅助手，支持 Web 管理界面、扫码授权、添加订阅、RSS 生成、全文配置、导出 Markdown/JSON 等能力。但后续实测确认它需要公众号平台授权，不适合当前“微信小号/微信读书订阅”方案。

最小 Docker 方案：

```bash
docker run -d --name we-mp-rss -p 8001:8001 -v ./data:/app/data ghcr.io/rachelos/we-mp-rss:latest
```

SQLite compose 方案包含：

```yaml
ports:
  - "8001:8001"
environment:
  - DB=sqlite:///data/we_mp_rss.db
  - USERNAME=admin
  - PASSWORD=admin@123
volumes:
  - ./data:/app/data
```

关键能力：

- Web 管理界面：`http://127.0.0.1:8001`
- 登录：默认可用 `admin / admin@123`，之后需要微信扫码授权。
- 输出：`/rss`、`/feed/{feed_id}.xml`、`/feed/{feed_id}.json` 等接口。
- 全文：配置项 `RSS_FULL_CONTEXT=True`，并有 `GATHER.CONTENT=True`、`GATHER.CONTENT_MODE=web` 等采集相关配置。
- API：RSS 接口可在本地读取；管理 API 需要登录或 Access Key。

### 本机实际尝试

1. Docker 运行失败：

```text
docker: error getting credentials - err: exit status 1, out: `Keychain Error. (-67674)`
```

这个错误在 `ghcr.io/rachelos/we-mp-rss:latest` 和 Docker Hub `rachelos/we-mp-rss:latest` 均出现，说明是本机 Docker Desktop credential store 异常，不是项目配置或镜像单点问题。

2. 源码运行尝试：

- 已 clone 到 `.external/we-mp-rss`，该目录被 `.gitignore` 忽略。
- 本机 Python 是 3.9.6，项目文档要求 Python 3.13.1+。
- 依赖安装第一次阻塞在 `psycopg2-binary==2.9.10`，缺少 `pg_config`。
- SQLite POC 过滤掉 `psycopg2-binary` 后继续安装，又阻塞在 `redis==7.2.1`，该版本不支持当前 Python 3.9 环境。

### 是否成功启动

未成功启动。

### 是否能订阅数字生命卡兹克

未能实际验证。需要先解决 Docker credential store，启动 Web UI 后由用户在本机扫码授权，再只添加 `数字生命卡兹克`。

### 是否能输出全文

未能实际验证。文档和接口显示具备全文输出能力，但本机未完成启动和扫码，因此本轮不能把它标成 `auto_ready`。

### 2026-06-16 复验结论

`we-mp-rss` Docker 镜像已能启动，服务地址为 `http://127.0.0.1:8001/`。但其“扫码授权”需要公众号平台授权，不是普通微信小号或微信读书登录。由于用户个人主体已经注册过公众号，且不希望绑定已有公众号主体，本方案降级为 `blocked_not_recommended_for_current_use`。

后续不再把 `we-mp-rss` 作为当前 P1 主路线。

## wewe-rss 测试结果

### 文档结论

`wewe-rss` 支持微信公众号订阅、历史文章、RSS/Atom/JSON、`mode=fulltext` / `FEED_MODE=fulltext`。但它基于微信读书登录，增加了一个额外账号依赖。

SQLite Docker 方案：

```bash
docker run -d \
  --name wewe-rss \
  -p 4000:4000 \
  -e DATABASE_TYPE=sqlite \
  -e AUTH_CODE=<local-admin-code> \
  -v $(pwd)/data:/app/data \
  cooderl/wewe-rss-sqlite:latest
```

本地访问：`http://127.0.0.1:4000`

关键接口：

- `/feeds/all.atom`
- `/feeds/<feed_id>.atom?limit=5&mode=fulltext`
- `/feeds/<feed_id>.json?limit=5&mode=fulltext`

### 2026-06-16 本机实际尝试

Docker credential store 修复后，`wewe-rss` 使用 SQLite Docker 镜像成功启动：

```bash
docker run -d \
  --name ai-radar-wewe-rss \
  -p 4000:4000 \
  -e DATABASE_TYPE=sqlite \
  -e AUTH_CODE=<local-admin-code> \
  -e FEED_MODE=fulltext \
  -e SERVER_ORIGIN_URL=http://127.0.0.1:4000 \
  -v .local_services/wewe-rss/data:/app/data \
  cooderl/wewe-rss-sqlite:latest
```

本地访问地址：

```text
http://127.0.0.1:4000/dash
```

用户在本机浏览器完成微信读书 / 微信小号扫码登录，并添加 `数字生命卡兹克` 后，服务返回：

```json
[{"id":"MP_WXS_3223096120","name":"数字生命卡兹克","intro":"希望能激发你对AI的好奇。"}]
```

### 是否成功启动

已成功启动。

### 是否能订阅数字生命卡兹克

已成功订阅。日志显示 `getMpArticles(MP_WXS_3223096120) page: 1 articles: 48`，并创建了最近 48 篇文章索引。

### 是否能输出全文

已验证可输出全文。接口：

```text
http://127.0.0.1:4000/feeds/all.json?limit=5&mode=fulltext
```

返回 JSON Feed，字段包含：

- `title`
- `url`
- `date_modified`
- `author.name`
- `content_html`

本地 probe 结果：

| 标题 | 正文长度 | 是否全文 |
| --- | ---: | --- |
| 2026年的毕业生们，正在花钱向AI证明自己是人类。 | 5558 | 是 |
| Prompt该退环境了，未来属于Loop Engineering。 | 7046 | 是 |
| 实测GLM-5.2，国产Coding模型的又一座新高峰。 | 4471 | 是 |
| 让5个AI文明自己活15天，Claude建成了乌托邦，Grok四天团灭。 | 5817 | 是 |
| 从0到1带你速通WorkBuddy，这可能是最适合国内的Agent产品。 | 7617 | 是 |

### 当前建议

`needs_user_dependency -> usable_p1_provider`

原因：需要用户本机扫码登录微信读书 / 微信小号，并维护低频本地服务；但一旦登录和订阅完成，能够稳定输出卡兹克最近文章全文，适合作为 P1 全文 provider 接入现有 `--fetch-wechat-feed` 链路。

## 是否拿到卡兹克最近文章全文

已通过 `wewe-rss` 拿到。

当前已知：

- Wechat2RSS 公共 feed 能发现卡兹克文章列表，但只能提供很短摘要。
- 单篇公众号 URL 解析在某些链接上可以拿全文，但 feed 中的 `mp.weixin.qq.com/s?__biz=...` 链接本轮返回页面中缺少 `js_content`，无法稳定全文。
- `we-mp-rss` 因公众号平台授权限制降级。
- `wewe-rss` 已验证可作为普通微信 / 微信读书侧全文源。

## 风险边界

1. 不使用主微信号。
2. 不高频刷新。
3. 不批量订阅公众号。
4. 不批量抓历史文章。
5. 不保存二维码截图、cookie、token、账号密码到仓库。
6. 外部服务、数据库、缓存、登录态只允许放在 `.external/` 或 `.local_services/` 等 `.gitignore` 覆盖目录。
7. 本地 provider 只能作为 P1/P2 低频补全文源，不应直接变成默认高频采集。

## 最终建议

| 方案 | 结论 | 原因 |
| --- | --- | --- |
| we-mp-rss | `blocked_not_recommended_for_current_use` | 已可启动，但需要公众号平台扫码授权；不适合当前微信小号订阅方案，也不建议绑定已有公众号主体。 |
| wewe-rss | `usable_p1_provider` | 已用 Docker 启动，用户完成微信读书 / 微信小号扫码后成功订阅数字生命卡兹克，并输出最近文章全文 JSON Feed。 |
| Wechat2RSS 公共 feed | `discovery_only` | 可发现卡兹克文章列表，但不是稳定全文源。 |

## 下一步接入方式

建议路线：

1. `Wechat2RSS` 公共 feed 继续负责发现卡兹克文章列表。
2. `wewe-rss` 本地服务负责补全文。
3. 用 `scripts/wechat_fulltext_provider_probe.py` 验证输出能转成 ContentItem。
4. 若至少连续 2-3 天稳定，再把全文 provider 接到现有 `--fetch-wechat-feed` 链路，作为“公共 feed 发现 + 本地 provider 补全文”的 P1 增强。
5. 如果 wewe-rss 登录态失效或 provider 不稳定，降级回“公共 feed 发现 + 02 URL 投喂补全文”。

示例验证命令：

```bash
python3 scripts/wechat_fulltext_provider_probe.py --config config/wechat_fulltext_provider.example.yaml --provider-id wewe-rss --limit 5 --dry-run
```

该脚本只读本地 provider 输出，不写飞书。显式接入现有管道时使用：

```bash
python3 scripts/daily_pipeline.py --fetch-wechat-fulltext-provider --wechat-fulltext-provider wewe-rss --wechat-feed-limit 5
python3 scripts/daily_pipeline.py --fetch-wechat-fulltext-provider --wechat-fulltext-provider wewe-rss --wechat-feed-limit 5 --write-feishu
```

也可以和 Wechat2RSS 发现源一起运行：

```bash
python3 scripts/daily_pipeline.py --fetch-wechat-feed --wechat-fulltext-provider wewe-rss --wechat-feed-limit 5
```

默认 `python3 scripts/daily_pipeline.py` 不调用 `wewe-rss`。
