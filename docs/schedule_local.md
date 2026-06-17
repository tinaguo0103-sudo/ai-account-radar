# macOS 本机定时运行说明

当前先不启用定时任务。这里仅记录以后确认稳定后如何开启。

## 1. 先做 dry-run 测试

在项目目录运行：

```bash
python3 scripts/daily_pipeline.py
```

dry-run 会抓取 AIHOT、读取手动内容样例、生成内容拆解和今日候选池，并打印将写入飞书的候选摘要；不会写入飞书。

dry-run 输出只写入 `output/dry_runs/<run_id>/`，并同步到 `output/latest_dry_run/`。它不会覆盖最近一次正式写入飞书的 `output/latest_write/`，也不会覆盖根目录兼容文件 `output/today_10_topics.csv`。

只使用手动样例、不访问 AIHOT：

```bash
python3 scripts/daily_pipeline.py --no-fetch-aihot
```

## 2. 正式写入飞书

确认 dry-run 质量稳定后，再显式加 `--write-feishu`：

```bash
FEISHU_APP_ID=你的AppID \
FEISHU_APP_SECRET=你的AppSecret \
FEISHU_BASE_APP_TOKEN=你的BaseAppToken \
python3 scripts/daily_pipeline.py --write-feishu
```

写入边界：

- 只写入 `04 分析与选题` 的今日候选池。
- 正式输出写入 `output/runs/<run_id>/`，并同步到 `output/latest_write/`；根目录 `output/today_10_topics.csv` 仅作为最近一次正式写入的兼容文件。
- 不写入被淘汰的调试候选。
- 不新增业务表。
- 不自动发布。
- 不生成完整成稿。
- 不强抓抖音、小红书、视频号。

## 3. 所需环境变量

- `FEISHU_APP_ID`
- `FEISHU_APP_SECRET`
- `FEISHU_BASE_APP_TOKEN`

不要把这些值写入仓库。建议放在本机 shell 配置、密码管理器或本机 `.env` 文件里；`.env` 已被 `.gitignore` 排除。

## 4. 失败后查看日志

脚本会写入：

```text
output/logs/daily_pipeline_YYYY-MM-DD.json
```

常见处理：

- AIHOT 失败：流程仍会继续，可用 `--no-fetch-aihot` 或手动补充内容。
- 飞书环境变量缺失：补齐 `FEISHU_APP_ID`、`FEISHU_APP_SECRET`、`FEISHU_BASE_APP_TOKEN` 后重跑。
- 飞书权限不足：检查自建应用是否有多维表格读写权限。

## 5. 手动重跑

dry-run：

```bash
python3 scripts/daily_pipeline.py
```

正式写入：

```bash
FEISHU_APP_ID=你的AppID \
FEISHU_APP_SECRET=你的AppSecret \
FEISHU_BASE_APP_TOKEN=你的BaseAppToken \
python3 scripts/daily_pipeline.py --write-feishu
```

当前不建议开启 launchd 定时任务。等确认今日候选池质量和飞书写入节奏稳定后，再创建本机定时任务。
