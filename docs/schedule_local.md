# 本机轻量 watcher 运行说明

当前正式脚本包生成走本机轻量 watcher。原因是 `06 完整脚本与制作包` 需要 Codex 和全局私有 Skill 参与，不能退化成纯模板代码；但也不应该每小时固定消耗 Codex automation 额度。

边界要清楚：

- 卡片点击回写仍由腾讯云 SCF receiver 承接。
- `06` 脚本包生成由本机轻量 watcher `scripts/watch_script_package_queue.py` 承接。
- watcher 每隔几分钟扫描飞书 `04`，空队列只做飞书 API 检查，不调用 Codex；只有存在待生成记录时才调用 `codex_script_package_runner.py` 和 `codex exec`。
- 自动队列默认和卡片有效期一致，只扫近 5 天推荐记录，并排除明显测试标题；旧记录或测试记录要用 `--record-id` / `--include-test-records` 手动补跑。
- 锁屏但 Mac 不睡眠、不断网时可以跑；睡眠后唤醒一般会继续跑；关机或重启期间不会跑，但登录后会由 LaunchAgent 自动拉起。

## 1. 当前生产 watcher

当前不使用 Codex App automation。旧的 `ai-06` 每小时 automation 已停用，避免空队列时仍占用 Codex automation 调度额度。

安装为登录后自动启动：

```bash
python3 scripts/install_script_package_watcher_launch_agent.py --interval-minutes 5 --limit 2 --max-age-days 5
```

这个命令会先把运行时同步到 `~/.codex/ai-account-radar-runtime`，再安装用户级 LaunchAgent。这样后台进程不直接读取 Desktop 下的仓库，避免 macOS TCC 拦截。之后如果改了 watcher、runner、Skill 镜像或字段映射，要重新运行一次安装命令，或只同步 runtime：

```bash
python3 scripts/install_script_package_watcher_launch_agent.py --sync-runtime-only
```

查看 LaunchAgent 状态：

```bash
python3 scripts/install_script_package_watcher_launch_agent.py --status
```

启动前台 watcher：

```bash
python3 scripts/watch_script_package_queue.py --interval-minutes 5 --limit 2 --max-age-days 5
```

启动后台 watcher：

```bash
screen -dmS ai06-watcher python3 scripts/watch_script_package_queue.py --interval-minutes 5 --limit 2 --max-age-days 5
```

查看是否在运行：

```bash
screen -ls
```

停止后台 watcher：

```bash
screen -S ai06-watcher -X quit
```

旧的 `install_codex_script_package_launchd.py` 已不作为生产路径。原因是它让 launchd 直接定时跑生成器，既不符合轻量 watcher 语义，也容易因为项目目录在 Desktop 下触发 macOS TCC 后台权限拦截。当前只使用 `install_script_package_watcher_launch_agent.py` 安装登录自启 watcher，且 LaunchAgent 从非 Desktop runtime 目录启动。

只检查队列、不调用 Codex：

```bash
python3 scripts/watch_script_package_queue.py --once --dry-run --limit 5 --max-age-days 5
```

手动立即生成：

```bash
python3 scripts/codex_script_package_runner.py --write-feishu --limit 2 --max-age-days 5
```

指定单条生成：

```bash
python3 scripts/codex_script_package_runner.py --write-feishu --record-id <04_record_id>
```

## 2. 日常采集和选题

当前 watcher 只负责生成 `06`，不会生成新选题，也不会发送第一张选题卡。

在项目目录运行：

```bash
python3 scripts/daily_pipeline.py --resolve-url-intake --write-feishu
```

日常使用以飞书为准，不需要先 dry-run。脚本会处理 `02 URL投喂入口` 的新链接，复用当天抖音主页采集缓存，生成今日候选池，写入 `03 内容收件箱`、`04 分析与选题` 并刷新 `00 主控台`。

抖音主页采集默认同一天只跑一次；当天再次运行会复用 `output/source_collection_cache/YYYY-MM-DD/` 里的采集结果，避免反复触发平台风控。只有改采集逻辑、主页链接、登录态或明确复验采集时，才加：

```bash
python3 scripts/daily_pipeline.py --resolve-url-intake --force-fetch-douyin --write-feishu
```

## 3. 开发验证和排障

dry-run 只用于改代码、改采集规则或排查字段问题，不作为日常步骤：

```bash
python3 scripts/daily_pipeline.py --resolve-url-intake
```

只使用手动样例、不访问 AIHOT：

```bash
python3 scripts/daily_pipeline.py --no-fetch-aihot
```

只测试 Skill 或标题判断时，不要重新采集，直接复用最近一次正式输出：

```bash
python3 scripts/editorial_skill_runner.py \
  --engine codex \
  --input output/latest_write/today_10_topics.csv \
  --output output/latest_write/today_10_topics.csv \
  --report output/latest_write/editorial_skill_report.json
```

写入边界：

- 只写入 `04 分析与选题` 的今日候选池。
- 正式输出写入 `output/runs/<run_id>/`，并同步到 `output/latest_write/`；根目录 `output/today_10_topics.csv` 仅作为最近一次正式写入的兼容文件。
- 不写入被淘汰的调试候选。
- 不新增业务表。
- 不自动发布。
- 定时采集/选题阶段不生成完整成稿；已确认选题的口播稿与执行包由本机轻量 watcher 按需生成。
- 不强抓抖音、小红书、视频号。

## 4. 所需环境变量

- `FEISHU_APP_ID`
- `FEISHU_APP_SECRET`
- `FEISHU_BASE_APP_TOKEN`
- `CODEX_BIN` 可选；默认 `/Applications/Codex.app/Contents/Resources/codex`

不要把这些值写入仓库。建议放在本机 shell 配置、密码管理器或本机 `.env` 文件里；`.env` 已被 `.gitignore` 排除。

## 5. 失败后查看日志

daily pipeline 会写入：

```text
output/logs/daily_pipeline_YYYY-MM-DD.json
```

06 watcher 会写入：

```text
output/logs/script_package_watcher_YYYY-MM-DD.log
```

06 runner 会写入：

```text
output/logs/codex_script_package_runner_YYYY-MM-DD.log
```

常见处理：

- AIHOT 失败：流程仍会继续，可用 `--no-fetch-aihot` 或手动补充内容。
- 飞书环境变量缺失：补齐 `FEISHU_APP_ID`、`FEISHU_APP_SECRET`、`FEISHU_BASE_APP_TOKEN` 后重跑。
- 飞书权限不足：检查自建应用是否有多维表格读写权限。
- Codex 失败：确认 Codex 桌面端已登录，`/Applications/Codex.app/Contents/Resources/codex exec` 可在终端运行。
- watcher 不触发：先运行 `python3 scripts/install_script_package_watcher_launch_agent.py --status`，再查看 `~/Library/Logs/ai-account-radar/script_package_watcher_launch_agent.err.log` 和 `output/logs/script_package_watcher_YYYY-MM-DD.log`。

## 6. 手动重跑

正式写入：

```bash
FEISHU_APP_ID=你的AppID \
FEISHU_APP_SECRET=你的AppSecret \
FEISHU_BASE_APP_TOKEN=你的BaseAppToken \
python3 scripts/daily_pipeline.py --write-feishu
```

生成 06 脚本包：

```bash
python3 scripts/codex_script_package_runner.py --write-feishu --limit 2 --max-age-days 5
```
