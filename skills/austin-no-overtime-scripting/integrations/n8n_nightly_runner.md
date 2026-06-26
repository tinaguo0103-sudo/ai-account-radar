# n8n 夜间批处理建议

v0.5 直接生成完整口播稿与执行包，并把 `05 Brief与制作` 作为轻量索引回写；不自动拆 `06 内容任务主表`。

节点建议：

1. Schedule Trigger：每天固定时间。
2. Feishu Bitable / HTTP Request：读取 `04` 中已进入 Brief/制作的记录。
3. Function：过滤未生成脚本稿的记录，并合并第二张“制作方向补充”结果。
4. Execute Command：调用 `scripts/content_ops_pipeline.py --write-feishu` 生成 `full_script_execution_package.md`。
5. Execute Command：调用 `merge_daily_index.py` 汇总本地执行包。
6. Feishu Update/Create Record：回写 `05` 轻量索引。
7. Bot Webhook：推送执行包路径和 QA 摘要。

注意：如果要拆拍摄、录屏、剪辑、发布任务，需要在用户确认执行包可制作后再进入后续流程。
