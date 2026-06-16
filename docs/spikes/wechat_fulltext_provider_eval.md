# 微信公众号全文源本地验证 POC

## 测试目标

验证是否能用免费自建方案稳定拿到微信公众号全文，优先测试 `we-mp-rss`，备选测试 `wewe-rss`。本轮不改默认 `daily_pipeline.py`，不写飞书，不进入 `03 内容收件箱` 或 `04 今日Top10`。

测试账号：`数字生命卡兹克`

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

`we-mp-rss` 是更贴合本项目需求的优先路线：它定位为微信公众号订阅助手，支持 Web 管理界面、扫码授权、添加订阅、RSS 生成、全文配置、导出 Markdown/JSON 等能力。

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

### 当前建议

`needs_user_dependency`

原因：路线正确，但依赖用户修复 Docker Desktop credential store，并用微信小号扫码授权；全文能力需在服务启动后通过实际 RSS/API 返回验证。

## wewe-rss 测试结果

### 文档结论

`wewe-rss` 支持微信公众号订阅、历史文章、RSS/Atom/JSON、`mode=fulltext` / `FEED_MODE=fulltext`。但它基于微信读书登录，增加了一个额外账号依赖。

SQLite Docker 方案：

```bash
docker run -d \
  --name wewe-rss \
  -p 4000:4000 \
  -e DATABASE_TYPE=sqlite \
  -e AUTH_CODE=123567 \
  -v $(pwd)/data:/app/data \
  cooderl/wewe-rss-sqlite:latest
```

本地访问：`http://127.0.0.1:4000`

关键接口：

- `/feeds/all.atom`
- `/feeds/<feed_id>.atom?limit=5&mode=fulltext`
- `/feeds/<feed_id>.json?limit=5&mode=fulltext`

### 本机实际尝试

Docker 运行同样失败：

```text
docker: error getting credentials - err: exit status 1, out: `Keychain Error. (-67674)`
```

本机 Node 可用，但 pnpm 未安装。可通过 corepack 后续启用 pnpm，但这仍不能替代微信读书扫码登录和订阅验证。

### 是否成功启动

未成功启动。

### 是否能订阅数字生命卡兹克

未能实际验证。需要启动服务、登录微信读书账号，并通过公众号分享链接添加订阅。

### 是否能输出全文

未能实际验证。文档支持 `mode=fulltext`，但本轮没有实际服务返回。

### 当前建议

`needs_user_dependency`

原因：可作为备选，但依赖微信读书登录态和 Docker / Node 环境；比 `we-mp-rss` 多一层账号依赖。

## 是否拿到卡兹克最近文章全文

未拿到。

当前已知：

- Wechat2RSS 公共 feed 能发现卡兹克文章列表，但只能提供很短摘要。
- 单篇公众号 URL 解析在某些链接上可以拿全文，但 feed 中的 `mp.weixin.qq.com/s?__biz=...` 链接本轮返回页面中缺少 `js_content`，无法稳定全文。
- `we-mp-rss / wewe-rss` 的全文能力需要本地服务启动 + 用户扫码授权后才能验证。

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
| we-mp-rss | `needs_user_dependency` | 最符合公众号全文采集，但本机 Docker credential store 阻塞，源码要求 Python 3.13+；需用户扫码授权后复验全文。 |
| wewe-rss | `needs_user_dependency` | 支持 fulltext，但依赖微信读书登录态；Docker 同样被 Keychain 阻塞。 |
| Wechat2RSS 公共 feed | `discovery_only` | 可发现卡兹克文章列表，但不是稳定全文源。 |

## 下一步接入方式

如果用户修复 Docker Desktop credential store，并完成本机扫码授权：

1. 启动 `we-mp-rss`。
2. 只订阅 `数字生命卡兹克`。
3. 打开本地 feed/API，确认返回最近文章全文。
4. 用 `scripts/wechat_fulltext_provider_probe.py` 验证输出能转成 ContentItem。
5. 若至少连续 2-3 天稳定，再把全文 provider 接到现有 `--fetch-wechat-feed` 链路，作为“公共 feed 发现 + 本地 provider 补全文”的 P1 增强。

示例验证命令：

```bash
python3 scripts/wechat_fulltext_provider_probe.py --config config/wechat_fulltext_provider.example.yaml --dry-run
```

该脚本只读本地 provider 输出，不写飞书，不进 `03`，不进 `04`。
