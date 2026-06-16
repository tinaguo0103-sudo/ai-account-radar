# ai-account-radar

`ai-account-radar` 是「AI账号信息雷达 + 今日候选池 + 飞书执行台」。

## 实施判断

当前系统不是竞品数据监控，也不是账号互动数据抓取。它的核心是“对标内容拆解器 + AIHOT选题筛选器”：让 AIHOT 热点、对标视频、公众号文章进入系统，拆解它们如何选题、开头、组织结构、证明专业、引导转化，再转成适合你账号定位的今日选题。

当前主路径是飞书 8 张核心业务表 + `99 规则与字典`。CSV/Excel 仍保留为降级输出和排错备份，但不再作为日常主要使用路径。

自动采集只接入公开、低风险来源；抖音、小红书、视频号等平台第一版只做浅层内容采样或手动链接、标题、封面、简介、字幕/OCR 文本导入后分析，不强抓、不下载、不绕过限制。

降级方案：AIHOT API 失败时，脚本不会中断；你可以把 AIHOT 页面、日报或链接内容粘贴进 `data/manual/manual_items.example.jsonl` 再运行。

## 最快理解方式

`00 主控台` 是唯一入口。每天先从这里看系统地图、今日候选池、待办任务和异常提示。

- `04 分析与选题` 决定今天做什么：从今日候选池里选 1 条进入 Brief、本周做、暂存或不做。
- `05 Brief与制作` 决定怎么做成内容：补 Hook、案例、平台结构、封面、CTA 和人工判断，不生成完整成稿。
- `06 内容任务主表` 决定今天做哪些任务：写稿、拍摄、剪辑、封面、发布、直播和复盘提醒都在这里执行。
- `07 资产与复盘` 决定发完后是否复刻和资产化：看 24小时、72小时、7天反馈，判断是否改角度再发、沉淀清单/SOP/案例库。
- `01 来源与采样`、`02 URL投喂入口`、`03 内容收件箱`、`99 规则与字典` 是后台或说明层，不需要每天打开。

更短的系统地图见 `docs/system_map.md`。

## 已理解的账号定位

你不是 AI 新闻号、工具搬运号或提示词教程号。你的账号是“懂营销、懂内容、懂导演、正在做 AI 业务系统的人”。内容要把 AI 从工具、模型和热点，翻译成内容团队、品牌增长和创业项目可执行的流程、资产和结果。

首月目标不是全平台日更，而是沉淀可复用资产：来源配置、选题机制、内容模板、资料包、线索承接和复盘系统。

## 当前主对标账号

最新主对标池以这版为准，旧对标账号池已降级为历史参考/备用观察池，默认不参与今日候选池主流程。`01 来源与采样` 会显性展示 `名称`、`来源角色`、`栏目`、`栏目权重`、`平台`、`主页链接`、`是否参与主采样`、`默认启用`、`优先级`、`抓取方式`、`跟踪频率`、`关注重点` 和 `备注`。

- `AI业务定调`，权重 15%：数字生命卡兹克-公众号文章、秋芝2046；数字生命卡兹克-抖音教程视频是同一权重组内的辅助源。
- `真实工作流改造`，权重 25%：xuan酱、ami.moment。
- `汽车与内容营销`，权重 25%：Bob同学、数字游牧人。
- `AI导演工作流`，权重 25%：编导李让、何止维、徐老师AI。
- `AI项目复盘`，权重 10%：`AI项目复盘-待定`，当前是占位来源，不参与主采样。

除数字生命卡兹克外，其他主对标账号当前都按抖音视频源处理。抖音源第一版只做浅层采样或手动文本/OCR 导入：标题、封面、简介、可见字幕、评论问题和截图文字；不绕过登录、验证码、反爬，不保存 cookie/token。

数字生命卡兹克拆成两个独立内容源：

- `数字生命卡兹克-公众号文章`：主源，重点拆长文、行业判断、AI产品分析、观点定调和信息筛选逻辑。
- `数字生命卡兹克-抖音教程视频`：辅助源，更新频率较低，重点拆教程视频结构、工具演示节奏和普通用户可执行动作。

来源池角色统一为：

- `current_main_competitor`：当前主对标账号，参与主采样和栏目权重。
- `current_aux_competitor`：当前辅助对标来源，例如卡兹克抖音教程视频；可参与采样，但不单独增加栏目配额。
- `current_main_competitor_placeholder`：当前主对标栏目占位，例如 `AI项目复盘-待定`；显示权重缺口，但不参与主采样。
- `historical_reference`：历史对标账号，保留为备用观察池，`default_enabled=false`，默认不参与今日候选池。
- `system_hotspot_source`：系统热点源，例如 AIHOT精选、AIHOT日报。
- `official_source`：官方/技术/工具源，例如 OpenAI官网动态、Anthropic Newsroom、Product Hunt AI工具、HN AI相关讨论。
- `manual_entry`：正式手动入口；当前唯一启用入口是 `02 URL投喂入口`。
- `legacy_manual_entry`：旧手动入口说明；`手动链接/粘贴/截图转文字` 和 `粘贴入口` 已停用。

公众号文章源优先拆标题、开头、观点、论证结构、案例和 CTA；抖音视频源优先拆标题/封面、前三秒钩子、口播结构、画面/演示方式、评论问题和商业入口。

## 目录

- `sources.example.yaml`：来源配置示例，含 AIHOT、官方博客、技术社区、手动导入和重点对标账号。
- `config/content_sources.yaml`：内容源配置，说明每个对标账号对应栏目、主要内容形态、重点学习什么、不能照搬什么、转成你的什么方向。
- `data/manual/manual_items.example.jsonl`：手动导入样例，适合放抖音/小红书/视频号/公众号/AIHOT复制内容。
- `data/manual/content_items.example.jsonl`：内容对象样例，适合放公众号正文、对标视频标题/封面/字幕/OCR/评论问题。
- `scripts/run_radar.py`：采集与分析脚本。
- `scripts/content_sampler.py`：内容采样与拆解脚本，输出内容对象、内容拆解和今日候选池。
- `scripts/daily_pipeline.py`：日常总入口，默认 dry-run；显式传入 `--write-feishu` 才写入飞书。
- `scripts/url_content_resolver.py`：正式 URL 内容采样 adapter，把公众号文章、抖音单条视频、RSS/Atom、普通网页解析成标准 ContentItem；默认只输出本地文件，显式 `--write-feishu` 才写入 `03 内容收件箱`。
- `scripts/push_today10_to_feishu.py`：把今日候选池写入飞书 `04 分析与选题`，不写被淘汰的调试候选。
- `scripts/reorganize_feishu_tables.py`：保留 table_id 和数据，按新逻辑顺序重命名飞书表，并创建 `06 内容任务主表`。
- `scripts/content_ops_pipeline.py`：从 `04 分析与选题` 拆出 `05 Brief与制作` 平台内容和 `06 内容任务主表` 执行任务；默认 dry-run。
- `scripts/simplify_feishu_workspace.py`：按 v0.2 白名单清理飞书视图和 `03/04` 字段，保持每张表一个主视图。
- `output/`：运行后生成 CSV 和 Excel。
- `docs/schedule_local.md`：macOS 本机定时运行说明；当前只记录，不启用定时任务。
- `prompt_templates.md`：对标分析、热点分析、转选题、生成 Brief 的 Prompt。
- `feishu_setup.md`：飞书字段类型、视图、公式和提醒建议。
- `config/system_rules.yaml`：机器可读的系统规则源文件，说明表逻辑、字段字典、状态流转、评分规则和 AI 边界。
- `scripts/sync_rules_dictionary.py`：导出/同步 `99 规则与字典`，并在 `00 主控台` 写入规则入口卡片。
- `scripts/refresh_console_daily.py`：刷新 `00 主控台` 伪仪表盘卡片，并生成每日 `AI账号雷达日报`。

## 一键运行

日常总入口，默认 dry-run，不写飞书：

```bash
python3 scripts/daily_pipeline.py
```

只用手动样例、不访问 AIHOT：

```bash
python3 scripts/daily_pipeline.py --no-fetch-aihot
```

你只丢 URL 的正式用法：

```bash
python3 scripts/url_content_resolver.py --file data/manual/urls.example.txt --dry-run
```

这会把 URL 解析成：

- `output/url_content_items.jsonl`
- `output/url_content_items.csv`
- `output/url_content_items_manual.jsonl`

正式支持：

- 公众号文章 URL；
- 抖音单条视频 URL，包括搜索页 `modal_id` 和 `iesdouyin/share/video`；
- RSS/Atom；
- 普通网页，走 Jina Reader。

公众号文章按全文解析处理：`03 内容收件箱` 会显示 `是否全文解析`、`正文长度`、`正文/全文`、`原始payload路径` 和 `解析说明`。如果正文超过飞书字段可用长度，飞书里保留前 20000 字并在 `解析说明` 标明截断，全文仍保留在本地 payload 路径供排查和拆解复核。

抖音能力分层：

- P0：单条视频浅层解析，拿标题/文案、作者、视频 ID、封面、发布时间、标签、下载链接；不默认下载视频。
- P1：口播字幕/ASR/画面 OCR 需要下载或访问视频，并配置 ASR/OCR API key，例如 `DASHSCOPE_API_KEY` 或百炼 API key；本轮验证不作为默认接入。
- P2：账号主页最近 N 条、评论区问题，除非后续稳定且无需登录，否则暂缓。

暂不支持：

- 抖音账号主页自动抓最近 N 条；
- 抖音口播/字幕转写；
- 评论区抓取；
- 小红书；
- Twitter/X；
- Reddit。

解析失败会保留失败原因，不会静默丢弃。确认本地输出没问题后，显式写入飞书 `03 内容收件箱`：

```bash
FEISHU_APP_ID=xxx \
FEISHU_APP_SECRET=xxx \
FEISHU_BASE_APP_TOKEN=xxx \
python3 scripts/url_content_resolver.py --file data/manual/urls.example.txt --write-feishu
```

你也可以让日常管道先解析 URL，再进入内容拆解和今日候选池：

```bash
python3 scripts/daily_pipeline.py --url-file data/manual/urls.example.txt
```

默认不启用 URL 解析，只有传入 `--url-file` 或 `--resolve-url-intake` 时才运行 resolver。

卡兹克公众号 feed 已进入 P1 显式接入验证。默认 `daily_pipeline.py` 不会拉取公众号 feed；只有主动传入参数时才会读取下面这个 Wechat2RSS feed，把文章转成 ContentItem，进入 `03 内容收件箱` 和 `04 今日候选池`：

```text
https://wechat2rss.xlab.app/feed/7b1c10c25bdfe69d0a08a5349cf3b032e55f4f05.xml
```

本地验证 feed：

```bash
python3 scripts/wechat_feed_intake.py --config config/wechat_feed_candidates.yaml --limit 5 --dry-run
```

显式把 feed 合入日常管道 dry-run：

```bash
python3 scripts/daily_pipeline.py --fetch-wechat-feed --wechat-feed-limit 5
```

确认质量后，显式写入飞书：

```bash
FEISHU_APP_ID=xxx \
FEISHU_APP_SECRET=xxx \
FEISHU_BASE_APP_TOKEN=xxx \
python3 scripts/daily_pipeline.py --fetch-wechat-feed --wechat-feed-limit 5 --write-feishu
```

如果 feed 失效，继续回退到 `02 URL投喂入口` 单篇公众号文章 URL。当前 feed 条目会尝试调用公众号全文解析；如果微信页面限制导致全文解析失败，系统保留 feed 摘要、失败原因和原始 payload 路径，不伪装成全文。

卡兹克公众号全文 provider 已作为 P1 显式源接入。本地 `wewe-rss` 服务启动并订阅 `数字生命卡兹克` 后，可以显式拉取全文：

```bash
python3 scripts/daily_pipeline.py --fetch-wechat-fulltext-provider --wechat-fulltext-provider wewe-rss --wechat-feed-limit 5
```

确认质量后，显式写入飞书：

```bash
python3 scripts/daily_pipeline.py --fetch-wechat-fulltext-provider --wechat-fulltext-provider wewe-rss --wechat-feed-limit 5 --write-feishu
```

默认 `python3 scripts/daily_pipeline.py` 不拉取 `wewe-rss`，也不依赖本地全文服务。`wewe-rss` 只作为低频 P1 全文补充源；如果本地服务不可用，继续回退到 Wechat2RSS 发现源或 `02 URL投喂入口` 单篇文章 URL。

规则测试时，如果飞书里没有新的待处理 URL，但你想让已解析过的公众号/抖音内容重新参与本轮候选池，可以显式加复用参数：

```bash
FEISHU_APP_ID=xxx \
FEISHU_APP_SECRET=xxx \
FEISHU_BASE_APP_TOKEN=xxx \
python3 scripts/daily_pipeline.py --resolve-url-intake --include-resolved-url-intake
```

这个参数不会改变默认日常流程：默认仍只处理待解析 URL；开启后会复用 `02 URL投喂入口` 中已解析/重复/已存在的 URL，重新生成本地 ContentItem 参与候选，不重复写入 `03 内容收件箱`，也不会把 `02` 的状态改回待解析。正式写入时可加：

```bash
FEISHU_APP_ID=xxx \
FEISHU_APP_SECRET=xxx \
FEISHU_BASE_APP_TOKEN=xxx \
python3 scripts/daily_pipeline.py --resolve-url-intake --include-resolved-url-intake --write-feishu
```

飞书投喂方式：在 `02 URL投喂入口` 的 `URL` 字段粘贴公众号文章、抖音单条视频、RSS/Atom 或普通网页链接，然后运行：

```bash
FEISHU_APP_ID=xxx \
FEISHU_APP_SECRET=xxx \
FEISHU_BASE_APP_TOKEN=xxx \
python3 scripts/daily_pipeline.py --resolve-url-intake
```

默认仍是 dry-run，不写入飞书 `03` 或 `04`。确认字段映射、去重和今日候选池后再加 `--write-feishu`：

```bash
FEISHU_APP_ID=xxx \
FEISHU_APP_SECRET=xxx \
FEISHU_BASE_APP_TOKEN=xxx \
python3 scripts/daily_pipeline.py --resolve-url-intake --write-feishu
```

如果本机遇到 `open.feishu.cn` DNS 解析失败，可临时指定飞书开放平台域名：

```bash
FEISHU_API_BASE_URL=https://open.larksuite.com \
FEISHU_APP_ID=xxx \
FEISHU_APP_SECRET=xxx \
FEISHU_BASE_APP_TOKEN=xxx \
python3 scripts/daily_pipeline.py --resolve-url-intake --write-feishu
```

`--write-feishu` 会把解析出的新 URL 内容写入 `03 内容收件箱`，同时把本轮参与分析的 AIHOT 等 ContentItem 也同步进 `03`，再把今日候选池写入 `04 分析与选题`，并回写 `02 URL投喂入口` 的处理状态、失败原因和解析结果摘要。`02 URL投喂入口` 只作为临时链接入口，解析完成后可手动删除记录，不需要长期保留。

今日候选池不再强制凑满 10 条。AIHOT 可以是主来源，但默认最多占 8 条；如果本轮有 URL 投喂或开启已解析 URL 复用，并且 URL 内容解析成功、分数过线，系统会优先保留进入候选池。调试文件 `output/debug_today10_generation.csv` / `.md` 会显示每条候选是否来自已解析 URL 复用、是否进入候选池、标题结构模板、事件锚点、业务变化判断、内部切入角度和可发布标题。

确认 dry-run 输出没问题后，显式写入飞书 `04 分析与选题` 并刷新 `00 主控台`：

```bash
FEISHU_APP_ID=xxx \
FEISHU_APP_SECRET=xxx \
FEISHU_BASE_APP_TOKEN=xxx \
python3 scripts/daily_pipeline.py --write-feishu
```

`daily_pipeline.py` 会串起：读取内容源配置、抓取 AIHOT、读取手动公众号/视频样例、生成 ContentItem、生成内容拆解、筛选今日候选池、dry-run 展示、按需写入飞书、按需刷新主控台和输出日志。默认不写入飞书，不自动发布，不生成完整成稿。每次运行会生成一个 `运行批次`，用于在 `03 内容收件箱 / 今日采集` 和 `04 分析与选题 / 今日候选池` 中追踪本轮数据。

`04 分析与选题` 已收敛为今日候选池决策表：主字段 `选题标题` 优先展示可读标题，用于日常决策和进入 Brief。飞书默认视图只展示业务决策字段，例如 `今日建议级别`、`编辑判断分`、`AI味风险`、`内容可信度`、`推荐动作`、`原始来源标题`、`业务场景`、`推荐理由`、`不建议做的原因` 和 `可沉淀资产`。代码和调试文件仍保留更多算法字段，但默认飞书视图不展示。写入前会先经过 `editorial_judgement` 编辑判断层，让你能区分“今日最值得做”“可选候选”和“暂存观察”。没有足够人设角度或内容支撑的内容不会为了凑数进入候选池。

同步来源池和栏目权重到飞书 `01 来源与采样`：

```bash
python3 scripts/sync_source_sampling.py
```

默认只 dry-run。确认无误后，在有飞书环境变量的 shell 里运行：

```bash
FEISHU_APP_ID=xxx \
FEISHU_APP_SECRET=xxx \
FEISHU_BASE_APP_TOKEN=xxx \
python3 scripts/sync_source_sampling.py --write-feishu
```

这个脚本只 upsert `01 来源与采样` 的来源记录和最小字段，不删除记录、不重建表、不影响今日候选池规则。

生成“内容采样 + 今日候选池”：

```bash
python3 scripts/content_sampler.py
```

只用手动样例，不访问 AIHOT：

```bash
python3 scripts/content_sampler.py --no-fetch-aihot
```

输出文件：

- `output/content_items.csv`：内容对象，不是数据指标表。
- `output/content_breakdowns.csv`：每条内容的钩子、结构、专业证明、商业入口、可学习点、不能照搬点。
- `output/today_10_topics.csv`：今日候选池兼容输出文件。
- `output/daily_reports/today_10_topics_YYYY-MM-DD.md`：给你看的今日候选池 Markdown。

旧雷达导入包仍可运行，但不是当前核心方向：

在当前目录执行：

```bash
python3 scripts/run_radar.py
```

只使用手动样例、不访问 AIHOT：

```bash
python3 scripts/run_radar.py --no-fetch
```

## 不手动导入：直接写入飞书 API

飞书线上结构以 8 张核心业务表 + `99 规则与字典` 为准。`push_to_feishu.py` 当前只会处理当前 8 表命名，不会再创建旧拆分表，例如 `热点分析表`、`对标分析表`、`选题候选库`、`发布复盘表`。

如果你给一个飞书自建应用的 `App ID` / `App Secret`，并给它多维表格权限，可以直接运行：

```bash
FEISHU_APP_ID=xxx \
FEISHU_APP_SECRET=xxx \
python3 scripts/push_to_feishu.py
```

如果你已经建好一个空白多维表格，也可以加：

```bash
FEISHU_BASE_APP_TOKEN=xxx
```

这条路径用于“我来写入飞书，你不用导入 Excel”。如果 API 权限不足，脚本会明确报缺哪个权限，不会影响本地 CSV/XLSX 输出。

安全边界：`FEISHU_REPLACE_TABLES=1` 已禁用，避免误删或重建当前执行台；`99 规则与字典` 是受保护表。

## 输出文件

运行后会生成：

- `output/feishu_import_workbook.xlsx`：飞书可导入的多 Sheet 工作簿。
- `output/sources_config.csv`：来源配置表。
- `output/content_inbox.csv`：内容收件箱。
- `output/hotspot_analysis.csv`：热点分析表。
- `output/competitor_analysis.csv`：对标分析表。
- `output/topic_candidates.csv`：选题候选库。
- `output/content_briefs.csv`：内容 Brief 表。
- `output/publishing_review.csv`：发布复盘表。
- `output/system_rules_dictionary.csv`：规则与字典表，可导入飞书。
- `output/system_rules_dictionary.xlsx`：规则与字典 Excel 版。
- `output/run_log.json`：运行日志和各表数量。
- `output/content_items.csv`：AIHOT、公众号文章、对标视频、手动补充统一后的内容对象。
- `output/content_breakdowns.csv`：内容拆解结果。
- `output/today_10_topics.csv`：今日候选池兼容输出文件。

## 系统不是黑盒

当前飞书执行台有一张特殊说明表：`99 规则与字典`。它不是业务数据表，也不是新的工作流程，而是系统说明书。

当你看不懂某张表为什么存在、字段是什么意思、状态怎么流转、评分为什么这么打、AI 哪些地方参与、哪些地方必须人工判断时，先看 `99 规则与字典`。

你可以按 `规则类型` 查看：

- `表逻辑`：核心业务表分别为什么存在，是前台表还是后台表，数据从哪里来，流向哪里，是否每天需要打开。
- `字段字典`：关键字段的含义、生产者、消费者、是否可编辑、AI 是否参与。
- `状态字典`：`03 内容收件箱` 的内容处理流、`04 分析与选题` 的选题决策流、`05 Brief与制作` 的制作发布流。
- `评分规则`：选题总分的维度、权重、高分标准、低分标准和人工修正规则。
- `AI处理规则`：AI 能做摘要、分类、分析、候选选题和 Brief 提纲；不能做完整成稿、自动发布、伪造数据、绕过平台限制或替你最终决定观点。
- `主控台/日报规则`：`00 主控台` 是伪仪表盘/每日入口；日报如果生成，只回答“今天我该做什么”，不写成长报告。

本地规则源文件是 `config/system_rules.yaml`。后续如果你觉得某条规则不合理，可以直接让 Codex 修改这份文件，并重新运行：

```bash
python3 scripts/sync_rules_dictionary.py
```

如果需要同步到飞书，使用：

```bash
FEISHU_APP_ID=xxx \
FEISHU_APP_SECRET=xxx \
FEISHU_BASE_APP_TOKEN=xxx \
python3 scripts/sync_rules_dictionary.py --sync-feishu
```

注意：`99 规则与字典` 是受保护表。旧的飞书写入脚本在执行重建时不会删除它。

## 飞书执行台用法

当前真实使用路径以飞书执行台为主：

1. 每天默认打开 `00 主控台 / 今日工作台`，只看当天动作、预警、进度和临时入口。
2. 如果想理解系统关系，再切到 `00 主控台 / 系统导航`，或看 `docs/system_map.md`。
3. 优先看 `今日候选池`，而不是看原始内容、粉丝数、点赞数或竞品报表。
4. 进入 `04 分析与选题` 做选题决策：状态只使用 `待判断`、`进入Brief`、`本周做`、`暂存`、`归档`、`不做`。
5. 进入 `05 Brief与制作` 补案例和制作：状态只使用 `待补案例`、`可制作`、`已制作待发布`、`已发布待复盘`、`复盘完成`。
6. 进入 `06 内容任务主表` 看今天要完成的写稿、拍摄、封面、发布、直播和复盘任务。
7. 必要时打开 `02 URL投喂入口`，手动粘贴公众号文章、抖音单条视频、RSS/Atom 或普通网页链接；小红书、视频号、评论区和抖音主页批量抓取暂不支持。
8. 不要每天看系统导航，也不需要每天打开 `01 来源与采样`、`02 URL投喂入口`、`03 内容收件箱` 和 `99 规则与字典`。

CSV/Excel 仍可作为降级输出或导入包，但不要把 `topic_candidates.csv`、`content_briefs.csv` 当作主要工作台。

当前飞书表视图以“日常入口清爽”为准；`00 主控台` 有两个入口视图，`01 来源与采样` 有四个来源管理视图：

- `00 主控台 / 今日工作台`：默认日常入口，只显示今日工作、预警提醒、进度统计、临时入口。
- `00 主控台 / 系统导航`：系统地图和表格说明，不是每天要看的工作台。
- `01 来源与采样 / 当前主对标池`
- `01 来源与采样 / 历史参考池`
- `01 来源与采样 / 系统/官方源`
- `01 来源与采样 / 手动入口`
- `02 URL投喂入口 / URL投喂入口`
- `03 内容收件箱 / 内容收件箱`
- `03 内容收件箱 / 今日采集`
- `04 分析与选题 / 今日候选池`
- `05 Brief与制作 / Brief制作后台`
- `06 内容任务主表 / 今日待办`
- `07 资产与复盘 / 资产复盘后台`
- `99 规则与字典 / 规则与字典`

其中 `01 来源与采样 / 当前主对标池` 默认只看主对标、辅助对标和 AI项目复盘占位；旧字段 `来源名称`、`获取方式`、`关注重点/原始内容`、`是否主对标`、`是否重点跟踪` 不进入默认视图。`02 URL投喂入口` 是输入层，不是任务表；`03 内容收件箱` 是所有参与拆解的内容账本，包括 URL 投喂和 AIHOT；`04 分析与选题 / 今日候选池` 是选题决策区；`06 内容任务主表` 才是写稿、拍摄、剪辑、封面、发布、直播、复盘、私信跟进和资产化任务的执行表。

## 每天打开什么

每天：

1. 打开 `00 主控台 / 今日工作台`。
2. 先看 `今日候选池`、`今日必须完成`、`明日预警`、`本周内容进度`。
3. 再看 `待复盘内容`、`可复刻内容`、`来源异常/采集失败`。
4. 进入 `04 分析与选题`，把值得做的选题改为 `进入Brief` 或 `本周做`。
5. 如需把 `04` 的选题拆成 Brief 和任务，运行 `python3 scripts/content_ops_pipeline.py --write-feishu`，它会把状态为 `进入Brief` 或 `本周做` 且未拆分的选题承接到 `05 Brief与制作` 和 `06 内容任务主表`。
6. 进入 `05 Brief与制作`，补真实案例、个人判断、视觉建议和 CTA。
7. 进入 `06 内容任务主表`，只看今天必须完成和本周任务。
8. 必要时进入 `02 URL投喂入口`，粘贴公众号文章、抖音单条视频、RSS/Atom 或普通网页链接。
9. 只有看不懂表关系时，才切到 `00 主控台 / 系统导航` 或打开 `docs/system_map.md`。

每周：

1. 打开 `07 资产与复盘`。
2. 看可沉淀资产、复盘结果和下周方向。
3. 只选择一个最值得先做的清单、SOP、流程图、案例库或资料包。

刷新主控台和生成日报：

```bash
FEISHU_APP_ID=xxx \
FEISHU_APP_SECRET=xxx \
FEISHU_BASE_APP_TOKEN=xxx \
python3 scripts/refresh_console_daily.py
```

日报输出在 `output/daily_reports/`。日报只回答“今天我该做什么”，不会生成完整成稿、自动发布、伪造数据或绕过平台限制。

本机定时运行先不启用；以后确认稳定后可参考 `docs/schedule_local.md`。

说明：脚本会确保核心视图存在。`06 内容任务主表` 第一版视图包括 `今日待办`、`明日预警`、`本周任务`、`发布相关任务`、`直播排期`、`复盘任务`。如果飞书 OpenAPI 不支持复杂筛选条件，先只创建视图名，筛选规则写在 README 和规则表里。

## 每天怎么用

1. 打开 `00 主控台 / 今日工作台`。
2. 进入 `04 分析与选题`，优先看高分和推荐等级 A/B 的候选，决定 `进入Brief`、`本周做`、`暂存`、`归档` 或 `不做`。
3. 进入 `05 Brief与制作`，补真实案例、个人判断、素材、截图和边界。
4. 必要时用 `02 URL投喂入口` 手动粘贴公众号文章、抖音单条视频、RSS/Atom 或普通网页链接。
5. 如果需要排查来源、重复内容或采集状态，再打开 `03 内容收件箱`。

## 每周怎么复盘

每周打开 `07 资产与复盘`，看哪些内容能沉淀成资料包、模板、SOP、案例库或服务入口，再调整下周选题。

周复盘重点看三类信号：

- 哪类内容带来收藏：说明资产有价值。
- 哪类内容带来评论/私信：说明用户有真实业务问题。
- 哪类内容带来资料包领取/咨询线索：说明能承接转化。

下周只加码一个强栏目，砍掉一个弱钩子。首月优先验证咨询和工作流诊断，不急着卖课。

## 评分规则

当前 `今日候选池` 的评分不是过滤技术热点，而是判断“适不适合我蹭，以及从什么角度蹭”。模型更新、框架更新、平台能力变化、Agent 框架变化、AI视频模型变化都可以进入候选池；不能原样搬运资讯，但可以转成产品生死线、工作流重排、非技术人机会、内容团队变化、AI导演工作流、Agent落地或商业化机会。

`对应栏目` 和 `热点切入方式` 分开判断：

- `对应栏目` 只表示最终属于哪个账号栏目：AI业务定调、真实工作流改造、汽车与内容营销、AI导演工作流、AI项目复盘。
- `热点切入方式` 只表示这条热点怎么蹭：产品生死线、工作流重排、非技术人机会、内容团队变化、AI导演流程、Agent落地、品牌风控/信任危机、商业化机会、项目复盘启发、对标结构学习。
- AIHOT 来源不默认等于 AI业务定调。只有行业判断、反常识观点、趋势解释才归到 AI业务定调。
- Runway/Kling/Luma/Seedance/视频/分镜/镜头/成片优先校准到 AI导演工作流；公众号首图、小红书卡片、教程步骤卡、视觉模板优先校准到真实工作流改造；LlamaIndex/OpenRouter/Codex/Claude Code/Agent/MCP/API 优先校准到真实工作流改造，热点切入方式标记为 Agent落地 或 非技术人机会；SHEIN、AI假人、虚假广告、带货、品牌、汽车、信任、合规、审核优先校准到 汽车与内容营销 或 AI业务定调。
- 标题必须像真实可发布选题，指向具体人群或流程；不要把原始标题硬塞进重复模板。

今日候选池默认栏目配额不是硬切，但会优先保持题材多样：

- `AI业务定调`：1-2 条。
- `真实工作流改造`：2-3 条。
- `汽车与内容营销`：2-3 条。
- `AI导演工作流`：2-3 条。
- `AI项目复盘`：0-1 条。

如果某个栏目当天没有高质量候选，可以由其他高分栏目补位；日报或日志里应说明补位原因。卡兹克公众号文章属于 AI业务定调主源，卡兹克抖音教程视频属于 AI业务定调辅助源，不因为拆成两个源就让 AI业务定调过量占比。

今日候选池核心权重：

- 热度/时效性 20%
- 账号角度匹配 20%
- 业务影响 20%
- 差异化解读 15%
- 可转化动作 15%
- 制作成本反向 10%

推荐动作分层：

- `立即蹭热点`：每天 3-4 条，适合短视频、小红书短帖、公众号短评，不一定需要完整 Brief。
- `进入Brief`：每天最多 1-2 条，必须适合沉淀成流程、清单、案例、长文或资料包。
- `本周做`：每天最多 2 条，重要但需要补材料。
- `暂存观察`：热但角度还不够明确。
- `不做`：和定位无关，或只能资讯搬运。

日报和 Markdown 会额外给出 `今日最建议动作`，只推荐 1 条最值得优先处理的内容，避免把 10 条都变成“今天必须做”。

旧版热点评分权重仍可作为降级脚本参考：

- 时效性 12%
- 来源可信度 14%
- 与内容/营销/Agent/AI视频/业务流程相关度 20%
- 差异化解读空间 16%
- 可执行资产沉淀价值 16%
- 平台适配度 10%
- 制作成本反向 12%

对标评分权重：

- 钩子可学习性 14%
- 结构可复用性 16%
- 评论需求强度 14%
- 差异化改写空间 18%
- 资产沉淀价值 16%
- 定位匹配 12%
- 制作成本反向 10%

分数只是排序工具，必须同时看“推荐理由”“可学习点”“不能照搬点”“业务场景切入”。

## AIHOT 接入说明

AIHOT 官方公开方式包括网页、Skill、RSS 和 REST API。公开页面说明它匿名免费、无需 token；API 端点需要带浏览器 User-Agent，否则会被 403 拦截。脚本已内置合规 UA。

当前脚本使用：

- 精选条目：`/api/public/items?mode=selected&take=30`
- 日报：`/api/public/daily`

如果单个 AIHOT 源返回 HTML、空内容或非 JSON，脚本会记录来源名、URL、HTTP/解析状态和返回 preview，并在主控台显示“部分源失败”；不会阻塞 AIHOT精选、内容拆解或今日候选池。

AIHOT 的做法值得学习：信源分级、官方源优先、AI 预筛、聚类去重、模型/产品/行业/论文/技巧分桶、固定日报。但你的二次筛选标准不是“AI 新闻重要吗”，而是“它能否进入内容团队、品牌增长、AI导演、Agent或创业项目的业务现场”。

## 自动拉取边界

当前默认自动源是 AIHOT精选、AIHOT日报、官方 RSS/Atom、官方网页/普通网页/Jina Reader，以及 `02 URL投喂入口` 的单条 URL 投喂。主对标账号自动抓取仍处于 P1：抖音主页最近 N 条、公众号历史文章列表不直接进入默认 `daily_pipeline.py`。`--include-resolved-url-intake` 只用于复用已解析 URL 做规则测试，不作为默认日常流程；卡兹克公众号 Wechat2RSS 发现源只在显式传 `--fetch-wechat-feed` 时拉取，本地 `wewe-rss` 全文源只在显式传 `--fetch-wechat-fulltext-provider` 或 `--wechat-fulltext-provider wewe-rss` 时拉取。

完整路线、主对标池逐个判断和未来 PoC 分支命名见 [docs/source_autofetch_plan.md](docs/source_autofetch_plan.md)。

卡兹克公众号发现源与全文源的结论见 [docs/source_autofetch_plan.md](docs/source_autofetch_plan.md) 和 [docs/spikes/wechat_fulltext_provider_eval.md](docs/spikes/wechat_fulltext_provider_eval.md)。当前口径是：Wechat2RSS 公共 feed 适合发现文章列表，不是稳定全文源；`wewe-rss` 已验证可作为本地全文 provider，但必须显式启用；`we-mp-rss` 因需要公众号平台扫码授权，已从当前主路线降级。默认流程不依赖这些服务。

抖音开源工具评估见 [docs/spikes/douyin_open_source_tool_eval.md](docs/spikes/douyin_open_source_tool_eval.md)。当前口径是：单条视频 metadata 继续使用项目内 `url_content_resolver.py`；账号主页最近 N 条的 P1 候选是 `MediaCrawler`，但需要抖音小号和本机浏览器登录态，必须单独 probe；口播字幕/ASR 的 P1 候选是 `douyin-mcp-server`、`wanyi-watermark` 或 `social-post-extractor-mcp`，需要显式命令和 ASR key，不进入默认流程。

## 采集边界

- 不绕过登录、验证码、反爬或平台限制。
- 不保存账号密码、cookie、token。
- 不强抓抖音、小红书、视频号等高风险平台。
- 抖音当前只支持单条视频浅采样：标题/文案、作者、视频 ID、封面、发布时间、标签、下载链接记录；不下载视频，不抓主页列表，不抓评论，不做默认转写。已验证 `douyin-mcp-server` / `wanyi-watermark` 等上游工具的转写能力依赖 ASR API key，基础解析不比当前 `_ROUTER_DATA` 解析更稳。
- 小红书、Twitter/X、Reddit 暂不接入正式采样链路。
- 能通过 RSS、公开网页、公开 API、AIHOT、GitHub、Product Hunt、Hacker News、官方博客稳定获取的内容，才进入自动化。

## 内容拆解边界

本系统要看的不是粉丝数和点赞数，而是视频/文章如何选题、如何开头、如何组织内容、如何证明专业、如何引导转化，以及它能如何转成你的选题。

ContentItem 字段包括：来源类型、平台、账号名/公众号名、内容标题、内容链接、内容形态、封面文字、正文/字幕/简介片段、发布时间、评论区问题、截图/OCR文本、抓取方式、抓取状态、失败原因、内容指纹。

每条对标内容都会拆解：

- 这条内容讲了什么
- 标题/前三秒钩子
- 内容结构
- 专业性证明方式
- 商业入口/转化动作
- 我可以学什么
- 不能照搬什么
- 如何转成我的业务现场选题
- 对应栏目
- 是否值得进入今日候选池

## 内容生成边界

本阶段只生成内容 Brief：一句话核心判断、目标用户、开头3秒、内容结构、必须讲清的3个点、可用案例、视觉建议、CTA、资料包承接方式。不会生成完整小红书文案、公众号文章或短视频成稿。

## 后续增强

- 把飞书表字段改成正式关联字段、单选、多选、评分、公式。
- 接入飞书 API：当 token 可用时直接写入多维表格；不可用时继续导出 Excel/CSV。
- 增加 RSS 拉取器：OpenAI、Anthropic、Hacker News、GitHub Trending。
- 增加“手动粘贴长文本解析”：把整段 AIHOT 日报或对标内容自动拆成多条 JSONL。
