# n8n 夜间批处理参考

当前生产路径已经改为本机轻量 watcher + Codex runner。n8n 只作为历史替代方案或以后跨系统编排参考，不作为当前生产路径。

v0.6 直接生成完整口播稿与执行包，并把 `06 完整脚本与制作包` 回写；不自动拆拍摄、剪辑、发布任务。

节点建议：

1. Schedule Trigger：每天固定时间。
2. Feishu Bitable / HTTP Request：读取 `04` 中人工状态为 `生成脚本包 / 进入Brief / 本周做` 的记录。
3. Function：过滤未生成脚本稿的记录，并合并第二张“制作方向补充”结果。
4. Execute Command：调用 `scripts/codex_script_package_runner.py --write-feishu --limit 2 --max-age-days 5` 生成本地平铺 Markdown，仅作为替代编排方案。
5. Execute Command：调用 `merge_daily_index.py` 汇总本地执行包。
6. Feishu Update/Create Record：回写 `06 完整脚本与制作包` 轻量记录。
7. Bot Webhook：推送执行包路径和 QA 摘要。

注意：如果要拆拍摄、录屏、剪辑、发布任务，需要后续单独设计任务表，不恢复旧中间 Brief 表。
