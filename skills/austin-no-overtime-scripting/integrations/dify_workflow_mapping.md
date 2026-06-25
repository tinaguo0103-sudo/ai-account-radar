# Dify 工作流映射

v0.2 可拆成以下节点：

1. Start：接收 Topic Card JSON。
2. Parameter Extractor：标准化字段。
3. If/Else：判断缺字段。
4. LLM Node：模板判断。
5. LLM Node：核心观点、视频大纲、给06的生成输入。
6. Output：返回单一 Markdown 脚本大纲确认稿。

注意：Dify 只负责生成 `05` 脚本大纲确认稿，不负责自动发布，也不直接拆 `06`。完整脚本包由 `06` 在人工确认后生成。
