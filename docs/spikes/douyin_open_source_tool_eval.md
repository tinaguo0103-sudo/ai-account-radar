# 抖音开源采样工具评估

## 一页结论

本轮没有从零写抖音采样器，而是盘点并验证已有路线。结论是：

- **单条视频 metadata**：继续优先使用项目内已接入的 `url_content_resolver.py` / `_ROUTER_DATA` 解析。它对本轮测试的抖音搜索页 `modal_id` 链接成功，能拿标题/文案、作者、视频 ID、发布时间、标签、封面 URL、下载链接和部分统计/描述线索。
- **单条视频 ASR/字幕**：`douyin-mcp-server` / `wanyi-watermark` 更适合作为 P1 显式转写路线。当前优先 `paraformer-v2`，因为用户已开启百炼 paraformer 免费额度和“用完即停”；不进入默认链路。
- **账号主页最近 N 条**：已通过专用 Chrome CDP profile + 抖音小号低频验证，可以从部分主页拿到多条可信作品并复用单条 resolver。它仍是 P1 显式探针，不直接进默认 `daily_pipeline.py`。
- **Agent-Reach**：更像工具路由、安装器和能力清单，不适合作为本项目正式生产依赖；可以复用它的工具选择思路。
- **Douyin_TikTok_Download_API**：能力最全，文档声明支持视频数据、用户主页作品、评论等，但明确需要自行处理 Cookie/风控配置。适合自部署评估，不适合当前直接默认接入。

## 已有主线能力

| 能力 | 当前状态 | 文件/结论 |
| --- | --- | --- |
| 抖音单条 URL 浅层解析 | 已进入 main | `scripts/url_content_resolver.py` |
| 抖音浅层内容进入选题时降权/复核 | 已进入 main | `scripts/content_sampler.py`、`config/system_rules.yaml` |
| 抖音主页最近 N 条 | 历史 POC 结论：无登录公开 HTML 不稳定 | `docs/spikes/archive_spike_findings.md` |
| 抖音 ASR/OCR | 历史 POC 结论：需要外部 ASR key 或本地模型 | `docs/douyin_video_analysis_options.md` |

## 本轮真实验证

测试链接：

```text
https://www.douyin.com/search/%E6%AD%B8%E8%97%8F%20guizang.ai?aid=e99f9039-d470-40fc-9fa2-1e55799bb2b8&modal_id=7548465155441544475&type=general
```

### 项目内 `_ROUTER_DATA` 解析

命令：

```bash
python3 scripts/url_content_resolver.py --file /tmp/douyin_url.txt --dry-run
```

结果：成功。

拿到字段：

- 账号名：`歸藏`
- 标题/文案：`藏师傅教你一镜到底｜新 AI 爆款视频密码 #人工智能 #ai新星计划 #视频制作`
- 视频链接 / 原始链接
- 视频 ID / 下载链接
- 发布时间：`2025-09-10T22:20:19`
- 标签、封面 URL、作者签名
- 内容指纹

缺失字段：

- 口播字幕
- 音频转写
- 画面 OCR
- 评论区问题
- 镜头结构

### douyin-mcp-server / wanyi-watermark

本机已有 `.venv-douyin`，版本：

- `douyin-mcp-server 1.2.1`
- `wanyi-watermark 1.0.1`
- `dashscope 1.25.20`

同一条搜索页 `modal_id` 链接测试结果：失败。

失败信息：

```text
抖音视频解析失败: list index out of range；兜底解析失败：未从页面中发现可用的视频直链
```

判断：它更适合普通分享短链/视频页、无水印下载链接和后续 ASR；对搜索页 `modal_id` 的适配不如当前 `_ROUTER_DATA` 解析稳。

### Douyin_TikTok_Download_API 在线演示

对同一链接请求在线 demo：

```text
https://api.douyin.wtf/api/hybrid/video_data?minimal=false&url=...
```

结果：HTTP 520。  
判断：线上 demo 不适合作为稳定依赖。该项目更适合自部署；但自部署需要处理 Cookie/风控配置，不适合直接进默认流程。

### 账号主页最近 N 条

本轮未读取用户浏览器 profile、未保存 cookie、未绕验证码，因此没有直接抓主页最近作品。根据 MediaCrawler 文档和源码：

- 支持平台：抖音。
- 支持类型：关键词搜索、指定帖子 ID、二级评论、指定创作者主页。
- 登录态：支持 `qrcode` / `phone` / `cookie`，支持保存登录状态。
- 技术路线：Playwright + CDP，使用真实浏览器上下文获取签名参数。
- 风险：需要用户小号登录、低频、可能遇到手机号验证/验证码/风控。

## 能力矩阵

| 工具 | 已验证能力 | 账号主页最近 N 条 | 单条视频 metadata | 下载链接 | 字幕/ASR | 依赖 | 风险 | 推荐等级 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `url_content_resolver.py` / `_ROUTER_DATA` | 本轮真实跑通搜索页 `modal_id` | 不支持 | 支持 | 支持记录下载链接 | 不支持 | 无登录，公开页面 | 页面结构变化 | **主路线：单条 metadata** |
| `Agent-Reach` | 文档盘点 | 不直接承担 | 通过路由外部工具 | 取决于工具 | 取决于工具 | 多工具、Cookie 路由 | 依赖面大 | 仅调研/工具清单 |
| `douyin-mcp-server` / `wanyi-watermark` | 本机已装；同一 `modal_id` 链接失败 | 不支持主页订阅 | 普通分享链接可能支持 | 支持 | 支持但需 ASR key | `DASHSCOPE_API_KEY`/百炼、可能下载视频 | 下载/ASR 成本 | **备选：P1 单条转写** |
| `social-post-extractor-mcp` | 文档盘点 | 未验证 | 面向单条链接 | 可能支持 | 百炼 ASR/OCR | `BAILIAN_API_KEY`/DashScope 类 key | 外部服务、媒体处理 | 备选：P1 转写/OCR |
| `MediaCrawler` | 文档和源码盘点 | 支持创作者主页 | 支持指定帖子 | 不作为重点 | 不作为重点 | Playwright/CDP、登录态缓存 | 验证码/风控/非商业许可 | **主路线候选：P1 主页采样** |
| `douyin_cdp_source_watch_probe.mjs` | 专用 Chrome 登录后真实跑通多账号低频主页采样 | 支持部分账号低频采样，多条作品 | 复用 resolver | 不作为重点 | 不支持 | 本机 Chrome 远程调试、用户小号登录态 | 页面异常、登录/验证、推荐流污染 | **P1 主路线候选** |
| `Douyin_TikTok_Download_API` | 文档盘点；在线 demo 本轮 520 | 文档声明支持 | 文档声明支持 | 支持 | 不内置完整 ASR | 自部署、Cookie/风控配置 | 维护成本高 | 备选：自部署 API |
| `douyin-downloader` | 未找到比以上更稳的独立主路线 | 未验证 | 单条下载/metadata 候选 | 支持下载方向 | 通常不含 ASR | 取决于项目 | 维护分散 | 仅调研 |

## 推荐路线

1. **订阅/主页最近 N 条**  
   当前优先用项目内 `douyin_cdp_source_watch_probe.mjs` 做 P1 显式低频采样：专用 Chrome profile、抖音小号登录态、每账号最近 3-5 条、只读、不抓评论、不批量历史、不进入默认 `daily_pipeline.py`。如果后续需要更稳定的批量账号管理，再评估 `MediaCrawler`。

2. **单条视频 metadata**  
   继续用当前 `url_content_resolver.py`。它对搜索页 `modal_id` 和公开页面 `_ROUTER_DATA` 的覆盖比本轮测试的 `douyin-mcp-server` 更适合本项目。

3. **视频 ASR / 字幕**  
   P1 可选 `douyin-mcp-server` / `wanyi-watermark` 或 `social-post-extractor-mcp`，但需要显式命令、ASR key、低频执行，不默认跑。当前新增两层保护：先用 `douyin_transcript_candidates.py` 选候选，再用 `douyin_video_transcribe.py` 显式调用 `paraformer-v2`。建议命令形态：

```bash
python3 scripts/douyin_transcript_candidates.py
python3 scripts/douyin_video_transcribe.py --url <douyin_url> --model paraformer-v2 --confirm-free-quota --yes
```

4. **是否需要自有 wrapper**  
   需要，但只做很薄的 wrapper：把外部工具结果转成标准 ContentItem，不重新实现平台协议。下一步可以基于 `scripts/douyin_open_source_probe.py` 扩展正式 P1 source watch。

5. **暂不进入默认流程的能力**  
   抖音主页最近 N 条、评论区问题、口播转写、画面 OCR、批量下载、全量历史抓取，都不进入默认 `daily_pipeline.py`。

## 抖音小号下一步

如果要继续 P1 主页采样，用户需要：

1. 在本机 Chrome 用抖音小号登录。
2. 提供 2-3 个对标账号主页链接。
3. 允许单独 P1 probe 用 Playwright/CDP 低频只读打开主页。
4. 如果出现验证码/手机号验证，由用户在浏览器里手动完成；不要把 cookie、二维码、profile 发到聊天里。

## 本轮边界

- 未写飞书。
- 未改默认 `daily_pipeline.py`。
- 未改 Top10 规则。
- 未下载视频/音频。
- 未保存 cookie、token、二维码、浏览器 profile。
- 未抓评论区。

## 2026-06-16 主页 source watch probe 复验

新增显式脚本：

```bash
python3 scripts/douyin_source_watch_probe.py --account-limit 3 --video-limit 2
```

本脚本不写飞书、不接默认 `daily_pipeline.py`、不保存 cookie/token/profile、不下载视频、不抓评论。它读取 `config/content_sources.yaml` 里的当前主/辅助抖音对标账号，低频请求主页公开 HTML；如果能解析到作品 ID，就复用现有 `url_content_resolver.py` 做单条视频浅层解析并输出本地 ContentItem。

本轮真实结果：

| 账号 | 结果 | 说明 |
| --- | --- | --- |
| 秋芝2046 | partial | 公开页面可访问，但返回内容更像 JS 壳，没有解析出作品 ID。 |
| xuan酱 | partial | 公开页面可访问，但返回内容更像 JS 壳，没有解析出作品 ID。 |
| ami.moment | needs_url | 当前配置缺少抖音主页链接。 |

结论：

- 无登录公开主页抓取仍不足以稳定发现最近 N 条作品。
- `douyin_source_watch_probe.py` 可以作为轻量探针保留，用于验证某些主页是否公开暴露作品 ID。
- 真正要推进账号主页最近 N 条，应进入 P1 登录态路线：优先 `MediaCrawler`，使用抖音小号、本机浏览器登录态、每账号最近 3 条、低频只读。
- 单条视频 metadata 仍继续用 `url_content_resolver.py`；主页发现和视频理解不要混成一个默认流程。

## 2026-06-16 Chrome CDP 主页探针复验

新增显式脚本：

```bash
node scripts/douyin_cdp_source_watch_probe.mjs --account-limit 3 --video-limit 2
```

脚本边界：

- 只连接本机 `http://127.0.0.1:9222` 的 Chrome DevTools Protocol。
- 不导出浏览器 profile。
- 不读取、保存或提交 cookie/token。
- 不写飞书、不进 `03/04`、不接默认 `daily_pipeline.py`。
- 不下载视频、不抓评论、不做批量历史。
- 只有发现可信账号作品 ID 时才复用 `url_content_resolver.py` 生成本地 ContentItem。

本轮真实结果：

| 账号 | 结果 | 说明 |
| --- | --- | --- |
| 秋芝2046 | needs_login_or_verification | CDP 可打开主页，但作品区提示“服务异常/重新刷新拉取数据”；页面里发现的 video ID 更像热门推荐，不可信。 |
| xuan酱 | needs_login_or_verification | CDP 可打开主页，但作品区同样异常；发现的 video ID 不作为账号最近作品。 |
| ami.moment | needs_url | 当前配置缺少抖音主页链接。 |

关键修正：

- 早期探针能从页面 HTML 里看到 `/video/` ID，但这些 ID 并不一定来自目标账号主页作品区。
- 现在脚本会检测 `服务异常` / `重新刷新拉取数据` 等页面状态；如果作品区异常，发现的 ID 只写入 `untrusted_video_ids`，不会进入 resolver，也不会输出 ContentItem。
- 这一步很重要：它避免把抖音热门推荐误当成对标账号最近作品，防止后续选题池被脏数据污染。

结论：

- Chrome CDP 路线比纯 HTML 请求更接近可用，因为它可以复用用户本机浏览器状态。
- 但在未获得健康登录态/可信作品区前，仍不能说“账号主页最近 N 条已跑通”。
- 下一步若继续，应由用户在本机 Chrome 远程调试 profile 中登录抖音小号，手动完成任何验证码/手机号验证；脚本再低频复验 2-3 个账号，每个账号最多最近 2-3 条。
- 即便后续跑通，也应先作为 P1 显式 probe，不进入默认 `daily_pipeline.py`。
