# Codex 本机定时运行说明

当前正式脚本包生成走本机定时。原因是 `06 完整脚本与制作包` 需要 Codex 和全局私有 Skill 参与，不能退化成纯模板代码。

边界要清楚：

- 卡片点击回写仍由腾讯云 SCF receiver 承接。
- `06` 脚本包生成由 Codex App automation `ai-06` 定时运行 `scripts/codex_script_package_runner.py`。
- runner 先扫描飞书 `04`，只有存在待生成记录时才调用 `codex exec`，不会空跑消耗 LLM。
- 自动队列默认和卡片有效期一致，只扫近 5 天推荐记录，并排除明显测试标题；旧记录或测试记录要用 `--record-id` / `--include-test-records` 手动补跑。
- 锁屏但 Mac 不睡眠、不断网时可以跑；睡眠、关机、断网时不会跑，恢复后等下一次定时触发或手动补跑。

## 1. 当前生产定时任务

当前已创建 Codex App automation：

- ID：`ai-06`
- 名称：`AI账号 06脚本包生成`
- 频率：每小时一次
- 工作区：`/Users/congcong/Desktop/AI/AI项目/AI账号工作流/ai_account_radar`
- 命令：`python3 scripts/codex_script_package_runner.py --write-feishu --limit 2 --max-age-days 5`

`launchd` 方案已不作为生产路径。原因是项目目录在 Desktop 下，macOS TCC 会阻止后台 `/usr/bin/python3` 打开脚本，日志表现为 `Operation not permitted`。保留 `scripts/install_codex_script_package_launchd.py` 只作为以后迁移到非 Desktop 路径后的备用方案。

手动重跑：

```bash
python3 scripts/codex_script_package_runner.py --write-feishu --limit 2 --max-age-days 5
```

只检查是否有待生成选题，不调用 Codex：

```bash
python3 scripts/codex_script_package_runner.py --skip-codex --limit 2 --max-age-days 5
```

指定单条生成：

```bash
python3 scripts/codex_script_package_runner.py --write-feishu --record-id <04_record_id>
```

## 2. 日常采集和选题

当前 `ai-06` 只负责生成 `06`，不会生成新选题，也不会发送第一张选题卡。

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
- 定时采集/选题阶段不生成完整成稿；已确认选题的口播稿与执行包由 Codex App automation 生成。
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

06 定时 runner 会写入：

```text
output/logs/codex_script_package_runner_YYYY-MM-DD.log
```

常见处理：

- AIHOT 失败：流程仍会继续，可用 `--no-fetch-aihot` 或手动补充内容。
- 飞书环境变量缺失：补齐 `FEISHU_APP_ID`、`FEISHU_APP_SECRET`、`FEISHU_BASE_APP_TOKEN` 后重跑。
- 飞书权限不足：检查自建应用是否有多维表格读写权限。
- Codex 失败：确认 Codex 桌面端已登录，`/Applications/Codex.app/Contents/Resources/codex exec` 可在终端运行。
- 定时不触发：确认 Mac 没有睡眠，检查 Codex App automation `ai-06` 是否处于 ACTIVE。

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
