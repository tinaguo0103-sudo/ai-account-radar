# n8n 夜间批处理建议

v0.2 只做单一脚本大纲确认稿生成和 `05` 轻量索引回写，不自动拆 `06`。

节点建议：

1. Schedule Trigger：每天23:30。
2. Feishu Bitable / HTTP Request：读取 `04` 中进入Brief/本周做的记录。
3. Function：过滤未生成脚本稿的记录。
4. Execute Command：调用 `batch_render.py` 生成 `script_outline_brief.md`。
5. Execute Command：调用 `merge_daily_index.py`。
6. Feishu Update Record：回写 `05`。
7. Bot Webhook：推送索引链接。
