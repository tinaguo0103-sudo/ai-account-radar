# 自动拉取路线收口

本文件用于约束 `ai-account-radar` 的自动拉取边界。当前项目默认链路仍然是：

`AIHOT / 官方源 / URL投喂` -> `03 内容收件箱` -> 内容拆解 -> `04 今日Top10`

主对标账号自动抓取仍处于 P1 probe 阶段，不直接进入 `main` 的默认 `daily_pipeline.py`。

## P0 默认自动拉取

### AIHOT精选

- 当前已接入。
- 适合默认进入 `daily_pipeline.py`。
- 写入 `03 内容收件箱`，再参与 `04 今日Top10`。

### AIHOT日报

- 当前已修复 API。
- 适合默认进入 `daily_pipeline.py`。
- 与 AIHOT精选需要继续做去重和同主题合并，避免热点重复占位。

### 官方 RSS / Atom

- 适合默认自动拉取。
- 用于 OpenAI、Anthropic、GitHub、产品官方博客、工具更新等公开结构化来源。
- 适合写入 `03 内容收件箱`，再进入 Top10 候选。

### 官方网页 / 普通网页 / Jina Reader

- 适合默认自动拉取或作为 URL 解析能力。
- 适合官方博客、产品文档、工具发布页、公开网页。
- 如果网页解析失败，要记录失败原因，不阻塞主流程。

### 单条 URL 投喂

- 继续作为 P0 能力保留。
- 用户在 `02 URL投喂入口` 粘贴公众号文章、抖音单条视频、普通网页等。
- 系统解析后写入 `03 内容收件箱`，再参与 `04 今日Top10`。

## P0.5 测试/复盘增强

### `--include-resolved-url-intake`

- 已解析 URL 可复用参与本轮候选测试。
- 不重复写入 `03 内容收件箱`。
- 只更新最近参与运行批次、最近采样日期等运行追踪字段。
- 用于规则测试、Top10 调试、对比 AIHOT + URL 混合候选效果。
- 不作为默认日常流程。

### `--fetch-wechat-feed`

- 卡兹克公众号 feed 已从候选验证进入 P1 显式接入验证。
- feed URL：`https://wechat2rss.xlab.app/feed/7b1c10c25bdfe69d0a08a5349cf3b032e55f4f05.xml`。
- 默认 `daily_pipeline.py` 不拉取该 feed。
- 只有用户主动运行 `python3 scripts/daily_pipeline.py --fetch-wechat-feed --wechat-feed-limit 5` 时，feed 文章才进入本轮 ContentItem 候选池。
- 显式加 `--write-feishu` 后，feed 内容写入 `03 内容收件箱`，再参与 `04 今日Top10` 候选。
- 如果 feed 失效或全文解析受限，继续回退到 `02 URL投喂入口` 单篇公众号文章 URL。

## P1 单独 PoC / source_watch_probe

这类来源不允许直接进入 `main` 的默认 `daily_pipeline.py`。必须单独开分支做 PoC。

### 公众号自动发现最新文章

- 重点源：数字生命卡兹克-公众号文章。
- 单篇公众号 URL 解析已经可用；Wechat2RSS feed 已做成 P1 显式接入能力。
- 可研究方向：`we-mp-rss`、`wewe-rss`、其他公众号 RSS/订阅服务。
- 这类方案通常需要单独部署、维护、登录态或服务配置，不能直接接入默认流程。
- 当前 P1 只允许显式拉取，不进入默认流程；正式默认化前需要连续稳定性、去重和字段完整性验证。

### 抖音主页自动发现最近 N 条

- 涉及账号：数字生命卡兹克-抖音教程视频、秋芝2046、xuan酱、ami.moment、Bob同学、数字游牧人、编导李让、何止维、徐老师AI。
- 当前单条抖音视频 URL P0 浅层解析可用。
- 账号主页最近 N 条自动发现未稳定验证。
- 可能卡点：JS 壳、登录态、验证码、反爬、接口变动、浏览器回退、下载成本。
- 可研究方向：当前 `_ROUTER_DATA` 单条解析、Douyin_TikTok_Download_API、douyin-downloader、douyin-mcp-server、Agent-Reach 相关能力。
- P1 只允许单独分支 probe，不进入 `main` 默认流程。

### 评论区问题抓取

- 暂不进入 P0。
- 可能需要登录态、接口签名、风控绕行。
- 暂缓。

### 小红书 / X / Reddit

- 当前不做默认抓取。
- 小红书和 X 需要登录态或风控处理。
- Reddit 已遇到 403 或接口限制，暂缓。

## P2 正式接入主对标自动采集

只有 P1 `source_watch_probe` 连续稳定通过的来源，才允许进入正式采集。正式接入时必须满足：

1. 能稳定发现最近内容。
2. 能拿到标题、链接、发布时间、作者/账号。
3. 能写入 `03 内容收件箱`。
4. 有内容指纹去重。
5. 有失败原因记录。
6. 不需要人工登录态。
7. 不污染 `04 今日Top10`。
8. 可以被关闭或降级。

## 当前主对标池逐个判断

### 数字生命卡兹克-公众号文章

- 平台：微信公众号。
- 当前能力：单篇 URL 解析可用，全文解析可用。
- 自动发现：Wechat2RSS feed 已验证可读，并已接入显式参数。
- 推荐：继续用 `--fetch-wechat-feed` 做 P1 观察；稳定后再讨论是否默认化。
- 不进入默认 `daily_pipeline.py`。

### 数字生命卡兹克-抖音教程视频

- 平台：抖音。
- 当前能力：单条视频 URL 浅层解析可用。
- 自动发现：未验证。
- 推荐：P1 抖音主页 probe。
- 不进入默认 `daily_pipeline.py`。

### 秋芝2046、xuan酱、ami.moment、Bob同学、数字游牧人、编导李让、何止维、徐老师AI

- 平台：抖音。
- 当前能力：如果用户提供单条视频 URL，可走 P0 浅层解析。
- 自动发现主页最近 N 条：未验证。
- 推荐：P1 统一做抖音主页 `source_watch_probe`。
- 不进入默认 `daily_pipeline.py`。

### AI项目复盘-待定

- 当前没有目标账号。
- 不做自动拉取。

## 未来 PoC 分支命名

- 公众号自动发现 PoC：`spike/source-watch-wechat-rss`
- 抖音主页最近 N 条 PoC：`spike/source-watch-douyin-homepage`
- 抖音 ASR / 口播转写 PoC：`spike/douyin-asr-transcript`

这些 PoC 不允许直接在 `main` 上开发。PoC 默认只输出本地报告，不写飞书、不进 Top10、不改飞书表结构。

## 分支治理建议

`spike/agent-reach-content-ingestion` 的主要结论已经迁移到 `main` 的正式 URL 解析能力和本文件。该分支可列为关闭候选；如需删除远端分支，应先由用户确认。
