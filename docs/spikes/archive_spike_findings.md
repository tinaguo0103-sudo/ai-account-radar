# Spike 结论归档

本文件只保留 spike 分支的最终判断和迁移去向，不迁移实验脚本、外部依赖、生成产物、大文件、媒体文件、cookie、token 或 secret。

## spike/agent-reach-content-ingestion

- 原始目标：验证 Agent-Reach 及上游工具是否适合接入内容采样链路。
- 最终结论：不建议把 Agent-Reach 整套作为生产主依赖；已验证 P0 能力应抽取为轻量 URL 解析器。
- 已迁移：是。
- 迁移到：
  - `scripts/url_content_resolver.py`
  - `docs/source_autofetch_plan.md`
  - `docs/douyin_video_analysis_options.md`
- 删除判断：可删除。本分支主要结论已经进入 main，实验脚本和输出不保留。
- 未保留内容：`scripts/spike_*`、外部安装尝试和 `output/spikes/*`。

## spike/source-watch-wechat-rss

- 原始目标：验证公众号自动发现最新文章是否能接入卡兹克来源。
- 最终结论：`needs_user_dependency`。单篇公众号 URL 已稳定；公众号自动发现需要用户可控 RSS/订阅服务。
- 已迁移：是。
- 迁移到：
  - `docs/source_autofetch_plan.md`
  - `scripts/wechat_feed_intake.py`
  - `config/wechat_feed_candidates.yaml`
  - `docs/spikes/wechat_fulltext_provider_eval.md`
- 删除判断：旧候选验证脚本和长报告可删除；结论已迁移到 source_autofetch_plan 和全文 provider 评估文档。
- 未保留内容：`scripts/spike_source_watch_wechat_rss.py`、`scripts/probe_wechat_feed_candidates.py`、`docs/spikes/wechat_feed_candidate_verification.md`。

## spike/source-watch-douyin-homepage

- 原始目标：验证抖音主页最近 N 条自动发现。
- 最终结论：默认接入为 `blocked_not_recommended`；只读公开 HTML 没有稳定最近视频 metadata。
- 已迁移：是。
- 迁移到：
  - `docs/source_autofetch_plan.md`
  - `docs/douyin_video_analysis_options.md`
- 删除判断：可删除。主页自动发现不进入 main 默认流程。
- 未保留内容：`scripts/spike_source_watch_douyin_homepage.py`。

## spike/douyin-asr-transcript

- 原始目标：验证抖音口播转写能力。
- 最终结论：`needs_user_dependency`。DashScope/百炼需要 API key；本地 Whisper/SenseVoice 需要 ffmpeg、模型和本地算力。
- 已迁移：是。
- 迁移到：
  - `docs/douyin_video_analysis_options.md`
- 删除判断：可删除。P1 若要继续，应另开显式 ASR 功能分支，不进入默认 daily_pipeline。
- 未保留内容：`scripts/douyin_video_transcribe.py`。

## spike/wechat-feed-source-watch

- 原始目标：设计 RSS/Atom/JSON feed source watch POC。
- 最终结论：`needs_user_dependency`。如果有可用 feed URL，可用轻量 source watch 转成 ContentItem；但默认不写飞书、不进 Top10。
- 已迁移：是。
- 迁移到：
  - `scripts/wechat_feed_intake.py`
  - `config/wechat_feed_candidates.yaml`
  - `scripts/wechat_fulltext_provider_probe.py`
  - `config/wechat_fulltext_provider.example.yaml`
  - `docs/spikes/wechat_fulltext_provider_eval.md`
- 删除判断：可删除。main 已保留显式 feed intake 和 wewe-rss fulltext provider adapter，不再保留旧候选验证 POC。
- 未保留内容：`config/wechat_feed_sources.example.yaml`、`scripts/wechat_feed_source_watch_poc.py`。

## spike/short-video-visible-sampler

- 原始目标：验证短视频可见内容采样。
- 最终结论：`needs_user_dependency`。稳定采样需要用户本地已登录浏览器 profile，且只适合低频只读 P1/P2，不进默认流程。
- 已迁移：是。
- 迁移到：
  - `docs/source_autofetch_plan.md`
  - `docs/douyin_video_analysis_options.md`
- 删除判断：可删除。该路线不应保留为 main 默认能力。
- 未保留内容：`config/short_video_sources.example.yaml`、`scripts/short_video_visible_sampler_poc.py`。

## spike/video-asr-ocr-understanding

- 原始目标：验证视频 ASR / OCR 内容理解。
- 最终结论：`needs_user_dependency`。完整视频理解需要下载/访问视频、抽音频、ASR，可选 OCR；只适合显式 P1，不进入默认采集。
- 已迁移：是。
- 迁移到：
  - `docs/douyin_video_analysis_options.md`
- 删除判断：可删除。
- 未保留内容：`scripts/video_understanding_poc.py` 和生成 transcript。

## 删除后保留原则

- main 只保留稳定脚本、配置样例和结论文档。
- 后续如需继续做公众号自动发现，应新开小分支，并基于 `scripts/wechat_feed_intake.py` 或新的 `source_watch_probe` 实现；不得把实验依赖和外部仓库提交进 main。
- 后续如需继续做抖音主页、短视频可见采样、ASR/OCR，应重新开独立 spike，并继续遵守“不写飞书、不改表结构、不进 Top10”的边界。
