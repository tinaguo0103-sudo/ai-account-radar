# AR-050 视频理解 Runtime

正常 08:00 仍只调用 `scripts/run_daily_workflow.py`。视频理解是 collection 后的内部
stage，不创建第二个 daemon、schedule 或数据 authority。

## Runtime 准备

1. 将 `scripts/douyin_video_vision_ocr.swift` 编译到项目 ignored runtime：

   ```bash
   swiftc scripts/douyin_video_vision_ocr.swift \
     -o .runtime/ar050/douyin_video_vision_ocr
   ```

2. 在 ignored venv 安装已验收的 SenseVoice/FunASR runtime，模型使用本机明确路径。
3. 如需英文专名或词级时间轴 fallback，再配置 MLX Full Large-v3；Turbo 不属于正常
   runtime。
4. 从 `config/douyin_video_runtime.example.json` 创建 ignored runtime config，并通过
   `DOUYIN_VIDEO_RUNTIME_CONFIG` 指向它。配置、模型和 binary 不提交 Git。
5. 发布前 check-only：

   ```bash
   python3 scripts/douyin_video_understanding_producer.py \
     --runtime-config "$DOUYIN_VIDEO_RUNTIME_CONFIG" --check-only
   ```

缺少 ffmpeg、Vision binary、SenseVoice Python、SenseVoice 模型或 FSMN-VAD 模型时，
公共 workflow 在创建 workflow DB、采集、Skill 调用或 projection 前 typed fail。
不自动修改 Homebrew、system Python 或下载模型。

## 正常调用图

`run_daily_workflow.py`
→ AR-048 fixed-profile preflight
→ `douyin_video_discovery.mjs` 在既有页面观察推荐流和宽泛动态搜索
→ OR policy + 去重/预算
→ 公开媒体下载
→ ffmpeg keyframes/audio
→ product-owned macOS Vision OCR
→ SenseVoice + FSMN-VAD 补缺
→ 必要时 Full Large-v3
→ exact-run atomic package/read-back/cleanup
→ active editorial Skill
→ exact editorial-selected unparsed IDs on-demand parse
→ active scripting Skills
→ Website projection。

`--video-candidates/--video-decisions/--video-packages` 只允许配合显式
`--video-mode qa-fixture` 或 `offline-recovery`。`--video-mode normal` 发现这些参数会
fail closed，避免下一次正式运行再次依赖人工预制 JSON。

任何 fixed 9333 风控信号立即 source-global stop，之后导航与媒体动作均为 0。系统不
处理验证码，不读取 Cookie/session，不切换 profile，不使用代理或指纹方案。
