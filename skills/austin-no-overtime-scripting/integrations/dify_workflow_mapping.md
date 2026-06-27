# Dify 工作流映射

v0.6 可拆成以下节点：

1. Start：接收 Topic Card JSON 和可选制作方向补充。
2. Parameter Extractor：标准化 `04` 字段，不从短标题重新理解选题。
3. If/Else：判断缺字段、证据缺口和事实核验点。
4. LLM/Tool Node：调用 `austin-voice-scriptwriter` 生成 Austin 口播全文。
5. LLM/Template Node：编排视频结构、录屏清单、剪辑交接、发布包和 QA。
6. Output：返回单一 Markdown 主文档 `full_script_execution_package.md` 和 `06 完整脚本与制作包` 轻量记录字段。

注意：Dify 不负责自动发布，也不拆拍摄、剪辑、发布任务；任务表后续单独设计。
