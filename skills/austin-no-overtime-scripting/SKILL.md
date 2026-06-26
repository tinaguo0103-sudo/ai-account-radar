---
name: austin-no-overtime-scripting
description: 将奥斯汀AI账号已确认选题、04工作流实验命题卡、Topic Card或制作方向补充，编排成完整口播稿与制作执行包。适用于 AI业务定调、真实工作流改造、AI导演工作流、非技术Agent实战、AI汽车与品牌增长现场、AI项目复盘。默认调用 austin-voice-scriptwriter 生成真人口播全文，并输出 full_script_execution_package.md；05 Brief与制作只作为飞书轻量索引，不再生成中间大纲。不得用于自动发布、自动剪辑或替用户最终决定观点。
---

# Austin不加班脚本Skill

这个 Skill 现在只做一件事：把已经选中的 `04` 选题和人工补充方向，转成一份能直接进入拍摄准备的完整执行包。

默认输出一个主文档：

```text
full_script_execution_package.md
```

飞书 `05 Brief与制作` 只保存状态、摘要、本地文档路径和 QA，不再让用户先读一份中间大纲。

## 职责边界

1. `04 分析与选题` 负责选题判断、切入点、工作流痛点、AI介入点、验证方式和证据线索。
2. 交互式选题卡负责让用户勾选要推进的题；第二张补充卡负责补“这条想怎么讲、用什么案例、不要讲什么”。
3. `austin-voice-scriptwriter` 负责写接近 Austin 真人口播习惯的全文。
4. 本 Skill 负责把口播全文编排成执行包：视频结构、分段口播、录屏/素材清单、剪辑交接、发布包草稿、QA。
5. `05 Brief与制作` 是索引表，不是内容承载表；完整内容以本地 Markdown 为准。
6. `06 内容任务主表` 只有在用户确认执行包可制作后，后续流程才拆任务。

## 核心原则

1. 不要从短标题重新理解选题，必须承接 `04 工作流实验命题卡` 和人工制作补充。
2. 不要再生成 `script_outline_brief.md` 作为默认中间产物；旧函数只为历史兼容保留。
3. 输出必须摘要先行：用户打开文档第一屏就能看到生产判断、钩子、待办、核验点和边界。
4. 口播全文要优先由 `austin-voice-scriptwriter` 生成；本 Skill 不重新塞一套口播风格规则。
5. 不要强制指定用户必须使用某个案例或资产；案例只能作为“可选参考”或“建议素材”。
6. 公开事实和可拍素材要分开：官网、公示、公告是事实依据；自己的截图、字段表、失败样例、人工修正点才是制作证据。
7. 如果公开资料可检索，Codex 使用本 Skill 时应先主动检索和引用；搜不到或无法核验时才标为缺口。
8. 涉及产品能力、平台规则、价格、政策、数据、汽车功能边界时，必须进入“发布前核验”。
9. `暂无`、`待补实验动作`、`待补旧流程痛点` 等占位内容不能当成真实字段；缺关键字段就标记 blocked。
10. 执行包要保留真实失败、人工修正和边界提醒，不要把 AI 写成一次就全对。

## 工作流

1. 将输入映射成 Topic Card。
2. 校验必填字段和证据状态，得到 `pass/revise/blocked`。
3. 判断制作模板：`Skill公开型`、`热点业务转译型`、`认知定调型`、`真实工作流改造型`、`Agent实战型`、`项目复盘型`。
4. 读取全局私有 `austin-voice-scriptwriter`，生成口播全文；需要测试仓库脱敏镜像时必须显式设置 `AUSTIN_VOICE_SCRIPT_SKILL_DIR`。
5. 生成 `full_script_execution_package.md`。
6. 飞书 `05 Brief与制作` 只写轻量索引：脚本状态、推荐模板、核心观点、视频结构摘要、执行包输入、说明、本地文档、是否可进入06、版本。
7. 不自动写 `06 内容任务主表`，除非后续流程已有用户确认。

## 输出文档

`full_script_execution_package.md` 必须按这个顺序写：

1. `一屏结论`：生产判断、推荐模板、核心观点、开头钩子、拍摄前待办、发布前核验、本条边界。
2. `视频结构`：按时间线写完整结构，不是散点清单。
3. `口播全文`：直接可读、可改、可放进提词器。
4. `分段执行方案`：时间、段落、真人口播、画面/录屏、剪辑重点、QA。
5. `录屏与素材清单`：已有证据、待补素材、用途、优先级、状态。
6. `剪辑交接`：结果闪现、前后对比、字幕重点、真人切回、节奏停顿。
7. `发布包草稿`：标题、封面大字、置顶评论。
8. `QA`：`pass/revise/blocked` 和原因。

不要恢复 00-08 多文件散包，也不要把完整内容塞进飞书长文本。

## 资源使用

- 批量从 `04` 生成完整执行包并写 `05` 索引：运行仓库脚本 `scripts/content_ops_pipeline.py --write-feishu`。
- 从指定 `04` 记录生成单条执行包：运行 `scripts/generate_script_execution_package.py --record-id <04_record_id> --write-feishu`。
- 只校验 Topic Card：运行 `scripts/validate_topic_card.py`。
- 汇总某天本地执行包：运行 `scripts/merge_daily_index.py`。
- 调整 Austin 真人口播风格：修改独立 Skill `austin-voice-scriptwriter`。
- 需要字段映射细节：读 `integrations/feishu_bitable_mapping.md`。

## 运行时规则

- 生产默认读取全局私有版：`/Users/congcong/.codex/skills/austin-no-overtime-scripting`。
- 仓库版本是公开脱敏镜像，只用于 Git、迁移、同步和显式测试。
- 生产脚本如果找不到全局私有版，应直接失败；不要静默回退仓库镜像。
- 需要测试仓库镜像时，显式设置 `AUSTIN_SCRIPT_SKILL_DIR=skills/austin-no-overtime-scripting`。
- 全局私有版可以在 `references/private/` 保存真实项目、敏感案例、内部表达偏好；这些内容不要提交到 Git。

## 安全边界

- 不要自动发布、自动剪辑、自动创建最终任务。
- 不要把完整脚本当成事实核验完成的发布稿。
- 不要把建议案例写成已经发生的真实案例。
- 不要声称工具能力已经验证，除非输入里有可展示证据。
