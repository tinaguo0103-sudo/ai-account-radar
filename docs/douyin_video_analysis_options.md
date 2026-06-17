# 抖音视频内容解析能力分层

本项目当前不把抖音完整视频理解接入默认链路。默认链路只做可公开访问、低风险、可解释的单条链接浅层解析。

## P0 当前已接

- 分享链接 / `modal_id` 解析
- 视频 ID / metadata
- 标题 / 文案
- 作者
- 发布时间
- 标签
- 封面
- 下载链接记录
- 部分统计字段（如点赞、收藏、评论数，取决于公开 payload）

P0 不下载视频，不保存 cookie/token，不抓评论区，不抓账号主页列表，不做默认口播转写。

## P1 可选增强

P1 只建议对用户指定的单条视频显式执行，例如：

```bash
python3 scripts/douyin_video_transcribe.py --url <douyin_url> --asr dashscope
```

或：

```bash
python3 scripts/douyin_video_transcribe.py --url <douyin_url> --asr local
```

建议链路：

1. 解析分享链接，拿视频 ID 和可访问视频 URL。
2. 临时下载视频到 ignored cache，或直接把视频 URL 交给云端 ASR。
3. 抽音频。
4. ASR 转写口播。
5. 可选抽帧做 OCR / VLM。
6. 把转写结果作为补充字段进入内容拆解，但不替代人工判断。

DashScope / 百炼不是必须。它只是当前工程上最轻的 P1 路线之一。完整视频理解不是“把抖音链接丢给百炼就结束”，而是：抖音 URL 解析 → 拿视频 URL → 下载或提交可访问视频 → ASR 转写口播 → 转写文本再进入内容拆解。

### 路线 A：DashScope / 百炼 ASR / paraformer

优点：

- 和 `douyin-mcp-server`、`wanyi-watermark`、`social-post-extractor-mcp` 等工具链兼容度高。
- 云端跑，工程接入较轻。
- 中文口播场景可直接用 `DASHSCOPE_API_KEY` 或 `BAILIAN_API_KEY`。
- 当前优先模型为 `paraformer-v2`，原因是用户已在百炼控制台开启 paraformer 免费额度并设置“免费额度用完即停”。

缺点：

- 需要 API key。
- 有调用成本，通常按语音时长或任务量计费；适合小规模显式转写测试，具体费用以官方控制台和文档为准。
- 依赖外部服务稳定性和配额。

适合先做 P1 显式命令，不适合默认跑。

成本保护：

1. 不对主页采样到的所有视频默认转写。
2. 先运行候选筛选，只根据标题/文案/账号/时长判断是否值得转写：

```bash
python3 scripts/douyin_transcript_candidates.py
```

3. 候选筛选会输出：

```text
output/spikes/douyin_transcript_candidates/transcript_candidates.csv
output/spikes/douyin_transcript_candidates/transcript_candidates.md
```

4. 真正调用 ASR 必须显式执行，并且默认 dry-run：

```bash
python3 scripts/douyin_video_transcribe.py --url <douyin_url>
```

5. 如果确认百炼 paraformer 免费额度已开启且“用完即停”，再执行：

```bash
python3 scripts/douyin_video_transcribe.py --url <douyin_url> --model paraformer-v2 --confirm-free-quota --yes
```

6. 长视频默认拒绝转写。超过 `DOUYIN_ASR_MAX_SINGLE_MINUTES`（默认 15 分钟）需要显式加 `--allow-long`，否则不会消耗额度。

可选环境变量：

```bash
DOUYIN_ASR_MODEL=paraformer-v2
DOUYIN_ASR_MAX_SINGLE_MINUTES=15
DOUYIN_ASR_MANUAL_CONFIRM_MINUTES=30
DOUYIN_ASR_MAX_SUGGESTED_PER_RUN=2
DOUYIN_ASR_PRICE_PER_MINUTE=<如需按单价估算再填写>
DOUYIN_ASR_MAX_COST_YUAN=<如需硬成本上限再填写>
```

如果不配置单价，脚本不会猜测费用，只会显示视频时长，并依赖百炼控制台的免费额度/用完即停保护。

### 路线 B：本地 Whisper / faster-whisper

优点：

- 不依赖云端 key。
- 数据不出本机。

缺点：

- 需要安装 `ffmpeg`、模型和运行环境。
- 首次部署较重。
- 速度和硬件压力更大，长视频更明显；对少量指定视频可行，但不建议当前作为每日批量默认链路。

适合后续需要低成本批量转写时评估。

### 路线 C：SenseVoice

优点：

- 中文语音场景可能更适合。
- 具备较强 ASR 和语音理解能力。

缺点：

- 需要本地模型和运行环境。
- 工程复杂度高于云端 ASR。
- 仍需要处理下载、抽音频、长视频切分和错误重试。
- 对中文口播可能更适合，但本地负载、模型管理和环境维护成本高于云端方案。

适合作为本地化 P1/P2 方案，不建议当前默认接入。

## P2 暂缓

- 评论区问题抓取
- 账号主页最近 N 条自动发现
- 镜头结构理解
- 全自动批量视频理解

这些能力更容易遇到登录态、验证码、反爬、页面结构变化、媒体下载成本和事实核查风险。当前阶段先不进入默认链路。

## 当前推荐

短期继续保持 P0：单条链接浅层解析 + 人工判断。

主页采样已通过本机专用 Chrome CDP profile + 抖音小号低频验证：同一账号主页可以拿到多条可信作品，并复用 `url_content_resolver.py` 输出本地 ContentItem。但它仍是 P1 显式探针，不进入默认 `daily_pipeline.py`。

为避免 Chrome 页面打扰用户，先启动或复用后台专用 Chrome：

```bash
python3 scripts/start_douyin_cdp_chrome.py --port 9333
```

这条命令默认使用 `hidden` 模式，尽量不把专用 Chrome 顶到前台。主页采样脚本会使用后台 target 打开博主页，并尽量最小化专用 Chrome，避免每个主页采样时反复弹窗。登录过期、验证码或扫码时再临时前台打开：

```bash
python3 scripts/start_douyin_cdp_chrome.py --port 9333 --foreground
```

登录完成后，低频采样：

```bash
node scripts/douyin_cdp_source_watch_probe.mjs --cdp http://127.0.0.1:9333 --account-limit 3 --video-limit 3
```

如果要做 P1，优先做显式命令，不要改默认 `daily_pipeline.py`：

- 云端优先：DashScope / 百炼 `paraformer-v2` ASR，适合快速验证。
- 本地备选：faster-whisper 或 SenseVoice，适合后续对成本和数据边界更敏感时评估。
