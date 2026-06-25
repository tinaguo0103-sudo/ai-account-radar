# 自动拉取路线收口

本文件用于约束 `ai-account-radar` 的自动拉取边界。当前项目默认链路仍然是：

`AIHOT / 官方源 / URL投喂` -> `03 内容收件箱` -> 内容拆解 -> `04 分析与选题 / 今日挑选卡片`

`04 分析与选题` 的前台语义是“工作流实验命题卡挑选台”，不是标题榜。采集源只负责提供素材；主编 Skill 会把素材转成短 `选题命题 / 我要做的实验 / 热点触发点 / 我的工作流痛点 / 旧流程痛点 / AI介入点 / 验证方式 / 可沉淀资产`，再进入 `今日挑选卡片` 供人工判断。`可发布标题` 只有在 `title_permission=可发布标题` 时才生成。

采集源不负责替用户做主编判断。进入 `04` 之后，`选题命题` 必须短而自然，`验证方式` 必须能照着做，`可沉淀资产` 必须具体到该选题；如果只能写成通用 `Workflow SOP / 字段规则 / Brief 模板 / QA 清单`，说明还没有变成可用的工作流实验卡。

主对标抖音账号的主页标题/文案采样已经进入 `main` 的默认 `daily_pipeline.py`，但它有同日采集闸门：每天最多实际访问一次抖音主页；同一天后续运行默认复用 `output/source_collection_cache/YYYY-MM-DD/` 的缓存结果。它只做低频只读采样：抓主页最近作品标题/文案和单条视频 metadata，不下载视频、不抓评论、不做自动转写；单账号失败会重试，重试仍失败就记录原因并继续下一个账号。

测试规则：修改选题、Skill、飞书字段写入时，不重新采集抖音；只有改采集逻辑、主页链接、登录状态或明确复验采集时，才使用 `--force-fetch-douyin`。

## P0 默认自动拉取

### AIHOT精选

- 当前已接入。
- 适合默认进入 `daily_pipeline.py`。
- 写入 `03 内容收件箱`，再参与 `04 分析与选题 / 今日挑选卡片`。

### AIHOT日报

- 当前已修复 API。
- 适合默认进入 `daily_pipeline.py`。
- 与 AIHOT精选需要继续做去重和同主题合并，避免热点重复占位。

### 官方 RSS / Atom

- 适合默认自动拉取。
- 用于 OpenAI、Anthropic、GitHub、产品官方博客、工具更新等公开结构化来源。
- 适合写入 `03 内容收件箱`，再进入今日候选池。

### 官方网页 / 普通网页 / Jina Reader

- 适合默认自动拉取或作为 URL 解析能力。
- 适合官方博客、产品文档、工具发布页、公开网页。
- 如果网页解析失败，要记录失败原因，不阻塞主流程。

### 单条 URL 投喂

- 继续作为 P0 能力保留。
- 用户在 `02 URL投喂入口` 粘贴公众号文章、抖音单条视频、普通网页等。
- 系统解析后写入 `03 内容收件箱`，再参与 `04 分析与选题 / 今日挑选卡片`。

## P0.5 测试/复盘增强

### `--include-resolved-url-intake`

- 已解析 URL 可复用参与本轮候选测试。
- 不重复写入 `03 内容收件箱`。
- 只更新最近参与运行批次、最近采样日期等运行追踪字段。
- 用于规则测试、候选池调试、对比 AIHOT + URL 混合候选效果。
- 不作为默认日常流程。

### `--fetch-wechat-feed`

- 卡兹克公众号公共 feed 已降级为发现源说明，不再进入日常候选池。
- feed URL：`https://wechat2rss.xlab.app/feed/7b1c10c25bdfe69d0a08a5349cf3b032e55f4f05.xml`。
- 默认 `daily_pipeline.py` 不拉取该 feed。
- 主动运行 `python3 scripts/daily_pipeline.py --fetch-wechat-feed --wechat-feed-limit 5` 时，脚本只输出 no-op 提醒，不会把公共 feed 摘要写入 `03` 或送入 `04`。
- 原因：公共 feed 能发现文章列表，但不能稳定提供全文；摘要会让选题继续有“资讯味”和“猜测感”。
- 如果要处理卡兹克公众号文章，使用本地 `wewe-rss` 全文 provider，或把单篇公众号文章 URL 放入 `02 URL投喂入口`。

### `--fetch-wechat-fulltext-provider` / `--wechat-fulltext-provider wewe-rss`

- Wechat2RSS 公共 feed 适合发现卡兹克文章列表，但本轮验证中不能稳定提供全文。
- `wewe-rss` 已验证可作为本地全文 provider：本机服务 `http://127.0.0.1:4000`，JSON 全文接口 `/feeds/all.json?limit=5&mode=fulltext`。
- `we-mp-rss` 已从当前主路线降级：它需要公众号平台扫码授权，不适合当前微信小号/微信读书订阅方案，也不建议绑定用户已有公众号主体。
- 本地 POC 见 `docs/spikes/wechat_fulltext_provider_eval.md`。
- 当前结论是 `usable_p1_provider`：需要用户在本机维护低频 `wewe-rss` 服务，并用微信读书/微信小号扫码登录；不保存 cookie、token、二维码或数据库到仓库。
- `scripts/wechat_fulltext_provider_probe.py` 已作为显式 provider adapter 使用：读取本地 `wewe-rss` 输出，转成标准 ContentItem；默认 `daily_pipeline.py` 不调用。
- 显式 dry-run：`python3 scripts/daily_pipeline.py --fetch-wechat-fulltext-provider --wechat-fulltext-provider wewe-rss --wechat-feed-limit 5`。
- 显式写入：`python3 scripts/daily_pipeline.py --fetch-wechat-fulltext-provider --wechat-fulltext-provider wewe-rss --wechat-feed-limit 5 --write-feishu`。
- 如需全文候选，只运行全文源：`python3 scripts/daily_pipeline.py --fetch-wechat-fulltext-provider --wechat-fulltext-provider wewe-rss --wechat-feed-limit 5`。公共 feed 不再和全文源混合进候选池。

## P1 单独 PoC / source_watch_probe

这类来源不允许直接进入 `main` 的默认 `daily_pipeline.py`，除非已经像抖音主页标题/文案采样一样完成低频验证、失败降级和不阻塞策略。新平台、新深度能力仍必须单独开分支做 PoC。

### 公众号自动发现最新文章

- 重点源：数字生命卡兹克-公众号文章。
- 单篇公众号 URL 解析已经可用；Wechat2RSS feed 只作为发现源说明，不再作为候选来源。
- 可研究方向：以 `wewe-rss` 作为低频全文 provider；其他公众号 RSS/订阅服务只作为备选。
- 这类方案通常需要单独部署、维护、登录态或服务配置，不能直接接入默认流程。
- 当前 P1 只允许显式拉取，不进入默认流程；正式默认化前需要连续稳定性、去重和字段完整性验证。
- 如果要拿全文，当前优先使用本地 `wewe-rss`；公共 Wechat2RSS feed 只作为发现源，不作为全文源。

### 抖音主页自动发现最近 N 条

- 涉及账号：数字生命卡兹克-抖音教程视频、秋芝2046、xuan酱、ami.moment、Bob同学、数字游牧人、编导李让、何止维、徐老师AI。
- 当前单条抖音视频 URL P0 浅层解析可用。
- 账号主页最近 N 条自动发现未稳定验证。
- 可能卡点：JS 壳、登录态、验证码、反爬、接口变动、浏览器回退、下载成本。
- 已盘点开源路线：`Agent-Reach` 更适合作为工具清单而不是正式依赖；当前 `_ROUTER_DATA` 单条解析比本轮测试的 `douyin-mcp-server` 更适合 metadata；`MediaCrawler` 是账号主页最近 N 条的 P1 主候选；`Douyin_TikTok_Download_API` 能力全但需要自部署和 Cookie/风控配置，暂作备选。详见 `docs/spikes/douyin_open_source_tool_eval.md`。
- 当前已有轻量探针：`scripts/douyin_source_watch_probe.py`。它读取当前主对标池里的抖音主页，尝试从公开页面发现作品 ID；如果发现作品 ID，就复用单条视频 resolver 输出本地 ContentItem。它不写飞书、不保存登录态、不下载视频、不抓评论。
- 2026-06-16 复验结果：秋芝2046、xuan酱公开页面可访问但更像 JS 壳，未解析出作品 ID；ami.moment 缺主页链接。结论是无登录公开主页不足以稳定发现最近 N 条。
- 当前已有 Chrome CDP 探针：`scripts/douyin_cdp_source_watch_probe.mjs`。它只连接本机远程调试 Chrome，不导出 profile，不保存 cookie/token。2026-06-16 更新：使用专用 Chrome profile 和抖音小号登录后，秋芝2046、xuan酱、数字游牧人、数字生命卡兹克-抖音教程视频等账号已能低频拿到可信主页作品，并复用单条 resolver 生成本地 ContentItem。它现在默认进入 `daily_pipeline.py`，正式 `--write-feishu` 时和其他来源一样写入 `03 内容收件箱` 并参与 `04 分析与选题`；普通失败只记录，不阻塞其他来源。同一天重复运行不会再次访问抖音主页，除非显式传 `--force-fetch-douyin`。
- 为避免干扰用户日常 Chrome，使用 `scripts/start_douyin_cdp_chrome.py --port 9333` 以默认 `hidden` 模式后台启动或复用专用 Chrome。采样时 CDP 探针使用后台 target 打开主页，并尽量最小化专用 Chrome，避免每个博主页采样时反复弹窗。若某个账号明确需要登录/验证码，daily pipeline 会前台打开专用 Chrome 到该账号主页，等待用户处理后只重试这些账号；无人值守可用 `--douyin-verification-action log-only` 只记录不弹窗。`--headless` 仅作为实验模式保留，不作为抖音登录态采样首选。
- 口播转写不默认执行。先用 `scripts/douyin_transcript_candidates.py` 根据标题/文案/时长筛选候选；确认值得转写后，再显式调用 `scripts/douyin_video_transcribe.py --raw-payload <raw.json> --model paraformer-v2 --confirm-free-quota --yes`。长视频默认被成本护栏拦截。
- 已转写内容可通过 `scripts/douyin_video_transcribe.py --raw-payload <raw.json> --transcript-file <transcript.md>` 包装成标准 ContentItem，再用 `python3 scripts/daily_pipeline.py --include-douyin-transcripts` 显式进入候选池。这个流程不重复消耗 ASR 额度，不默认写飞书。
- 对标视频只提供话题、结构和交付逻辑，不作为可复制表达。用户可见标题不得出现其他博主名字，也不要写成“这条视频/这条内容”的模仿式标题。
- 抖音主页标题/文案采样已进入 `main` 默认流程；抖音口播转写、画面 OCR、评论区问题和更深的视频理解仍然只允许显式 P1，不进入默认流程。

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
7. 不污染 `04 分析与选题`。
8. 可以被关闭或降级。

## 当前主对标池逐个判断

### 数字生命卡兹克-公众号文章

- 平台：微信公众号。
- 当前能力：单篇 URL 解析可用，全文解析可用。
- 自动发现：Wechat2RSS feed 已验证可读，但已降级为发现源说明，不进入候选池。
- 全文 provider：本地 `wewe-rss` 已验证可输出最近文章全文，并已接入显式参数。
- 推荐：需要全文时显式运行 `--fetch-wechat-fulltext-provider --wechat-fulltext-provider wewe-rss`，或用 `02 URL投喂入口` 放单篇文章 URL。公共 feed 不再进入候选池，避免摘要污染判断。
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

这些 PoC 不允许直接在 `main` 上开发。PoC 默认只输出本地报告，不写飞书、不进候选池、不改飞书表结构。

## 分支治理建议

`spike/agent-reach-content-ingestion` 的主要结论已经迁移到 `main` 的正式 URL 解析能力和本文件。该分支可列为关闭候选；如需删除远端分支，应先由用户确认。
